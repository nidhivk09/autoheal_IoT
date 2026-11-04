#!/usr/bin/env python3
"""
Fault injection controller for the ESP32 fleet.

Called from the Django `incidents` app (as an importable function) or
standalone from the CLI for manual testing. Faults are injected through IoT
Hub direct methods handled by the firmware (firmware/src/faults.cpp), which
replaces the Pi-era SSH + tc/systemctl/stress-ng approach - an ESP32 has no
shell to SSH into.

The fault is still real on the device (WiFi really drops, the app task
really hangs, memory really leaks), so detection is still observed from the
outside; only the trigger channel changed. That's noted in
docs/research_notes.md.

Every call returns as soon as the device has acknowledged the fault - the
caller should record `injected_at = now()` right after inject_fault()
returns, not after the fault "finishes".
"""
from __future__ import annotations

import logging

from dotenv import load_dotenv

try:
    from .iothub_rest import IoTHubService, MethodResult
except ImportError:  # run as a script: python controller/fault_injector.py
    from iothub_rest import IoTHubService, MethodResult

load_dotenv()

logger = logging.getLogger("fault_injector")

# Faults the firmware injects itself via the inject_fault direct method.
DEVICE_FAULTS = {"network_loss", "task_hang", "memory_leak", "message_flood", "config_drift"}
# bad_firmware is injected by pushing the deliberately broken build over OTA.
OTA_FAULTS = {"bad_firmware"}
ALL_FAULTS = DEVICE_FAULTS | OTA_FAULTS


def _describe(result: MethodResult) -> str:
    return f"status={result.status} payload={result.payload}"


def inject_fault(
    device_id: str,
    fault_type: str,
    params: dict | None = None,
    hub: IoTHubService | None = None,
) -> tuple[bool, str]:
    """Triggers the given fault on the device. Returns (success, output).

    params:
      network_loss: {"duration_s": 30}
      bad_firmware: {"url": <SAS URL of the bad build>, "version": "..."} (required)
    """
    if fault_type not in ALL_FAULTS:
        raise ValueError(f"unknown fault_type: {fault_type}")
    hub = hub or IoTHubService.from_env()
    params = params or {}

    if fault_type == "bad_firmware":
        if not params.get("url"):
            raise ValueError("bad_firmware needs params['url'] pointing at the bad build")
        result = hub.invoke_method(device_id, "ota_update", {"url": params["url"], "version": params.get("version", "")})
    else:
        result = hub.invoke_method(device_id, "inject_fault", {"type": fault_type, **params})

    logger.info("inject %s on %s: %s", fault_type, device_id, _describe(result))
    return result.ok, _describe(result)


def clear_fault(device_id: str, fault_type: str, hub: IoTHubService | None = None) -> tuple[bool, str]:
    """Reverses a previously injected fault. Used by manual-mode resolution.

    network_loss can't be cleared remotely while the device is offline (that's
    the point of the fault); the simulated outage ends on its own.
    """
    if fault_type not in ALL_FAULTS:
        raise ValueError(f"unknown fault_type: {fault_type}")
    hub = hub or IoTHubService.from_env()

    if fault_type == "bad_firmware":
        result = hub.invoke_method(device_id, "rollback_firmware")
    else:
        result = hub.invoke_method(device_id, "clear_fault", {"type": fault_type})

    logger.info("clear %s on %s: %s", fault_type, device_id, _describe(result))
    return result.ok, _describe(result)


def get_health(device_id: str, hub: IoTHubService | None = None) -> MethodResult:
    hub = hub or IoTHubService.from_env()
    return hub.invoke_method(device_id, "get_health", timeout_s=10)


def _main() -> int:
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["inject", "clear", "health"])
    parser.add_argument("device_id")
    parser.add_argument("fault_type", nargs="?", choices=sorted(ALL_FAULTS))
    parser.add_argument("--params", default="{}", help='JSON, e.g. \'{"duration_s": 45}\'')
    args = parser.parse_args()

    if args.action == "health":
        result = get_health(args.device_id)
        print(json.dumps({"status": result.status, "payload": result.payload}, indent=2))
        return 0 if result.ok else 1
    if not args.fault_type:
        parser.error("fault_type is required for inject/clear")

    if args.action == "inject":
        ok, output = inject_fault(args.device_id, args.fault_type, json.loads(args.params))
    else:
        ok, output = clear_fault(args.device_id, args.fault_type)
    print(output)
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(_main())
