# Setup from scratch

This guide starts from a fresh laptop and an Azure account and ends with a
working system. It has two stages:

- **Part A (steps 1–9): local dev.** Azure hosts the IoT Hub, Postgres and
  blob storage. Django, the ingestion service and the remediation Function
  all run on your laptop. Do this first. It's the quickest way to get
  devices showing up on the dashboard.
- **Part B (step 10): full cloud deploy.** Optional. Moves the three
  services into Azure (Function App, AKS, Container Apps).

Commands assume macOS/Linux with zsh/bash. On Windows, use WSL.

> **Cost warning:** `main.bicep` creates an IoT Hub S1, a Postgres B1ms
> server and an AKS cluster with **2 × B2s nodes**. Together that's roughly
> USD 100+/month if you leave it all running. When you're not running trials,
> stop AKS (`az aks stop -g rg-selfhealing-iot -n selfheal-aks`) and Postgres
> (`az postgres flexible-server stop -g rg-selfhealing-iot -n selfheal-pg`).
> To tear everything down, run
> `az group delete -n rg-selfhealing-iot`.

---

## 1. Install the tools

| Tool | Install (macOS) | Used for |
|---|---|---|
| Git | `xcode-select --install` | cloning the repo |
| Python 3.11 | `brew install python@3.11` | Django, ingestion service, Function |
| Azure CLI | `brew install azure-cli` | creating everything in Azure |
| Azure IoT CLI extension | `az extension add --name azure-iot` | device identities, connection strings |
| Azure Functions Core Tools v4 | `brew tap azure/functions && brew install azure-functions-core-tools@4` | running the Function locally |
| PostgreSQL client (optional) | `brew install libpq` | `psql` for checking the DB |
| PlatformIO + pyserial | `pip install platformio pyserial` | building and flashing the ESP32 |
| USB-serial driver | CP210x or CH340, depending on the board | talking to the ESP32 |

Then clone the repo:

```bash
git clone <repo-url> autoheal_IoT
cd autoheal_IoT
```

## 2. Log in to Azure and create the resource group

```bash
az login
az account show --query "{name:name, subscription:id, tenant:tenantId}" -o table
# If more than one subscription is listed:
az account set --subscription "<subscription id>"

# The resource providers this project uses (only needed once per subscription)
for p in Microsoft.Devices Microsoft.DBforPostgreSQL Microsoft.ContainerService \
         Microsoft.Storage Microsoft.OperationalInsights Microsoft.Insights Microsoft.Web; do
  az provider register --namespace $p
done

az group create -n rg-selfhealing-iot -l centralindia   # or any region near you
```

> Student and free subscriptions sometimes block Postgres or AKS in some
> regions. If the deploy in the next step fails with a "location is
> restricted" or quota error, delete the group and recreate it in another
> region (for example `eastus` or `southeastasia`).

## 3. Deploy the Azure infrastructure (IoT Hub, Postgres, storage, AKS, monitoring)

Choose a strong Postgres admin password and **write it down**. You need it
again for `POSTGRES_URL`.

```bash
az deployment group create \
  -g rg-selfhealing-iot \
  -n main \
  --template-file cloud/infra/main.bicep \
  --parameters postgresAdminPassword='<YourStrongPassword123!>'
```

The deploy takes 10–20 minutes, mostly waiting for AKS. When it finishes,
print the outputs:

```bash
az deployment group show -g rg-selfhealing-iot -n main \
  --query properties.outputs -o json
```

This creates:

| Resource | Name |
|---|---|
| IoT Hub | `selfheal-iothub` |
| Postgres server / database | `selfheal-pg` / `autoheal` (admin user `autoheal_admin`) |
| Storage account (firmware) | `selfhealfw<random>`, shown as `firmwareStorageAccountName` in the outputs |
| AKS cluster | `selfheal-aks` |
| Log Analytics | `selfheal-logs` |

## 4. Let your laptop reach Postgres

The Bicep template only opens the firewall to Azure services, so add a rule
for your own IP address:

```bash
MYIP=$(curl -s https://api.ipify.org)
az postgres flexible-server firewall-rule create \
  -g rg-selfhealing-iot -n selfheal-pg \
  --rule-name my-laptop --start-ip-address $MYIP --end-ip-address $MYIP
```

