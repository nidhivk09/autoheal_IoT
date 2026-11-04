#pragma once
// Device identity lives in NVS, never in the binary. Provision once over
// serial (115200 baud), e.g. with scripts/provision.py:
//   set ssid <wifi ssid>
//   set pass <wifi password>
//   set host <hub-name>.azure-devices.net
//   set device_id <IoT Hub device id>
//   set device_key <device primary symmetric key>
//   reboot
// Other commands: show, factory-reset.

#include <Arduino.h>

struct Credentials {
    String ssid;
    String pass;
    String host;
    String deviceId;
    String deviceKey;
};

namespace prov {
bool load(Credentials& out);
// Blocks until the device is provisioned and rebooted. Never returns.
[[noreturn]] void runConsoleUntilProvisioned();
// Non-blocking serial console for a provisioned device.
void pollConsole();
}  // namespace prov
