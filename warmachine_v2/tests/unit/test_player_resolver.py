"""Tests for warmachine.data.player_resolver."""

from __future__ import annotations

from unittest import mock

import pytest

from warmachine.data.player_resolver import (
    PlayerResolution,
    PlayerResolver,
    TeamResolution,
    normalize_name,
)


# ---------------- helpers ----------------


def _make_resolver(
    player_fetchone=None,
    players_fetchall=None,
    team_fetchone=None,
    extra_fetchone=None,
    extra_fetchall=None,
):
    """
    Build a resolver with a fully-controlled mock DB.
    player_fetchone: result for exact-name lookup.
    players_fetchall: result for _db_all_player_names().
    team_fetchone: result for team lookup.
    extra_fetchone / extra_fetchall: additional results for later calls.
    """
    db = mock.MagicMock()
    cursor = mock.MagicMock()
    db.cursor.return_value = cursor

    # Build fetchall side effects:
    # __init__.preload: 2 fetchalls (empty cache)
    # + optional players_fetchall for _db_all_player_names
    # + any additional
    fetchall_seq = [[], []]
    if players_fetchall is not None:
        fetchall_seq.append(players_fetchall)
    if extra_fetchall is not None:
        fetchall_seq.extend(extra_fetchall)
    cursor.fetchall.side_effect = fetchall_seq

    # Build fetchone side effects:
    fetchone_seq = []
    if player_fetchone is not None:
        fetchone_seq.append(player_fetchone)
    if team_fetchone is not None:
        fetchone_seq.append(team_fetchone)
    if extra_fetchone is not None:
        fetchone_seq.extend(extra_fetchone)
    cursor.fetchone.side_effect = fetchone_seq if fetchone_seq else [None]

    resolver = PlayerResolver(db)
    return resolver, db, cursor


def _executed_sqls(cursor) -> list[str]:
    return [
        call.args[0] if call.args else ""
        for call in cursor.execute.call_args_list
    ]


# =================== normalize_name (1-8) ===================


def test_lowercase():
    assert normalize_name("Scottie Barnes") == "scottie barnes"


def test_strip_accents():
    assert normalize_name("Nikola Jokić") == "nikola jokic"


def test_strip_apostrophe():
    assert normalize_name("D'Angelo Russell") == "dangelo russell"


def test_strip_period():
    assert normalize_name("C.J. McCollum") == "cj mccollum"


def test_strip_jr_suffix():
    assert normalize_name("Kelly Oubre Jr.") == "kelly oubre"


def test_strip_iii_suffix():
    assert normalize_name("Horace Spencer III") == "horace spencer"


def test_collapse_whitespace():
    assert normalize_name("  John   Doe  ") == "john doe"


def test_empty_returns_empty():
    assert normalize_name("") == ""
    assert normalize_name(None) == ""  # type: ignore[arg-type]


# =================== Player resolution (9-17) ===================


def test_cache_hit_returns_cached():
    resolver, db, cursor = _make_resolver()
    # Prepopulate memory cache
    pre = PlayerResolution(
        kalshi_uuid="U1",
        nba_player_id=1234,
        player_name="Scottie Barnes",
        method="exact",
        confidence=1.0,
    )
    resolver._player_cache["U1"] = pre
    cursor.reset_mock()

    result = resolver.resolve_player("U1", "Scottie Barnes")

    assert result is pre
    assert cursor.execute.call_count == 0  # no DB query on cache hit


def test_exact_name_match_sets_method_exact():
    resolver, db, cursor = _make_resolver(
        player_fetchone=(1630567, "Scottie Barnes"),
    )
    result = resolver.resolve_player("U1", "Scottie Barnes")
    assert result is not None
    assert result.method == "exact"
    assert result.confidence == 1.0
    assert result.nba_player_id == 1630567
    assert result.player_name == "Scottie Barnes"


def test_exact_match_case_insensitive():
    # DB returns canonical casing even when hint is upper
    resolver, db, cursor = _make_resolver(
        player_fetchone=(1630567, "Scottie Barnes"),
    )
    result = resolver.resolve_player("U1", "SCOTTIE BARNES")
    assert result is not None
    assert result.method == "exact"
    assert result.player_name == "Scottie Barnes"


def test_normalized_accent_match():
    # Exact lookup misses (different spelling), normalized matches
    resolver, db, cursor = _make_resolver(
        player_fetchone=None,  # exact miss
        players_fetchall=[(203999, "Nikola Jokić")],
    )
    result = resolver.resolve_player("U1", "Nikola Jokic")
    assert result is not None
    assert result.method == "normalized"
    assert result.confidence == 0.95
    assert result.player_name == "Nikola Jokić"


