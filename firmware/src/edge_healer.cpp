#include "edge_healer.h"

#include <ArduinoJson.h>
#include <Preferences.h>
#include <WiFi.h>

#include "app_component.h"
#include "app_state.h"
#include "connectivity.h"
#include "device_config.h"

namespace {

struct Healing {
    bool active = false;
    const char* signal = "";
    const char* action = "";
    double detectedAt = 0;
    double remediatedAt = 0;
    uint32_t deadlineMs = 0;
    bool (*verify)() = nullptr;
};

Healing s_healing;
uint32_t s_lastCheckMs = 0;
uint32_t s_heapLowCount = 0;
uint32_t s_floodCount = 0;
uint32_t s_lastEnqueued = 0;
uint32_t s_lastRate = 0;
bool s_bootEventPending = false;

bool verifyHeartbeat() { return app::heartbeatFresh(); }
bool verifyHeap() { return ESP.getFreeHeap() >= cfg::desired().heapLowBytes; }
bool verifyFloodStopped() { return s_lastRate <= EDGE_FLOOD_RATE_LIMIT; }
bool verifyConfig() { return cfg::inSync(); }

void restartApp() { app::restart(); }

void heal(const char* signal, const char* action, void (*act)(), bool (*verify)()) {
    LOGI("edge: %s detected -> %s", signal, action);
    s_healing.active = true;
    s_healing.signal = signal;
    s_healing.action = action;
    s_healing.detectedAt = nowEpoch();
    act();
    s_healing.remediatedAt = nowEpoch();
    s_healing.deadlineMs = millis() + EDGE_VERIFY_TIMEOUT_MS;
    s_healing.verify = verify;
    g_stats.edgeHeals++;
}

void serviceHealing(uint32_t now) {
    if (s_healing.verify()) {
        LOGI("edge: %s healed", s_healing.signal);
        edge::emitRecovery(s_healing.signal, "edge", s_healing.action, s_healing.detectedAt,
                           s_healing.remediatedAt, nowEpoch(), 0);
        s_healing.active = false;
    } else if ((int32_t)(now - s_healing.deadlineMs) >= 0) {
        LOGI("edge: %s not healed by %s, escalating to reboot", s_healing.signal, s_healing.action);
        edge::storeBootEvent(s_healing.signal, "reboot", s_healing.detectedAt);
        conn::noteIntentionalDisconnect();
        scheduleReboot(200, "edge escalation");
        s_healing.active = false;
    }
}

void flushBootEvent() {
    if (!s_bootEventPending || !conn::online() || g_stats.telemetryPublished == 0) {
        return;
    }
    Preferences prefs;
    prefs.begin("edge", false);
    const String signal = prefs.getString("sig", "");
    const String action = prefs.getString("act", "");
    const double detectedAt = prefs.getDouble("det", 0);
    const double remediatedAt = prefs.getDouble("rem", 0);
    prefs.clear();
    prefs.end();
    s_bootEventPending = false;
    if (!signal.isEmpty()) {
        edge::emitRecovery(signal.c_str(), "edge", action.c_str(), detectedAt, remediatedAt, nowEpoch(), 0);
    }
}

}  // namespace

namespace edge {

void begin() {
    Preferences prefs;
    prefs.begin("edge", true);
    s_bootEventPending = prefs.isKey("sig");
    prefs.end();
}

void loop() {
    flushBootEvent();

    const uint32_t now = millis();
    if (now - s_lastCheckMs < EDGE_CHECK_INTERVAL_MS) {
        return;
    }
    s_lastCheckMs = now;

    const uint32_t enqueued = g_stats.appEnqueued;
    s_lastRate = enqueued - s_lastEnqueued;
    s_lastEnqueued = enqueued;
    if (WiFi.status() == WL_CONNECTED) {
        g_stats.rssi = WiFi.RSSI();
    }

    if (s_healing.active) {
        serviceHealing(now);
        return;
    }
    if (!cfg::running().edgeHealing) {
        s_heapLowCount = 0;
        s_floodCount = 0;
        return;
    }

    if (!cfg::inSync()) {
        heal("config_drift", "reapply_desired_config", cfg::applyDesired, verifyConfig);
        return;
    }
    if (!app::heartbeatFresh()) {
        heal("heartbeat_lost", "restart_app", restartApp, verifyHeartbeat);
        return;
    }

    const uint32_t heapLow = cfg::running().heapLowBytes;
    if (heapLow > 0 && ESP.getFreeHeap() < heapLow) {
        if (++s_heapLowCount >= HEAP_LOW_CONSECUTIVE) {
            s_heapLowCount = 0;
            heal("heap_low", "restart_app", restartApp, verifyHeap);
            return;
        }
    } else {
        s_heapLowCount = 0;
    }

    if (s_lastRate > EDGE_FLOOD_RATE_LIMIT) {
        if (++s_floodCount >= FLOOD_CONSECUTIVE) {
            s_floodCount = 0;
            heal("message_flood", "restart_app", restartApp, verifyFloodStopped);
            return;
        }
    } else {
        s_floodCount = 0;
    }
}

bool busy() { return s_healing.active; }

void emitRecovery(const char* signal, const char* healer, const char* action, double detectedAt,
                  double remediatedAt, double verifiedAt, uint32_t droppedDuring) {
    JsonDocument doc;
    doc["event"] = "recovered";
    doc["signal"] = signal;
    doc["healer"] = healer;
    doc["action"] = action;
    doc["detected_at"] = detectedAt;
    doc["remediated_at"] = remediatedAt;
    doc["verified_at"] = verifiedAt;
    doc["dropped_during"] = droppedDuring;
    doc["fw"] = FW_VERSION;
    char body[OUT_MSG_MAX];
    serializeJson(doc, body, sizeof(body));
    if (!enqueueOut(MsgKind::Event, body)) {
        g_stats.dropped++;
    }
}

void storeBootEvent(const char* signal, const char* action, double detectedAt) {
    Preferences prefs;
    prefs.begin("edge", false);
    prefs.putString("sig", signal);
    prefs.putString("act", action);
    prefs.putDouble("det", detectedAt);
    prefs.putDouble("rem", nowEpoch());
    prefs.end();
}

}  // namespace edge
