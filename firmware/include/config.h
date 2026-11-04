#pragma once
// Compile-time constants. Values that the cloud is allowed to change at
// runtime (telemetry interval, heap threshold, edge healing on/off) are only
// *defaults* here - the live values come from the device twin, see
// device_config.h.

#include <stdint.h>
#include <stddef.h>

#ifndef FW_VERSION
#define FW_VERSION "0.0.0-dev"
#endif

#ifndef AUTOHEAL_ENABLE_FAULT_INJECTION
#define AUTOHEAL_ENABLE_FAULT_INJECTION 0
#endif

// --- Desired-config defaults (overridden by twin desired.config) ---
constexpr uint32_t DEFAULT_TELEMETRY_INTERVAL_S = 5;
constexpr uint32_t DEFAULT_HEAP_LOW_BYTES = 70000;
constexpr bool DEFAULT_EDGE_HEALING = true;

// --- App component (the telemetry task) ---
constexpr uint32_t APP_TICK_MS = 200;
constexpr uint32_t APP_TASK_STACK = 8192;
constexpr uint32_t HEARTBEAT_STALE_MS = 5000;

// --- Messaging ---
constexpr size_t OUT_MSG_MAX = 768;
constexpr size_t OUTBOX_LEN = 24;
constexpr size_t MQTT_BUFFER_BYTES = 4096;
constexpr uint32_t SAS_TTL_S = 3600;
constexpr uint32_t SAS_REFRESH_MARGIN_S = 300;
constexpr size_t OFFLINE_BUFFER_MAX_BYTES = 96 * 1024;
constexpr uint32_t BUFFER_DRAIN_BATCH = 5;

// --- Connectivity ---
constexpr uint32_t WIFI_ATTEMPT_TIMEOUT_MS = 10000;
// edge healing: exponential backoff between attempts
constexpr uint32_t BACKOFF_MIN_MS = 1000;
constexpr uint32_t BACKOFF_MAX_MS = 8000;
constexpr uint32_t WIFI_DRIVER_RESET_AFTER = 8;          // failed attempts
constexpr uint32_t EDGE_OFFLINE_REBOOT_MS = 10UL * 60 * 1000;
// edge healing off: fixed-interval retries, like typical tutorial firmware
constexpr uint32_t NAIVE_WIFI_RETRY_MS = 30000;
constexpr uint32_t NAIVE_MQTT_RETRY_MS = 5000;

// --- Edge healer ---
constexpr uint32_t EDGE_CHECK_INTERVAL_MS = 1000;
constexpr uint32_t EDGE_VERIFY_TIMEOUT_MS = 5000;
constexpr uint32_t HEAP_LOW_CONSECUTIVE = 3;
constexpr uint32_t EDGE_FLOOD_RATE_LIMIT = 5;            // app messages per second
constexpr uint32_t FLOOD_CONSECUTIVE = 3;

// --- OTA ---
constexpr uint32_t OTA_SELF_TEST_WINDOW_MS = 60000;
constexpr uint32_t OTA_SELF_TEST_MIN_PUBLISHES = 3;
constexpr uint32_t OTA_MAX_PENDING_BOOTS = 3;

// --- Fault injection ---
constexpr uint32_t NETWORK_FAULT_DEFAULT_S = 30;
constexpr size_t LEAK_CHUNK_BYTES = 4096;
constexpr size_t LEAK_FLOOR_BYTES = 45000;               // never leak below this, keep TLS alive
constexpr uint32_t FLOOD_MSGS_PER_TICK = 20;             // ~100 msg/s
constexpr uint32_t DRIFT_TELEMETRY_INTERVAL_S = 20;

// --- Watchdog for the main loop (last-resort hardware safety net) ---
constexpr uint32_t LOOP_WDT_TIMEOUT_S = 30;