Repeat this whenever your IP changes, for example on different Wi-Fi.

(To skip Azure Postgres for local dev, run Postgres on your laptop
instead with `brew install postgresql@16 && brew services start postgresql@16 && createdb autoheal`.)

## 5. Create the `.env` file

```bash
cp .env.example .env
```

Fill in each value as follows:

```bash
# --- IoT Hub ---
IOTHUB_CONNECTION_STRING=      # az iot hub connection-string show -n selfheal-iothub --policy-name iothubowner -o tsv

# --- Database ---
POSTGRES_URL=postgresql://autoheal_admin:<password from step 3>@selfheal-pg.postgres.database.azure.com:5432/autoheal?sslmode=require
#   If the password has special characters (@ : / # ? &), URL-encode them, e.g.
#   python3 -c "import urllib.parse; print(urllib.parse.quote('<password>', safe=''))"

# --- Azure ---
AZURE_SUBSCRIPTION_ID=         # az account show --query id -o tsv
AZURE_TENANT_ID=               # az account show --query tenantId -o tsv
AZURE_CLIENT_ID=               # leave blank for local dev (your `az login` is used)
AZURE_CLIENT_SECRET=           # leave blank for local dev
AZURE_RESOURCE_GROUP=rg-selfhealing-iot
AZURE_IOTHUB_NAME=selfheal-iothub
AZURE_AKS_CLUSTER_NAME=selfheal-aks

# --- Firmware storage ---
FIRMWARE_STORAGE_CONNECTION_STRING=    # az storage account show-connection-string -g rg-selfhealing-iot -n <selfhealfw...> -o tsv
FIRMWARE_CONTAINER=firmware

# --- Django ---
DJANGO_SECRET_KEY=             # python3 -c "import secrets; print(secrets.token_urlsafe(50))"
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

# --- Internal API (pick any random string, same everywhere) ---
INTERNAL_API_TOKEN=            # python3 -c "import secrets; print(secrets.token_urlsafe(32))"
DJANGO_INTERNAL_API_BASE=http://localhost:8000

# --- Remediation Function (running locally in step 7) ---
REMEDIATION_DISPATCH_URL=http://localhost:7071/api/remediation_dispatcher
ESCALATION_WEBHOOK_URL=        # optional: Slack/Discord/Teams incoming webhook URL
DASHBOARD_BASE_URL=http://localhost:8000

# --- Ingestion service ---
IOTHUB_EVENTHUB_COMPATIBLE_CONNECTION_STRING=   # az iot hub connection-string show -n selfheal-iothub --default-eventhub -o tsv
IOTHUB_EVENTHUB_COMPATIBLE_PATH=                # az iot hub show -n selfheal-iothub --query properties.eventHubEndpoints.events.path -o tsv
```

Never commit `.env`. It's already listed in `.gitignore`.

## 6. Run the Django dashboard

```bash
cd webapp
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate            # creates the tables in Postgres
python manage.py createsuperuser    # your dashboard login
python manage.py runserver
```

Open <http://localhost:8000> and log in. Leave this terminal running.

If `migrate` hangs or times out, check two things: that the firewall rule
from step 4 is in place, and that `POSTGRES_URL` ends in `?sslmode=require`.

## 7. Run the remediation Function locally

The Functions host doesn't read `.env`, so its settings go in
`cloud/functions/local.settings.json`. That file is gitignored, so create it
yourself:

```json
{
  "IsEncrypted": false,
  "Values": {
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "AzureWebJobsStorage": "<same value as FIRMWARE_STORAGE_CONNECTION_STRING>",
    "IOTHUB_CONNECTION_STRING": "<from .env>",
    "DJANGO_INTERNAL_API_BASE": "http://localhost:8000",
    "INTERNAL_API_TOKEN": "<from .env>",
    "DASHBOARD_BASE_URL": "http://localhost:8000",
    "ESCALATION_WEBHOOK_URL": "",
    "AZURE_SUBSCRIPTION_ID": "<from .env>",
    "AZURE_RESOURCE_GROUP": "rg-selfhealing-iot",
    "AZURE_IOTHUB_NAME": "selfheal-iothub",
    "AZURE_AKS_CLUSTER_NAME": "selfheal-aks"
  }
}
```

Then, in a new terminal:

