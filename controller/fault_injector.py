#!/usr/bin/env python3
"""
SSH-based fault injection controller.

Called from the Django `incidents` app (as an importable function) or
standalone from the CLI for manual testing. Connects to the Raspberry Pi over
SSH and runs the fault-injection commands from the build plan (Section 6).

Commands that start a long-running background effect (resource_exhaustion)
are launched with `&` and the SSH call returns immediately - the caller
should record `injected_at = now()` right after `inject_fault()` returns,
not after the fault "finishes", since the whole point is to time detection
against the moment the fault started.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import paramiko
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("fault_injector")

INJECT_COMMANDS = {
    "network_loss": "sudo tc qdisc add dev {iface} root netem loss 100%",
    "process_crash": "sudo systemctl stop {service}",
    "resource_exhaustion": "stress-ng --cpu 4 --vm 2 --vm-bytes 80% --timeout {duration}s > /tmp/stress-ng.log 2>&1 &",
}

CLEAR_COMMANDS = {
    "network_loss": "sudo tc qdisc del dev {iface} root netem",
    "process_crash": "sudo systemctl start {service}",
    "resource_exhaustion": "pkill -f stress-ng || true",
}

# Faults that background themselves and should not block the SSH call on completion
NON_BLOCKING_FAULTS = {"resource_exhaustion"}


@dataclass
class SSHConfig:
    host: str
    user: str
    key_path: str
    iface: str = "eth0"
    service: str = "edge-agent.service"
    resource_exhaustion_duration_seconds: int = 120

    @classmethod
    def from_env(cls) -> "SSHConfig":
        host = os.environ.get("PI_SSH_HOST")
        user = os.environ.get("PI_SSH_USER")
        key_path = os.environ.get("PI_SSH_KEY_PATH")
        if not all([host, user, key_path]):
            raise RuntimeError(
                "PI_SSH_HOST, PI_SSH_USER, and PI_SSH_KEY_PATH must all be set "
                "(see .env.example)"
            )
        return cls(
            host=host,
            user=user,
            key_path=key_path,
            iface=os.environ.get("PI_NETWORK_IFACE", "eth0"),
            service=os.environ.get("EDGE_AGENT_SYSTEMD_UNIT", "edge-agent.service"),
        )


def _connect(config: SSHConfig) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.load_system_host_keys()
    client.connect(
        hostname=config.host,
        username=config.user,
        key_filename=config.key_path,
        timeout=10,
    )
    return client


def _run_remote(client: paramiko.SSHClient, command: str, wait: bool) -> tuple[bool, str]:
    logger.info("running on %s: %s", client.get_transport().getpeername()[0], command)
    stdin, stdout, stderr = client.exec_command(command, timeout=15)

    if not wait:
        # don't block on exit status for background/long-running commands
        return True, "(non-blocking, launched)"

    exit_status = stdout.channel.recv_exit_status()
    output = stdout.read().decode() + stderr.read().decode()
    return exit_status == 0, output.strip()


def _format_command(template: str, config: SSHConfig) -> str:
    return template.format(
        iface=config.iface,
        service=config.service,
        duration=config.resource_exhaustion_duration_seconds,
    )


def inject_fault(fault_type: str, config: SSHConfig | None = None) -> tuple[bool, str]:
    """Triggers the given fault on the Pi. Returns (success, output).

    Call this immediately before recording Incident.injected_at - it returns
    as soon as the fault has been launched, not when it "completes".
    """
    if fault_type not in INJECT_COMMANDS:
        raise ValueError(f"unknown fault_type: {fault_type}")

    config = config or SSHConfig.from_env()
    command = _format_command(INJECT_COMMANDS[fault_type], config)
    wait = fault_type not in NON_BLOCKING_FAULTS

    client = _connect(config)
    try:
        return _run_remote(client, command, wait=wait)
    finally:
        client.close()


def clear_fault(fault_type: str, config: SSHConfig | None = None) -> tuple[bool, str]:
    """Reverses a previously injected fault. Used by manual-mode trials and cleanup."""
    if fault_type not in CLEAR_COMMANDS:
        raise ValueError(f"unknown fault_type: {fault_type}")

    config = config or SSHConfig.from_env()
    command = _format_command(CLEAR_COMMANDS[fault_type], config)

    client = _connect(config)
    try:
        return _run_remote(client, command, wait=True)
    finally:
        client.close()


def _main() -> int:
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inject", "clear"])
    parser.add_argument("fault_type", choices=list(INJECT_COMMANDS.keys()))
    args = parser.parse_args()

    fn = inject_fault if args.action == "inject" else clear_fault
    ok, output = fn(args.fault_type)
    print(output)
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(_main())
