"""Direct-method calls to devices through IoT Hub."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("remediation.device")

_registry = None


def invoke(device_id: str, method: str, payload: dict | None = None, timeout_s: int = 15) -> tuple[int | None, dict]:
    """Returns (status, payload). status is None when the device couldn't be
    reached at all (offline, or it didn't answer in time)."""
    from azure.iot.hub import IoTHubRegistryManager
    from azure.iot.hub.models import CloudToDeviceMethod

    global _registry
    if _registry is None:
        _registry = IoTHubRegistryManager(os.environ["IOTHUB_CONNECTION_STRING"])
    method_call = CloudToDeviceMethod(
        method_name=method,
        payload=payload or {},
        response_timeout_in_seconds=timeout_s,
    )
    try:
        result = _registry.invoke_device_method(device_id, method_call)
        return result.status, (result.payload or {})
    except Exception as exc:  # noqa: BLE001 - offline/timeout are expected outcomes here
        logger.info("direct method %s on %s failed: %s", method, device_id, exc)
        return None, {"error": str(exc)}
