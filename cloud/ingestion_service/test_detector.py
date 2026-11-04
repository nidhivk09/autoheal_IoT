"""
    cd cloud/ingestion_service && pip install pytest && python -m pytest test_detector.py
"""
from detector import (
    FLOOD_MAX_PER_WINDOW,
    STALE_AFTER_SECONDS,
    DesiredConfig,
    FleetDetector,
)

DESIRED = DesiredConfig(telemetry_interval_s=5, heap_low_bytes=70000, edge_healing=True)


def telemetry(**overrides):
    body = {"free_heap": 180000, "telemetry_interval_s": 5, "heap_low_bytes": 70000, "edge_healing": True}
    body.update(overrides)
    return body


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make():
    fired = []
    clock = Clock()
    return FleetDetector(on_detection=fired.append, clock=clock), fired, clock


def signals(fired):
    return [(d.device_id, d.signal) for d in fired]


def test_stale_fires_once_per_outage_and_rearms():
    det, fired, _ = make()
    det.sweep({"esp32-01": STALE_AFTER_SECONDS + 1, "esp32-02": 3})
    det.sweep({"esp32-01": STALE_AFTER_SECONDS + 6})
    assert signals(fired) == [("esp32-01", "telemetry_stale")]

    det.observe("esp32-01", telemetry(), DESIRED, replay=False)  # back online
    det.sweep({"esp32-01": STALE_AFTER_SECONDS + 1})
    assert signals(fired) == [("esp32-01", "telemetry_stale")] * 2


def test_replayed_messages_do_not_clear_staleness():
    det, fired, _ = make()
    det.sweep({"esp32-01": 60})
    det.observe("esp32-01", telemetry(), DESIRED, replay=True)
    det.sweep({"esp32-01": 65})
    assert len(fired) == 1


def test_heap_low_needs_consecutive_readings():
    det, fired, _ = make()
    det.observe("esp32-01", telemetry(free_heap=50000), DESIRED, replay=False)
    assert fired == []
    det.observe("esp32-01", telemetry(free_heap=48000), DESIRED, replay=False)
    det.observe("esp32-01", telemetry(free_heap=47000), DESIRED, replay=False)
    assert signals(fired) == [("esp32-01", "heap_low")]
    assert fired[0].evidence["threshold"] == 70000


def test_flood_detected_per_device():
    det, fired, clock = make()
    for _ in range(FLOOD_MAX_PER_WINDOW + 1):
        clock.t += 0.01
        det.observe("esp32-02", {"seq": 1}, DESIRED, replay=False)
    det.observe("esp32-01", telemetry(), DESIRED, replay=False)
    assert signals(fired) == [("esp32-02", "message_flood")]


def test_normal_rate_is_not_a_flood():
    det, fired, clock = make()
    for _ in range(40):
        clock.t += 5
        det.observe("esp32-01", telemetry(), DESIRED, replay=False)
    assert fired == []


def test_config_drift_against_desired():
    det, fired, _ = make()
    drifted = telemetry(telemetry_interval_s=20, heap_low_bytes=0)
    det.observe("esp32-01", drifted, DESIRED, replay=False)
    det.observe("esp32-01", drifted, DESIRED, replay=False)
    assert signals(fired) == [("esp32-01", "config_drift")]
    assert fired[0].evidence["running"]["heap_low_bytes"] == 0

    det.observe("esp32-01", telemetry(), DESIRED, replay=False)  # reconciled
    det.observe("esp32-01", drifted, DESIRED, replay=False)
    det.observe("esp32-01", drifted, DESIRED, replay=False)
    assert len(fired) == 2


def test_detection_job_shape():
    det, fired, _ = make()
    det.sweep({"esp32-03": 99})
    job = fired[0].as_job()
    assert job["kind"] == "detection" and job["device_id"] == "esp32-03"
    assert job["signal"] == "telemetry_stale" and job["detected_at"]
