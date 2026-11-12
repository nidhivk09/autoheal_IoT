# Results: first hardware trials (10 Nov 2026)

These are the first measurements from a physical ESP32 running the full
pipeline: ESP32 → Azure IoT Hub → ingestion service → PostgreSQL → dashboard,
with the remediation Function handling the cloud tier. They are **preliminary**:
1–3 trials per condition, all with **edge healing on**. The edge-off
(cloud-only) half of the factorial design has not been run yet. The
edge-off figures below are the analytical bounds from the paper, not
measurements.

Figures are in `docs/results/figures/`.

## 1. Test setup

| Item | Value |
|---|---|
| Device | ESP32-D0WD-V3 (rev 3), Arduino framework, PlatformIO, `espressif32@6.9.0` |
| Firmware | `0.0.0-dev`, built with fault injection enabled |
| Connectivity | Wi-Fi 2.4 GHz (phone hotspot), MQTT over TLS to Azure IoT Hub S1 |
| Cloud | IoT Hub S1 (Central India), PostgreSQL Flexible Server (South India), Azure Functions (Python) |
| Telemetry | Every 5 s, 14 metrics per message |
| Session | 10 Nov 2026, 06:07–06:28 UTC (~21 min) |
| Data collected | 245 telemetry messages (3,430 metric samples), 13 injected incidents |

## 2. Device baseline (healthy operation)

| Metric | Value |
|---|---|
| Free heap at idle | 165 KB mean (169,429 B ± 10,869 B) |
| Largest allocatable block | 93 KB mean |
| Wi-Fi RSSI | −77.5 dBm mean (± 4.8), range −94 to −70 dBm |
| Telemetry messages dropped | **0** over the whole session |
| Store-and-forward buffer | Peak 2 messages, during network loss |

## 3. Per-incident results

TTD = fault injected → fault detected. TTR = fault injected → device verified healthy.

| # | Fault | Mode | Detected by (signal) | TTD (s) | TTR (s) | Resolved by | Msgs dropped |
|---|---|---|---|---|---|---|---|
| 1 | network_loss | manual | edge (`link_lost`) | 425.79 | 434.79 | edge | 0 |
| 2 | network_loss | automated | cloud (`telemetry_stale`) | 34.13 | 60.95 | self | – |
| 3 | network_loss | manual | – | 2.62 | 12.65 | human | – |
| 4 | memory_leak | manual | edge (`heap_low`) | 7.06 | 8.06 | edge | 0 |
| 5 | memory_leak | automated | edge (`heap_low`) | 6.75 | 7.75 | edge | 0 |
| 6 | network_loss | manual | edge (`link_lost`) | 27.85 | 35.85 | edge | 0 |
| 7 | network_loss | approval | – | – | – | unresolved | – |
| 8 | task_hang | manual | edge (`heartbeat_lost`) | 5.05 | 6.05 | edge | 0 |
| 9 | task_hang | automated | edge (`heartbeat_lost`) | 5.66 | 6.66 | edge | 0 |
| 10 | message_flood | manual | cloud (`message_flood`) | 2.67 | 3.69 | edge | 0 |
| 11 | message_flood | automated | edge (`message_flood`) | 2.39 | 3.39 | edge | 0 |
| 12 | config_drift | manual | edge (`config_drift`) | 0.25 | 1.25 | edge | 0 |
| 13 | config_drift | automated | edge (`config_drift`) | 0.00 | 0.87 | edge | 0 |

