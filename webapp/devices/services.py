"""
Fleet configuration: pushes each Device's desired config to its IoT Hub
device twin (desired.config). The firmware persists it and reports the
running copy back in every telemetry message.
"""
import time
import uuid

from .models import Device


def push_desired_config(device: Device, reconcile: bool = False) -> None:
    """Writes desired.config to the twin. reconcile=True also bumps
    desired.reconcile_token, which makes the device reapply its desired
    config even if nothing changed - the cloud-tier fix for config drift."""
    from controller.iothub_rest import IoTHubService

    desired = {"config": device.desired_config()}
    if reconcile:
        desired["reconcile_token"] = uuid.uuid4().hex
    IoTHubService.from_env().update_desired(device.device_id, desired)


def wait_for_running_config(device: Device, timeout_seconds: int = 60) -> bool:
    """Polls telemetry until the device reports running config == desired."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        device.refresh_from_db()
        if Device.running_config(device.latest_metrics()) == device.desired_config():
            return True
        time.sleep(3)
    return False
