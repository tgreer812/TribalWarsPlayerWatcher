# Azure Deployment Plan – TribalWars Player Watcher

## Overview

This document describes how to deploy the TribalWars Player Watcher Discord bot to the Azure cloud using **Azure Container Apps** and **Azure Container Registry (ACR)**, with automatic deployments triggered by GitHub Actions on every merge to `main`.

---

## Architecture

```
GitHub (main branch)
    │
    ▼ (GitHub Actions: .github/workflows/deploy.yml)
    │
    ├─► Azure Container Registry (ACR)
    │       stores versioned Docker images
    │
    └─► Azure Container Apps
            runs the bot container 24/7
            reads DISCORD_TOKEN from Azure secrets
            persists rules.json via a mounted Azure File Share
```

### Components

| Component | Purpose |
|---|---|
| **Azure Container Registry** | Stores Docker images built by CI/CD |
| **Azure Container Apps Environment** | Networking + observability layer for Container Apps |
| **Azure Container App** | Runs the bot container; restarts automatically on failure |
| **Azure Storage Account + File Share** | Persistent volume for `data/rules.json` |
| **Azure Key Vault** *(optional)* | Securely manages `DISCORD_TOKEN`; referenced as a Container Apps secret |

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

1. **Azure subscription** with permission to create resources.
2. **Azure CLI** installed locally (`az` command).
3. **Docker** installed locally (for local builds/testing).

---

## One-Time Azure Setup

Run these commands once to provision the required Azure infrastructure. Replace the placeholder values with your own.

```bash
# Variables – edit these
RESOURCE_GROUP="rg-tribalwars-watcher"
LOCATION="eastus"
ACR_NAME="acrTribalWarsWatcher"         # must be globally unique, alphanumeric only
APP_ENV="cae-tribalwars"
APP_NAME="tribalwars-player-watcher"
STORAGE_ACCOUNT="sttribalwarswatcher"   # must be globally unique, lowercase only
FILE_SHARE="botdata"
DISCORD_TOKEN="<your-discord-token>"

# 1. Resource group
az group create --name $RESOURCE_GROUP --location $LOCATION

# 2. Azure Container Registry
az acr create \
  --resource-group $RESOURCE_GROUP \
  --name $ACR_NAME \
  --sku Basic \
  --admin-enabled true

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

# 6. Initial Container App deployment
ACR_SERVER=$(az acr show --name $ACR_NAME --query loginServer -o tsv)
ACR_USERNAME=$(az acr credential show --name $ACR_NAME --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

az containerapp create \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --environment $APP_ENV \
  --image "$ACR_SERVER/tribalwars-player-watcher:latest" \
  --registry-server $ACR_SERVER \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --secrets discord-token="$DISCORD_TOKEN" \
  --env-vars \
      DISCORD_TOKEN=secretref:discord-token \
      RULES_FILE=/data/rules.json \
      POLL_INTERVAL_SECONDS=300 \
  --volume-name botdata \
  --bind-mount-path /data \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu 0.25 \
  --memory 0.5Gi
```

---

## GitHub Secrets

Add the following secrets to the GitHub repository under **Settings → Secrets and variables → Actions**:

| Secret name | Description |
|---|---|
| `AZURE_CREDENTIALS` | JSON output of `az ad sp create-for-rbac` (see below) |
| `ACR_LOGIN_SERVER` | ACR login server, e.g. `acrTribalWarsWatcher.azurecr.io` |
| `ACR_USERNAME` | ACR admin username |
| `ACR_PASSWORD` | ACR admin password |
| `CONTAINER_APP_NAME` | Name of the Container App, e.g. `tribalwars-player-watcher` |
| `RESOURCE_GROUP` | Resource group name, e.g. `rg-tribalwars-watcher` |

### Generating `AZURE_CREDENTIALS`

```bash
az ad sp create-for-rbac \
  --name "sp-tribalwars-watcher-deploy" \
  --role contributor \
  --scopes /subscriptions/<subscription-id>/resourceGroups/$RESOURCE_GROUP \
  --sdk-auth
```

Copy the entire JSON output and paste it as the value of the `AZURE_CREDENTIALS` secret.

---

## CI/CD Workflow (`.github/workflows/deploy.yml`)

The workflow runs automatically on every push to `main`:

1. **Checkout** – pulls the latest code.
2. **Log in to ACR** – authenticates Docker with the registry using `ACR_USERNAME` / `ACR_PASSWORD`.
3. **Build and push image** – builds `deploy/Dockerfile` from the repo root context and pushes two tags: `latest` and a short Git SHA (for rollback).
4. **Log in to Azure** – authenticates the Azure CLI using the `AZURE_CREDENTIALS` service principal.
5. **Deploy** – updates the Container App to use the newly pushed `latest` image.

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

- **ACR credentials**: Rotate via `az acr credential renew`; update the three ACR GitHub secrets.
- **Discord token**: Update the `discord-token` Container Apps secret and restart the revision:
  ```bash
  az containerapp secret set \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --secrets discord-token="<new-token>"

  az containerapp revision restart \
    --name $APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --revision $(az containerapp revision list \
        --name $APP_NAME \
        --resource-group $RESOURCE_GROUP \
        --query "[0].name" -o tsv)
  ```

---

## Rollback

Each successful CI/CD run pushes a `<short-sha>` tagged image to ACR in addition to `latest`. To roll back:

```bash
az containerapp update \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --image "$ACR_SERVER/tribalwars-player-watcher:<previous-sha>"
```

---

## Cost Estimate

| Resource | SKU | Approximate monthly cost |
|---|---|---|
| Azure Container Registry | Basic | ~$5 |
| Azure Container Apps | Consumption (1 replica, 0.25 vCPU / 0.5 GiB) | ~$5–$10 |
| Azure Storage (File Share) | Standard LRS | < $1 |
| **Total** | | **~$10–$16 / month** |

Costs vary by region and actual usage. Check the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) for exact estimates.
