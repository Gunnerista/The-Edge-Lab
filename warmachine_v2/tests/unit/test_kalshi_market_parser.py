"""Tests for warmachine.data.kalshi_market_parser."""

from __future__ import annotations

from datetime import timezone
from unittest import mock

import pytest

from warmachine.data.kalshi_market_parser import (
    KalshiMarketFetcher,
    PropMarket,
    dollars_str_to_cents,
    extract_player_name_from_title,
    parse_event_teams,
    parse_iso_utc,
    parse_market,
)


# ---------------- real probe sample ----------------


REAL_MARKET = {
    "can_close_early": True,
    "close_time": "2026-05-10T17:00:00Z",
    "created_time": "2026-04-24T03:02:30.575706Z",
    "custom_strike": {
        "basketball_player": "fb9a7f9e-8a0c-4e90-94e8-051dea709bef",
        "basketball_team": "96c447dd-2cdd-4c58-a151-ef0df87de981",
    },
    "event_ticker": "KXNBAPTS-26APR26CLETOR",
    "expected_expiration_time": "2026-04-26T20:00:00Z",
    "floor_strike": 9.5,
    "no_ask_dollars": "0.0600",
    "no_bid_dollars": "0.0300",
    "no_sub_title": "Scottie Barnes: 10+",
    "occurrence_datetime": "2026-04-26T20:00:00Z",
    "rules_primary": (
        "If Scottie Barnes records 10+ Points in the Cleveland vs Toronto "
        "professional basketball game originally scheduled for Apr 26, 2026, "
        "then the market resolves to Yes."
    ),
    "status": "active",
    "strike_type": "structured",
    "ticker": "KXNBAPTS-26APR26CLETOR-TORSBARNES4-10",
    "title": "Scottie Barnes: 10+ points",
    "yes_ask_dollars": "0.9700",
    "yes_ask_size_fp": "5.00",
    "yes_bid_dollars": "0.9400",
    "yes_bid_size_fp": "5.00",
    "yes_sub_title": "Scottie Barnes: 10+",
}

REAL_EVENT = {
    "event_ticker": "KXNBAPTS-26APR26CLETOR",
    "series_ticker": "KXNBAPTS",
    "title": "Cleveland at Toronto: Points",
    "sub_title": "CLE vs TOR (Apr 26)",
}


# =================== dollars_str_to_cents (1-6) ===================


def test_dollar_string_converts_correctly():
    assert dollars_str_to_cents("0.9700") == 97
    assert dollars_str_to_cents("0.0600") == 6
    assert dollars_str_to_cents("0.5000") == 50


def test_none_returns_zero():
    assert dollars_str_to_cents(None) == 0


def test_empty_string_returns_zero():
    assert dollars_str_to_cents("") == 0
    assert dollars_str_to_cents("   ") == 0


def test_int_cents_passthrough():
    assert dollars_str_to_cents(99) == 99
    assert dollars_str_to_cents(1) == 1
    assert dollars_str_to_cents(50) == 50


def test_float_fraction_converts():
    assert dollars_str_to_cents(0.35) == 35
    assert dollars_str_to_cents(0.97) == 97


def test_invalid_string_returns_zero():
    assert dollars_str_to_cents("abc") == 0
    assert dollars_str_to_cents("not a number") == 0


# =================== parse_iso_utc (7-9) ===================


