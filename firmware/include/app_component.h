#pragma once
// The "app component": a FreeRTOS task that samples device health and
// produces telemetry. It's the ESP32 stand-in for the Pi's edge-agent
// process - restart_service restarts this task (freeing everything it owns),
// the way restarting a process frees its memory.
//
// The task_hang, memory_leak and message_flood faults all live inside this
// component, so restarting it is a real fix for them, not a fault toggle.

namespace app {
void begin();
void restart();
bool heartbeatFresh();

void setHang(bool on);
void setLeak(bool on);
void setFlood(bool on);
}  // namespace app
