#pragma once
// Over-the-air updates with a post-update self-test and automatic rollback.
//
// ota_update {url, version}: downloads the image over HTTPS into the
// inactive OTA slot and reboots into it. The new image then has
// OTA_SELF_TEST_WINDOW_MS to prove itself (MQTT up and at least
// OTA_SELF_TEST_MIN_PUBLISHES telemetry messages actually sent):
//   - pass                       -> image marked valid
//   - fail, edge healing on      -> rolls back to the previous slot (tier 1)
//   - fail, edge healing off     -> image is kept; the cloud has to notice
//                                   and call rollback_firmware (tier 2)
// A boot loop (more than OTA_MAX_PENDING_BOOTS boots without passing) also
// triggers rollback when edge healing is on.
//
// Pending-update state is tracked in NVS rather than relying only on the
// bootloader's rollback support, so it behaves the same whether or not the
// Arduino core was built with CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE.

#include <ArduinoJson.h>

namespace ota {
void beginBoot();
void loop();
int requestUpdate(JsonVariantConst payload, JsonObject response);
int requestRollback(JsonObject response);
bool rollbackPossible();
bool selfTestActive();
void fillStatus(JsonObject out);
}  // namespace ota
