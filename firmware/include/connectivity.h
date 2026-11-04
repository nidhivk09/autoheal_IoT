#pragma once
// WiFi + MQTT supervision. Behaviour depends on running config edge_healing:
//   on  - exponential backoff (1s..8s), WiFi driver reset after repeated
//         failures, reboot after 10 minutes offline
//   off - fixed 30s WiFi / 5s MQTT retries, nothing else (the baseline)
// Either way, when the link comes back after an unplanned outage a
// "recovered" event is emitted so the incident gets timestamps.

#include "provisioning.h"

namespace conn {
void begin(const Credentials& creds);
void loop();
bool online();
// Planned disconnects (reboot, OTA, token refresh, reconnect_network) don't
// produce a recovery event.
void noteIntentionalDisconnect();
void requestReconnect();
}  // namespace conn
