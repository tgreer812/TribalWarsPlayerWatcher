"""
Watch-rule management.

Rules are persisted to a JSON file so they survive bot restarts.
Each rule describes:
  - which Discord guild + channel should receive alerts
  - which TribalWars world to watch
  - which player (by numeric ID and display name) to track
  - the coordinate bounding box that triggers an alert

Schema (stored as a JSON array):
[
  {
    "id": "<uuid>",
    "guild_id": 123,
    "channel_id": 456,
    "world": "en123",
    "player_id": 789,
    "player_name": "SomeName",
    "x_min": 400,
    "x_max": 600,
    "y_min": 400,
    "y_max": 600
  },
  ...
]
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class WatchRule:
    id: str
    guild_id: int
    channel_id: int
    world: str
    player_id: int
    player_name: str
    x_min: int
    x_max: int
    y_min: int
    y_max: int

    def matches(self, new_owner_id: int, x: int, y: int) -> bool:
        """Return True if a conquer event matches this rule."""
        return (
            self.player_id == new_owner_id
            and self.x_min <= x <= self.x_max
            and self.y_min <= y <= self.y_max
        )


class RuleStore:
    """Thread-safe (single-process) rule store backed by a JSON file."""

    def __init__(self, path: str = "data/rules.json") -> None:
        self._path = path
        self._rules: list[WatchRule] = []
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self._path):
            self._rules = []
            return
        with open(self._path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self._rules = [WatchRule(**r) for r in raw]

    def _save(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in self._rules], f, indent=2)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(
        self,
        guild_id: int,
        channel_id: int,
        world: str,
        player_id: int,
        player_name: str,
        x_min: int,
        x_max: int,
        y_min: int,
        y_max: int,
    ) -> WatchRule:
        rule = WatchRule(
            id=str(uuid.uuid4()),
            guild_id=guild_id,
            channel_id=channel_id,
            world=world,
            player_id=player_id,
            player_name=player_name,
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
        )
        self._rules.append(rule)
        self._save()
        return rule

    def remove(self, rule_id: str) -> Optional[WatchRule]:
        for i, rule in enumerate(self._rules):
            if rule.id == rule_id:
                removed = self._rules.pop(i)
                self._save()
                return removed
        return None

    def list_for_guild(self, guild_id: int) -> list[WatchRule]:
        return [r for r in self._rules if r.guild_id == guild_id]

    def all(self) -> list[WatchRule]:
        return list(self._rules)

    def worlds(self) -> set[str]:
        """Return the unique set of worlds that have at least one active rule."""
        return {r.world for r in self._rules}
