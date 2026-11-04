#include "iot_hub.h"

#include <PubSubClient.h>
#include <WiFiClientSecure.h>
#include <mbedtls/base64.h>
#include <mbedtls/md.h>
#include <time.h>

#include <string>
#include <vector>

#include "ca_certs.h"

namespace {

struct MethodEntry {
    const char* name;
    hub::MethodHandler handler;
};

struct Inbound {
    std::string topic;
    std::string payload;
};

WiFiClientSecure s_tls;
PubSubClient s_mqtt(s_tls);
Credentials s_creds;
time_t s_tokenExpiry = 0;
uint32_t s_rid = 0;

MethodEntry s_methods[16];
size_t s_methodCount = 0;
hub::DesiredHandler s_desiredHandler = nullptr;
hub::ConnectedHandler s_connectedHandler = nullptr;

// PubSubClient reuses its buffer for the next packet, so the callback only
// copies messages here; they're handled after mqtt.loop() returns.
std::vector<Inbound> s_inbox;

const char* const kMethodPrefix = "$iothub/methods/POST/";
const char* const kTwinResPrefix = "$iothub/twin/res/";
const char* const kDesiredPrefix = "$iothub/twin/PATCH/properties/desired/";

String urlEncode(const String& in) {
    static const char* hex = "0123456789ABCDEF";
    String out;
    out.reserve(in.length() * 3);
    for (size_t i = 0; i < in.length(); i++) {
        char c = in[i];
        if (isalnum((unsigned char)c) || c == '-' || c == '_' || c == '.' || c == '~') {
            out += c;
        } else {
            out += '%';
            out += hex[(c >> 4) & 0xF];
            out += hex[c & 0xF];
        }
    }
    return out;
}

// https://learn.microsoft.com/azure/iot-hub/authenticate-authorize-sas#sas-tokens
String makeSasToken(time_t expiry) {
    String resource = urlEncode(s_creds.host + "/devices/" + s_creds.deviceId);
    String toSign = resource + "\n" + String((unsigned long)expiry);

    unsigned char key[64];
    size_t keyLen = 0;
    if (mbedtls_base64_decode(key, sizeof(key), &keyLen,
                              (const unsigned char*)s_creds.deviceKey.c_str(),
                              s_creds.deviceKey.length()) != 0) {
        LOGI("device_key is not valid base64");
        return "";
    }

    unsigned char hmac[32];
    const mbedtls_md_info_t* md = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (mbedtls_md_hmac(md, key, keyLen, (const unsigned char*)toSign.c_str(), toSign.length(),
                        hmac) != 0) {
        return "";
    }

    unsigned char sig[64];
    size_t sigLen = 0;
    if (mbedtls_base64_encode(sig, sizeof(sig), &sigLen, hmac, sizeof(hmac)) != 0) {
        return "";
    }
    String sigStr((const char*)sig);
    return "SharedAccessSignature sr=" + resource + "&sig=" + urlEncode(sigStr) +
           "&se=" + String((unsigned long)expiry);
}

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
    s_inbox.push_back({std::string(topic), std::string((const char*)payload, length)});
}

void requestTwin() {
    String topic = "$iothub/twin/GET/?$rid=get" + String(++s_rid);
    s_mqtt.publish(topic.c_str(), "");
}

void handleMethod(const std::string& topic, const std::string& payload) {
    // $iothub/methods/POST/{method name}/?$rid={request id}
    const size_t prefixLen = strlen(kMethodPrefix);
    const size_t nameEnd = topic.find("/?$rid=", prefixLen);
    if (nameEnd == std::string::npos) {
        return;
    }
    const std::string name = topic.substr(prefixLen, nameEnd - prefixLen);
    const std::string rid = topic.substr(nameEnd + 7);

    JsonDocument request;
    if (!payload.empty()) {
        deserializeJson(request, payload);
    }
    JsonDocument response;
    JsonObject responseObj = response.to<JsonObject>();

    int status = 404;
    bool found = false;
    for (size_t i = 0; i < s_methodCount; i++) {
        if (name == s_methods[i].name) {
            found = true;
            status = s_methods[i].handler(request.as<JsonVariantConst>(), responseObj);
            break;
        }
    }
    if (!found) {
        responseObj["error"] = "unknown method";
    }
    LOGI("direct method %s -> %d", name.c_str(), status);

    String body;
    serializeJson(response, body);
    String resTopic = "$iothub/methods/res/" + String(status) + "/?$rid=" + rid.c_str();
    s_mqtt.publish(resTopic.c_str(), body.c_str());
}

