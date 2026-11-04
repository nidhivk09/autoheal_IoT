#include <sys/time.h>

#include "app_state.h"

Stats g_stats;
QueueHandle_t g_outbox = nullptr;

static uint32_t s_rebootAtMs = 0;
static bool s_rebootScheduled = false;

double nowEpoch() {
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    if (tv.tv_sec < 1700000000) {
        return 0;
    }
    return tv.tv_sec + tv.tv_usec / 1e6;
}

bool enqueueOut(MsgKind kind, const char* body) {
    if (g_outbox == nullptr) {
        return false;
    }
    OutMsg msg;
    msg.kind = kind;
    strlcpy(msg.body, body, sizeof(msg.body));
    return xQueueSend(g_outbox, &msg, 0) == pdTRUE;
}

void scheduleReboot(uint32_t delayMs, const char* reason) {
    LOGI("reboot scheduled in %lums: %s", (unsigned long)delayMs, reason);
    s_rebootAtMs = millis() + delayMs;
    s_rebootScheduled = true;
}

void serviceScheduledReboot() {
    if (s_rebootScheduled && (int32_t)(millis() - s_rebootAtMs) >= 0) {
        Serial.flush();
        ESP.restart();
    }
}