def test_normalized_apostrophe_match():
    resolver, db, cursor = _make_resolver(
        player_fetchone=None,
        players_fetchall=[(1626156, "D'Angelo Russell")],
    )
    result = resolver.resolve_player("U1", "DAngelo Russell")
    assert result is not None
    assert result.method == "normalized"
    assert result.player_name == "D'Angelo Russell"


def test_fuzzy_match_above_cutoff():
    resolver, db, cursor = _make_resolver(
        player_fetchone=None,
        players_fetchall=[(1630567, "Scottie Barnes")],
    )
    # "Scottie Barns" (missing 'e') normalized vs "scottie barnes" -> ratio ~0.96
    result = resolver.resolve_player("U1", "Scottie Barns")
    assert result is not None
    assert result.method == "fuzzy"
    assert result.confidence >= 0.90
    assert result.player_name == "Scottie Barnes"
    assert result.nba_player_id == 1630567


def test_no_match_below_cutoff_returns_none():
    resolver, db, cursor = _make_resolver(
        player_fetchone=None,
        players_fetchall=[(1630567, "Scottie Barnes")],
    )
    result = resolver.resolve_player("U1", "Completely Different Name")
    assert result is None


def test_resolution_persisted_to_db():
    resolver, db, cursor = _make_resolver(
        player_fetchone=(1630567, "Scottie Barnes"),
    )
    resolver.resolve_player("U1", "Scottie Barnes")
    # Check that an INSERT INTO kalshi_player_map was executed
    inserts = [
        sql for sql in _executed_sqls(cursor)
        if "INSERT INTO kalshi_player_map" in sql
    ]
    assert len(inserts) == 1
    assert db.commit.called


def test_second_resolution_same_uuid_uses_cache():
    resolver, db, cursor = _make_resolver(
        player_fetchone=(1630567, "Scottie Barnes"),
    )
    resolver.resolve_player("U1", "Scottie Barnes")
    execute_count_after_first = cursor.execute.call_count

    result = resolver.resolve_player("U1", "Scottie Barnes")

    assert result is not None
    assert result.method == "exact"  # from cache
    assert cursor.execute.call_count == execute_count_after_first  # no new SQL


# =================== Team resolution (18-20) ===================


def test_team_exact_abbr_match():
    resolver, db, cursor = _make_resolver(
        team_fetchone=(1610612754, "CLE"),
    )
    result = resolver.resolve_team("TEAM_UUID_1", "CLE")
    assert result is not None
    assert result.nba_team_id == 1610612754
    assert result.team_abbr == "CLE"


def test_team_case_insensitive():
    resolver, db, cursor = _make_resolver(
        team_fetchone=(1610612754, "CLE"),
    )
    result = resolver.resolve_team("TEAM_UUID_1", "cle")
    assert result is not None
    assert result.team_abbr == "CLE"


def test_team_cache_hit():
    resolver, db, cursor = _make_resolver()
    pre = TeamResolution(
        kalshi_uuid="TEAM_UUID_1", nba_team_id=1610612754, team_abbr="CLE"
    )
    resolver._team_cache["TEAM_UUID_1"] = pre
    cursor.reset_mock()

    result = resolver.resolve_team("TEAM_UUID_1", "CLE")
    assert result is pre
    assert cursor.execute.call_count == 0


# =================== cache_stats (21) ===================


def test_cache_stats_counts_by_method():
    resolver, db, cursor = _make_resolver()
    resolver._player_cache = {
        "u1": PlayerResolution(
            kalshi_uuid="u1", nba_player_id=1, player_name="A",
            method="exact", confidence=1.0,
        ),
        "u2": PlayerResolution(
            kalshi_uuid="u2", nba_player_id=2, player_name="B",
            method="exact", confidence=1.0,
        ),
        "u3": PlayerResolution(
            kalshi_uuid="u3", nba_player_id=3, player_name="C",
            method="normalized", confidence=0.95,
        ),
        "u4": PlayerResolution(
            kalshi_uuid="u4", nba_player_id=4, player_name="D",
            method="fuzzy", confidence=0.93,
        ),
    }
    resolver._team_cache = {
        "t1": TeamResolution(kalshi_uuid="t1", nba_team_id=10, team_abbr="CLE"),
    }

    stats = resolver.cache_stats()
    assert stats["player_cache_size"] == 4
    assert stats["team_cache_size"] == 1
    assert stats["methods"]["exact"] == 2
    assert stats["methods"]["normalized"] == 1
    assert stats["methods"]["fuzzy"] == 1
    assert stats["methods"]["cache"] == 0
