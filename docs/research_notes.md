# Research Notes — Self-Healing IoT Infrastructure

Working notes for the research write-up. Fill this in as you go (build plan
Section 5, Phase 8) rather than leaving it until the end.

## Research questions

1. How much does automated remediation reduce MTTR (mean and variance)
   compared to a manual-response baseline, across fault types, for a real
   edge-device fleet?
2. How much of that is achievable **on the device itself** (edge healing)
   versus needing the cloud, and what does each tier cost in detection time,
   recovery time and lost telemetry?
3. Does an escalation ladder with verification, a circuit breaker and an
   approval gate keep automated remediation safe without giving back the
   MTTR gains?

## Study design

- **Devices under test**: (fill in: number of ESP32 boards, board model,
  firmware version, WiFi setup / AP model, distance to AP)
- **Factors** (full factorial):
  - fault type: `network_loss`, `task_hang`, `memory_leak`, `message_flood`,
    `config_drift`, `bad_firmware` (firmware/src/faults.cpp,
    controller/fault_injector.py)
  - cloud remediation mode: `manual`, `automated`, `approval`
  - edge healing: `on`, `off` (device twin `desired.config.edge_healing`)
- **Trials per condition**: target 20–30.
- **Measures** (CSV export): MTTD, MTTR, which tier detected and which tier
  resolved (`detected_by`, `resolved_by`), cloud actions taken, telemetry
  messages dropped during outages, escalations.
- **Manual / approval baseline**: (fill in: real human, or the batch
  runner's scripted delay in
  `webapp/dashboard/scheduler.py::DEFAULT_MANUAL_RESPONSE_DELAY_SECONDS`
  (45s)? State the value used and why it's a reasonable stand-in.)
- **Baseline firmware behaviour** (edge healing off): fixed 30s WiFi / 5s
  MQTT retries, no offline buffering, no self-test after OTA (new image
  trusted immediately), no on-device heap/hang/flood/drift checks.
- **Detection thresholds**: cloud staleness 30s, heap below the desired
  threshold for 2 readings, >30 messages in 10s, config mismatch for 2
  readings (`cloud/ingestion_service/detector.py`); edge checks run every
  second (`firmware/src/edge_healer.cpp`).

## Fault model (what each fault really does on the device)

| Fault | Mechanism | Edge fix | Cloud ladder |
|---|---|---|---|
| network_loss | WiFi dropped, every reconnect attempt fails for `duration_s` | backoff reconnect + buffer | can only detect + escalate |
| task_hang | app task stops making progress | restart app task | probe → restart_service → … |
| memory_leak | app task leaks 4KB/tick down to a 45KB floor | restart app task (frees it) | restart_service → reboot |
| message_flood | app task sends ~100 msg/s | restart app task | restart_service → reboot |
| config_drift | running config: interval 20s, heap guard off | reapply desired | twin reconcile → apply_desired_config → reboot |
| bad_firmware | OTA to a build that never sends telemetry | self-test fails → rollback | restart_service → rollback_firmware → reboot |

## Known limitations

- Faults are triggered by direct methods to the firmware rather than SSH
  (an ESP32 has no shell). The failure itself is real and observed from
  outside, but the injection channel is the same IoT Hub connection the
  cloud uses, so network_loss starts 500ms after the method is acknowledged.
- `network_loss` simulates an AP outage in firmware (attempts fail while it's
  active) rather than cutting the AP, so radio-level behaviour (association
  time after a real outage) is only partly represented. Validate a few trials
  by powering off the AP.
- `task_hang` is cooperative: the hung task still honours stop requests, so
  restarting it is always safe. A real deadlock might need a reboot.
- Device-reported timestamps (edge detections, recoveries) come from the
  ESP32's NTP-synced clock; server-side times come from Django. Skew is
  clamped at `injected_at` (`incidents/services.py`); measure typical skew
  and report it.
- One ESP32 alone may not exceed IoT Hub S1's per-unit throttle limit with
  `message_flood`, so the hub-level throttling alert (and unit scaling)
  might only fire with several devices flooding at once.
- The cloud can't fix connectivity it depends on: for `network_loss` the
  ladder can only wait, then escalate. Every network recovery comes from the
  device (edge or baseline retry). This is a finding, not a bug.
- (fill in others as you discover them during trial runs)

## Results

### Summary statistics

_(paste the table from the Analytics dashboard, or re-derive from the CSV
export at `/export/csv/`)_

| Fault type | Mode | Edge | n | MTTR mean (s) | MTTR median (s) | MTTR std dev (s) | MTTD mean (s) | Resolved by | Dropped msgs |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |

### Charts

_(drop the MTTR comparison chart / box plots here, generated from the CSV
export via pandas/matplotlib or R)_

## Discussion

_(fill in: what the numbers show; edge vs cloud vs human; whether
automated remediation reduced mean MTTR, variance, both, neither, and why;
how often the circuit breaker / approval gate fired)_
