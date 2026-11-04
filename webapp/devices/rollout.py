"""
Canary firmware rollouts (run as one-off APScheduler jobs, see
dashboard/scheduler.py). Devices are updated one at a time in the order the
rollout lists them, the first being the canary. A device counts as updated
only once get_health reports it healthy on the new version; if it rolled
itself back, or never came back healthy, the rollout halts and the remaining
devices keep their current firmware.
"""
import logging
import time
from datetime import timedelta

from django.utils import timezone

from .models import Device, FirmwareRollout

logger = logging.getLogger(__name__)

DEVICE_UPDATE_TIMEOUT_SECONDS = 240
POLL_SECONDS = 5
# staleness detection is paused this long per device while it downloads + reboots
MAINTENANCE_WINDOW = timedelta(seconds=90)


def _wait_for_update(hub, device: Device, version: str) -> tuple[bool, str]:
    deadline = time.monotonic() + DEVICE_UPDATE_TIMEOUT_SECONDS
    last = "no answer yet"
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        result = hub.invoke_method(device.device_id, "get_health", timeout_s=10)
        if not result.ok:
            last = "device unreachable (downloading or rebooting)"
            continue
        fw = result.payload.get("firmware", {})
        rollback = fw.get("last_rollback") or {}
        if fw.get("version") != version and rollback.get("from") == version:
            return False, f"device rolled back from {version}: {rollback.get('reason')}"
        if fw.get("version") == version and fw.get("state") == "valid" and result.payload.get("healthy"):
            return True, "healthy on new version"
        last = f"version={fw.get('version')} state={fw.get('state')} healthy={result.payload.get('healthy')}"
    return False, f"timed out after {DEVICE_UPDATE_TIMEOUT_SECONDS}s ({last})"


def run_rollout(rollout_id: int, canary_device_id: str | None = None) -> None:
    from controller.firmware_store import sas_url
    from controller.iothub_rest import IoTHubService

    rollout = FirmwareRollout.objects.get(id=rollout_id)
    rollout.status = FirmwareRollout.Status.RUNNING
    rollout.save(update_fields=["status"])
    hub = IoTHubService.from_env()

    devices = list(rollout.devices.order_by("device_id"))
    devices.sort(key=lambda d: d.device_id != canary_device_id)  # canary first, stable otherwise
    for index, device in enumerate(devices):
        role = "canary" if index == 0 else f"device {index + 1}/{len(devices)}"
        rollout.append_log(f"{role} {device.device_id}: sending ota_update {rollout.version}")

        Device.objects.filter(id=device.id).update(maintenance_until=timezone.now() + MAINTENANCE_WINDOW)
        result = hub.invoke_method(
            device.device_id, "ota_update", {"url": sas_url(rollout.blob_name), "version": rollout.version}
        )
        if not result.ok:
            rollout.append_log(f"{device.device_id}: ota_update rejected ({result.status} {result.payload})")
            break

        ok, detail = _wait_for_update(hub, device, rollout.version)
        rollout.append_log(f"{device.device_id}: {detail}")
        if not ok:
            break
    else:
        rollout.status = FirmwareRollout.Status.COMPLETED
        rollout.finished_at = timezone.now()
        rollout.save(update_fields=["status", "finished_at"])
        rollout.append_log("rollout completed")
        return

    rollout.status = FirmwareRollout.Status.HALTED
    rollout.finished_at = timezone.now()
    rollout.save(update_fields=["status", "finished_at"])
    rollout.append_log("rollout halted; remaining devices left on their current firmware")
    logger.warning("rollout %s halted", rollout_id)
