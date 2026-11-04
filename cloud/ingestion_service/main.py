#!/usr/bin/env python3
"""
Ingestion service - runs as a single-replica Deployment on AKS.

Consumes everything the fleet sends to IoT Hub's built-in Event
Hub-compatible endpoint:
  - telemetry: each numeric field becomes a (device, timestamp, metric,
    value) row in Postgres; the Device row's last_seen / status /
    firmware_version are updated; live (non-replayed) messages feed the
    per-device detector (detector.py)
  - recovery events (message property type=event): forwarded to Django's
    internal API, which stamps the matching incident

Detections are posted to the remediation Function's dispatcher, which
queues them for the escalation ladder.

Schema written here matches the Django models in webapp/devices/models.py
(Device, DeviceMetric) - this service writes with raw SQL rather than the
ORM so it has no dependency on the Django codebase, but the table/column
names must stay in sync with those migrations.

The detector keeps per-device state in memory, which is why this must run
as exactly one replica.
"""
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import psycopg2
import requests
from azure.eventhub import EventHubConsumerClient
from dotenv import load_dotenv

from detector import Detection, DesiredConfig, FleetDetector

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("ingestion_service")

EVENTHUB_COMPATIBLE_CONNECTION_STRING = os.environ["IOTHUB_EVENTHUB_COMPATIBLE_CONNECTION_STRING"]
EVENTHUB_COMPATIBLE_PATH = os.environ["IOTHUB_EVENTHUB_COMPATIBLE_PATH"]
CONSUMER_GROUP = os.environ.get("IOTHUB_CONSUMER_GROUP", "$Default")
POSTGRES_URL = os.environ["POSTGRES_URL"]
DJANGO_INTERNAL_API_BASE = os.environ["DJANGO_INTERNAL_API_BASE"].rstrip("/")
INTERNAL_API_TOKEN = os.environ["INTERNAL_API_TOKEN"]
REMEDIATION_DISPATCH_URL = os.environ["REMEDIATION_DISPATCH_URL"]

SWEEP_INTERVAL_SECONDS = 5
# devices quiet for longer than this are considered retired, not failing
SWEEP_LOOKBACK = "1 hour"

# telemetry keys that should NOT be stored as individual (metric, value) rows
_NON_METRIC_KEYS = {"ts", "seq"}

_outbound = ThreadPoolExecutor(max_workers=4, thread_name_prefix="outbound")


def get_db_connection():
    conn = psycopg2.connect(POSTGRES_URL)
    conn.autocommit = True
    return conn


def _post(url: str, payload: dict, headers: dict | None = None) -> None:
    try:
        resp = requests.post(url, json=payload, headers=headers or {}, timeout=15)
        if resp.status_code >= 300:
            logger.warning("POST %s -> %s %s", url.split("?")[0], resp.status_code, resp.text[:300])
    except requests.RequestException as exc:
        logger.warning("POST %s failed: %s", url.split("?")[0], exc)


def dispatch_detection(detection: Detection) -> None:
    logger.info("DETECTION %s on %s: %s", detection.signal, detection.device_id, detection.evidence)
    _outbound.submit(_post, REMEDIATION_DISPATCH_URL, detection.as_job())


def forward_recovery_event(device_id: str, event: dict) -> None:
    logger.info("recovery event from %s: %s", device_id, event)
    _outbound.submit(
        _post,
        f"{DJANGO_INTERNAL_API_BASE}/internal/recovery-events/",
        {"device_id": device_id, "event": event},
        {"Authorization": f"Bearer {INTERNAL_API_TOKEN}"},
    )


detector = FleetDetector(on_detection=dispatch_detection)


def upsert_device(cursor, device_id: str, seen_at: datetime, firmware: str | None) -> tuple[int, DesiredConfig]:
    cursor.execute(
        """
        INSERT INTO devices_device (device_id, display_name, device_type, is_physical, ip_address,
                                    status, last_seen, firmware_version)
        VALUES (%s, %s, 'esp32', TRUE, '', 'online', %s, COALESCE(%s, ''))
        ON CONFLICT (device_id) DO UPDATE
            SET last_seen = EXCLUDED.last_seen,
                status = 'online',
                firmware_version = COALESCE(%s, devices_device.firmware_version)
        RETURNING id, desired_telemetry_interval_s, desired_heap_low_bytes, desired_edge_healing
        """,
        (device_id, device_id, seen_at, firmware, firmware),
    )
    pk, interval, heap_low, edge = cursor.fetchone()
    return pk, DesiredConfig(interval, heap_low, edge)


