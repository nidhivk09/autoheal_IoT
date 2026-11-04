#include "ota.h"

#include <HTTPUpdate.h>
#include <Preferences.h>
#include <WiFiClientSecure.h>
#include <esp_ota_ops.h>
#include <esp_task_wdt.h>

#include "app_state.h"
#include "ca_certs.h"
#include "connectivity.h"
#include "device_config.h"
#include "edge_healer.h"
#include "iot_hub.h"

// Arduino-ESP32 calls this at startup; returning true stops the core from
// marking a freshly-updated image valid before our self-test has run.
extern "C" bool verifyRollbackLater() { return true; }

namespace {

bool s_selfTestActive = false;
uint32_t s_selfTestStartMs = 0;
String s_pendingUrl;
String s_pendingVersion;
bool s_rollbackRequested = false;
uint32_t s_actionAtMs = 0;
String s_lastError;

void markRunningValid() { esp_ota_mark_app_valid_cancel_rollback(); }

void clearPending() {
    Preferences prefs;
    prefs.begin("ota", false);
    prefs.putBool("pending", false);
    prefs.putUInt("boots", 0);
    prefs.end();
}

void reportFirmware() {
    JsonDocument patch;
    ota::fillStatus(patch["firmware"].to<JsonObject>());
    hub::reportProperties(patch);
}

void accept(const char* reason) {
    LOGI("ota: image %s accepted (%s)", FW_VERSION, reason);
    markRunningValid();
    clearPending();
    s_selfTestActive = false;
    reportFirmware();
}

bool doRollback() {
    const esp_partition_t* running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(running, &state) == ESP_OK && state == ESP_OTA_IMG_PENDING_VERIFY) {
        // Bootloader-managed rollback; only returns on failure.
        esp_ota_mark_app_invalid_rollback_and_reboot();
    }
    const esp_partition_t* other = esp_ota_get_next_update_partition(nullptr);
    esp_app_desc_t desc;
    if (other == nullptr || esp_ota_get_partition_description(other, &desc) != ESP_OK) {
        s_lastError = "no valid image in the other OTA slot";
        LOGI("ota: rollback impossible: %s", s_lastError.c_str());
        return false;
    }
    if (esp_ota_set_boot_partition(other) != ESP_OK) {
        s_lastError = "esp_ota_set_boot_partition failed";
        return false;
    }
    LOGI("ota: rolling back to slot %s", other->label);
    conn::noteIntentionalDisconnect();
    hub::disconnect();
    delay(100);
    ESP.restart();
    return true;
}

void failSelfTest(const char* reason) {
    LOGI("ota: self-test failed (%s)", reason);
    s_selfTestActive = false;
    const double now = nowEpoch();
    Preferences prefs;
    prefs.begin("ota", false);
    prefs.putString("rb_from", FW_VERSION);
    prefs.putString("rb_reason", reason);
    prefs.putDouble("rb_at", now);
    prefs.end();
    clearPending();
    edge::storeBootEvent("ota_self_test_failed", "ota_rollback", now);
    if (!doRollback()) {
        // Nothing to roll back to; keep running rather than boot-looping.
        accept("rollback impossible");
    }
}

void performUpdate() {
    const String url = s_pendingUrl;
    const String version = s_pendingVersion;
    s_pendingUrl = "";
    LOGI("ota: downloading %s", version.c_str());

    conn::noteIntentionalDisconnect();
    hub::disconnect();  // frees the MQTT TLS session's RAM for the download

    // The download blocks the loop for longer than the loop watchdog allows.
    esp_task_wdt_delete(nullptr);

    WiFiClientSecure client;
    client.setCACert(AZURE_ROOT_CAS);
    httpUpdate.rebootOnUpdate(false);
    httpUpdate.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
    const t_httpUpdate_return result = httpUpdate.update(client, url);

    if (result == HTTP_UPDATE_OK) {
        Preferences prefs;
        prefs.begin("ota", false);
        prefs.putBool("pending", true);
        prefs.putUInt("boots", 0);
        prefs.putString("prev", FW_VERSION);
        prefs.putString("target", version);
        prefs.putDouble("at", nowEpoch());
        prefs.end();
        LOGI("ota: update written, rebooting");
        delay(100);
        ESP.restart();
    }

    s_lastError = httpUpdate.getLastErrorString();
    LOGI("ota: update failed: %s", s_lastError.c_str());
    esp_task_wdt_add(nullptr);
}

}  // namespace

