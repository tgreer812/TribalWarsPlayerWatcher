# Azure Deployment Plan – TribalWars Player Watcher

## Overview

This document describes how the TribalWars Player Watcher Discord bot is deployed to Azure using **Azure Container Apps** and **Azure Container Registry (ACR)**, with automatic deployments triggered by GitHub Actions on every push to `main`.

---

## Architecture

```
GitHub (main branch)
    │
    ▼ (GitHub Actions: .github/workflows/deploy.yml)
    │
    ├─► Azure Container Registry (tylergreer.azurecr.io)
    │       stores versioned Docker images
    │       tags: latest + short git SHA per build
    │
    └─► Azure Container Apps (tg-tribalwars-bot)
            runs the bot container 24/7 (single replica)
            reads DISCORD_TOKEN from Container Apps secret
            persists rules.json via mounted Azure File Share
```

### Components

| Component | Actual Resource Name | Purpose |
|---|---|---|
| **Resource Group** | `tg-tribalwars-rg` | Contains all tribalwars-related resources |
| **Azure Container Registry** | `tylergreer` (`tylergreer.azurecr.io`) | Stores Docker images built by CI/CD (shared ACR, Basic SKU) |
| **Container Apps Environment** | `tg-tribalwars-env` | Networking + observability layer |
| **Container App** | `tg-tribalwars-bot` | Runs the bot container; restarts automatically on failure |
| **Storage Account + File Share** | `tgtribalwarsstorage` / `botdata` | Persistent volume for `data/rules.json` |
| **Service Principal** | `sp-tribalwars-watcher-deploy` (app ID: `6a6d058b-ffb9-45c3-8d29-8d3f4d5a78f2`) | Used by GitHub Actions to authenticate with Azure |

### Container App Configuration

| Setting | Value |
|---|---|
| Image | `tylergreer.azurecr.io/tribalwars-player-watcher:latest` |
| CPU / Memory | 0.25 vCPU / 0.5 GiB |
| Min / Max replicas | 1 / 1 |
| Volume mount | Azure File Share `botdata` → `/data` |
| Env: `DISCORD_TOKEN` | `secretref:discord-token` (stored as Container Apps secret) |
| Env: `RULES_FILE` | `/data/rules.json` |
| Env: `POLL_INTERVAL_SECONDS` | `300` |

---

## Repository Layout

```
/
├── .github/
│   └── workflows/
│       └── deploy.yml          # CI/CD: build → push → deploy
├── deploy/
│   └── Dockerfile              # Container image definition
├── docs/
│   └── azure-deployment-plan.md
├── src/
│   ├── bot/                    # Application source code
│   │   ├── main.py
│   │   ├── tribalwars.py
│   │   └── watcher.py
│   └── tests/                  # Unit tests
│       ├── __init__.py
│       └── test_bot.py
├── .env.example
├── requirements.txt
└── README.md
```

---

## Prerequisites

1. **Azure subscription** — currently using "Visual Studio Enterprise Subscription" (`ab355bca-21aa-4453-898a-492c0f0685f9`).
2. **Azure CLI** installed locally (`az` command) — login with `az login --tenant c1a4372c-8bef-445e-834c-af28f5614398 --use-device-code`.
3. **GitHub CLI** (`gh`) — for setting repository secrets.
4. **Docker** installed locally (for local builds/testing only; CI/CD builds in GitHub Actions).

---

## One-Time Azure Setup (Already Completed)

The following resources have been provisioned. These commands are documented here for reference in case they need to be recreated.

```bash
# Variables (actual values used)
RESOURCE_GROUP="tg-tribalwars-rg"
LOCATION="eastus"
ACR_NAME="tylergreer"                  # shared ACR (already existed)
APP_ENV="tg-tribalwars-env"
APP_NAME="tg-tribalwars-bot"
STORAGE_ACCOUNT="tgtribalwarsstorage"
FILE_SHARE="botdata"

# 1. Resource group
az group create --name $RESOURCE_GROUP --location $LOCATION

# 2. Azure Container Registry (already existed in tylergreer-rg, reused)
# az acr create --resource-group tylergreer-rg --name tylergreer --sku Basic --admin-enabled true

# 3. Container Apps environment
az containerapp env create \
  --name $APP_ENV \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION

# 4. Storage account + file share (for persistent rules.json)
az storage account create \
  --name $STORAGE_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --sku Standard_LRS

az storage share create \
  --name $FILE_SHARE \
  --account-name $STORAGE_ACCOUNT

STORAGE_KEY=$(az storage account keys list \
  --resource-group $RESOURCE_GROUP \
  --account-name $STORAGE_ACCOUNT \
  --query "[0].value" -o tsv)

# 5. Attach file share to the Container Apps environment
az containerapp env storage set \
  --name $APP_ENV \
  --resource-group $RESOURCE_GROUP \
  --storage-name botdata \
  --azure-file-account-name $STORAGE_ACCOUNT \
  --azure-file-account-key $STORAGE_KEY \
  --azure-file-share-name $FILE_SHARE \
  --access-mode ReadWrite

# 6. Build and push initial image using ACR cloud build
az acr build \
  --registry tylergreer \
  --image tribalwars-player-watcher:latest \
  --file deploy/Dockerfile .

# 7. Get ACR credentials
ACR_SERVER=$(az acr show --name $ACR_NAME --query loginServer -o tsv)
ACR_USERNAME=$(az acr credential show --name $ACR_NAME --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

# 8. Create the Container App
az containerapp create \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --environment $APP_ENV \
  --image "$ACR_SERVER/tribalwars-player-watcher:latest" \
  --registry-server $ACR_SERVER \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --secrets discord-token="<your-discord-token>" \
  --env-vars \
      DISCORD_TOKEN=secretref:discord-token \
      RULES_FILE=/data/rules.json \
      POLL_INTERVAL_SECONDS=300 \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu 0.25 \
  --memory 0.5Gi

# 9. Mount the file share volume (via YAML export/update)
# Export current config, add volume + volumeMount, then:
az containerapp update \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --yaml updated-config.yaml
```

