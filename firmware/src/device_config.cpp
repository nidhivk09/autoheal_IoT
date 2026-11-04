#include "device_config.h"

#include <Preferences.h>

#include "app_state.h"

namespace {
DeviceConfig s_desired{DEFAULT_TELEMETRY_INTERVAL_S, DEFAULT_HEAP_LOW_BYTES, DEFAULT_EDGE_HEALING};
DeviceConfig s_running = s_desired;

void persist() {
    Preferences prefs;
    prefs.begin("cfg", false);
    prefs.putUInt("interval", s_desired.telemetryIntervalS);
    prefs.putUInt("heaplow", s_desired.heapLowBytes);
    prefs.putBool("edge", s_desired.edgeHealing);
    prefs.end();
}
}  // namespace

namespace cfg {

void begin() {
    Preferences prefs;
    prefs.begin("cfg", true);
    s_desired.telemetryIntervalS = prefs.getUInt("interval", DEFAULT_TELEMETRY_INTERVAL_S);
    s_desired.heapLowBytes = prefs.getUInt("heaplow", DEFAULT_HEAP_LOW_BYTES);
    s_desired.edgeHealing = prefs.getBool("edge", DEFAULT_EDGE_HEALING);
    prefs.end();
    s_running = s_desired;
    LOGI("config: interval=%lus heap_low=%lu edge_healing=%d",
         (unsigned long)s_running.telemetryIntervalS, (unsigned long)s_running.heapLowBytes,
         s_running.edgeHealing);
}

const DeviceConfig& desired() { return s_desired; }
const DeviceConfig& running() { return s_running; }

bool mergeDesired(JsonObjectConst config) {
    DeviceConfig next = s_desired;
    if (config["telemetry_interval_s"].is<uint32_t>()) {
        next.telemetryIntervalS = constrain(config["telemetry_interval_s"].as<uint32_t>(), 1u, 300u);
    }
    if (config["heap_low_bytes"].is<uint32_t>()) {
        next.heapLowBytes = constrain(config["heap_low_bytes"].as<uint32_t>(), 0u, 200000u);
    }
    if (config["edge_healing"].is<bool>()) {
        next.edgeHealing = config["edge_healing"].as<bool>();
    }
    if (next == s_desired) {
        return false;
    }
    s_desired = next;
    persist();
    return true;
}

void applyDesired() {
    if (s_running != s_desired) {
        LOGI("applying desired config");
    }
    s_running = s_desired;
}

void drift() {
    s_running.telemetryIntervalS = DRIFT_TELEMETRY_INTERVAL_S;
    s_running.heapLowBytes = 0;
}

bool inSync() { return s_running == s_desired; }

void toJson(const DeviceConfig& c, JsonObject out) {
    out["telemetry_interval_s"] = c.telemetryIntervalS;
    out["heap_low_bytes"] = c.heapLowBytes;
    out["edge_healing"] = c.edgeHealing;
}

}  // namespace cfg
