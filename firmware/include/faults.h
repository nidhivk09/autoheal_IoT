#pragma once
// Device-side fault injection, triggered by the inject_fault / clear_fault
// direct methods (controller/fault_injector.py). Replaces the Pi's
// SSH + tc/systemctl/stress-ng hooks:
//
//   network_loss   simulated AP outage: WiFi drops and every reconnect
//                  attempt fails for duration_s (default 30s)
//   task_hang      the app component stops making progress
//   memory_leak    the app component leaks 4KB per tick
//   message_flood  the app component sends ~100 msg/s (runaway loop)
//   config_drift   the running config diverges from the desired config
//
// bad_firmware is not injected here - the controller pushes the bad build
// through the normal ota_update method.

#include <ArduinoJson.h>

namespace faults {
int inject(const char* type, JsonVariantConst params, JsonObject response);
int clear(const char* type, JsonObject response);
bool networkOutageActive();
}  // namespace faults