### Service Principal (for GitHub Actions)

```bash
# Create SP with contributor role scoped to the resource group
az ad sp create-for-rbac \
  --name "sp-tribalwars-watcher-deploy" \
  --role contributor \
  --scopes /subscriptions/ab355bca-21aa-4453-898a-492c0f0685f9/resourceGroups/tg-tribalwars-rg

# Output JSON is used as the AZURE_CREDENTIALS GitHub secret
# Format: {"clientId":"...","clientSecret":"...","subscriptionId":"...","tenantId":"..."}

# To reset credentials if needed:
az ad sp credential reset --id 6a6d058b-ffb9-45c3-8d29-8d3f4d5a78f2
```

---

## GitHub Secrets

The following secrets are configured in the GitHub repository under **Settings → Secrets and variables → Actions**. These were set using `gh secret set`.

| Secret name | Actual Value | Description |
|---|---|---|
| `AZURE_CREDENTIALS` | SP JSON (see below) | Service principal credentials for `azure/login@v2` |
| `ACR_LOGIN_SERVER` | `tylergreer.azurecr.io` | ACR login server URL |
| `ACR_USERNAME` | `tylergreer` | ACR admin username |
| `ACR_PASSWORD` | *(stored in GitHub)* | ACR admin password |
| `CONTAINER_APP_NAME` | `tg-tribalwars-bot` | Name of the Container App |
| `RESOURCE_GROUP` | `tg-tribalwars-rg` | Azure resource group name |

> **Note:** GitHub secrets are write-only — you can update them but never read them back from the GitHub UI or API.

### `AZURE_CREDENTIALS` Format

```json
{
  "clientId": "6a6d058b-ffb9-45c3-8d29-8d3f4d5a78f2",
  "clientSecret": "<regenerate with: az ad sp credential reset --id 6a6d058b-ffb9-45c3-8d29-8d3f4d5a78f2>",
  "subscriptionId": "ab355bca-21aa-4453-898a-492c0f0685f9",
  "tenantId": "c1a4372c-8bef-445e-834c-af28f5614398"
}
```

### Setting Secrets via CLI

```bash
gh secret set AZURE_CREDENTIALS --body '<json>' --repo tgreer812/TribalWarsPlayerWatcher
gh secret set ACR_LOGIN_SERVER --body 'tylergreer.azurecr.io' --repo tgreer812/TribalWarsPlayerWatcher
gh secret set ACR_USERNAME --body 'tylergreer' --repo tgreer812/TribalWarsPlayerWatcher
gh secret set ACR_PASSWORD --body '<password>' --repo tgreer812/TribalWarsPlayerWatcher
gh secret set CONTAINER_APP_NAME --body 'tg-tribalwars-bot' --repo tgreer812/TribalWarsPlayerWatcher
gh secret set RESOURCE_GROUP --body 'tg-tribalwars-rg' --repo tgreer812/TribalWarsPlayerWatcher
```

### Generating `AZURE_CREDENTIALS`

The service principal already exists. If you need to regenerate the secret:

```bash
az ad sp credential reset --id 6a6d058b-ffb9-45c3-8d29-8d3f4d5a78f2 --output json
# Then update the GitHub secret with the new clientSecret value
```

---

## CI/CD Workflow (`.github/workflows/deploy.yml`)

The workflow runs automatically on every push to `main`, and can also be triggered manually via `workflow_dispatch`.

### Job 1: `build-and-push`
1. **Checkout** – pulls the latest code.
2. **Log in to ACR** – authenticates Docker with the registry using `ACR_USERNAME` / `ACR_PASSWORD`.
3. **Extract metadata** – generates image tags: `latest` and a short Git SHA (for rollback).
4. **Build and push image** – builds `deploy/Dockerfile` from the repo root context and pushes to ACR.

