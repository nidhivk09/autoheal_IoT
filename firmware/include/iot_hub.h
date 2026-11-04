#pragma once
// Minimal Azure IoT Hub device client over MQTT (PubSubClient + TLS):
// SAS-token auth, telemetry, direct methods and device twin.
// Protocol reference: https://learn.microsoft.com/azure/iot/iot-mqtt-connect-to-iot-hub
//
// Not thread-safe: call everything from the main loop task only.

#include <ArduinoJson.h>

#include "app_state.h"
#include "provisioning.h"

namespace hub {
// Returns the direct-method status code; fills `response` (already an object).
using MethodHandler = int (*)(JsonVariantConst payload, JsonObject response);
using DesiredHandler = void (*)(JsonObjectConst desired);
using ConnectedHandler = void (*)();

void begin(const Credentials& creds);
bool connect();
void disconnect();
bool connected();
void loop();
bool tokenNeedsRefresh();

// replay=true marks messages coming out of the offline buffer (message
// property replay=1) so the cloud doesn't count them toward flood detection.
bool publish(MsgKind kind, const char* body, bool replay);
bool reportProperties(JsonDocument& patch);

void registerMethod(const char* name, MethodHandler handler);
void onDesired(DesiredHandler handler);
void onConnected(ConnectedHandler handler);
}  // namespace hub
