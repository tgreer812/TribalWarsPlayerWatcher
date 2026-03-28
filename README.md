# TribalWarsPlayerWatcher

A Discord bot that alerts your server whenever a tracked player **nobles a village inside a specified coordinate area** on a Tribal Wars world.

---

## Features

- `/watch add` – Register an alert rule: pick a world, a player name, and a coordinate bounding box. The bot posts a rich embed to the chosen channel whenever the player conquers a village inside that box.
- `/watch list` – Show all active rules for the current Discord server.
- `/watch remove` – Delete a rule by its short ID.
- Rules persist across bot restarts (JSON file).
- Polls the TribalWars `get_conquer` endpoint every 5 minutes by default.
- Deployable to **Azure App Service** (or any Docker host) via the included `Dockerfile`.

---

## Quick Start

### 1. Create a Discord Application & Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications) and create an application.
2. Add a **Bot** user and copy the **token**.
3. Under *OAuth2 → URL Generator*, select scopes `bot` + `applications.commands` and permissions `Send Messages` + `Embed Links`. Use the generated URL to invite the bot to your server.

### 2. Configure Environment Variables

```bash
cp .env.example .env
# Edit .env and set DISCORD_TOKEN=<your token>
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_TOKEN` | ✅ | – | Your Discord bot token |
| `RULES_FILE` | ❌ | `data/rules.json` | Path where watch rules are stored |
| `POLL_INTERVAL_SECONDS` | ❌ | `300` | How often (seconds) to check for new conquers |

### 3. Run Locally

```bash
pip install -r requirements.txt
cd bot
python main.py
```

### 4. Run with Docker

```bash
docker build -t tw-watcher .
docker run -d --env-file .env -v $(pwd)/data:/app/data tw-watcher
```

---

## Deploy to Azure

### Azure App Service (Container)

1. Push the image to Azure Container Registry (ACR):
   ```bash
   az acr build --registry <your-acr> --image tw-watcher:latest .
   ```
2. Create an App Service Plan (Linux) and a Web App for Containers pointing at your ACR image.
3. Set application settings (`DISCORD_TOKEN`, optionally `RULES_FILE`, `POLL_INTERVAL_SECONDS`).
4. Mount an Azure Files share at `/app/data` to persist the rules JSON across container restarts.

---

## Slash Commands

### `/watch add`

| Parameter | Description |
|---|---|
| `world` | World code, e.g. `en123` |
| `player` | Player name (exact, case-insensitive) |
| `x_min` | West boundary (minimum X coordinate, inclusive) |
| `x_max` | East boundary (maximum X coordinate, inclusive) |
| `y_min` | North boundary (minimum Y coordinate, inclusive) |
| `y_max` | South boundary (maximum Y coordinate, inclusive) |
| `channel` | (Optional) Text channel for alerts; defaults to the current channel |

### `/watch list`

Lists all active rules for the server with their short IDs.

### `/watch remove`

| Parameter | Description |
|---|---|
| `rule_id` | The 8-character rule ID prefix shown by `/watch list` |

---

## Data Sources

The bot uses the public [Tribal Wars world data endpoints](https://forum.tribalwars.net/index.php?threads/tw-api-reference.12375/):

| Endpoint | Usage |
|---|---|
| `https://{world}.tribalwars.net/interface.php?func=get_conquer&since=<ts>` | Near-real-time conquer feed |
| `https://{world}.tribalwars.net/map/player.txt.gz` | Player name → ID resolution |
| `https://{world}.tribalwars.net/map/village.txt.gz` | Village coordinates & names |

---

## Development

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```