def test_z_suffix_parsed_as_utc():
    dt = parse_iso_utc("2026-04-26T20:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0
    assert dt.year == 2026 and dt.month == 4 and dt.day == 26
    assert dt.hour == 20


def test_none_returns_none():
    assert parse_iso_utc(None) is None
    assert parse_iso_utc("") is None


def test_invalid_returns_none():
    assert parse_iso_utc("not a date") is None
    assert parse_iso_utc("2026-13-40T99:99:99Z") is None


# =================== extract_player_name_from_title (10-12) ===================


def test_standard_title_extracts_name():
    assert (
        extract_player_name_from_title("Scottie Barnes: 10+ points")
        == "Scottie Barnes"
    )
    assert extract_player_name_from_title("LeBron James: 25+ points") == "LeBron James"


def test_no_colon_returns_none():
    assert extract_player_name_from_title("just a title") is None


def test_empty_returns_none():
    assert extract_player_name_from_title("") is None
    assert extract_player_name_from_title(None) is None


# =================== parse_event_teams (13-16) ===================


def test_vs_format_extracts_both():
    assert parse_event_teams("CLE vs TOR (Apr 26)") == ("CLE", "TOR")
    assert parse_event_teams("LAL vs GSW") == ("LAL", "GSW")


def test_at_format_extracts_both():
    assert parse_event_teams("CLE @ TOR") == ("CLE", "TOR")
    assert parse_event_teams("CLE at TOR") == ("CLE", "TOR")


def test_empty_returns_none_tuple():
    assert parse_event_teams("") == (None, None)
    assert parse_event_teams(None) == (None, None)


def test_no_match_returns_none_tuple():
    assert parse_event_teams("some random string") == (None, None)
    assert parse_event_teams("no teams here") == (None, None)


# =================== parse_market (17-20) ===================


def test_valid_market_returns_propmarket():
    pm = parse_market(REAL_MARKET, REAL_EVENT)
    assert pm is not None
    assert isinstance(pm, PropMarket)
    assert pm.ticker == "KXNBAPTS-26APR26CLETOR-TORSBARNES4-10"
    assert pm.event_ticker == "KXNBAPTS-26APR26CLETOR"
    assert pm.prop_type == "points"
    assert pm.player_uuid == "fb9a7f9e-8a0c-4e90-94e8-051dea709bef"
    assert pm.team_uuid == "96c447dd-2cdd-4c58-a151-ef0df87de981"
    assert pm.player_name == "Scottie Barnes"
    assert pm.line == 9.5
    assert pm.is_over is True
    assert pm.yes_ask_cents == 97
    assert pm.yes_bid_cents == 94
    assert pm.no_ask_cents == 6
    assert pm.no_bid_cents == 3
    assert pm.yes_ask_depth == 5
    assert pm.tip_off_utc.year == 2026 and pm.tip_off_utc.month == 4
    assert pm.market_close_utc.tzinfo is not None


def test_missing_floor_strike_returns_none():
    bad = dict(REAL_MARKET)
    del bad["floor_strike"]
    assert parse_market(bad) is None


def test_missing_player_uuid_returns_none():
    bad = dict(REAL_MARKET)
    bad["custom_strike"] = {}
    assert parse_market(bad) is None


def test_non_nba_series_returns_none():
    bad = dict(REAL_MARKET)
    bad["event_ticker"] = "KXSOME-OTHER-EVENT"
    assert parse_market(bad) is None


# =================== KalshiMarketFetcher (21-22) ===================


def _events_resp(events: list, cursor=None) -> dict:
    return {"events": events, "cursor": cursor}


def test_fetch_aggregates_4_series():
    client = mock.MagicMock()

    def build_event(series: str):
        market = dict(REAL_MARKET)
        market["event_ticker"] = f"{series}-26APR26CLETOR"
        return {
            "event_ticker": f"{series}-26APR26CLETOR",
            "sub_title": "CLE vs TOR (Apr 26)",
            "markets": [market],
        }

    def side_effect(**kwargs):
        series = kwargs["series_ticker"]
        return _events_resp([build_event(series)])

    client.get_events.side_effect = side_effect

    fetcher = KalshiMarketFetcher(client)
    markets = fetcher.fetch_all_open_nba_prop_markets()

    # 4 series -> 1 event per series, 1 market per event = 4 markets
    assert len(markets) == 4
    assert client.get_events.call_count == 4


def test_fetch_pagination_respects_cursor():
    client = mock.MagicMock()

    call_count = [0]

    def side_effect(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            event = {
                "event_ticker": "KXNBAPTS-X",
                "sub_title": "LAL vs BOS",
                "markets": [REAL_MARKET],
            }
            return _events_resp([event], cursor="PAGE2")
        if call_count[0] == 2:
            return _events_resp([], cursor=None)
        return _events_resp([], cursor=None)

    client.get_events.side_effect = side_effect

    fetcher = KalshiMarketFetcher(client)
    markets = fetcher.fetch_all_open_nba_prop_markets()

    # First series made 2 calls (page 1 + page 2); 3 other series = 3 calls = 5 total
    assert client.get_events.call_count == 5
    assert len(markets) == 1
