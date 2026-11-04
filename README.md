# Self-Healing IoT Infrastructure

Research-grade system demonstrating automated failure detection and
remediation for an IoT fleet: a fleet of ESP32 edge devices, Azure backend
(IoT Hub, Azure Monitor, Azure Functions, AKS, PostgreSQL, Blob Storage), and
a Django dashboard used both as the demo UI and as the tool that generates the
MTTD/MTTR research dataset.

Healing happens in two tiers, and the experiment compares them:

1. **Edge (on-device)**: the firmware watches itself and fixes what it can
   locally: a hung app task, low memory, a runaway message loop, config
   drift, WiFi outages (backoff + store-and-forward), and bad firmware
   (post-OTA self-test with automatic rollback). Can be switched off per
   device for baseline trials.
2. **Cloud**: the ingestion service detects failures per device; the
   remediation Function walks an escalation ladder (cheapest fix first,
   verify after each step, circuit breaker, approval gate for disruptive
   steps) and hands off to a human when it runs out of options.

## Repository layout

```
firmware/                 ESP32 firmware (PlatformIO): edge healer, OTA + rollback, fault injection
cloud/infra/              Bicep IaC: IoT Hub, Postgres, AKS, Log Analytics, firmware storage
cloud/ingestion_service/  AKS service: IoT Hub -> Postgres, per-device detection (detector.py)
cloud/functions/          Remediation Function: dispatcher (HTTP -> queue) + worker (escalation ladders)
webapp/                   Django app: fleet, firmware rollouts, incidents, analytics
controller/               IoT Hub REST client, fault injection, firmware store
.github/workflows/        CI/CD (infra, functions, webapp, firmware build)
docs/research_notes.md    Research write-up, fill in as trials run
```

## How a failure flows through the system

```
ESP32 ── telemetry / recovery events ──> IoT Hub ──> ingestion service (AKS)
  │  (edge healer fixes it locally,                     │  stores metrics, runs FleetDetector
  │   reports a "recovered" event)                      │  (stale / heap_low / flood / drift)
  │                                                     ├─ recovery events ──> Django internal API
  │                                                     └─ detections ──> remediation_dispatcher (HTTP)
  │                                                                              │ queue
  └──── direct methods / twin <──── remediation_worker: probe → restart → rollback → reboot → escalate
                                         │                                        │
                                         └── Django internal API (incident timestamps, actions)
                                                                                  └── chat webhook (human)
```

## Setup (local dev)

1. Copy `.env.example` to `.env` in the repo root and fill in real values.
   **Never commit `.env`**; it's already in `.gitignore`.
2. Start a local Postgres (or point `POSTGRES_URL` at one), then:
   ```
   cd webapp
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py createsuperuser
   python manage.py runserver
   ```
3. Flash and provision one or more ESP32 boards (see `firmware/README.md`).
   Devices appear on the Fleet page once their first telemetry arrives.

Tests:
```
cd webapp && python manage.py test                        # needs Postgres
cd cloud/functions && python -m pytest tests              # escalation ladders
cd cloud/ingestion_service && python -m pytest test_detector.py
cd firmware && pio run -e esp32dev -e esp32dev_bad        # firmware builds
```

## Deploying the rest of the stack

1. **Azure infra**: `az login`, create the resource group, then deploy
   `cloud/infra/main.bicep` (see `.github/workflows/deploy-infra.yml` for the
   exact command and required secrets).
2. **Devices**: one IoT Hub identity per board
   (`az iot hub device-identity create --hub-name <hub> --device-id esp32-01`),
   then flash + provision (`firmware/README.md`).
3. **Firmware builds**: the `build-firmware.yml` workflow builds the good and
   bad variants and uploads them to the `firmware` container. Roll them out
   from the dashboard's Firmware page (canary first).
4. **Remediation Function**: `func azure functionapp publish`, or the
   `deploy-functions.yml` workflow. App settings: `IOTHUB_CONNECTION_STRING`,
   `DJANGO_INTERNAL_API_BASE`, `INTERNAL_API_TOKEN`, `ESCALATION_WEBHOOK_URL`,
   `DASHBOARD_BASE_URL`, plus the `AZURE_*` values for infra remediation. The
   queue (`remediation-jobs`) lives in the Function App's own storage account.
5. **Ingestion service**: build `cloud/ingestion_service/Dockerfile`, push
   to ACR, create the secret listed in `k8s/deployment.yaml` (it now needs
   the Django internal API and the dispatcher URL too), then `kubectl apply`.
6. **Monitor alert rules**: redeploy `main.bicep` with
   `remediationFunctionWebhookUrl` set to the dispatcher URL once the Function
   exists (hub throttling alert; per-device alerts are no longer needed).
7. **Django webapp**: `webapp/Dockerfile`, deployed via `deploy-webapp.yml`.
   Needs `IOTHUB_CONNECTION_STRING`, `FIRMWARE_STORAGE_CONNECTION_STRING`
   and `REMEDIATION_DISPATCH_URL` in addition to the database/secret key.
8. **Run trials**: Analytics → "Run batch trial" (choose fault, remediation
   mode, edge healing on/off) to generate the MTTR/MTTD dataset, export via
   `/export/csv/`, write up findings in `docs/research_notes.md`.

## Secrets

Everything sensitive is an environment variable (see `.env.example` for the
full list and `.gitignore` for what's excluded from version control). GitHub
Actions secrets and/or Azure Key Vault hold the deployed-environment values.
Device credentials live only in each board's NVS (provisioned over serial),
so firmware images contain no secrets and devices only ever receive
short-lived read-only SAS URLs for OTA downloads.
