# Prompt for updating the presentation

Attach your old .pptx, `docs/results/RESULTS.md`, and the PNGs from
`docs/results/figures/`. Then paste the prompt below.

---

I'm attaching my existing presentation for my project **"AutoHeal IoT: a two-tier
(edge + cloud) self-healing framework for IoT devices on Azure"**, plus a
results file (RESULTS.md) and figures. Please update the deck to reflect
what has been built and measured since the last version. Keep my existing
slide order, theme, fonts and colors. Edit slides that are now out of date,
and add new slides where listed below. Don't invent numbers: use only
figures from RESULTS.md, and keep the "preliminary" caveats.

**What's new since the last version (update or add slides for each):**

1. **Real hardware, not simulation.** The system now runs on a physical
   ESP32-D0WD-V3 (Arduino framework, PlatformIO). It is provisioned over USB
   serial with Wi-Fi details and its IoT Hub device key, connects over
   2.4 GHz Wi-Fi, and uses MQTT over TLS to Azure IoT Hub. It sends 14 health
   metrics every 5 s: free heap, minimum free heap, largest allocatable block,
   RSSI, uptime, Wi-Fi/MQTT reconnects, edge-heal count, buffered and dropped
   messages, reset reason, and running config. Add a "Hardware setup" slide.

2. **Azure deployment is live.** Infrastructure is defined as code in Bicep:
   IoT Hub S1, PostgreSQL Flexible Server, Storage (firmware blobs), Log
   Analytics, and AKS for the ingestion service. Update the architecture slide
   to show this flow: ESP32 → IoT Hub (device-to-cloud telemetry, direct
   methods, device twin) → ingestion service (per-device detector) →
   PostgreSQL → Django dashboard, with the remediation Azure Function
   receiving dispatches and calling the device back through direct methods.

3. **Two-tier healing implemented end to end.**
   - Edge tier on the device: a watchdog for task hangs, a low-heap restart,
     a message-flood limiter, re-applying the desired config on drift, and
     reconnect with backoff plus store-and-forward for network loss.
   - Cloud tier: an escalation ladder (probe → restart → reboot → rollback →
     escalate) in an Azure Function, with circuit breaking.
   - Three remediation modes: manual, automated, and approval for disruptive
     steps.

4. **Management dashboard.** Fleet overview with live status, a device page
   with live heap and RSSI charts and desired/running config (device twin),
   a fault injection panel, an incident log with a live recovery timeline,
   analytics (MTTR/MTTD per condition, batch trials, CSV export), and canary
   firmware/OTA rollouts. Add one slide with 2–3 screenshots
   (dashboard_fleet.png, dashboard_device.png,
   dashboard_incident_timeline.png).

5. **First results (new "Results" slides). Use these exact numbers:**
   - 13 faults injected across 5 fault types on real hardware; 245
     telemetry messages, 0 dropped.
   - Edge-on time to recover:
     - config drift 1.06 s
     - message flood 3.54 s
     - task hang 6.36 s
     - memory leak 7.91 s
     - network loss (30 s outage) 35.85 s
   - Mean TTR for local faults is 4.72 s; 8 of 8 were repaired on the device.
   - All measured values are at or below the analytical bounds.
   - Detect → verified healthy takes ≈ 1 s, so detection time dominates
     recovery.
   - Predicted cloud-only (edge off) TTR: config drift 23–46 s, task hang
     33–46 s. Edge healing is therefore about 5–40× faster *(predicted; edge-off
     trials pending)*.
   - Use figure ttr.png (measured vs. predicted) and breakdown.png (TTD vs.
     repair time).

6. **Limitations and next steps slide.** Results are preliminary (n = 1–3
   per condition). Edge-off trials, bad-firmware OTA rollback trials, and 20
   trials per condition for statistics are next. Include the anomalies listed
   in RESULTS.md section 6 in one short bullet: one outlier, one
   approval-mode trial unresolved, and one network-loss credit-assignment
   issue under investigation.

7. **Engineering challenges solved (optional slide).**
   - IoT Hub drops all telemetry unless the fallback route is enabled. Fixed
     in Bicep.
   - The Azure IoT Hub Python SDK no longer builds (uamqp). Replaced with
     REST calls signed with SAS tokens.
   - Azure Postgres closes idle connections. Fixed with per-job connection
     recycling in the background scheduler.
   - The ESP32 supports only 2.4 GHz Wi-Fi.

**Style rules:** at most 5 bullets per slide and at most about 12 words per
bullet. Put numbers in large type on result slides. Don't use purple or dark
navy gradients, don't use emoji, and don't use stock "AI" imagery. If my
theme has no accent color, use deep green (#1F6F50) with warm neutrals.
Put the detailed notes from this prompt in the speaker notes, not on the
slides. Finally, list which slides you changed, added or removed.
