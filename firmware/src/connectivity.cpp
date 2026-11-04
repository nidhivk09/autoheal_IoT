#include "connectivity.h"

#include <WiFi.h>

#include "app_state.h"
#include "device_config.h"
#include "edge_healer.h"
#include "faults.h"
#include "iot_hub.h"

namespace {

Credentials s_creds;
bool s_online = false;
bool s_everOnline = false;
bool s_timeConfigured = false;

bool s_wifiAttemptInFlight = false;
uint32_t s_wifiAttemptStartedMs = 0;
uint32_t s_nextWifiAttemptMs = 0;
uint32_t s_wifiBackoffMs = BACKOFF_MIN_MS;
uint32_t s_wifiFailures = 0;
uint32_t s_nextMqttAttemptMs = 0;
uint32_t s_mqttBackoffMs = BACKOFF_MIN_MS;

// unplanned-outage bookkeeping for the recovery event
bool s_intentional = false;
bool s_outageTracked = false;
double s_outageStartEpoch = 0;
double s_wifiRestoredEpoch = 0;
uint32_t s_droppedAtOutageStart = 0;
uint32_t s_offlineSinceMs = 0;

bool edgeOn() { return cfg::running().edgeHealing; }

uint32_t takeBackoff(uint32_t& backoff) {
    const uint32_t current = backoff;
    backoff = min(backoff * 2, BACKOFF_MAX_MS);
    return current;
}

void onLinkLost() {
    s_online = false;
    s_offlineSinceMs = millis();
    // Drop the TLS socket now; otherwise it can look alive after WiFi comes
    // back and we'd sit on a dead MQTT session until keepalive times out.
    hub::disconnect();
    if (!s_intentional && s_everOnline) {
        s_outageTracked = true;
        s_outageStartEpoch = nowEpoch();
        s_wifiRestoredEpoch = 0;
        s_droppedAtOutageStart = g_stats.dropped;
        LOGI("link lost");
    }
}

void onOnline() {
    s_online = true;
    s_offlineSinceMs = 0;
    s_mqttBackoffMs = BACKOFF_MIN_MS;
    if (s_everOnline) {
        g_stats.mqttReconnects++;
    }
    s_everOnline = true;
    if (s_outageTracked) {
        const double now = nowEpoch();
        edge::emitRecovery("link_lost", edgeOn() ? "edge" : "none",
                           edgeOn() ? "reconnect_backoff" : "fixed_interval_retry", s_outageStartEpoch,
                           s_wifiRestoredEpoch > 0 ? s_wifiRestoredEpoch : now, now,
                           g_stats.dropped - s_droppedAtOutageStart);
    }
    s_outageTracked = false;
    s_intentional = false;
}

void scheduleNextWifiAttempt(uint32_t now) {
    s_nextWifiAttemptMs = now + (edgeOn() ? takeBackoff(s_wifiBackoffMs) : NAIVE_WIFI_RETRY_MS);
}

void serviceWifi(uint32_t now) {
    if (s_wifiAttemptInFlight) {
        if (now - s_wifiAttemptStartedMs < WIFI_ATTEMPT_TIMEOUT_MS) {
            return;
        }
        s_wifiAttemptInFlight = false;
        s_wifiFailures++;
        WiFi.disconnect();
        scheduleNextWifiAttempt(now);
        return;
    }
    if ((int32_t)(now - s_nextWifiAttemptMs) < 0) {
        return;
    }

    if (edgeOn()) {
        if (s_offlineSinceMs != 0 && now - s_offlineSinceMs > EDGE_OFFLINE_REBOOT_MS) {
            scheduleReboot(100, "edge: offline too long");
            return;
        }
        if (s_wifiFailures > 0 && s_wifiFailures % WIFI_DRIVER_RESET_AFTER == 0) {
            LOGI("edge: resetting WiFi driver after %lu failures", (unsigned long)s_wifiFailures);
            WiFi.mode(WIFI_OFF);
            delay(200);
            WiFi.mode(WIFI_STA);
        }
    }

    if (faults::networkOutageActive()) {
        // Simulated AP outage: the attempt fails without touching the radio.
        s_wifiFailures++;
        scheduleNextWifiAttempt(now);
        return;
    }
    LOGI("wifi: connecting to %s", s_creds.ssid.c_str());
    WiFi.begin(s_creds.ssid.c_str(), s_creds.pass.c_str());
    s_wifiAttemptInFlight = true;
    s_wifiAttemptStartedMs = now;
}

}  // namespace

namespace conn {

void begin(const Credentials& creds) { s_creds = creds; }

void loop() {
    const uint32_t now = millis();
    bool wifiUp = WiFi.status() == WL_CONNECTED;
    if (wifiUp && faults::networkOutageActive()) {
        WiFi.disconnect();
        wifiUp = false;
    }

    if (!wifiUp) {
        if (s_online) {
            onLinkLost();
        }
        serviceWifi(now);
        return;
    }

    if (s_wifiAttemptInFlight) {
        s_wifiAttemptInFlight = false;
        s_wifiFailures = 0;
        s_wifiBackoffMs = BACKOFF_MIN_MS;
        s_wifiRestoredEpoch = nowEpoch();
        if (s_everOnline) {
            g_stats.wifiReconnects++;
        }
        LOGI("wifi: connected, ip=%s", WiFi.localIP().toString().c_str());
        if (!s_timeConfigured) {
            configTime(0, 0, "pool.ntp.org", "time.google.com");
            s_timeConfigured = true;
        }
    }

    if (!hub::connected()) {
        if (s_online) {
            onLinkLost();
        }
        if ((int32_t)(now - s_nextMqttAttemptMs) >= 0) {
            if (hub::connect()) {
                onOnline();
            } else {
                s_nextMqttAttemptMs = now + (edgeOn() ? takeBackoff(s_mqttBackoffMs) : NAIVE_MQTT_RETRY_MS);
            }
        }
        return;
    }

    if (hub::tokenNeedsRefresh()) {
        LOGI("mqtt: refreshing SAS token");
        s_intentional = true;
        hub::disconnect();
        onLinkLost();
        return;
    }
    hub::loop();
}

bool online() { return s_online && hub::connected(); }

void noteIntentionalDisconnect() { s_intentional = true; }

void requestReconnect() {
    s_intentional = true;
    hub::disconnect();
    WiFi.disconnect();
    s_nextWifiAttemptMs = millis() + 500;
    s_wifiBackoffMs = BACKOFF_MIN_MS;
}

}  // namespace conn
