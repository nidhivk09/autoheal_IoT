"""
Per-device failure detection for the fleet.

Fixes the Pi-era limitation that Azure Monitor's IoT Hub metrics carry no
device id: detection happens here, where every message is attributed to a
device. Signals:

  telemetry_stale  no live telemetry for STALE_AFTER_SECONDS     (sweeper)
  heap_low         free_heap under the desired threshold, HEAP_LOW_CONSECUTIVE readings in a row
  message_flood    more than FLOOD_MAX_PER_WINDOW live messages in FLOOD_WINDOW_SECONDS
  config_drift     running config in telemetry != desired config in Django,
                   DRIFT_CONSECUTIVE readings in a row

Each signal fires once on its off->on transition and re-arms when the
condition clears, so a long outage produces one detection, not hundreds.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

# Kept in sync with devices.models.STALE_AFTER_SECONDS in the Django app.
STALE_AFTER_SECONDS = 30
HEAP_LOW_CONSECUTIVE = 2
DEFAULT_HEAP_LOW_BYTES = 70000
FLOOD_WINDOW_SECONDS = 10
FLOOD_MAX_PER_WINDOW = 30
FLOOD_CLEAR_PER_WINDOW = 10
DRIFT_CONSECUTIVE = 2

CONFIG_KEYS = ("telemetry_interval_s", "heap_low_bytes", "edge_healing")


@dataclass
class DesiredConfig:
    telemetry_interval_s: int
    heap_low_bytes: int
    edge_healing: bool

    def as_dict(self) -> dict:
        return {
            "telemetry_interval_s": self.telemetry_interval_s,
            "heap_low_bytes": self.heap_low_bytes,
            "edge_healing": self.edge_healing,
        }


@dataclass
class Detection:
    device_id: str
    signal: str
    detected_at: str
    evidence: dict

    def as_job(self) -> dict:
        return {"kind": "detection", "device_id": self.device_id, "signal": self.signal,
                "detected_at": self.detected_at, "evidence": self.evidence}


@dataclass
class _DeviceState:
    active: set = field(default_factory=set)
    heap_low_streak: int = 0
    drift_streak: int = 0
    live_times: deque = field(default_factory=deque)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FleetDetector:
    def __init__(self, on_detection: Callable[[Detection], None], clock: Callable[[], float] = time.monotonic):
        self._on_detection = on_detection
        self._clock = clock
        self._lock = threading.Lock()
        self._devices: dict[str, _DeviceState] = {}

    def _state(self, device_id: str) -> _DeviceState:
        return self._devices.setdefault(device_id, _DeviceState())

    def _set(self, fired: list, device_id: str, signal: str, on: bool, evidence: dict) -> None:
        """Caller holds the lock; new detections are collected in `fired` and
        delivered after it's released."""
        state = self._state(device_id)
        if on and signal not in state.active:
            state.active.add(signal)
            fired.append(Detection(device_id, signal, _now_iso(), evidence))
        elif not on:
            state.active.discard(signal)

    def _deliver(self, fired: list) -> None:
        for detection in fired:
            self._on_detection(detection)

    def observe(self, device_id: str, body: dict, desired: DesiredConfig, replay: bool) -> None:
        """One telemetry message. Replayed (buffered) messages say nothing
        about the device's current state, so they're ignored here."""
        if replay:
            return
        fired: list[Detection] = []
        with self._lock:
            state = self._state(device_id)
            now = self._clock()

            # any live message means telemetry is flowing again
            self._set(fired, device_id, "telemetry_stale", False, {})

            state.live_times.append(now)
            while state.live_times and now - state.live_times[0] > FLOOD_WINDOW_SECONDS:
                state.live_times.popleft()
            rate = len(state.live_times)
            if rate > FLOOD_MAX_PER_WINDOW:
                self._set(fired, device_id, "message_flood", True, {"messages_in_window": rate, "window_s": FLOOD_WINDOW_SECONDS})
            elif rate < FLOOD_CLEAR_PER_WINDOW:
                self._set(fired, device_id, "message_flood", False, {})

            free_heap = body.get("free_heap")
            if isinstance(free_heap, (int, float)):
                threshold = desired.heap_low_bytes or DEFAULT_HEAP_LOW_BYTES
                if free_heap < threshold:
                    state.heap_low_streak += 1
                    if state.heap_low_streak >= HEAP_LOW_CONSECUTIVE:
                        self._set(fired, device_id, "heap_low", True, {"free_heap": free_heap, "threshold": threshold})
                else:
                    state.heap_low_streak = 0
                    self._set(fired, device_id, "heap_low", False, {})

            if all(k in body for k in CONFIG_KEYS):
                running = {
                    "telemetry_interval_s": int(body["telemetry_interval_s"]),
                    "heap_low_bytes": int(body["heap_low_bytes"]),
                    "edge_healing": bool(body["edge_healing"]),
                }
                if running != desired.as_dict():
                    state.drift_streak += 1
                    if state.drift_streak >= DRIFT_CONSECUTIVE:
                        self._set(fired, device_id, "config_drift", True, {"running": running, "desired": desired.as_dict()})
                else:
                    state.drift_streak = 0
                    self._set(fired, device_id, "config_drift", False, {})
        self._deliver(fired)

    def sweep(self, last_seen_age_seconds: dict[str, float]) -> None:
        """Called periodically with {device_id: seconds since last telemetry}
        for devices that aren't in a maintenance window."""
        fired: list[Detection] = []
        with self._lock:
            for device_id, age in last_seen_age_seconds.items():
                if age > STALE_AFTER_SECONDS:
                    self._set(fired, device_id, "telemetry_stale", True, {"seconds_since_telemetry": round(age, 1)})
        self._deliver(fired)
