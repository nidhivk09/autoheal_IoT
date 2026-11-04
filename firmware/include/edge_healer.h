#pragma once
// Tier-1 (on-device) self-healing. Runs once a second from the main loop
// when running config edge_healing is on:
//
//   symptom                         action                verify
//   app heartbeat stale (5s)        restart app component heartbeat resumes
//   free heap < heap_low_bytes x3   restart app component heap recovers
//   app sends > 5 msg/s for 3s      restart app component rate normal
//   running config != desired       reapply desired       configs match
//
// If verification fails within 5s the healer escalates to a reboot and
// reports the heal after coming back. Link loss and OTA self-test failures
// are handled in connectivity.cpp and ota.cpp but reported through here.
//
// Every heal becomes a "recovered" event (message property type=event) that
// the ingestion service forwards to Django to timestamp the incident.

#include <stdint.h>

namespace edge {
void begin();
void loop();
// True while a heal is waiting for verification.
bool busy();

void emitRecovery(const char* signal, const char* healer, const char* action, double detectedAt,
                  double remediatedAt, double verifiedAt, uint32_t droppedDuring);

// For heals that finish with a reboot: saved to NVS and emitted once the
// device is back online and sending telemetry.
void storeBootEvent(const char* signal, const char* action, double detectedAt);
}  // namespace edge
