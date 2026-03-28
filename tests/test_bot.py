"""
Tests for bot/tribalwars.py and bot/watcher.py.

Uses unittest.mock to avoid real network calls.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

from tribalwars import ConquerEvent, Player, TribalWarsClient, Village
from watcher import RuleStore, WatchRule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gz(text: str) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(text.encode())
    return buf.getvalue()


PLAYER_DATA = "1,PlayerOne,10,5,50000,3\n2,PlayerTwo,0,1,1000,10\n"
VILLAGE_DATA = "100,My+Village,450,500,1,3000,5\n101,Another+Village,200,300,2,500,20\n"


def _make_mock_response(data: bytes | str, status: int = 200):
    """Build a minimal async context-manager mock for aiohttp responses."""
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.raise_for_status = MagicMock()
    if isinstance(data, bytes):
        mock_resp.read = AsyncMock(return_value=data)
    else:
        mock_resp.text = AsyncMock(return_value=data)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# TribalWarsClient tests
# ---------------------------------------------------------------------------

class TestTribalWarsClientPlayers(unittest.IsolatedAsyncioTestCase):
    async def test_get_players_parses_correctly(self):
        session = MagicMock()
        session.get = MagicMock(return_value=_make_mock_response(_gz(PLAYER_DATA)))
        client = TribalWarsClient(session)

        players = await client.get_players("en123")

        self.assertIn(1, players)
        self.assertEqual(players[1].name, "PlayerOne")
        self.assertEqual(players[1].tribe_id, 10)
        self.assertEqual(players[1].villages, 5)
        self.assertEqual(players[1].points, 50000)
        self.assertEqual(players[1].rank, 3)

        self.assertIn(2, players)
        self.assertEqual(players[2].name, "PlayerTwo")

    async def test_get_players_skips_malformed_lines(self):
        bad_data = "1,OnlyOneField\n2,PlayerTwo,0,1,1000,10\n"
        session = MagicMock()
        session.get = MagicMock(return_value=_make_mock_response(_gz(bad_data)))
        client = TribalWarsClient(session)

        players = await client.get_players("en123")
        self.assertNotIn(1, players)
        self.assertIn(2, players)


class TestTribalWarsClientVillages(unittest.IsolatedAsyncioTestCase):
    async def test_get_villages_parses_correctly(self):
        session = MagicMock()
        session.get = MagicMock(return_value=_make_mock_response(_gz(VILLAGE_DATA)))
        client = TribalWarsClient(session)

        villages = await client.get_villages("en123")

        self.assertIn(100, villages)
        v = villages[100]
        self.assertEqual(v.name, "My Village")  # URL-decoded
        self.assertEqual(v.x, 450)
        self.assertEqual(v.y, 500)
        self.assertEqual(v.player_id, 1)
        self.assertEqual(v.points, 3000)

    async def test_get_villages_skips_short_lines(self):
        bad = "100,VillageName,450\n101,Another+Village,200,300,2,500,20\n"
        session = MagicMock()
        session.get = MagicMock(return_value=_make_mock_response(_gz(bad)))
        client = TribalWarsClient(session)

        villages = await client.get_villages("en123")
        self.assertNotIn(100, villages)
        self.assertIn(101, villages)


class TestTribalWarsClientConquers(unittest.IsolatedAsyncioTestCase):
    async def test_get_conquers_since_parses_events(self):
        now = int(time.time())
        raw = f"100,{now},1,0\n101,{now},2,1\n"
        session = MagicMock()
        session.get = MagicMock(return_value=_make_mock_response(raw))
        client = TribalWarsClient(session)

        events = await client.get_conquers_since("en123", now - 600)

        self.assertEqual(len(events), 2)
        e = events[0]
        self.assertEqual(e.village_id, 100)
        self.assertEqual(e.new_owner_id, 1)
        self.assertEqual(e.old_owner_id, 0)

    async def test_get_conquers_since_skips_bad_lines(self):
        raw = "bad,line\n100,9999,1,0\n"
        session = MagicMock()
        session.get = MagicMock(return_value=_make_mock_response(raw))
        client = TribalWarsClient(session)

        events = await client.get_conquers_since("en123", 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].village_id, 100)


class TestResolvePlayerId(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_exact_match(self):
        session = MagicMock()
        session.get = MagicMock(return_value=_make_mock_response(_gz(PLAYER_DATA)))
        client = TribalWarsClient(session)

        pid = await client.resolve_player_id("en123", "PlayerOne")
        self.assertEqual(pid, 1)

    async def test_resolve_case_insensitive(self):
        session = MagicMock()
        session.get = MagicMock(return_value=_make_mock_response(_gz(PLAYER_DATA)))
        client = TribalWarsClient(session)

        pid = await client.resolve_player_id("en123", "playerone")
        self.assertEqual(pid, 1)

    async def test_resolve_returns_none_for_unknown_player(self):
        session = MagicMock()
        session.get = MagicMock(return_value=_make_mock_response(_gz(PLAYER_DATA)))
        client = TribalWarsClient(session)

        pid = await client.resolve_player_id("en123", "NoSuchPlayer")
        self.assertIsNone(pid)


# ---------------------------------------------------------------------------
# WatchRule.matches tests
# ---------------------------------------------------------------------------

class TestWatchRuleMatches(unittest.TestCase):
    def _rule(self, player_id=1, x_min=400, x_max=600, y_min=400, y_max=600):
        return WatchRule(
            id="test-id",
            guild_id=1,
            channel_id=2,
            world="en123",
            player_id=player_id,
            player_name="SomePlayer",
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
        )

    def test_matches_inside_box(self):
        rule = self._rule()
        self.assertTrue(rule.matches(1, 500, 500))

    def test_matches_on_boundary(self):
        rule = self._rule()
        self.assertTrue(rule.matches(1, 400, 400))
        self.assertTrue(rule.matches(1, 600, 600))

    def test_no_match_wrong_player(self):
        rule = self._rule(player_id=1)
        self.assertFalse(rule.matches(2, 500, 500))

    def test_no_match_outside_x(self):
        rule = self._rule()
        self.assertFalse(rule.matches(1, 399, 500))
        self.assertFalse(rule.matches(1, 601, 500))

    def test_no_match_outside_y(self):
        rule = self._rule()
        self.assertFalse(rule.matches(1, 500, 399))
        self.assertFalse(rule.matches(1, 500, 601))


# ---------------------------------------------------------------------------
# RuleStore tests
# ---------------------------------------------------------------------------

class TestRuleStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.rules_path = os.path.join(self.tmpdir, "rules.json")
        self.store = RuleStore(self.rules_path)

    def _add(self, player_name="Player", player_id=1, guild_id=10, channel_id=20):
        return self.store.add(
            guild_id=guild_id,
            channel_id=channel_id,
            world="en123",
            player_id=player_id,
            player_name=player_name,
            x_min=400,
            x_max=600,
            y_min=400,
            y_max=600,
        )

    def test_add_and_list(self):
        rule = self._add()
        rules = self.store.list_for_guild(10)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].id, rule.id)

    def test_remove_existing_rule(self):
        rule = self._add()
        removed = self.store.remove(rule.id)
        self.assertIsNotNone(removed)
        self.assertEqual(removed.id, rule.id)
        self.assertEqual(len(self.store.list_for_guild(10)), 0)

    def test_remove_nonexistent_rule_returns_none(self):
        result = self.store.remove("does-not-exist")
        self.assertIsNone(result)

    def test_list_scoped_to_guild(self):
        self._add(guild_id=10)
        self._add(guild_id=99)
        self.assertEqual(len(self.store.list_for_guild(10)), 1)
        self.assertEqual(len(self.store.list_for_guild(99)), 1)

    def test_worlds_returns_unique_worlds(self):
        self._add(guild_id=10)
        # Add a second rule for the same world
        self.store.add(
            guild_id=10, channel_id=20, world="en123",
            player_id=2, player_name="Other", x_min=0, x_max=100, y_min=0, y_max=100
        )
        self.store.add(
            guild_id=10, channel_id=20, world="en456",
            player_id=3, player_name="Third", x_min=0, x_max=100, y_min=0, y_max=100
        )
        worlds = self.store.worlds()
        self.assertEqual(worlds, {"en123", "en456"})

    def test_persistence_survives_reload(self):
        rule = self._add()
        # Create a new store pointing at the same file
        new_store = RuleStore(self.rules_path)
        rules = new_store.list_for_guild(10)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].id, rule.id)
        self.assertEqual(rules[0].player_name, "Player")

    def test_empty_store_returns_empty_list(self):
        self.assertEqual(self.store.list_for_guild(999), [])

    def test_all_returns_all_rules(self):
        self._add(guild_id=10)
        self._add(guild_id=20)
        self.assertEqual(len(self.store.all()), 2)


if __name__ == "__main__":
    unittest.main()
