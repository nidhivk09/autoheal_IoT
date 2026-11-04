#!/usr/bin/env python3
"""
Provision an ESP32 over USB serial: writes WiFi + IoT Hub identity into the
device's NVS through the firmware's serial console, then reboots it.

    pip install pyserial
    python firmware/scripts/provision.py --port /dev/cu.usbserial-0001 \
        --ssid MyWifi --password '...' \
        --hub-name selfheal-iothub --device-id esp32-01

The device key is fetched with the Azure CLI (az iot hub device-identity
show) unless --device-key is given. Nothing is written to disk.
"""
import argparse
import getpass
import subprocess
import sys
import time

import serial


def fetch_device_key(hub_name: str, device_id: str) -> str:
    result = subprocess.run(
        ["az", "iot", "hub", "device-identity", "show", "--hub-name", hub_name,
         "--device-id", device_id, "--query", "authentication.symmetricKey.primaryKey", "-o", "tsv"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        sys.exit(f"could not fetch device key via az CLI: {result.stderr.strip()}")
    return result.stdout.strip()


def send(port: serial.Serial, line: str, echo: str | None = None) -> None:
    port.write((line + "\n").encode())
    port.flush()
    time.sleep(0.3)
    reply = port.read(port.in_waiting or 1).decode(errors="replace").strip()
    print(f"> {echo or line}")
    if reply:
        print("  " + reply.replace("\n", "\n  "))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True)
    parser.add_argument("--ssid", required=True)
    parser.add_argument("--password", help="WiFi password (prompted if omitted)")
    parser.add_argument("--hub-name", required=True, help="IoT Hub name, without .azure-devices.net")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--device-key", help="device primary key (fetched with az CLI if omitted)")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    password = args.password if args.password is not None else getpass.getpass("WiFi password: ")
    device_key = args.device_key or fetch_device_key(args.hub_name, args.device_id)

    with serial.Serial(args.port, args.baud, timeout=1) as port:
        time.sleep(2)  # opening the port resets most dev boards
        port.reset_input_buffer()
        send(port, f"set ssid {args.ssid}")
        send(port, f"set pass {password}", echo="set pass ********")
        send(port, f"set host {args.hub_name}.azure-devices.net")
        send(port, f"set device_id {args.device_id}")
        send(port, f"set device_key {device_key}", echo="set device_key ********")
        send(port, "show")
        send(port, "reboot")
    print("provisioned; open `pio device monitor` to watch it connect")
    return 0


if __name__ == "__main__":
    sys.exit(main())
