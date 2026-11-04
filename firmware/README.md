# ESP32 edge firmware

PlatformIO project for an ESP32 dev board (any `esp32dev`-compatible board
with 4MB flash, e.g. ESP32-DevKitC / NodeMCU-32S). No sensors are needed:
the device reports its own health (heap, WiFi RSSI, uptime, reset reason,
reconnect counts, edge-heal counts, buffered/dropped messages, running config).

## Flash and provision a board

```
pip install platformio pyserial
az iot hub device-identity create --hub-name <hub> --device-id esp32-01

cd firmware
pio run -e esp32dev -t upload          # flash the firmware (identity-free)
python scripts/provision.py --port /dev/cu.usbserial-XXXX \
    --ssid <wifi> --hub-name <hub> --device-id esp32-01
pio device monitor                     # watch it connect
```

Repeat with `esp32-02`, `esp32-03`, ... for a fleet. Devices register
themselves in the dashboard as soon as their first telemetry arrives.

Credentials live in NVS, not in the binary, so the same image serves every
device and can be stored in blob storage for OTA. The serial console also
accepts `show`, `reboot` and `factory-reset`.

## What runs on the device

| Module | Role |
|---|---|
| `iot_hub.cpp` | MQTT to IoT Hub: SAS auth, telemetry, direct methods, device twin |
| `connectivity.cpp` | WiFi/MQTT supervision; backoff and driver reset (edge healing on) or fixed 30s retries (off) |
| `app_component.cpp` | The telemetry task. `restart_service` restarts it |
| `edge_healer.cpp` | Tier-1 healing: hung task, low heap, message flood, config drift |
| `ota.cpp` | OTA updates, 60s post-update self-test, rollback |
| `offline_buffer.cpp` | LittleFS store-and-forward while offline |
| `faults.cpp` | Fault injection for trials (`inject_fault` / `clear_fault`) |

Edge healing is switched on and off from the device twin
(`desired.config.edge_healing`, set on the device's dashboard page). The
on/off comparison is the point of the experiment.

## Direct methods

`get_health`, `restart_service`, `reboot`, `reconnect_network`,
`apply_desired_config`, `ota_update {url, version}`, `rollback_firmware`,
`inject_fault {type, duration_s?}`, `clear_fault {type}`.

Try them without the dashboard: `python controller/fault_injector.py health esp32-01`.

## Build variants

- `esp32dev`: the real firmware.
- `esp32dev_bad`: the same code with a deliberate regression (telemetry is
  never sent). Only used by the `bad_firmware` fault through OTA.

CI (`.github/workflows/build-firmware.yml`) builds both, stamps the version
(`1.0.<run number>`), and uploads them to the `firmware` blob container.