### Job 2: `deploy`
1. **Log in to Azure** – authenticates the Azure CLI using the `AZURE_CREDENTIALS` service principal.
2. **Deploy** – runs `az containerapp update` to update the Container App to use the new image. This preserves existing Container App secrets (like `discord-token`) that were set during initial provisioning.

> **Important:** The deploy step uses `az containerapp update` (not the `azure/container-apps-deploy-action`). This was necessary because the GitHub Action would try to create a fresh deployment without the existing Container App secrets, causing a `ContainerAppSecretRefNotFound` error for the `discord-token` secret.

---

## Local Development & Testing

```bash
# Copy and fill in the environment file
cp .env.example .env
# Edit .env and set DISCORD_TOKEN

# Run tests
cd src && python -m pytest tests/

# Build the Docker image locally
docker build -f deploy/Dockerfile -t tribalwars-player-watcher .

# Run locally
docker run --env-file .env -v $(pwd)/data:/data tribalwars-player-watcher
```

---

## Secrets Rotation

### Discord Token
The Discord token is stored as a Container Apps secret (not in GitHub). To view or update it:

```bash
# View current token
az containerapp secret show \
  --name tg-tribalwars-bot \
  --resource-group tg-tribalwars-rg \
  --secret-name discord-token \
  --query value -o tsv

# Update token
az containerapp secret set \
  --name tg-tribalwars-bot \
  --resource-group tg-tribalwars-rg \
  --secrets discord-token="<new-token>"

# Restart to pick up the new token
az containerapp revision restart \
  --name tg-tribalwars-bot \
  --resource-group tg-tribalwars-rg \
  --revision $(az containerapp revision list \
      --name tg-tribalwars-bot \
      --resource-group tg-tribalwars-rg \
      --query "[0].name" -o tsv)
```

To generate a new Discord token: go to [Discord Developer Portal](https://discord.com/developers/applications) → your app → Bot → **Reset Token**.

### ACR Credentials
```bash
az acr credential renew --name tylergreer --password-name password
# Then update the three ACR GitHub secrets (ACR_LOGIN_SERVER, ACR_USERNAME, ACR_PASSWORD)
```

### Service Principal
```bash
az ad sp credential reset --id 6a6d058b-ffb9-45c3-8d29-8d3f4d5a78f2
# Then update the AZURE_CREDENTIALS GitHub secret
```

---

## Rollback

Each successful CI/CD run pushes a `<short-sha>` tagged image to ACR in addition to `latest`. To roll back:

```bash
az containerapp update \
  --name tg-tribalwars-bot \
  --resource-group tg-tribalwars-rg \
  --image "tylergreer.azurecr.io/tribalwars-player-watcher:<previous-sha>"
```

---

## Monitoring & Troubleshooting

```bash
# Check container app status
az containerapp show \
  --name tg-tribalwars-bot \
  --resource-group tg-tribalwars-rg \
  --query "properties.runningStatus" -o tsv

# View container logs
az containerapp logs show \
  --name tg-tribalwars-bot \
  --resource-group tg-tribalwars-rg \
  --follow

# View revisions
az containerapp revision list \
  --name tg-tribalwars-bot \
  --resource-group tg-tribalwars-rg \
  --output table

# Manually trigger a deploy (via GitHub Actions)
gh workflow run deploy.yml --repo tgreer812/TribalWarsPlayerWatcher --ref main

# Watch a workflow run
gh run list --repo tgreer812/TribalWarsPlayerWatcher --limit 3
gh run watch <run-id> --repo tgreer812/TribalWarsPlayerWatcher
```

---

## Discord Bot Setup

### Bot Application
- **Application ID:** `1487299730001232022`
- **Developer Portal:** https://discord.com/developers/applications

### Invite Link
Add the bot to a Discord server using:
```
https://discord.com/oauth2/authorize?client_id=1487299730001232022&permissions=18432&scope=bot+applications.commands
```

This requests:
- **bot** + **applications.commands** scopes (required for slash commands)
- **Send Messages** + **Embed Links** permissions (required for alert embeds)

Slash commands (`/watch add`, `/watch list`, `/watch remove`) may take up to 1 hour to propagate globally after the bot first starts.

---

## Cost Estimate

| Resource | SKU | Approximate monthly cost |
|---|---|---|
| Azure Container Registry | Basic | ~$5 |
| Azure Container Apps | Consumption (1 replica, 0.25 vCPU / 0.5 GiB) | ~$5–$10 |
| Azure Storage (File Share) | Standard LRS | < $1 |
| **Total** | | **~$10–$16 / month** |

Costs vary by region and actual usage. Check the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) for exact estimates.
