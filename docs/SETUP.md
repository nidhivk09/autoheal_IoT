# Setup from scratch

This guide starts from a fresh laptop and an Azure account and ends with a
working system. It has two stages:

- **Part A (steps 1–9): local dev.** Azure hosts the IoT Hub, Postgres and
  blob storage. Django, the ingestion service and the remediation Function
  all run on your laptop. Do this first. It's the quickest way to get
  devices showing up on the dashboard.
- **Part B (step 10): full cloud deploy.** Optional. Moves the three
  services into Azure (Function App, AKS, Container Apps).

Commands assume macOS/Linux with zsh/bash. **On Windows, see
[Appendix: Windows](#appendix-windows-powershell)** for the PowerShell versions.

> **Use Python 3.11 for every virtual environment** (`python3.11 -m venv .venv`).
> If your default `python3` is 3.13 or 3.14, `python3 -m venv` may fail or
> some packages won't install.

> **zsh tip:** pasting command blocks that contain `# comments` into zsh
> fails with "command not found: #" unless you first run
> `setopt interactivecomments`.

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
| Azure IoT CLI extension (optional) | `az extension add --name azure-iot` | creating devices from the CLI. Often fails to install (404 or pip errors); everything below works without it |
| Azure Functions Core Tools v4 | `npm install -g azure-functions-core-tools@4 --unsafe-perm true` (needs Node.js), or `brew tap azure/functions && brew trust azure/functions && brew install azure-functions-core-tools@4` | running the Function locally |
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

az group create -n rg-selfhealing-iot -l centralindia
```

(Use any region near you instead of `centralindia`.)

> Student and free subscriptions sometimes block Postgres or AKS in some
> regions. If the deploy in the next step fails with a "location is
> restricted" or quota error, delete the group and recreate it in another
> region (for example `eastus` or `southeastasia`).

## 3. Deploy the Azure infrastructure (IoT Hub, Postgres, storage, AKS, monitoring)

Choose your own Postgres admin password and **write it down**; you need it
again in step 5. Don't copy the placeholder below literally. Azure requires
three of: uppercase, lowercase, digits, symbols.

```bash
az deployment group create \
  -g rg-selfhealing-iot \
  -n main \
  --template-file cloud/infra/main.bicep \
  --parameters postgresAdminPassword='<YourStrongPassword123!>'
```

If it fails with **`SkuNotAvailable`** for Postgres (common on student
subscriptions), put just Postgres in another region and run it again. The
parts that already succeeded are left as they are:

```bash
az deployment group create -g rg-selfhealing-iot -n main \
  --template-file cloud/infra/main.bicep \
  --parameters postgresAdminPassword='<same password>' postgresLocation=southindia
```

(If `southindia` also fails, try `eastus` or `southeastasia`.)

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
  -g rg-selfhealing-iot --server-name selfheal-pg \
  --name my-laptop --start-ip-address $MYIP --end-ip-address $MYIP
```

Repeat this whenever your IP changes, for example on different Wi-Fi.

(To skip Azure Postgres for local dev, run Postgres on your laptop
instead with `brew install postgresql@16 && brew services start postgresql@16 && createdb autoheal`.)

## 5. Create the `.env` file

Run this from the repo root. It fetches every value from Azure (no
`azure-iot` extension needed), generates the random secrets, and writes
`.env` without printing any secrets. It asks for the Postgres password from
step 3.

```bash
read -s "PGPASS?Postgres password: "; echo
RG=rg-selfhealing-iot; HUB=selfheal-iothub
FWACCT=$(az storage account list -g $RG --query "[?starts_with(name,'selfhealfw')].name | [0]" -o tsv)
PGPASS_ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$PGPASS")
HUBKEY=$(az iot hub policy show --hub-name $HUB -n iothubowner --query primaryKey -o tsv 2>/dev/null)
HUBHOST=$(az iot hub show -n $HUB --query properties.hostName -o tsv 2>/dev/null)
EHEP=$(az iot hub show -n $HUB --query properties.eventHubEndpoints.events.endpoint -o tsv 2>/dev/null)
EHPATH=$(az iot hub show -n $HUB --query properties.eventHubEndpoints.events.path -o tsv 2>/dev/null)
cat > .env <<EOF
IOTHUB_CONNECTION_STRING=HostName=${HUBHOST};SharedAccessKeyName=iothubowner;SharedAccessKey=${HUBKEY}
POSTGRES_URL=postgresql://autoheal_admin:${PGPASS_ENC}@selfheal-pg.postgres.database.azure.com:5432/autoheal?sslmode=require
AZURE_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
AZURE_RESOURCE_GROUP=$RG
AZURE_IOTHUB_NAME=$HUB
AZURE_AKS_CLUSTER_NAME=selfheal-aks
FIRMWARE_STORAGE_CONNECTION_STRING=$(az storage account show-connection-string -g $RG -n $FWACCT -o tsv 2>/dev/null)
FIRMWARE_CONTAINER=firmware
DJANGO_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
INTERNAL_API_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
DJANGO_INTERNAL_API_BASE=http://localhost:8000
REMEDIATION_DISPATCH_URL=http://localhost:7071/api/remediation_dispatcher
ESCALATION_WEBHOOK_URL=
DASHBOARD_BASE_URL=http://localhost:8000
IOTHUB_EVENTHUB_COMPATIBLE_CONNECTION_STRING=Endpoint=${EHEP};SharedAccessKeyName=iothubowner;SharedAccessKey=${HUBKEY};EntityPath=${EHPATH}
IOTHUB_EVENTHUB_COMPATIBLE_PATH=${EHPATH}
EOF
grep -E "=$" .env | cut -d= -f1
```

The last line lists the variables that ended up empty. It's fine to see
`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `ESCALATION_WEBHOOK_URL` and
`IOTHUB_CONNECTION_STRING` (that one only *looks* empty because its key
ends in `=`). Anything else empty means an Azure lookup failed. Re-run
`az login` and try again.

`ESCALATION_WEBHOOK_URL` is optional: a Slack/Discord/Teams incoming
webhook URL for human escalation. See `.env.example` for what each variable
is used for.

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

`createsuperuser` asks for any username, email (optional) and password you
like. It's your dashboard login.

If `migrate` hangs or times out, check two things: that the firewall rule
from step 4 is in place, and that `POSTGRES_URL` ends in `?sslmode=require`.

## 7. Run the remediation Function locally

The Functions host doesn't read `.env`, so its settings go in
`cloud/functions/local.settings.json`. That file is gitignored. Generate it
from `.env` by running this from the repo root:

```bash
python3 - <<'EOF'
import json
env = dict(l.split("=", 1) for l in open(".env").read().splitlines() if "=" in l and not l.startswith("#"))
keys = ["IOTHUB_CONNECTION_STRING","DJANGO_INTERNAL_API_BASE","INTERNAL_API_TOKEN","DASHBOARD_BASE_URL",
        "ESCALATION_WEBHOOK_URL","AZURE_SUBSCRIPTION_ID","AZURE_RESOURCE_GROUP","AZURE_IOTHUB_NAME","AZURE_AKS_CLUSTER_NAME"]
values = {"FUNCTIONS_WORKER_RUNTIME": "python",
          "AzureWebJobsStorage": env["FIRMWARE_STORAGE_CONNECTION_STRING"],
          **{k: env.get(k, "") for k in keys}}
json.dump({"IsEncrypted": False, "Values": values}, open("cloud/functions/local.settings.json", "w"), indent=2)
print("wrote cloud/functions/local.settings.json")
EOF
```

Then, in a new terminal:

```bash
cd cloud/functions
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
func start
```

It's working when it lists
`remediation_dispatcher: [POST] http://localhost:7071/api/remediation_dispatcher`
and `remediation_worker: queueTrigger` with no `ModuleNotFoundError` above.
Leave it running.

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

Create one IoT Hub identity per board in the Portal: **IoT Hub
`selfheal-iothub` → Devices → Add device**, set the Device ID to `esp32-01`
(keep "Symmetric key" and "Auto-generate keys"), and save. Open the device and
copy its **Primary key**.

(If the `azure-iot` CLI extension did install for you, you can use
`az iot hub device-identity create --hub-name selfheal-iothub --device-id esp32-01`
instead, and leave out `--device-key` below.)

Plug in the board and find its serial port with `ls /dev/cu.*`. Then:

```bash
cd firmware
python3.11 -m venv .venv && source .venv/bin/activate
pip install platformio pyserial
pio run -e esp32dev -t upload
python scripts/provision.py --port /dev/cu.usbserial-XXXX \
    --ssid "<wifi name>" --hub-name selfheal-iothub --device-id esp32-01 \
    --device-key "<primary key from the Portal>"
pio device monitor
```

`provision.py` asks for the WiFi password. `pio device monitor` should show
WiFi and then IoT Hub connecting; press Ctrl+C to exit it.

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
| `SkuNotAvailable` for Postgres | Re-run step 3 with `postgresLocation=<other region>` |
| `az extension add --name azure-iot` fails with 404 or "Pip failed" | Skip it; steps 5 and 9 don't need it |
| `az ...` command seems to hang | It's waiting on a hidden "install extension?" prompt. Press Ctrl+C |
| `python3 -m venv` fails with an `ensurepip` error | Your default Python is too new; use `python3.11 -m venv .venv` (delete the broken `.venv` first) |
| `zsh: command not found: python` | The venv isn't active; run `source .venv/bin/activate` |
| `brew` says "Refusing to load formula ... from untrusted tap" | `brew trust azure/functions`, or install Core Tools with npm (step 1) |
| `brew` says "Command Line Tools are too outdated" | Update them in System Settings → Software Update, or install Core Tools with npm |
| `func start` shows `ModuleNotFoundError` | `pip install -r requirements.txt` failed; activate the venv, re-run it, and read the first error |

---

## Appendix: Windows (PowerShell)

Use native PowerShell, not WSL; flashing the ESP32 from WSL needs extra USB
passthrough setup. The steps are the same as above; only the commands differ.

**Installing tools.** If `winget` works:

```powershell
winget install Git.Git
winget install Python.Python.3.11
winget install Microsoft.AzureCLI
winget install Microsoft.Azure.FunctionsCoreTools
```

If `winget` isn't recognized, install **App Installer** from the Microsoft
Store (that's what provides winget), or download each installer directly:

| Tool | Download | Note |
|---|---|---|
| Git | https://git-scm.com/download/win | defaults are fine |
| Python 3.11 | https://www.python.org/downloads/windows/ (latest 3.11.x, 64-bit) | tick **"Add python.exe to PATH"** |
| Azure CLI | https://aka.ms/installazurecliwindowsx64 | |
| Functions Core Tools v4 | https://go.microsoft.com/fwlink/?linkid=2174087 | |
| ESP32 USB driver | Silicon Labs "CP210x VCP driver", or "CH341SER" | match the chip on the board |

Close and reopen PowerShell, then:

```powershell
py -3.11 -m pip install platformio pyserial
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Differences from the bash commands:**

| bash (above) | PowerShell |
|---|---|
| `python3.11 -m venv .venv && source .venv/bin/activate` | `py -3.11 -m venv .venv; .\.venv\Scripts\Activate.ps1` |
| `for p in A B C; do az provider register --namespace $p; done` | `foreach ($p in "A","B","C") { az provider register --namespace $p }` |
| line continuation `\` | backtick `` ` ``, or put the command on one line |
| `MYIP=$(curl -s https://api.ipify.org)` | `$MYIP = Invoke-RestMethod https://api.ipify.org` |
| `cp .env.example .env` | `Copy-Item .env.example .env` |
| `/dev/cu.usbserial-XXXX` | `COM3` (see Device Manager → Ports) |
| `/tmp/...` | `$env:TEMP\...` |

**Step 5 (`.env`) in PowerShell:**

```powershell
$RG="rg-selfhealing-iot"; $HUB="selfheal-iothub"
$PGPASS = Read-Host "Postgres password"
$PGENC = py -3.11 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" $PGPASS
$FW = az storage account list -g $RG --query "[?starts_with(name,'selfhealfw')].name | [0]" -o tsv
$KEY = az iot hub policy show --hub-name $HUB -n iothubowner --query primaryKey -o tsv
$HOST_ = az iot hub show -n $HUB --query properties.hostName -o tsv
$EHEP = az iot hub show -n $HUB --query properties.eventHubEndpoints.events.endpoint -o tsv
$EHPATH = az iot hub show -n $HUB --query properties.eventHubEndpoints.events.path -o tsv
$SUB = az account show --query id -o tsv
$TEN = az account show --query tenantId -o tsv
$FWCS = az storage account show-connection-string -g $RG -n $FW -o tsv
$SECRET = py -3.11 -c "import secrets; print(secrets.token_urlsafe(50))"
$TOKEN = py -3.11 -c "import secrets; print(secrets.token_urlsafe(32))"
@"
IOTHUB_CONNECTION_STRING=HostName=$HOST_;SharedAccessKeyName=iothubowner;SharedAccessKey=$KEY
POSTGRES_URL=postgresql://autoheal_admin:$PGENC@selfheal-pg.postgres.database.azure.com:5432/autoheal?sslmode=require
AZURE_SUBSCRIPTION_ID=$SUB
AZURE_TENANT_ID=$TEN
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
AZURE_RESOURCE_GROUP=$RG
AZURE_IOTHUB_NAME=$HUB
AZURE_AKS_CLUSTER_NAME=selfheal-aks
FIRMWARE_STORAGE_CONNECTION_STRING=$FWCS
FIRMWARE_CONTAINER=firmware
DJANGO_SECRET_KEY=$SECRET
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
INTERNAL_API_TOKEN=$TOKEN
DJANGO_INTERNAL_API_BASE=http://localhost:8000
REMEDIATION_DISPATCH_URL=http://localhost:7071/api/remediation_dispatcher
ESCALATION_WEBHOOK_URL=
DASHBOARD_BASE_URL=http://localhost:8000
IOTHUB_EVENTHUB_COMPATIBLE_CONNECTION_STRING=Endpoint=$EHEP;SharedAccessKeyName=iothubowner;SharedAccessKey=$KEY;EntityPath=$EHPATH
IOTHUB_EVENTHUB_COMPATIBLE_PATH=$EHPATH
"@ | Set-Content -Encoding ascii .env
```

For step 7, run the same Python snippet with `py -3.11 -` and paste the code
(between `<<'EOF'` and `EOF`), then press Ctrl+Z and Enter.

**Step 9:** always pass `--device-key` to `provision.py`; without it, the
script tries to call `az` itself, which fails on Windows.

**Windows gotchas:**
- **`python` opens the Microsoft Store:** use `py -3.11`, or turn off the
  Python "App execution aliases" in Settings.
- **"Activate.ps1 cannot be loaded":** run the `Set-ExecutionPolicy` line above.
- **`pio` not found:** use `py -3.11 -m platformio`.