void handleTwinResponse(const std::string& topic, const std::string& payload) {
    // $iothub/twin/res/{status}/?$rid={request id}[&$version=...]
    const int status = atoi(topic.c_str() + strlen(kTwinResPrefix));
    const size_t ridPos = topic.find("$rid=");
    if (ridPos == std::string::npos) {
        return;
    }
    const bool isGet = topic.compare(ridPos + 5, 3, "get") == 0;
    if (!isGet) {
        if (status >= 300) {
            LOGI("reported properties update failed: %d", status);
        }
        return;
    }
    if (status != 200) {
        LOGI("twin GET failed: %d", status);
        return;
    }
    JsonDocument doc;
    if (deserializeJson(doc, payload) == DeserializationError::Ok && s_desiredHandler) {
        s_desiredHandler(doc["desired"].as<JsonObjectConst>());
    }
}

void handleDesiredPatch(const std::string& payload) {
    JsonDocument doc;
    if (deserializeJson(doc, payload) == DeserializationError::Ok && s_desiredHandler) {
        s_desiredHandler(doc.as<JsonObjectConst>());
    }
}

void processInbox() {
    std::vector<Inbound> pending;
    pending.swap(s_inbox);
    for (const Inbound& msg : pending) {
        if (msg.topic.rfind(kMethodPrefix, 0) == 0) {
            handleMethod(msg.topic, msg.payload);
        } else if (msg.topic.rfind(kTwinResPrefix, 0) == 0) {
            handleTwinResponse(msg.topic, msg.payload);
        } else if (msg.topic.rfind(kDesiredPrefix, 0) == 0) {
            handleDesiredPatch(msg.payload);
        }
    }
}

}  // namespace

namespace hub {

void begin(const Credentials& creds) {
    s_creds = creds;
    s_tls.setCACert(AZURE_ROOT_CAS);
    s_mqtt.setServer(s_creds.host.c_str(), 8883);
    s_mqtt.setBufferSize(MQTT_BUFFER_BYTES);
    s_mqtt.setKeepAlive(30);
    s_mqtt.setSocketTimeout(10);
    s_mqtt.setCallback(onMqttMessage);
}

bool connect() {
    const time_t now = time(nullptr);
    if (now < 1700000000) {
        LOGI("mqtt: waiting for NTP before signing SAS token");
        return false;
    }
    s_tokenExpiry = now + SAS_TTL_S;
    const String token = makeSasToken(s_tokenExpiry);
    if (token.isEmpty()) {
        return false;
    }
    const String user = s_creds.host + "/" + s_creds.deviceId + "/?api-version=2021-04-12";

    LOGI("mqtt: connecting to %s as %s", s_creds.host.c_str(), s_creds.deviceId.c_str());
    if (!s_mqtt.connect(s_creds.deviceId.c_str(), user.c_str(), token.c_str())) {
        LOGI("mqtt: connect failed, state=%d", s_mqtt.state());
        s_tls.stop();
        return false;
    }
    s_mqtt.subscribe("$iothub/methods/POST/#");
    s_mqtt.subscribe("$iothub/twin/res/#");
    s_mqtt.subscribe("$iothub/twin/PATCH/properties/desired/#");
    requestTwin();
    LOGI("mqtt: connected");
    if (s_connectedHandler) {
        s_connectedHandler();
    }
    return true;
}

void disconnect() {
    s_mqtt.disconnect();
    s_tls.stop();
}

bool connected() { return s_mqtt.connected(); }

void loop() {
    s_mqtt.loop();
    processInbox();
}

bool tokenNeedsRefresh() {
    return s_mqtt.connected() && time(nullptr) > s_tokenExpiry - (time_t)SAS_REFRESH_MARGIN_S;
}

bool publish(MsgKind kind, const char* body, bool replay) {
    if (!s_mqtt.connected()) {
        return false;
    }
    // Message properties ride on the topic; IoT Hub exposes them to the
    // ingestion service as application properties.
    String topic = "devices/" + s_creds.deviceId + "/messages/events/$.ct=application%2Fjson&$.ce=utf-8";
    if (kind == MsgKind::Event) {
        topic += "&type=event";
    }
    if (replay) {
        topic += "&replay=1";
    }
    return s_mqtt.publish(topic.c_str(), body);
}

bool reportProperties(JsonDocument& patch) {
    if (!s_mqtt.connected()) {
        return false;
    }
    String body;
    serializeJson(patch, body);
    String topic = "$iothub/twin/PATCH/properties/reported/?$rid=rep" + String(++s_rid);
    return s_mqtt.publish(topic.c_str(), body.c_str());
}

void registerMethod(const char* name, MethodHandler handler) {
    if (s_methodCount < sizeof(s_methods) / sizeof(s_methods[0])) {
        s_methods[s_methodCount++] = {name, handler};
    }
}

void onDesired(DesiredHandler handler) { s_desiredHandler = handler; }
void onConnected(ConnectedHandler handler) { s_connectedHandler = handler; }

}  // namespace hub
