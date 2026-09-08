# Research Notes — Self-Healing IoT Infrastructure

Working notes for the research write-up. Fill this in as you go (build plan
Section 5, Phase 8) rather than leaving it until the end.

## Research question

How much does automated remediation reduce MTTR (mean and variance) compared
to a manual-response baseline, across different fault types, for a real edge
device?

## Study design

- **Device under test**: (fill in — Pi model, OS version, network setup)
- **Fault types**: network_loss, process_crash, resource_exhaustion (see
  `controller/fault_injector.py` for exact injected commands)
- **Trials per condition**: target 20–30 per (fault_type × remediation_mode)
- **Manual-mode baseline**: (fill in — did you use a real human clicking
  "Resolve manually", or the batch runner's scripted delay in
  `webapp/dashboard/scheduler.py::DEFAULT_MANUAL_RESPONSE_DELAY_SECONDS`?
  State the delay value used and why it's a reasonable stand-in.)
- **Detection window**: Azure Monitor alert rules evaluate on a 1-minute
  window (see `cloud/infra/monitor.bicep`) — this puts a floor on achievable
  MTTD regardless of remediation speed; note this as a limitation.

## Known limitations

- The "device disconnected" Azure Monitor alert cannot distinguish
  `network_loss` from `process_crash` from the alert payload alone — both
  present identically as "no telemetry ingress" to IoT Hub. The remediation
  function (`cloud/functions/remediation_dispatcher/remediation_logic.py`)
  handles this by trying `reconnect_network` then `restart_service` and
  checking health after each. Document how often each branch actually fired.
- (fill in others as you discover them during trial runs)

## Results

### Summary statistics

_(paste the table from the Analytics dashboard, or re-derive from the CSV
export at `/export/csv/`)_

| Fault type | Mode | n | MTTR mean (s) | MTTR median (s) | MTTR std dev (s) | MTTD mean (s) |
|---|---|---|---|---|---|---|
| | | | | | | |

### Charts

_(drop the MTTR comparison chart / box plots here, generated from the CSV
export via pandas/matplotlib or R)_

## Discussion

_(fill in — what the numbers show, whether automated remediation reduced
mean MTTR, variance, both, neither, and why)_
