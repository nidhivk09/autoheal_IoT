#include "faults.h"

#include "app_component.h"
#include "app_state.h"
#include "device_config.h"

namespace {
bool s_outageArmed = false;
uint32_t s_outageStartMs = 0;
uint32_t s_outageEndMs = 0;
}  // namespace

namespace faults {

int inject(const char* type, JsonVariantConst params, JsonObject response) {
#if !AUTOHEAL_ENABLE_FAULT_INJECTION
    (void)type;
    (void)params;
    response["error"] = "fault injection is disabled in this build";
    return 403;
#else
    const String t(type ? type : "");
    if (t == "network_loss") {
        const uint32_t duration = constrain(params["duration_s"] | NETWORK_FAULT_DEFAULT_S, 5u, 600u);
        // Start slightly in the future so the method response gets out first.
        s_outageStartMs = millis() + 500;
        s_outageEndMs = s_outageStartMs + duration * 1000;
        s_outageArmed = true;
        response["duration_s"] = duration;
    } else if (t == "task_hang") {
        app::setHang(true);
    } else if (t == "memory_leak") {
        app::setLeak(true);
    } else if (t == "message_flood") {
        app::setFlood(true);
    } else if (t == "config_drift") {
        cfg::drift();
    } else {
        response["error"] = "unknown fault type";
        return 400;
    }
    LOGI("FAULT INJECTED: %s", type);
    response["injected"] = type;
    return 200;
#endif
}

int clear(const char* type, JsonObject response) {
    const String t(type ? type : "");
    if (t == "network_loss") {
        s_outageArmed = false;
    } else if (t == "task_hang") {
        app::setHang(false);
    } else if (t == "memory_leak") {
        app::setLeak(false);
    } else if (t == "message_flood") {
        app::setFlood(false);
    } else if (t == "config_drift") {
        cfg::applyDesired();
    } else {
        response["error"] = "unknown fault type";
        return 400;
    }
    LOGI("fault cleared: %s", type);
    response["cleared"] = type;
    return 200;
}

bool networkOutageActive() {
    if (!s_outageArmed) {
        return false;
    }
    const uint32_t now = millis();
    if ((int32_t)(now - s_outageEndMs) >= 0) {
        s_outageArmed = false;
        LOGI("simulated network outage over");
        return false;
    }
    return (int32_t)(now - s_outageStartMs) >= 0;
}

}  // namespace faults
