"""Tests for warmachine.edge.clv_tracker."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from warmachine.edge.clv_tracker import CLVEntry, CLVStatus, CLVTracker


# ---------------- helpers ----------------


def _tip_off(offset_minutes: int = 0) -> str:
    base = datetime(2026, 4, 23, 19, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(minutes=offset_minutes)).isoformat()


def make_entry(
    ticker: str = "KXNBAPTS-26APR23TEAMX-P1-10",
    entry_price_cents: int = 30,
    side: str = "YES",
    model_prob: float = 0.50,
    edge_at_entry_pp: float = 20.0,
    prop_type: str = "points",
    player: str = "P1",
    line: float = 10.5,
    game_id: str = "G1",
    game_tip_off_utc: str = None,
    entry_time_utc: str = None,
) -> CLVEntry:
    return CLVEntry(
        ticker=ticker,
        entry_time_utc=entry_time_utc or datetime.now(timezone.utc).isoformat(),
        entry_price_cents=entry_price_cents,
        entry_price_dollars=entry_price_cents / 100.0,
        side=side,
        quantity=10,
        model_prob=model_prob,
        edge_at_entry_pp=edge_at_entry_pp,
        prop_type=prop_type,
        player=player,
        line=line,
        game_id=game_id,
        game_tip_off_utc=game_tip_off_utc or _tip_off(),
    )


@pytest.fixture
def tracker(tmp_path):
    return CLVTracker(tmp_path / "clv_log.jsonl")


# =============== Entry recording (1-3) ===============


def test_record_entry_appends_to_log(tracker):
    e1 = make_entry(ticker="A")
    e2 = make_entry(ticker="B")
    tracker.record_entry(e1)
    tracker.record_entry(e2)
    lines = tracker.log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["ticker"] == "A"
    assert json.loads(lines[1])["ticker"] == "B"


def test_entry_gets_unique_id(tracker):
    e1 = make_entry()
    e2 = make_entry()
    assert e1.entry_id != e2.entry_id
    assert len(e1.entry_id) > 0


def test_pydantic_validates_price_range_1_to_99():
    with pytest.raises(ValidationError):
        make_entry(entry_price_cents=0)
    with pytest.raises(ValidationError):
        make_entry(entry_price_cents=100)


# =============== Closing capture (4-8) ===============


def test_record_closing_computes_clv_pp_yes(tracker):
    e = make_entry(entry_price_cents=30, side="YES")
    tracker.record_entry(e)
    tracker.record_closing(e.entry_id, closing_price_cents=35, capture_time_utc=_tip_off(-5))
    loaded = tracker._load_all()[0]
    assert loaded.clv_pp == 5.0
    assert loaded.beat_close is True


def test_record_closing_computes_clv_pp_no(tracker):
    # NO side: we bought NO at 30c; closing at 25c means NO got cheaper (good for us buying NO)
    e = make_entry(entry_price_cents=30, side="NO")
    tracker.record_entry(e)
    tracker.record_closing(e.entry_id, closing_price_cents=25, capture_time_utc=_tip_off(-5))
    loaded = tracker._load_all()[0]
    assert loaded.clv_pp == 5.0
    assert loaded.beat_close is True


def test_record_closing_computes_clv_pct(tracker):
    e = make_entry(entry_price_cents=30, side="YES")
    tracker.record_entry(e)
    tracker.record_closing(e.entry_id, closing_price_cents=36, capture_time_utc=_tip_off(-5))
    loaded = tracker._load_all()[0]
    # 6 / 30 * 100 = 20%
    assert loaded.clv_pct == pytest.approx(20.0)


def test_beat_close_boolean_correct(tracker):
    # Entry at 40, closing at 35 for YES -> we paid MORE than closing (bad) -> beat_close False
    e = make_entry(entry_price_cents=40, side="YES")
    tracker.record_entry(e)
    tracker.record_closing(e.entry_id, closing_price_cents=35, capture_time_utc=_tip_off(-5))
    loaded = tracker._load_all()[0]
    assert loaded.clv_pp == -5.0
    assert loaded.beat_close is False


def test_status_transitions_to_closing_captured(tracker):
    e = make_entry()
    tracker.record_entry(e)
    assert tracker._load_all()[0].status == CLVStatus.PENDING_CLOSE
    tracker.record_closing(e.entry_id, closing_price_cents=35, capture_time_utc=_tip_off(-5))
    assert tracker._load_all()[0].status == CLVStatus.CLOSING_CAPTURED


# =============== Miss handling (9-11) ===============


def test_record_miss_transitions_status(tracker):
    e = make_entry()
    tracker.record_entry(e)
    tracker.record_miss(e.entry_id, "test")
    loaded = tracker._load_all()[0]
    assert loaded.status == CLVStatus.MISSED_CLOSE


def test_get_entries_needing_close_returns_pending_in_window(tracker):
    tip = datetime(2026, 4, 23, 19, 0, 0, tzinfo=timezone.utc)
    e = make_entry(game_tip_off_utc=tip.isoformat())
    tracker.record_entry(e)
    # 3 min before tip-off -> inside window (tip-5 .. tip+15)
    now = tip - timedelta(minutes=3)
    pending = tracker.get_entries_needing_close(now)
    assert len(pending) == 1
    assert pending[0].entry_id == e.entry_id


def test_get_entries_past_window_auto_marked_missed(tracker):
    tip = datetime(2026, 4, 23, 19, 0, 0, tzinfo=timezone.utc)
    e = make_entry(game_tip_off_utc=tip.isoformat())
    tracker.record_entry(e)
    # 30 min past tip-off -> past window_end (tip+15)
    now = tip + timedelta(minutes=30)
    pending = tracker.get_entries_needing_close(now)
    assert pending == []
    assert tracker._load_all()[0].status == CLVStatus.MISSED_CLOSE


# =============== Settlement (12) ===============


def test_record_settlement_populates_outcome_and_pnl(tracker):
    e = make_entry(ticker="T1")
    tracker.record_entry(e)
    tracker.record_closing(e.entry_id, 35, _tip_off(-5))
    tracker.record_settlement("T1", outcome="WIN", pnl=7.0)
    loaded = tracker._load_all()[0]
    assert loaded.outcome == "WIN"
    assert loaded.realized_pnl == 7.0
    assert loaded.status == CLVStatus.SETTLED
    assert loaded.settlement_time_utc is not None


# =============== Stats (13-17) ===============


def test_stats_empty_returns_no_data(tracker):
    s = tracker.stats()
    assert s["n"] == 0
    assert s["status"] == "no_data"


def test_stats_aggregates_correctly(tracker):
    # 3 entries: clv_pp = +5, +3, -2 -> avg 2.0, beat_rate 2/3
    for i, (ep, cp) in enumerate([(30, 35), (40, 43), (50, 48)]):
        e = make_entry(ticker=f"T{i}", entry_price_cents=ep, side="YES")
        tracker.record_entry(e)
        tracker.record_closing(e.entry_id, cp, _tip_off(-5))
    s = tracker.stats()
    assert s["n"] == 3
    assert s["avg_clv_pp"] == pytest.approx(2.0)
    assert s["beat_count"] == 2
    assert s["beat_rate"] == pytest.approx(round(2 / 3, 3))


def test_stats_classifies_strong_edge(tracker):
    # 10 entries, all +5 clv, beat_rate 1.0, avg 5.0 -> STRONG_EDGE (but INSUFFICIENT sample)
    for i in range(10):
        e = make_entry(ticker=f"T{i}", entry_price_cents=30, side="YES")
        tracker.record_entry(e)
        tracker.record_closing(e.entry_id, 35, _tip_off(-5))
    s = tracker.stats()
    assert s["performance"] == "STRONG_EDGE"
    assert s["sample_tier"] == "INSUFFICIENT"


def test_stats_classifies_warning_below_45_beat_rate(tracker):
    # 10 entries, 4 beat, 6 miss -> beat_rate 0.40 < 0.45 = WARNING
    for i in range(4):
        e = make_entry(ticker=f"W{i}", entry_price_cents=30, side="YES")
        tracker.record_entry(e)
        tracker.record_closing(e.entry_id, 35, _tip_off(-5))
    for i in range(6):
        e = make_entry(ticker=f"L{i}", entry_price_cents=40, side="YES")
        tracker.record_entry(e)
        tracker.record_closing(e.entry_id, 35, _tip_off(-5))
    s = tracker.stats()
    assert s["beat_rate"] == pytest.approx(0.4)
    assert s["performance"] == "WARNING"


def test_stats_sample_tier_thresholds(tracker):
    def populate(n: int):
        # Fresh tracker per call
        t = CLVTracker(tracker.log_path.parent / f"clv_{n}.jsonl")
        for i in range(n):
            e = make_entry(ticker=f"T{n}-{i}", entry_price_cents=30, side="YES")
            t.record_entry(e)
            t.record_closing(e.entry_id, 35, _tip_off(-5))
        return t.stats()

    assert populate(49)["sample_tier"] == "INSUFFICIENT"
    assert populate(50)["sample_tier"] == "PRELIMINARY"
    assert populate(200)["sample_tier"] == "MEANINGFUL"
    assert populate(500)["sample_tier"] == "STATISTICAL_CONFIDENCE"


# =============== Breakdowns (18-19) ===============


def test_breakdown_by_prop_type(tracker):
    for i in range(3):
        e = make_entry(ticker=f"P{i}", entry_price_cents=30, side="YES", prop_type="points")
        tracker.record_entry(e)
        tracker.record_closing(e.entry_id, 35, _tip_off(-5))
    for i in range(2):
        e = make_entry(ticker=f"R{i}", entry_price_cents=40, side="YES", prop_type="rebounds")
        tracker.record_entry(e)
        tracker.record_closing(e.entry_id, 38, _tip_off(-5))  # -2 clv, miss
    bd = tracker.breakdown_by_prop_type()
    assert bd["points"]["n"] == 3
    assert bd["points"]["beat_rate"] == 1.0
    assert bd["rebounds"]["n"] == 2
    assert bd["rebounds"]["beat_rate"] == 0.0


def test_breakdown_by_edge_bucket(tracker):
    # One in 15-20, two in 25-30
    for (edge, ticker) in [(17.0, "L"), (26.0, "M1"), (27.5, "M2")]:
        e = make_entry(ticker=ticker, entry_price_cents=30, side="YES", edge_at_entry_pp=edge)
        tracker.record_entry(e)
        tracker.record_closing(e.entry_id, 35, _tip_off(-5))
    bd = tracker.breakdown_by_edge_bucket()
    assert bd["15-20pp"]["n"] == 1
    assert bd["25-30pp"]["n"] == 2
    assert "20-25pp" not in bd
    assert "30pp+" not in bd