namespace ota {

void beginBoot() {
    Preferences prefs;
    prefs.begin("ota", false);
    const bool pending = prefs.getBool("pending", false);
    const uint32_t boots = prefs.getUInt("boots", 0) + 1;
    if (pending) {
        prefs.putUInt("boots", boots);
    }
    prefs.end();

    if (!pending) {
        markRunningValid();
        return;
    }
    if (!cfg::running().edgeHealing) {
        // Baseline behaviour: trust the new image immediately.
        LOGI("ota: edge healing off, accepting %s without self-test", FW_VERSION);
        markRunningValid();
        clearPending();
        return;
    }
    if (boots > OTA_MAX_PENDING_BOOTS) {
        failSelfTest("boot_loop");
        return;
    }
    LOGI("ota: new image %s, self-test running (boot %lu)", FW_VERSION, (unsigned long)boots);
    s_selfTestActive = true;
    s_selfTestStartMs = millis();
}

void loop() {
    if (s_selfTestActive) {
        if (hub::connected() && g_stats.telemetryPublished >= OTA_SELF_TEST_MIN_PUBLISHES) {
            accept("self-test passed");
        } else if (millis() - s_selfTestStartMs > OTA_SELF_TEST_WINDOW_MS) {
            if (cfg::running().edgeHealing) {
                failSelfTest("self_test_failed");
            } else {
                accept("edge healing turned off during self-test");
            }
        }
    }
    if (!s_pendingUrl.isEmpty() && (int32_t)(millis() - s_actionAtMs) >= 0) {
        performUpdate();
    }
    if (s_rollbackRequested && (int32_t)(millis() - s_actionAtMs) >= 0) {
        s_rollbackRequested = false;
        doRollback();
    }
}

int requestUpdate(JsonVariantConst payload, JsonObject response) {
    const char* url = payload["url"] | "";
    if (strncmp(url, "https://", 8) != 0) {
        response["error"] = "url must be https";
        return 400;
    }
    if (s_selfTestActive) {
        response["error"] = "self-test of the current image still running";
        return 409;
    }
    s_pendingUrl = url;
    s_pendingVersion = payload["version"] | "";
    s_actionAtMs = millis() + 500;  // let the method response go out first
    response["accepted"] = true;
    response["version"] = s_pendingVersion;
    return 202;
}

int requestRollback(JsonObject response) {
    if (!rollbackPossible()) {
        response["error"] = "no valid image in the other OTA slot";
        return 409;
    }
    s_rollbackRequested = true;
    s_actionAtMs = millis() + 500;
    response["accepted"] = true;
    return 202;
}

bool selfTestActive() { return s_selfTestActive; }

bool rollbackPossible() {
    const esp_partition_t* other = esp_ota_get_next_update_partition(nullptr);
    esp_app_desc_t desc;
    return other != nullptr && esp_ota_get_partition_description(other, &desc) == ESP_OK;
}

void fillStatus(JsonObject out) {
    Preferences prefs;
    prefs.begin("ota", true);
    const double updatedAt = prefs.getDouble("at", 0);
    const double now = nowEpoch();
    out["version"] = FW_VERSION;
    out["partition"] = esp_ota_get_running_partition()->label;
    out["state"] = s_selfTestActive ? "self_test" : "valid";
    out["previous_version"] = prefs.getString("prev", "");
    out["updated_at"] = updatedAt;
    out["since_update_s"] = (updatedAt > 0 && now > 0) ? (long)(now - updatedAt) : -1;
    out["rollback_possible"] = rollbackPossible();
    out["last_error"] = s_lastError;
    if (prefs.isKey("rb_from")) {
        JsonObject rb = out["last_rollback"].to<JsonObject>();
        rb["from"] = prefs.getString("rb_from", "");
        rb["reason"] = prefs.getString("rb_reason", "");
        rb["at"] = prefs.getDouble("rb_at", 0);
    }
    prefs.end();
}

}  // namespace ota
