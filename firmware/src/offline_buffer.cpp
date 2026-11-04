#include "offline_buffer.h"

#include <LittleFS.h>

namespace {
const char* const kPath = "/outbox.jsonl";
const char* const kTmpPath = "/outbox.tmp";
bool s_mounted = false;
uint32_t s_count = 0;
}  // namespace

namespace buffer {

void begin() {
    s_mounted = LittleFS.begin(true);
    if (!s_mounted) {
        LOGI("LittleFS mount failed - offline buffering disabled");
        return;
    }
    File f = LittleFS.open(kPath, "r");
    if (f) {
        while (f.available()) {
            if (f.read() == '\n') {
                s_count++;
            }
        }
        f.close();
    }
    LOGI("offline buffer: %lu messages pending", (unsigned long)s_count);
}

bool append(MsgKind kind, const char* body) {
    if (!s_mounted) {
        return false;
    }
    File f = LittleFS.open(kPath, "a");
    if (!f) {
        return false;
    }
    if (f.size() + strlen(body) + 2 > OFFLINE_BUFFER_MAX_BYTES) {
        f.close();
        return false;
    }
    // One line per message: a kind prefix ('T'/'E') followed by the JSON body.
    f.print(kind == MsgKind::Event ? 'E' : 'T');
    f.print(body);
    f.print('\n');
    f.close();
    s_count++;
    return true;
}

uint32_t count() { return s_count; }

uint32_t drain(PublishFn publish, uint32_t maxMsgs) {
    if (!s_mounted || s_count == 0) {
        return 0;
    }
    File in = LittleFS.open(kPath, "r");
    if (!in) {
        s_count = 0;
        return 0;
    }
    File out = LittleFS.open(kTmpPath, "w");

    uint32_t sent = 0;
    uint32_t kept = 0;
    bool stopped = false;
    while (in.available()) {
        String line = in.readStringUntil('\n');
        if (line.length() < 2) {
            continue;
        }
        if (!stopped && sent < maxMsgs) {
            MsgKind kind = line[0] == 'E' ? MsgKind::Event : MsgKind::Telemetry;
            if (publish(kind, line.c_str() + 1)) {
                sent++;
                continue;
            }
            stopped = true;
        }
        out.print(line);
        out.print('\n');
        kept++;
    }
    in.close();
    out.close();

    LittleFS.remove(kPath);
    if (kept > 0) {
        LittleFS.rename(kTmpPath, kPath);
    } else {
        LittleFS.remove(kTmpPath);
    }
    s_count = kept;
    return sent;
}

}  // namespace buffer
