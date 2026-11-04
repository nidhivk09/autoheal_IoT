#pragma once
// State shared between the main loop (network, MQTT, edge healer) and the
// app component task (telemetry). Counters are plain 32-bit values: single
// writer, word-sized reads are atomic on the ESP32.

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include "config.h"

#define LOGI(fmt, ...) Serial.printf("[%8lu] " fmt "\n", (unsigned long)millis(), ##__VA_ARGS__)

enum class MsgKind : uint8_t { Telemetry = 0, Event = 1 };

struct OutMsg {
    MsgKind kind;
    char body[OUT_MSG_MAX];
};

struct Stats {
    volatile uint32_t lastHeartbeatMs = 0;      // app task liveness
    volatile uint32_t lastPublishMs = 0;        // last *live* telemetry publish
    volatile uint32_t telemetryPublished = 0;   // live telemetry since boot
    volatile uint32_t appEnqueued = 0;          // messages produced by the app task
    volatile uint32_t published = 0;
    volatile uint32_t dropped = 0;
    volatile uint32_t wifiReconnects = 0;
    volatile uint32_t mqttReconnects = 0;
    volatile uint32_t edgeHeals = 0;
    volatile int32_t rssi = 0;
};

extern Stats g_stats;
extern QueueHandle_t g_outbox;

// Unix time with millisecond resolution, or 0 if NTP hasn't synced yet.
double nowEpoch();

// Non-blocking; returns false if the outbox is full.
bool enqueueOut(MsgKind kind, const char* body);

void scheduleReboot(uint32_t delayMs, const char* reason);
void serviceScheduledReboot();