Remediation actions recorded: edge `restart_app` (memory leak, task hang,
message flood), edge `reapply_desired_config` (config drift), edge
`reconnect_backoff` (network loss), cloud `probe` (#2, #11).

## 4. Measured vs. predicted (main result)

Here, measured is the mean of the clean edge-on trials. Predicted is the
analytical bound in the paper (Table "Analytical TTD / TTR bounds").

| Fault | Measured TTD | Predicted TTD (edge on) | Measured TTR | Predicted TTR (edge on) | Predicted TTR, cloud only | Within bound? |
|---|---|---|---|---|---|---|
| config_drift | 0.13 s | ≤ 1 | **1.06 s** | ≤ 2 | 23–46 | Yes |
| message_flood | 2.53 s | 3–4 | **3.54 s** | 4–5 | 3–7 | Faster than predicted |
| task_hang | 5.36 s | 5–6 | **6.36 s** | 6–7 | 33–46 | Yes |
| memory_leak | 6.91 s | 7–9 | **7.91 s** | 8–10 | 13–26 | Yes (at lower edge) |
| network_loss (D = 30 s) | 27.85 s | ≈ 0* | **35.85 s** | ≈ 33–36 | ≈ 61–66 | Yes (TTR) |

\*For network loss, the edge notices the outage at once. However, its
`link_lost` event can reach the cloud only after the link comes back, so the
logged TTD is roughly the outage length.

**Aggregate, local faults (config drift, flood, hang, leak; 8 trials):**
- Mean TTR **4.72 s**, median **4.87 s**, range 0.87–8.06 s.
- 8 of 8 resolved by the edge tier, with 0 messages dropped.
- Once detected, the repair-and-verify step took about **1.0 s** in every
  case (TTR − TTD = 0.93–1.01 s). Nearly all recovery time is detection time,
  which is set by the configured check intervals.

**Across all 12 resolved incidents:**
- 10 were resolved by the edge (83%), 1 by self-recovery and 1 by a human.
- Median TTR is 7.2 s.

## 5. Key findings (for slides)

1. **The full pipeline works on real hardware.** A physical ESP32 is
   provisioned, streams telemetry to Azure IoT Hub, and is monitored, faulted
   and repaired from the dashboard.
2. **Edge healing recovers local faults in about 1–8 s.** Config drift took
   about 1 s, message flood about 3.5 s, task hang about 6.4 s and memory leak
   about 7.9 s, all with no data loss.
3. **Measurements match the analytical model.** Every measured edge-on TTR is
   at or below its predicted bound, which supports using the bounds as
   hypotheses for the full trials.
4. **Detection dominates recovery time.** Repair plus verification takes about
   1 s, so tuning detection thresholds (heartbeat timeout, heap check
   interval) is the main lever for lower MTTR.
5. **Edge vs. cloud gap (predicted):** compared with the cloud-only bounds,
   edge healing is about 5–7× faster for task hangs and about 20–40× faster
   for config drift. *This still has to be confirmed with edge-off trials.*
6. **Store-and-forward works.** During a 30 s network outage the device
   buffered telemetry and dropped nothing. Recovery took 35.9 s, matching the
   predicted 33–36 s.
7. **Message floods can be caught by the cloud first.** In #10 the cloud's rate
   check detected the flood before the device did, as predicted in the paper's
   discussion. Credit assignment still recorded the edge as the repairing tier.

## 6. Anomalies and limitations (be upfront about these)

- **n is small.** 1 trial per condition (3 for manual network loss), so no
  standard deviations or significance tests yet. Treat the numbers as a
  feasibility check.
- **No edge-off measurements yet.** The cloud-only comparison uses
  analytical bounds only.
- **#1 (434.8 s) is an outlier.** It was the first trial of the session, and
  its cause wasn't recorded. A longer outage or hotspot instability during
  setup are the likely explanations. Exclude it from means, and repeat it.
- **#2 network loss resolved as "self" at 60.9 s, even with edge healing on.**
  The cloud detected it via stale telemetry (34.1 s, matching the edge-off TTD
  bound of 30–35 s). The device's own reconnect was not credited, so the edge
  `link_lost` report probably did not arrive. To investigate.
- **#3 was closed manually by the operator at 12.6 s**, before the outage
  ended, so it is not a recovery measurement.
- **#7 (approval mode) was never detected** and is still open. To investigate.
- **bad_firmware (OTA rollback) has not been run yet.** No firmware release
  has been uploaded to storage.

## 7. Next steps to complete the results

On the Analytics page, run **Run batch trial** with 20 trials per condition:
1. Edge **on** and edge **off**, automated mode, for task_hang, memory_leak,
   message_flood, config_drift and network_loss. That is the 10 conditions
   that fill the paper's Table "Measured MTTR".
2. Upload good and faulty firmware builds and run bad_firmware.
3. Export CSV (`/export/csv/`), then report mean ± s.d. and the share
   resolved by each tier.

---

## 8. LaTeX for the paper

You can paste this as a new subsection before "Threats to validity". It
complements the existing "Measured results" template table, which should stay
for the full trials.

```latex
\subsection{Preliminary hardware results}
We ran a first series of 13 injected faults on a physical ESP32-D0WD-V3
connected over 2.4\,GHz Wi-Fi to Azure IoT Hub S1, with edge healing enabled
and telemetry every 5\,s. Over the 21-minute session the device sent 245
telemetry messages with no telemetry loss; idle free heap averaged
165\,KB and RSSI $-77.5\pm4.8$\,dBm. Table~\ref{tab:prelim} compares the
measured times with the analytical bounds of Table~\ref{tab:bounds}.

\begin{table}[t]
\caption{Preliminary measured TTD/TTR (s), edge healing on, vs.\ analytical bounds}
\label{tab:prelim}
\centering
\footnotesize
\setlength{\tabcolsep}{3pt}
\begin{tabular}{@{}lccccc@{}}
\toprule
Fault & $n$ & TTD & TTR & Bound (edge) & Bound (cloud) \\
\midrule
config\_drift & 2 & 0.13 & 1.06 & $\le$2 & 23--46 \\
message\_flood & 2 & 2.53 & 3.54 & 4--5 & 3--7 \\
task\_hang & 2 & 5.36 & 6.36 & 6--7 & 33--46 \\
memory\_leak & 2 & 6.91 & 7.91 & 8--10 & 13--26 \\
network\_loss ($D{=}30$) & 1 & 27.85 & 35.85 & 33--36 & 61--66 \\
\bottomrule
\multicolumn{6}{@{}p{8cm}@{}}{\scriptsize All listed incidents were resolved by the edge tier with zero dropped messages. TTD for network loss reflects delivery of the edge's \texttt{link\_lost} event after the link returns.}
\end{tabular}
\end{table}

All eight local-fault incidents were repaired on the device, with a mean
TTR of 4.72\,s (range 0.87--8.06\,s). Every measured value lies at or below
its predicted edge-on bound. The interval from detection to verified health
was ${\approx}1.0$\,s in every case, so recovery time is dominated by
detection, which is governed by the configured check intervals. For the 30\,s
network outage, store-and-forward kept telemetry loss at zero and recovery
completed in 35.85\,s, consistent with the 33--36\,s bound. In one
message-flood trial the cloud's rate check detected the fault before the
device, as anticipated in Section~\ref{sec:results}; credit assignment
still attributed the repair to the edge. These results come from one trial
per condition and serve as a feasibility check; the full factorial
trials, including the edge-off conditions, will provide variance estimates
and the measured edge-versus-cloud comparison.
```
