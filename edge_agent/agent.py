#!/usr/bin/env python3
"""
Edge agent for the self-healing IoT project.

Runs on the Raspberry Pi as a systemd service. Responsibilities:
  - send telemetry (CPU temp, CPU load, memory) to Azure IoT Hub every 5s
  - expose direct methods for remote remediation: restart_service, get_health,
    reconnect_network
  - log all activity locally for debugging

Fault injection is intentionally NOT triggered from here — faults are injected
by controller/fault_injector.py over SSH so that the "detection" path is real
(the agent must actually go unhealthy and be observed from the outside).
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler

import psutil
from azure.iot.device.aio import IoTHubDeviceClient
from azure.iot.device import MethodResponse
from dotenv import load_dotenv

load_dotenv()

LOG_PATH = os.environ.get("EDGE_AGENT_LOG_PATH", "/var/log/edge-agent/edge-agent.log")
TELEMETRY_INTERVAL_SECONDS = float(os.environ.get("TELEMETRY_INTERVAL_SECONDS", "5"))
SERVICE_NAME = os.environ.get("EDGE_AGENT_SYSTEMD_UNIT", "edge-agent.service")
NETWORK_IFACE = os.environ.get("PI_NETWORK_IFACE", "eth0")

logger = logging.getLogger("edge_agent")
logger.setLevel(logging.INFO)


def _setup_logging() -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True) if os.path.dirname(LOG_PATH) else None
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z"
    )
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("could not open log file %s (%s); logging to stdout only", LOG_PATH, exc)


def read_cpu_temp_celsius() -> float | None:
    """Reads CPU temperature via psutil (Linux) or the Pi's thermal zone file as a fallback."""
    try:
        temps = psutil.sensors_temperatures()
        for key in ("cpu_thermal", "cpu-thermal", "coretemp"):
            if key in temps and temps[key]:
                return temps[key][0].current
    except (AttributeError, NotImplementedError):
        pass

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except OSError:
        return None


def collect_health() -> dict:
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_temp_c": read_cpu_temp_celsius(),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage("/").percent,
        "load_avg_1m": os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
        "uptime_seconds": time.time() - psutil.boot_time(),
        "timestamp": time.time(),
    }


def _run_shell(cmd: list[str], timeout: int = 15) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        ok = result.returncode == 0
        output = (result.stdout or "") + (result.stderr or "")
        return ok, output.strip()
    except subprocess.TimeoutExpired:
        return False, f"command timed out after {timeout}s"
    except OSError as exc:
        return False, str(exc)


async def handle_restart_service(method_request):
    """Direct method: restarts a named systemd service (defaults to this agent's own unit)."""
    payload = method_request.payload or {}
    unit = payload.get("service_name", SERVICE_NAME)
    logger.info("direct method restart_service invoked for unit=%s", unit)

    ok, output = _run_shell(["sudo", "systemctl", "restart", unit])
    status = 200 if ok else 500
    return MethodResponse.create_from_method_request(
        method_request, status, {"success": ok, "unit": unit, "output": output}
    )


async def handle_reconnect_network(method_request):
    """Direct method: clears any netem fault injected on the configured interface."""
    payload = method_request.payload or {}
    iface = payload.get("interface", NETWORK_IFACE)
    logger.info("direct method reconnect_network invoked for iface=%s", iface)

    ok, output = _run_shell(["sudo", "tc", "qdisc", "del", "dev", iface, "root", "netem"])
    # "del" fails with "No such file or directory" if there was nothing to remove -
    # that's not a real failure for our purposes, treat as success.
    already_clean = "No such file or directory" in output or "Cannot delete qdisc" in output
    ok = ok or already_clean
    status = 200 if ok else 500
    return MethodResponse.create_from_method_request(
        method_request, status, {"success": ok, "interface": iface, "output": output}
    )


async def handle_get_health(method_request):
    logger.info("direct method get_health invoked")
    health = collect_health()
    return MethodResponse.create_from_method_request(method_request, 200, health)


METHOD_HANDLERS = {
    "restart_service": handle_restart_service,
    "reconnect_network": handle_reconnect_network,
    "get_health": handle_get_health,
}


async def method_dispatch_loop(client: IoTHubDeviceClient) -> None:
    while True:
        method_request = await client.receive_method_request()
        handler = METHOD_HANDLERS.get(method_request.name)
        if handler is None:
            logger.warning("received unknown direct method: %s", method_request.name)
            response = MethodResponse.create_from_method_request(
                method_request, 404, {"error": f"unknown method {method_request.name}"}
            )
        else:
            try:
                response = await handler(method_request)
            except Exception as exc:  # noqa: BLE001 - must not crash the dispatch loop
                logger.exception("direct method %s raised an exception", method_request.name)
                response = MethodResponse.create_from_method_request(
                    method_request, 500, {"error": str(exc)}
                )
        await client.send_method_response(response)


async def telemetry_loop(client: IoTHubDeviceClient) -> None:
    while True:
        health = collect_health()
        message_body = json.dumps(health)
        try:
            await client.send_message(message_body)
            logger.info("telemetry sent: %s", message_body)
        except Exception:  # noqa: BLE001 - keep the loop alive across transient errors
            logger.exception("failed to send telemetry")
        await asyncio.sleep(TELEMETRY_INTERVAL_SECONDS)


async def main() -> None:
    _setup_logging()

    conn_str = os.environ.get("IOTHUB_DEVICE_CONNECTION_STRING")
    if not conn_str:
        logger.error("IOTHUB_DEVICE_CONNECTION_STRING is not set; refusing to start")
        sys.exit(1)

    client = IoTHubDeviceClient.create_from_connection_string(conn_str)
    await client.connect()
    logger.info("connected to IoT Hub")

    try:
        await asyncio.gather(
            telemetry_loop(client),
            method_dispatch_loop(client),
        )
    finally:
        await client.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
