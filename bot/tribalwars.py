"""
TribalWars API client.

Fetches player, village, and conquer data from the public TribalWars world endpoints.
All network calls are async (aiohttp).
"""

from __future__ import annotations

import gzip
import io
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

import aiohttp

# Base URL template.  Replace {world} with e.g. "en123".
_MAP_BASE = "https://{world}.tribalwars.net/map"
_IFACE_BASE = "https://{world}.tribalwars.net/interface.php"


@dataclass
class Player:
    id: int
    name: str  # URL-decoded
    tribe_id: int
    villages: int
    points: int
    rank: int


@dataclass
class Village:
    id: int
    name: str  # URL-decoded
    x: int
    y: int
    player_id: int
    points: int
    rank: int


@dataclass
class ConquerEvent:
    village_id: int
    timestamp: int
    new_owner_id: int
    old_owner_id: int


class TribalWarsClient:
    """Async client for the TribalWars public data API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_gz(self, url: str) -> bytes:
        """Fetch a .gz file and return the decompressed bytes."""
        async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            compressed = await resp.read()
        with gzip.open(io.BytesIO(compressed)) as f:
            return f.read()

    async def _get_text(self, url: str) -> str:
        """Fetch a plain-text URL and return the body as a string."""
        async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            return await resp.text()

    @staticmethod
    def _decode(value: str) -> str:
        return urllib.parse.unquote_plus(value)

    # ------------------------------------------------------------------
    # Public data fetchers
    # ------------------------------------------------------------------

    async def get_players(self, world: str) -> dict[int, Player]:
        """Return all players keyed by player ID."""
        url = f"{_MAP_BASE.format(world=world)}/player.txt.gz"
        raw = await self._get_gz(url)
        players: dict[int, Player] = {}
        for line in raw.decode().splitlines():
            parts = line.strip().split(",")
            if len(parts) < 6:
                continue
            pid = int(parts[0])
            players[pid] = Player(
                id=pid,
                name=self._decode(parts[1]),
                tribe_id=int(parts[2]),
                villages=int(parts[3]),
                points=int(parts[4]),
                rank=int(parts[5]),
            )
        return players

    async def get_villages(self, world: str) -> dict[int, Village]:
        """Return all villages keyed by village ID."""
        url = f"{_MAP_BASE.format(world=world)}/village.txt.gz"
        raw = await self._get_gz(url)
        villages: dict[int, Village] = {}
        for line in raw.decode().splitlines():
            parts = line.strip().split(",")
            if len(parts) < 7:
                continue
            vid = int(parts[0])
            villages[vid] = Village(
                id=vid,
                name=self._decode(parts[1]),
                x=int(parts[2]),
                y=int(parts[3]),
                player_id=int(parts[4]),
                points=int(parts[5]),
                rank=int(parts[6]),
            )
        return villages

    async def get_conquers_since(self, world: str, since: int) -> list[ConquerEvent]:
        """
        Return conquer events since *since* (Unix timestamp).

        Uses the dynamic endpoint which supports up to 24 hours lookback.
        Falls back to conquer.txt.gz if the timestamp is too old.
        """
        url = f"{_IFACE_BASE.format(world=world)}?func=get_conquer&since={since}"
        try:
            raw_text = await self._get_text(url)
            return self._parse_conquer_lines(raw_text.splitlines())
        except aiohttp.ClientError:
            # Fallback: full conquer dump
            return await self._get_all_conquers(world)

    async def _get_all_conquers(self, world: str) -> list[ConquerEvent]:
        url = f"{_MAP_BASE.format(world=world)}/conquer.txt.gz"
        raw = await self._get_gz(url)
        return self._parse_conquer_lines(raw.decode().splitlines())

    @staticmethod
    def _parse_conquer_lines(lines: list[str]) -> list[ConquerEvent]:
        events: list[ConquerEvent] = []
        for line in lines:
            parts = line.strip().split(",")
            if len(parts) < 4:
                continue
            try:
                events.append(
                    ConquerEvent(
                        village_id=int(parts[0]),
                        timestamp=int(parts[1]),
                        new_owner_id=int(parts[2]),
                        old_owner_id=int(parts[3]),
                    )
                )
            except ValueError:
                continue
        return events

    async def resolve_player_id(self, world: str, player_name: str) -> Optional[int]:
        """
        Return the numeric player ID for *player_name* (case-insensitive).
        Returns None if no match is found.
        """
        players = await self.get_players(world)
        name_lower = player_name.lower()
        for player in players.values():
            if player.name.lower() == name_lower:
                return player.id
        return None

    @staticmethod
    def now_ts() -> int:
        """Current Unix timestamp as an integer."""
        return int(time.time())
