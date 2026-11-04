#include "app_component.h"

#include <ArduinoJson.h>
#include <esp_system.h>

#include <vector>

#include "app_state.h"
#include "device_config.h"
#include "offline_buffer.h"

namespace {

TaskHandle_t s_task = nullptr;
volatile bool s_stopRequested = false;
volatile bool s_exited = false;
volatile bool s_hang = false;
volatile bool s_leak = false;
volatile bool s_flood = false;
volatile bool s_freeRequested = false;
uint32_t s_seq = 0;

// Only touched by the app task, or by restart() once the task has exited.
std::vector<void*> s_leaked;

void freeLeak() {
    for (void* p : s_leaked) {
        free(p);
    }
    s_leaked.clear();
    s_leaked.shrink_to_fit();
}

void leakStep() {
    if (ESP.getFreeHeap() < LEAK_FLOOR_BYTES + LEAK_CHUNK_BYTES) {
        return;
    }
    void* p = malloc(LEAK_CHUNK_BYTES);
    if (p != nullptr) {
        memset(p, 0xA5, LEAK_CHUNK_BYTES);
        s_leaked.push_back(p);
    }
}

void enqueueFromApp(MsgKind kind, const char* body) {
    if (enqueueOut(kind, body)) {
        g_stats.appEnqueued++;
    } else {
        g_stats.dropped++;
    }
}

void floodStep() {
    char body[96];
    for (uint32_t i = 0; i < FLOOD_MSGS_PER_TICK; i++) {
        snprintf(body, sizeof(body), "{\"ts\":%.3f,\"seq\":%lu}", nowEpoch(), (unsigned long)++s_seq);
        enqueueFromApp(MsgKind::Telemetry, body);
    }
}

void emitTelemetry() {
#ifdef AUTOHEAL_BAD_FIRMWARE
    // Deliberate regression shipped by the bad_firmware build: telemetry is
    // sampled but never sent. Everything else (MQTT, methods, twin) still
    // works, so only the OTA self-test or the cloud's staleness check can
    // catch it.
    return;
#endif
    const DeviceConfig& c = cfg::running();
    JsonDocument doc;
    const double ts = nowEpoch();
    if (ts > 0) {
        doc["ts"] = ts;
    }
    doc["seq"] = ++s_seq;
    doc["fw"] = FW_VERSION;
    doc["uptime_s"] = millis() / 1000;
    doc["free_heap"] = ESP.getFreeHeap();
    doc["min_free_heap"] = ESP.getMinFreeHeap();
    doc["max_alloc_heap"] = ESP.getMaxAllocHeap();
    doc["rssi"] = g_stats.rssi;
    doc["wifi_reconnects"] = g_stats.wifiReconnects;
    doc["mqtt_reconnects"] = g_stats.mqttReconnects;
    doc["edge_heals"] = g_stats.edgeHeals;
    doc["buffered"] = buffer::count();
    doc["dropped"] = g_stats.dropped;
    doc["reset_reason"] = (int)esp_reset_reason();
    // running config, so the cloud can detect drift against what it asked for
    doc["telemetry_interval_s"] = c.telemetryIntervalS;
    doc["heap_low_bytes"] = c.heapLowBytes;
    doc["edge_healing"] = c.edgeHealing;

    char body[OUT_MSG_MAX];
    const size_t n = serializeJson(doc, body, sizeof(body));
    if (n == 0 || n >= sizeof(body)) {
        g_stats.dropped++;
        return;
    }
    enqueueFromApp(MsgKind::Telemetry, body);
}

void taskMain(void*) {
    uint32_t lastTelemetryMs = 0;
    bool first = true;
    for (;;) {
        // Stop requests are honoured even while "hung", so restart() never has
        // to kill the task mid-malloc and risk the heap lock.
        if (s_stopRequested) {
            break;
        }
        if (s_hang) {
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        g_stats.lastHeartbeatMs = millis();

        if (s_freeRequested) {
            freeLeak();
            s_freeRequested = false;
        }
        if (s_leak) {
            leakStep();
        }
        if (s_flood) {
            floodStep();
        }

        const uint32_t intervalMs = cfg::running().telemetryIntervalS * 1000UL;
        if (first || millis() - lastTelemetryMs >= intervalMs) {
            first = false;
            lastTelemetryMs = millis();
            emitTelemetry();
        }
        vTaskDelay(pdMS_TO_TICKS(APP_TICK_MS));
    }
    s_exited = true;
    vTaskDelete(nullptr);
}

void startTask() {
    s_stopRequested = false;
    s_exited = false;
    g_stats.lastHeartbeatMs = millis();
    xTaskCreatePinnedToCore(taskMain, "app", APP_TASK_STACK, nullptr, 1, &s_task, 1);
}

}  // namespace

namespace app {

void begin() { startTask(); }

void restart() {
    LOGI("app: restarting component");
    if (s_task != nullptr) {
        s_stopRequested = true;
        const uint32_t start = millis();
        while (!s_exited && millis() - start < 2000) {
            delay(20);
        }
        if (!s_exited) {
            vTaskDelete(s_task);
        }
        s_task = nullptr;
    }
    // A fresh component starts with none of the old one's state.
    freeLeak();
    s_hang = false;
    s_leak = false;
    s_flood = false;
    s_freeRequested = false;
    startTask();
}

bool heartbeatFresh() { return millis() - g_stats.lastHeartbeatMs < HEARTBEAT_STALE_MS; }

void setHang(bool on) { s_hang = on; }

void setLeak(bool on) {
    s_leak = on;
    if (!on) {
        s_freeRequested = true;
    }
}

void setFlood(bool on) { s_flood = on; }

}  // namespace app
