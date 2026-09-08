#!/usr/bin/env python3
"""
Local fault-injection helper functions, meant to run ON the Raspberry Pi.

These are invoked remotely over SSH by controller/fault_injector.py — the Pi
never injects faults on itself autonomously. Each function mirrors a command
from the build plan (Section 6) but wraps it so it's idempotent and returns a
clear success/failure result instead of raw shell output.

Usage on the Pi (also how the controller calls it):
    python3 fault_hooks.py inject network_loss
    python3 fault_hooks.py clear network_loss
    python3 fault_hooks.py inject process_crash
    python3 fault_hooks.py clear process_crash
    python3 fault_hooks.py inject resource_exhaustion
    python3 fault_hooks.py clear resource_exhaustion   # (self-clearing after timeout anyway)
"""
import argparse
import subprocess
import sys

NETWORK_IFACE_DEFAULT = "eth0"
SERVICE_NAME_DEFAULT = "edge-agent.service"


def _run(cmd: list[str]) -> tuple[bool, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output.strip()


def inject_network_loss(iface: str = NETWORK_IFACE_DEFAULT) -> tuple[bool, str]:
    return _run(["sudo", "tc", "qdisc", "add", "dev", iface, "root", "netem", "loss", "100%"])


def clear_network_loss(iface: str = NETWORK_IFACE_DEFAULT) -> tuple[bool, str]:
    ok, output = _run(["sudo", "tc", "qdisc", "del", "dev", iface, "root", "netem"])
    already_clean = "No such file or directory" in output or "Cannot delete qdisc" in output
    return ok or already_clean, output


def inject_process_crash(service_name: str = SERVICE_NAME_DEFAULT) -> tuple[bool, str]:
    return _run(["sudo", "systemctl", "stop", service_name])


def clear_process_crash(service_name: str = SERVICE_NAME_DEFAULT) -> tuple[bool, str]:
    return _run(["sudo", "systemctl", "start", service_name])


def inject_resource_exhaustion(duration_seconds: int = 120) -> tuple[bool, str]:
    # Runs in the background and self-clears after duration_seconds; nohup so it
    # survives the SSH session that launched it (the controller does not block on it).
    cmd = (
        f"nohup stress-ng --cpu 4 --vm 2 --vm-bytes 80% "
        f"--timeout {duration_seconds}s > /tmp/stress-ng.log 2>&1 &"
    )
    return _run(["bash", "-c", cmd])


def clear_resource_exhaustion() -> tuple[bool, str]:
    return _run(["pkill", "-f", "stress-ng"])


FAULT_TYPE_HANDLERS = {
    "network_loss": (inject_network_loss, clear_network_loss),
    "process_crash": (inject_process_crash, clear_process_crash),
    "resource_exhaustion": (inject_resource_exhaustion, clear_resource_exhaustion),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inject", "clear"])
    parser.add_argument("fault_type", choices=list(FAULT_TYPE_HANDLERS.keys()))
    args = parser.parse_args()

    inject_fn, clear_fn = FAULT_TYPE_HANDLERS[args.fault_type]
    fn = inject_fn if args.action == "inject" else clear_fn
    ok, output = fn()

    print(output)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