```bash
cd cloud/functions
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
func start          # serves http://localhost:7071/api/remediation_dispatcher
```

The function's job queue (`remediation-jobs`) is created automatically in
that storage account.

## 8. Run the ingestion service locally

This service reads device telemetry from IoT Hub, writes it to Postgres,
detects failures and calls the Function. It reads the root `.env`. In a new
terminal:

```bash
cd cloud/ingestion_service
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## 9. Flash and provision the ESP32 boards

Create one IoT Hub identity per board:

```bash
az iot hub device-identity create --hub-name selfheal-iothub --device-id esp32-01
```

Plug in the board and find its serial port with `ls /dev/cu.*`. Then:

```bash
cd firmware
pio run -e esp32dev -t upload
python scripts/provision.py --port /dev/cu.usbserial-XXXX \
    --ssid "<wifi name>" --hub-name selfheal-iothub --device-id esp32-01
pio device monitor        # should show WiFi + IoT Hub connected
```

The WiFi must be **2.4 GHz** because the ESP32 can't connect to 5 GHz-only
networks. Within about a minute the device appears on the dashboard's
**Fleet** page. Repeat for `esp32-02`, `esp32-03` and so on.

**Firmware images for OTA trials (optional).** Build both variants and upload them:

```bash
cd firmware
V=1.0.1
PLATFORMIO_BUILD_FLAGS="-DFW_VERSION=\\\"$V\\\"" pio run -e esp32dev
cp .pio/build/esp32dev/firmware.bin /tmp/firmware-$V.bin
PLATFORMIO_BUILD_FLAGS="-DFW_VERSION=\\\"$V-bad\\\"" pio run -e esp32dev_bad
cp .pio/build/esp32dev_bad/firmware.bin /tmp/firmware-$V-bad.bin

CS="<FIRMWARE_STORAGE_CONNECTION_STRING>"
az storage blob upload --connection-string "$CS" -c firmware \
  -f /tmp/firmware-$V.bin -n firmware-$V.bin --metadata version=$V variant=good
az storage blob upload --connection-string "$CS" -c firmware \
  -f /tmp/firmware-$V-bad.bin -n firmware-$V-bad.bin --metadata version=$V-bad variant=bad
```

The images then show up on the dashboard's **Firmware** page.

**Quick end-to-end check:** run `python controller/fault_injector.py health esp32-01` from the
repo root, then trigger a fault from the device page and watch an incident
open and close under **Incidents**.

**Part A is now complete.** You can run trials from Analytics → "Run batch trial".

---

## 10. Full cloud deploy (optional)

Do this when the services need to keep running without the laptop. Run
each step from the repo root.

### 10a. Container registry (for the ingestion service and webapp images)

```bash
az acr create -g rg-selfhealing-iot -n selfhealacr<unique> --sku Basic
az aks update -g rg-selfhealing-iot -n selfheal-aks --attach-acr selfhealacr<unique>
```

### 10b. Function App

```bash
az storage account create -g rg-selfhealing-iot -n selfhealfunc<unique> --sku Standard_LRS
az functionapp create -g rg-selfhealing-iot -n selfheal-func<unique> \
  --storage-account selfhealfunc<unique> --consumption-plan-location <region> \
  --runtime python --runtime-version 3.11 --functions-version 4 --os-type Linux

# Give it a managed identity with rights over the resource group
# (used for infra remediation: scaling IoT Hub and restarting AKS pods)
PRINCIPAL=$(az functionapp identity assign -g rg-selfhealing-iot -n selfheal-func<unique> --query principalId -o tsv)
az role assignment create --assignee $PRINCIPAL --role Contributor \
  --scope $(az group show -n rg-selfhealing-iot --query id -o tsv)

# App settings: the same values as in local.settings.json, but with the
# public webapp URL (from 10d) in place of localhost
az functionapp config appsettings set -g rg-selfhealing-iot -n selfheal-func<unique> --settings \
  IOTHUB_CONNECTION_STRING="..." INTERNAL_API_TOKEN="..." \
  DJANGO_INTERNAL_API_BASE="https://<webapp url>" DASHBOARD_BASE_URL="https://<webapp url>" \
  ESCALATION_WEBHOOK_URL="..." AZURE_SUBSCRIPTION_ID="..." \
  AZURE_RESOURCE_GROUP=rg-selfhealing-iot AZURE_IOTHUB_NAME=selfheal-iothub \
  AZURE_AKS_CLUSTER_NAME=selfheal-aks

