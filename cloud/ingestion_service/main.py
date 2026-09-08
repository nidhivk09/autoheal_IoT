#!/usr/bin/env python3
"""
Ingestion service - runs as a stateless Deployment on AKS.

Consumes telemetry from IoT Hub's built-in Event Hub-compatible endpoint,
writes each metric as a row into Postgres (device_id, timestamp, metric,
value), and updates the owning Device row's last_seen/status.

Schema written here matches the Django models in webapp/devices/models.py
(Device, DeviceMetric) - this service writes with raw SQL rather than the
ORM so it has no dependency on the Django codebase, but the table/column
names must stay in sync with those migrations.

Device offline detection is a pull, not a push, from this service: it only
ever marks a device "online" on receipt of telemetry. A device is declared
"offline" by the Azure Monitor alert rule (Section 4, Phase 4 of the build
plan), which looks at staleness of last_seen independently.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

import psycopg2
from azure.eventhub import EventHubConsumerClient
from dotenv import load_dotenv

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

# health payload keys the edge agent sends that should NOT be stored as
# individual (metric, value) rows
_NON_METRIC_KEYS = {"timestamp"}


def get_db_connection():
    conn = psycopg2.connect(POSTGRES_URL)
    conn.autocommit = True
    return conn


def upsert_device_and_get_id(cursor, device_id: str, seen_at: datetime) -> int:
    cursor.execute(
        """
        INSERT INTO devices_device (device_id, display_name, device_type, is_physical, status, last_seen)
        VALUES (%s, %s, 'raspberry_pi', TRUE, 'online', %s)
        ON CONFLICT (device_id) DO UPDATE
            SET last_seen = EXCLUDED.last_seen,
                status = 'online'
        RETURNING id
        """,
        (device_id, device_id, seen_at),
    )
    return cursor.fetchone()[0]


def insert_metrics(cursor, device_pk: int, seen_at: datetime, health: dict) -> None:
    rows = [
        (device_pk, seen_at, metric, float(value))
        for metric, value in health.items()
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


def on_event_batch(partition_context, events):
    if not events:
        return

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            for event in events:
                device_id = event.message.annotations.get(
                    b"iothub-connection-device-id"
                )
                if device_id is None:
                    logger.warning("event missing device-id annotation, skipping")
                    continue
                device_id = device_id.decode("utf-8")

                try:
                    health = json.loads(next(event.body))
                except (StopIteration, json.JSONDecodeError) as exc:
                    logger.warning("could not decode event body from %s: %s", device_id, exc)
                    continue

                seen_at = datetime.now(timezone.utc)
                device_pk = upsert_device_and_get_id(cursor, device_id, seen_at)
                insert_metrics(cursor, device_pk, seen_at, health)

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


def main() -> None:
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
