#include "provisioning.h"

#include <LittleFS.h>
#include <Preferences.h>

#include "app_state.h"

namespace {

const char* const kKeys[] = {"ssid", "pass", "host", "device_id", "device_key"};
String s_line;

String readKey(Preferences& prefs, const char* key) {
    // NVS keys are limited to 15 chars; ours are short enough to use as-is.
    return prefs.getString(key, "");
}

bool isValidKey(const String& key) {
    for (const char* k : kKeys) {
        if (key == k) {
            return true;
        }
    }
    return false;
}

void printShow() {
    Preferences prefs;
    prefs.begin("prov", true);
    for (const char* k : kKeys) {
        String v = readKey(prefs, k);
        bool secret = strcmp(k, "pass") == 0 || strcmp(k, "device_key") == 0;
        Serial.printf("  %-10s = %s\n", k, v.isEmpty() ? "(unset)" : (secret ? "********" : v.c_str()));
    }
    prefs.end();
}

void handleLine(String line) {
    line.trim();
    if (line.isEmpty()) {
        return;
    }
    if (line == "show") {
        printShow();
    } else if (line == "reboot") {
        Serial.println("rebooting");
        Serial.flush();
        ESP.restart();
    } else if (line == "factory-reset") {
        for (const char* ns : {"prov", "cfg", "ota", "edge"}) {
            Preferences prefs;
            prefs.begin(ns, false);
            prefs.clear();
            prefs.end();
        }
        LittleFS.format();
        Serial.println("factory reset done, rebooting");
        Serial.flush();
        ESP.restart();
    } else if (line.startsWith("set ")) {
        int sp = line.indexOf(' ', 4);
        if (sp < 0) {
            Serial.println("usage: set <key> <value>");
            return;
        }
        String key = line.substring(4, sp);
        String value = line.substring(sp + 1);  // values may contain spaces (SSIDs)
        if (!isValidKey(key)) {
            Serial.println("unknown key; valid keys: ssid pass host device_id device_key");
            return;
        }
        Preferences prefs;
        prefs.begin("prov", false);
        prefs.putString(key.c_str(), value);
        prefs.end();
        Serial.printf("ok %s\n", key.c_str());
    } else {
        Serial.println("commands: set <key> <value> | show | reboot | factory-reset");
    }
}

}  // namespace

namespace prov {

bool load(Credentials& out) {
    Preferences prefs;
    prefs.begin("prov", true);
    out.ssid = readKey(prefs, "ssid");
    out.pass = readKey(prefs, "pass");
    out.host = readKey(prefs, "host");
    out.deviceId = readKey(prefs, "device_id");
    out.deviceKey = readKey(prefs, "device_key");
    prefs.end();
    return !out.ssid.isEmpty() && !out.host.isEmpty() && !out.deviceId.isEmpty() &&
           !out.deviceKey.isEmpty();
}

void runConsoleUntilProvisioned() {
    uint32_t lastPrompt = 0;
    for (;;) {
        if (millis() - lastPrompt > 5000) {
            lastPrompt = millis();
            Serial.println("NOT PROVISIONED - set ssid/pass/host/device_id/device_key, then 'reboot':");
            printShow();
        }
        pollConsole();
        delay(10);
    }
}

void pollConsole() {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (!s_line.isEmpty()) {
                handleLine(s_line);
            }
            s_line = "";
        } else if (s_line.length() < 256) {
            s_line += c;
        }
    }
}

}  // namespace prov
