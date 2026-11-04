#pragma once
// Store-and-forward buffer on LittleFS for messages produced while offline.
// Telemetry is only buffered when edge healing is on (it's one of the edge
// capabilities being measured); recovery events are always buffered because
// the research dataset depends on them. When full, new messages are dropped
// and counted in Stats::dropped.

#include "app_state.h"

namespace buffer {
using PublishFn = bool (*)(MsgKind kind, const char* body);

void begin();
bool append(MsgKind kind, const char* body);
uint32_t count();
// Replays up to maxMsgs oldest messages; stops at the first publish failure.
uint32_t drain(PublishFn publish, uint32_t maxMsgs);
}  // namespace buffer
