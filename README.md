# Self-Healing IoT Infrastructure

Research-grade system demonstrating automated failure detection and
remediation for an IoT fleet: a physical Raspberry Pi edge device, Azure
backend (IoT Hub, Azure Monitor, Azure Functions, AKS, PostgreSQL), and a
Django dashboard used both as the demo UI and as the tool that generates the
MTTD/MTTR research dataset.

## Repository layout

```
edge_agent/        Python daemon running on the Pi (systemd service)
cloud/infra/        Bicep IaC for IoT Hub, Postgres, AKS, Log Analytics
cloud/functions/    Remediation Azure Function
cloud/ingestion_service/  AKS-hosted telemetry ingestion (IoT Hub -> Postgres)
webapp/             Django app: devices, incidents, dashboard
controller/         SSH-based fault injection controller
.github/workflows/  CI/CD (infra, functions, webapp, edge agent)
docs/research_notes.md   Research write-up, fill in as trials run
```

## Setup (local dev)

1. Copy `.env.example` to `.env` in the repo root and fill in real values.
   **Never commit `.env`** — it's already in `.gitignore`.
2. Start a local Postgres (or point `POSTGRES_URL` at one), then:
   ```
   cd webapp
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python manage.py makemigrations devices incidents dashboard
   python manage.py migrate
   python manage.py createsuperuser
   python manage.py runserver
   ```
3. Register a `Device` row (matching an IoT Hub device identity) via
   `/admin/` so the dashboard has something to show.

## Deploying the rest of the stack

Follow the phase order in the original build plan:

1. **Azure infra** — `az login`, create the resource group, then deploy
   `cloud/infra/main.bicep` (see `.github/workflows/deploy-infra.yml` for the
   exact command / required secrets).
2. **Edge agent** — install on the Pi, register its IoT Hub device identity,
   install `edge_agent/systemd/edge-agent.service`.
3. **Ingestion service** — build `cloud/ingestion_service/Dockerfile`, push
   to ACR, `kubectl apply -f cloud/ingestion_service/k8s/deployment.yaml`.
4. **Monitor alert rules** — deployed as part of `main.bicep`
   (`cloud/infra/monitor.bicep`); redeploy once the remediation Function's
   webhook URL is known, passing `remediationFunctionWebhookUrl`.
5. **Remediation Function** — `func azure functionapp publish` or the
   `deploy-functions.yml` workflow.
6. **Django webapp** — `webapp/Dockerfile`, deployed via
   `deploy-webapp.yml` to Azure Container Apps (or AKS).
7. **CI/CD** — the four workflows under `.github/workflows/` cover infra,
   functions, webapp, and edge agent deploys respectively. Add the secrets
   each one's header comment lists before enabling it.
8. **Run trials** — use the dashboard's "Run batch trial" button
   (`/` → Analytics) to generate the MTTR/MTTD dataset, export via
   `/export/csv/`, write up findings in `docs/research_notes.md`.

## Secrets

Everything sensitive is an environment variable — see `.env.example` for the
full list and `.gitignore` for what's excluded from version control. GitHub
Actions secrets and/or Azure Key Vault hold the deployed-environment values;
nothing sensitive is hardcoded anywhere in this repo.

## Git workflow

This repo's commits and pushes are made by the project owner, not by any
automated tooling — changes are prepared locally/in CI and the resulting
`git` commands are left for the owner to review and run.