cd cloud/functions && func azure functionapp publish selfheal-func<unique> && cd -

# The real dispatch URL, including ?code=...; this becomes REMEDIATION_DISPATCH_URL everywhere
az functionapp function show -g rg-selfhealing-iot -n selfheal-func<unique> \
  --function-name remediation_dispatcher --query invokeUrlTemplate -o tsv
az functionapp function keys list -g rg-selfhealing-iot -n selfheal-func<unique> \
  --function-name remediation_dispatcher
```

### 10c. Ingestion service on AKS

```bash
az acr build -r selfhealacr<unique> -t ingestion-service:latest cloud/ingestion_service
az aks get-credentials -g rg-selfhealing-iot -n selfheal-aks

kubectl create secret generic ingestion-service-secrets \
  --from-literal=IOTHUB_EVENTHUB_COMPATIBLE_CONNECTION_STRING='...' \
  --from-literal=IOTHUB_EVENTHUB_COMPATIBLE_PATH='...' \
  --from-literal=POSTGRES_URL='...' \
  --from-literal=DJANGO_INTERNAL_API_BASE='https://<webapp url>' \
  --from-literal=INTERNAL_API_TOKEN='...' \
  --from-literal=REMEDIATION_DISPATCH_URL='https://...?code=...'

# Set image: to selfhealacr<unique>.azurecr.io/ingestion-service:latest in the manifest, then:
kubectl apply -f cloud/ingestion_service/k8s/deployment.yaml
kubectl logs -f deploy/ingestion-service
```

Stop the local `python main.py` from step 8 first. Two consumers running at
once would both process the same events.

### 10d. Django webapp on Container Apps

```bash
az extension add --name containerapp
az acr build -r selfhealacr<unique> -t webapp:latest -f webapp/Dockerfile .
az containerapp up -g rg-selfhealing-iot -n selfheal-web \
  --image selfhealacr<unique>.azurecr.io/webapp:latest --ingress external --target-port 8000 \
  --env-vars POSTGRES_URL="..." DJANGO_SECRET_KEY="..." DJANGO_DEBUG=False \
    DJANGO_ALLOWED_HOSTS="<app fqdn>" IOTHUB_CONNECTION_STRING="..." \
    FIRMWARE_STORAGE_CONNECTION_STRING="..." INTERNAL_API_TOKEN="..." \
    REMEDIATION_DISPATCH_URL="https://...?code=..."
```

### 10e. Connect the Azure Monitor alert to the Function

```bash
az deployment group create -g rg-selfhealing-iot -n main \
  --template-file cloud/infra/main.bicep \
  --parameters postgresAdminPassword='<same password>' \
               remediationFunctionWebhookUrl='https://...remediation_dispatcher?code=...'
```

### 10f. CI/CD (optional)

The workflows in `.github/workflows/` need a service principal with an OIDC
federated credential, plus the repo secrets listed at the top of each
workflow file: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
`AZURE_RESOURCE_GROUP`, `POSTGRES_ADMIN_PASSWORD`, `AZURE_FUNCTIONAPP_NAME`,
`ACR_LOGIN_SERVER`, `CONTAINERAPP_NAME` and `FIRMWARE_STORAGE_ACCOUNT`.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `RuntimeError: POSTGRES_URL is not set` | `.env` isn't in the repo root, or the variable is empty |
| `migrate` hangs or "connection timed out" | Postgres firewall (step 4), or the server is stopped (`az postgres flexible-server start ...`) |
| `password authentication failed` | Wrong password, or special characters in the password that need URL-encoding |
| Device never appears on Fleet | Check `pio device monitor`: 5 GHz WiFi, wrong hub name or device ID, or the ingestion service isn't running |
| Incidents open but nothing is remediated | Function not running, or `REMEDIATION_DISPATCH_URL` / `INTERNAL_API_TOKEN` differ between services |
| `KeyError: 'AZURE_AKS_CLUSTER_NAME'` in the Function | Add it to `local.settings.json` / app settings |
| Deploy fails with "location is restricted" or a quota error | Use a different region (step 2) |
