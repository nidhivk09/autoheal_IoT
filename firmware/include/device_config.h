#pragma once
// Runtime configuration, split into two copies:
//   desired - what the cloud asked for (device twin desired.config), persisted
//             to NVS so the device boots with it even while offline
//   running - what the firmware is actually using right now
// "Config drift" is running != desired. The config_drift fault corrupts the
// running copy; the edge healer (or a twin nudge from the cloud) reapplies
// desired.

#include <ArduinoJson.h>

struct DeviceConfig {
    uint32_t telemetryIntervalS;
    uint32_t heapLowBytes;  // 0 disables the edge heap guard
    bool edgeHealing;

    bool operator==(const DeviceConfig& o) const {
        return telemetryIntervalS == o.telemetryIntervalS && heapLowBytes == o.heapLowBytes &&
               edgeHealing == o.edgeHealing;
    }
    bool operator!=(const DeviceConfig& o) const { return !(*this == o); }
};

namespace cfg {
void begin();
const DeviceConfig& desired();
const DeviceConfig& running();
// Merges a twin desired.config object; returns true if anything changed.
bool mergeDesired(JsonObjectConst config);
void applyDesired();
void drift();
bool inSync();
void toJson(const DeviceConfig& c, JsonObject out);
}  // namespace cfg
