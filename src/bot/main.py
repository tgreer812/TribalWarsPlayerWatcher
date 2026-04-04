"""
TribalWars Player Watcher – Discord Bot
========================================
Slash commands
--------------
/watch add   – add a conquer-alert rule for a player in a coordinate area
/watch list  – list all rules for this guild
/watch remove – delete a rule by its short ID

Background task
---------------
Every POLL_INTERVAL_SECONDS the bot fetches conquer events for every world
that has at least one active rule and posts an embed to the configured channel
whenever the tracked player nobles a village inside the watched area.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from tribalwars import TribalWarsClient, Village
from watcher import RuleStore, WatchRule

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DISCORD_TOKEN: str = os.environ["DISCORD_TOKEN"]
RULES_FILE: str = os.environ.get("RULES_FILE", "data/rules.json")
POLL_INTERVAL_SECONDS: int = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tw_watcher")

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------


class WatcherBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.store = RuleStore(RULES_FILE)
        self._http_session: Optional[aiohttp.ClientSession] = None

    @property
    def tw(self) -> TribalWarsClient:
        assert self._http_session is not None
        return TribalWarsClient(self._http_session)

    async def setup_hook(self) -> None:
        self._http_session = aiohttp.ClientSession()
        # Sync slash commands globally (may take up to 1 hour to propagate).
        # For instant testing in a single server use:
        #   self.tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
        await self.tree.sync()
        conquer_poll.start(self)

    async def close(self) -> None:
        conquer_poll.cancel()
        if self._http_session:
            await self._http_session.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id)  # type: ignore[union-attr]


bot = WatcherBot()

# ---------------------------------------------------------------------------
# Slash commands  –  /watch  (group)
# ---------------------------------------------------------------------------

watch_group = app_commands.Group(name="watch", description="Manage conquer-alert rules")
bot.tree.add_command(watch_group)


@watch_group.command(name="add", description="Alert when a player nobles inside an area")
@app_commands.describe(
    world="World code, e.g. en123",
    player="Player name (exact, case-insensitive)",
    x_min="West boundary (minimum X coordinate)",
    x_max="East boundary (maximum X coordinate)",
    y_min="North boundary (minimum Y coordinate)",
    y_max="South boundary (maximum Y coordinate)",
    channel="Channel to post alerts in (defaults to this channel)",
)
async def watch_add(
    interaction: discord.Interaction,
    world: str,
    player: str,
    x_min: int,
    x_max: int,
    y_min: int,
    y_max: int,
    channel: Optional[discord.TextChannel] = None,
) -> None:
    await interaction.response.defer(ephemeral=True)

    alert_channel = channel or interaction.channel
    if not isinstance(alert_channel, discord.TextChannel):
        await interaction.followup.send("Please specify a valid text channel.", ephemeral=True)
        return

    if x_min > x_max or y_min > y_max:
        await interaction.followup.send(
            "Invalid coordinate range: x_min must be ≤ x_max and y_min must be ≤ y_max.",
            ephemeral=True,
        )
        return

    # Resolve player name → ID
    player_id = await bot.tw.resolve_player_id(world.lower(), player)
    if player_id is None:
        await interaction.followup.send(
            f"Could not find player **{player}** on world **{world}**. "
            "Check the spelling and world code.",
            ephemeral=True,
        )
        return

    rule = bot.store.add(
        guild_id=interaction.guild_id,  # type: ignore[arg-type]
        channel_id=alert_channel.id,
        world=world.lower(),
        player_id=player_id,
        player_name=player,
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
    )

    await interaction.followup.send(
        f"✅ Watch rule **{rule.id[:8]}** created.\n"
        f"Alerting in {alert_channel.mention} when **{player}** nobles inside "
        f"({x_min},{y_min})–({x_max},{y_max}) on **{world}**.",
        ephemeral=True,
    )


@watch_group.command(name="list", description="List all active watch rules for this server")
async def watch_list(interaction: discord.Interaction) -> None:
    rules = bot.store.list_for_guild(interaction.guild_id)  # type: ignore[arg-type]
    if not rules:
        await interaction.response.send_message("No active watch rules.", ephemeral=True)
        return

    lines = ["**Active watch rules:**"]
    for r in rules:
        channel = interaction.guild.get_channel(r.channel_id)  # type: ignore[union-attr]
        channel_mention = channel.mention if channel else f"<#{r.channel_id}>"
        lines.append(
            f"`{r.id[:8]}` | **{r.player_name}** on **{r.world}** | "
            f"({r.x_min},{r.y_min})–({r.x_max},{r.y_max}) → {channel_mention}"
        )

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@watch_group.command(name="remove", description="Remove a watch rule by its ID prefix")
@app_commands.describe(rule_id="First 8 characters of the rule ID (from /watch list)")
async def watch_remove(interaction: discord.Interaction, rule_id: str) -> None:
    rules = bot.store.list_for_guild(interaction.guild_id)  # type: ignore[arg-type]
    match = next((r for r in rules if r.id.startswith(rule_id)), None)
    if match is None:
        await interaction.response.send_message(
            f"No rule found with ID starting with `{rule_id}`.", ephemeral=True
        )
        return

    bot.store.remove(match.id)
    await interaction.response.send_message(
        f"🗑️ Rule `{match.id[:8]}` (watching **{match.player_name}** on **{match.world}**) removed.",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# Background polling task
# ---------------------------------------------------------------------------

# Track the last-checked timestamp per world so we only fetch new conquers.
_last_checked: dict[str, int] = {}


@tasks.loop(seconds=POLL_INTERVAL_SECONDS)
async def conquer_poll(client: WatcherBot) -> None:
    worlds = client.store.worlds()
    if not worlds:
        return

    log.info("Polling %d world(s): %s", len(worlds), ", ".join(sorted(worlds)))

    for world in worlds:
        await _check_world(client, world)


@conquer_poll.before_loop
async def before_poll() -> None:
    await bot.wait_until_ready()


async def _check_world(client: WatcherBot, world: str) -> None:
    since = _last_checked.get(world, TribalWarsClient.now_ts() - POLL_INTERVAL_SECONDS)
    now = TribalWarsClient.now_ts()

    try:
        events = await client.tw.get_conquers_since(world, since)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to fetch conquers for %s: %s", world, exc)
        return

    # Advance the checkpoint now so we never re-process the same time window,
    # even if the village/player fetch below fails.
    _last_checked[world] = now

    if not events:
        return

    # Only fetch villages/players if there are events to process
    rules = [r for r in client.store.all() if r.world == world]
    if not rules:
        return

    try:
        villages = await client.tw.get_villages(world)
        players = await client.tw.get_players(world)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to fetch map data for %s: %s", world, exc)
        return

    for event in events:
        # Skip barbarian conquers (new owner = 0) and conquers older than our window
        if event.new_owner_id == 0 or event.timestamp <= since:
            continue

        village = villages.get(event.village_id)
        if village is None:
            continue

        for rule in rules:
            if rule.matches(event.new_owner_id, village.x, village.y):
                old_owner = players.get(event.old_owner_id)
                old_name = old_owner.name if old_owner else "Barbarian"
                await _send_alert(client, rule, village, old_name, event.timestamp)


async def _send_alert(
    client: WatcherBot,
    rule: WatchRule,
    village: Village,
    old_owner_name: str,
    timestamp: int,
) -> None:
    channel = client.get_channel(rule.channel_id)
    if not isinstance(channel, discord.TextChannel):
        log.warning("Alert channel %d not found or not a text channel", rule.channel_id)
        return

    embed = discord.Embed(
        title="⚔️ Noble Alert!",
        description=(
            f"**{rule.player_name}** has nobled a village on **{rule.world}**!"
        ),
        color=discord.Color.red(),
    )
    embed.add_field(name="Village", value=f"{village.name} ({village.x}|{village.y})", inline=True)
    embed.add_field(name="Previous Owner", value=old_owner_name, inline=True)
    embed.add_field(name="Points", value=str(village.points), inline=True)
    embed.set_footer(text=f"Rule ID: {rule.id[:8]}")

    try:
        await channel.send(content="@everyone", embed=embed)
    except discord.DiscordException as exc:
        log.warning("Failed to send alert to channel %d: %s", rule.channel_id, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN, log_handler=None)
