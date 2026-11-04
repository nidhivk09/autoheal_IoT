// ESP32 edge firmware for the self-healing IoT project.
//
// Tasks:
//   loopTask (Arduino loop)  WiFi/MQTT supervision, direct methods, device
//                            twin, outbox -> IoT Hub, edge healer, OTA
//   app                      the app component: samples health, produces
//                            telemetry (app_component.cpp)
//
// Direct methods (invoked by the remediation Function and the controller):
//   get_health, restart_service, reboot, reconnect_network,
//   apply_desired_config, ota_update, rollback_firmware,
//   inject_fault, clear_fault

#include <Arduino.h>
#include <WiFi.h>
#include <esp_task_wdt.h>

#include "app_component.h"
#include "app_state.h"
#include "connectivity.h"
#include "device_config.h"
#include "edge_healer.h"
#include "faults.h"
#include "iot_hub.h"
#include "offline_buffer.h"
#include "ota.h"
#include "provisioning.h"

namespace {

Credentials s_creds;
bool s_reportPending = false;
uint32_t s_lastDrainMs = 0;

void requestReport() { s_reportPending = true; }

void sendReport() {
    JsonDocument patch;
    ota::fillStatus(patch["firmware"].to<JsonObject>());
    cfg::toJson(cfg::running(), patch["config"].to<JsonObject>());
    patch["config_in_sync"] = cfg::inSync();
    JsonObject device = patch["device"].to<JsonObject>();
    device["chip"] = ESP.getChipModel();
    device["mac"] = WiFi.macAddress();
    device["reset_reason"] = (int)esp_reset_reason();
    if (hub::reportProperties(patch)) {
        s_reportPending = false;
    }
}

// --- device twin ---

void onDesired(JsonObjectConst desired) {
    JsonObjectConst config = desired["config"];
    if (!config.isNull()) {
        cfg::mergeDesired(config);
    }
    // Always reapply: a desired patch without config changes (the cloud bumps
    // desired.reconcile_token) is how the cloud tier fixes config drift.
    cfg::applyDesired();
    requestReport();
}

// --- direct methods ---

uint32_t telemetryFreshWindowMs() {
    return max<uint32_t>(3 * cfg::desired().telemetryIntervalS * 1000UL, 15000);
}

int methodGetHealth(JsonVariantConst, JsonObject res) {
    const bool flowing = g_stats.telemetryPublished > 0 &&
                         millis() - g_stats.lastPublishMs < telemetryFreshWindowMs();
    const bool heapOk = ESP.getFreeHeap() >= cfg::desired().heapLowBytes;
    const bool configOk = cfg::inSync();
    const bool heartbeat = app::heartbeatFresh();

    JsonObject checks = res["checks"].to<JsonObject>();
    checks["telemetry_flowing"] = flowing;
    checks["heap_ok"] = heapOk;
    checks["config_in_sync"] = configOk;
    checks["app_heartbeat"] = heartbeat;
    res["healthy"] = flowing && heapOk && configOk && heartbeat;
    res["free_heap"] = ESP.getFreeHeap();
    res["uptime_s"] = millis() / 1000;
    res["edge_healing"] = cfg::running().edgeHealing;
    // Lets the cloud tier hold off while the device is mid-heal or mid-self-test.
    res["edge_busy"] = edge::busy() || ota::selfTestActive();
    ota::fillStatus(res["firmware"].to<JsonObject>());
    return 200;
}

int methodRestartService(JsonVariantConst, JsonObject res) {
    app::restart();
    res["restarted"] = "app";
    return 200;
}

int methodReboot(JsonVariantConst, JsonObject res) {
    conn::noteIntentionalDisconnect();
    scheduleReboot(500, "reboot direct method");
    res["accepted"] = true;
    return 202;
}

int methodReconnectNetwork(JsonVariantConst, JsonObject res) {
    conn::requestReconnect();
    res["accepted"] = true;
    return 202;
}

int methodApplyDesiredConfig(JsonVariantConst, JsonObject res) {
    cfg::applyDesired();
    requestReport();
    res["config_in_sync"] = cfg::inSync();
    return 200;
}

int methodOtaUpdate(JsonVariantConst payload, JsonObject res) { return ota::requestUpdate(payload, res); }

int methodRollbackFirmware(JsonVariantConst, JsonObject res) { return ota::requestRollback(res); }

int methodInjectFault(JsonVariantConst payload, JsonObject res) {
    return faults::inject(payload["type"] | "", payload, res);
}

int methodClearFault(JsonVariantConst payload, JsonObject res) {
    return faults::clear(payload["type"] | "", res);
}

void registerMethods() {
    hub::registerMethod("get_health", methodGetHealth);
    hub::registerMethod("restart_service", methodRestartService);
    hub::registerMethod("reboot", methodReboot);
    hub::registerMethod("reconnect_network", methodReconnectNetwork);
    hub::registerMethod("apply_desired_config", methodApplyDesiredConfig);
    hub::registerMethod("ota_update", methodOtaUpdate);
    hub::registerMethod("rollback_firmware", methodRollbackFirmware);
    hub::registerMethod("inject_fault", methodInjectFault);
    hub::registerMethod("clear_fault", methodClearFault);
}

// --- outbox ---

bool publishReplay(MsgKind kind, const char* body) {
    if (!hub::publish(kind, body, true)) {
        return false;
    }
    g_stats.published++;
    return true;
}

void drainOutbox() {
    OutMsg msg;
    while (xQueueReceive(g_outbox, &msg, 0) == pdTRUE) {
        if (conn::online() && hub::publish(msg.kind, msg.body, false)) {
            g_stats.published++;
            if (msg.kind == MsgKind::Telemetry) {
                g_stats.telemetryPublished++;
                g_stats.lastPublishMs = millis();
            }
            continue;
        }
        const bool keep = msg.kind == MsgKind::Event || cfg::running().edgeHealing;
        if (!(keep && buffer::append(msg.kind, msg.body))) {
            g_stats.dropped++;
        }
    }
    if (conn::online() && buffer::count() > 0 && millis() - s_lastDrainMs > 200) {
        s_lastDrainMs = millis();
        buffer::drain(publishReplay, BUFFER_DRAIN_BATCH);
    }
}

}  // namespace

void setup() {
    Serial.begin(115200);
    delay(200);
    LOGI("autoheal firmware %s%s", FW_VERSION,
#ifdef AUTOHEAL_BAD_FIRMWARE
         " (bad build)"
#else
         ""
#endif
    );

    if (!prov::load(s_creds)) {
        prov::runConsoleUntilProvisioned();
    }

    buffer::begin();
    cfg::begin();
    edge::begin();
    ota::beginBoot();

    g_outbox = xQueueCreate(OUTBOX_LEN, sizeof(OutMsg));

    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(false);  // connectivity.cpp owns reconnect policy

    hub::begin(s_creds);
    hub::onDesired(onDesired);
    hub::onConnected(requestReport);
    registerMethods();
    conn::begin(s_creds);
    app::begin();

    esp_task_wdt_init(LOOP_WDT_TIMEOUT_S, true);
    esp_task_wdt_add(nullptr);
}

void loop() {
    esp_task_wdt_reset();
    prov::pollConsole();
    conn::loop();
    drainOutbox();
    if (s_reportPending && conn::online()) {
        sendReport();
    }
    edge::loop();
    ota::loop();
    serviceScheduledReboot();
    delay(5);
}