def insert_metrics(cursor, device_pk: int, measured_at: datetime, body: dict) -> None:
    rows = [
        (device_pk, measured_at, metric, float(value))
        for metric, value in body.items()
        if metric not in _NON_METRIC_KEYS and isinstance(value, (int, float))
    ]
    if not rows:
        return
    cursor.executemany(
        """
        INSERT INTO devices_devicemetric (device_id, "timestamp", metric, value)
        VALUES (%s, %s, %s, %s)
        """,
        rows,
    )


def _app_property(event, name: str) -> str | None:
    props = event.properties or {}
    value = props.get(name.encode()) or props.get(name)
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return value


def handle_event(cursor, event) -> None:
    device_id = event.message.annotations.get(b"iothub-connection-device-id")
    if device_id is None:
        logger.warning("event missing device-id annotation, skipping")
        return
    device_id = device_id.decode("utf-8")

    try:
        body = json.loads(next(event.body))
    except (StopIteration, json.JSONDecodeError, TypeError) as exc:
        logger.warning("could not decode event body from %s: %s", device_id, exc)
        return

    if _app_property(event, "type") == "event":
        forward_recovery_event(device_id, body)
        return

    replay = _app_property(event, "replay") == "1"
    now = datetime.now(timezone.utc)
    # Buffered messages keep the time they were measured, not when they arrived.
    ts = body.get("ts")
    measured_at = datetime.fromtimestamp(ts, tz=timezone.utc) if replay and isinstance(ts, (int, float)) and ts > 1e9 else now

    fw = body.get("fw") if isinstance(body.get("fw"), str) else None
    device_pk, desired = upsert_device(cursor, device_id, now, fw)
    insert_metrics(cursor, device_pk, measured_at, body)
    detector.observe(device_id, body, desired, replay)


def on_event_batch(partition_context, events):
    if not events:
        return

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            for event in events:
                handle_event(cursor, event)
        logger.info("processed batch of %d events", len(events))
    finally:
        conn.close()

    # No checkpoint store is configured: this is a research demo with a
    # single consumer and starting_position="-1" (new events only), so
    # losing position on restart just means missing the events sent during
    # the restart window, not reprocessing. Add blob checkpoint storage
    # before running this with multiple replicas or needing exactly-once.


def on_error(partition_context, error):
    logger.error("error on partition %s: %s", partition_context.partition_id if partition_context else "?", error)


def staleness_sweeper() -> None:
    """Feeds the detector each device's telemetry age, skipping devices in a
    maintenance window (firmware rollouts)."""
    conn = None
    while True:
        try:
            if conn is None or conn.closed:
                conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT device_id, EXTRACT(EPOCH FROM (now() - last_seen))
                    FROM devices_device
                    WHERE last_seen > now() - interval '{SWEEP_LOOKBACK}'
                      AND (maintenance_until IS NULL OR maintenance_until < now())
                    """
                )
                detector.sweep({device_id: float(age) for device_id, age in cursor.fetchall()})
        except psycopg2.Error:
            logger.exception("staleness sweep failed")
            conn = None
        time.sleep(SWEEP_INTERVAL_SECONDS)


def main() -> None:
    threading.Thread(target=staleness_sweeper, name="sweeper", daemon=True).start()

    client = EventHubConsumerClient.from_connection_string(
        conn_str=EVENTHUB_COMPATIBLE_CONNECTION_STRING,
        consumer_group=CONSUMER_GROUP,
        eventhub_name=EVENTHUB_COMPATIBLE_PATH,
    )
    logger.info("starting ingestion service, consumer group=%s", CONSUMER_GROUP)
    with client:
        client.receive_batch(
            on_event_batch=on_event_batch,
            on_error=on_error,
            starting_position="-1",  # only new events; fine for a research demo
        )


if __name__ == "__main__":
    main()
