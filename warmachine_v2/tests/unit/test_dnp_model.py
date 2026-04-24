"""Tests for warmachine.models.dnp_model."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from warmachine.models.base import ModelInput, ModelOutput
from warmachine.models.dnp_model import DNP_FEATURES, DNPModel


# ---------------- helpers ----------------


def base_features(**overrides) -> dict[str, float]:
    """Healthy rotation player defaults. Override for edge cases."""
    f = {
        "minutes_per_game_last_5": 12.0,
        "minutes_per_game_last_10": 13.0,
        "minutes_per_game_season": 14.0,
        "games_played_pct_last_10": 1.0,
        "consecutive_games_played": 10,
        "consecutive_games_missed": 0,
        "is_back_to_back": 0,
        "days_since_last_game": 2,
        "age": 26,
        "has_injury_report_flag": 0,
        "injury_status_severity": 0,
        "season_dnp_rate": 0.05,
        "rotation_tier": 2,
        "is_rest_candidate": 0,
        "playoff_elimination_game": 0,
    }
    f.update(overrides)
    return f


def make_input(**overrides) -> ModelInput:
    return ModelInput(
        player_id="P1",
        game_id="G1",
        features=base_features(**overrides),
    )


@pytest.fixture
def model():
    return DNPModel(artifact_path=None)


# =============== Interface (1-4) ===============


def test_model_version_returns_string(model):
    assert isinstance(model.model_version, str)
    assert len(model.model_version) > 0


def test_predict_returns_model_output(model):
    out = model.predict(make_input())
    assert isinstance(out, ModelOutput)
    assert 0.0 <= out.calibrated_probability <= 1.0
    assert len(out.features_used) == len(DNP_FEATURES)


def test_predict_missing_features_raises(model):
    features = base_features()
    del features["age"]
    inp = ModelInput(player_id="P", game_id="G", features=features)
    with pytest.raises(ValueError, match="Missing features"):
        model.predict(inp)


def test_batch_predict_matches_individual(model):
    i1 = make_input(injury_status_severity=0, rotation_tier=3)
    i2 = make_input(injury_status_severity=4)
    batch = model.batch_predict([i1, i2])
    solo = [model.predict(i1), model.predict(i2)]
    assert len(batch) == 2
    assert batch[0].calibrated_probability == solo[0].calibrated_probability
    assert batch[1].calibrated_probability == solo[1].calibrated_probability


# =============== Rule-based fallback (5-12) ===============


def test_injury_out_returns_very_low_prob(model):
    out = model.predict(make_input(injury_status_severity=4))
    assert out.calibrated_probability == pytest.approx(0.05)


def test_injury_doubtful_returns_low_prob(model):
    out = model.predict(make_input(injury_status_severity=3))
    assert out.calibrated_probability == pytest.approx(0.30)


def test_questionable_returns_medium_prob(model):
    out = model.predict(make_input(injury_status_severity=2))
    assert out.calibrated_probability == pytest.approx(0.65)


def test_probable_returns_high_prob(model):
    out = model.predict(make_input(injury_status_severity=1))
    assert out.calibrated_probability == pytest.approx(0.85)


def test_healthy_starter_returns_very_high(model):
    out = model.predict(
        make_input(
            injury_status_severity=0,
            rotation_tier=3,
            minutes_per_game_last_5=32.0,
        )
    )
    assert out.calibrated_probability >= 0.90


def test_deep_bench_returns_uncertain(model):
    # Deep bench, low recent minutes
    out_low = model.predict(
        make_input(rotation_tier=0, minutes_per_game_last_5=4.0)
    )
    assert 0.30 <= out_low.calibrated_probability <= 0.45

    # Deep bench, higher recent minutes
    out_high = model.predict(
        make_input(rotation_tier=0, minutes_per_game_last_5=12.0)
    )
    assert 0.55 <= out_high.calibrated_probability <= 0.65


def test_b2b_rest_candidate_lowers_prob(model):
    out = model.predict(
        make_input(
            is_back_to_back=1,
            is_rest_candidate=1,
            age=36,
            injury_status_severity=0,
            rotation_tier=3,
        )
    )
    assert out.calibrated_probability == pytest.approx(0.55)


def test_recently_returning_from_injury(model):
    out = model.predict(
        make_input(
            consecutive_games_missed=5,
            injury_status_severity=1,
            rotation_tier=3,
        )
    )
    assert out.calibrated_probability == pytest.approx(0.50)


# =============== Clipping (13-14) ===============


def test_probability_clipped_to_0_01_min(model):
    with mock.patch.object(model, "_rule_based_fallback", return_value=-0.5):
        out = model.predict(make_input())
    assert out.calibrated_probability == pytest.approx(0.01)
    assert out.raw_probability == pytest.approx(0.01)


def test_probability_clipped_to_0_99_max(model):
    with mock.patch.object(model, "_rule_based_fallback", return_value=1.5):
        out = model.predict(make_input())
    assert out.calibrated_probability == pytest.approx(0.99)
    assert out.raw_probability == pytest.approx(0.99)


# =============== Prop integration (15-16) ===============


def test_adjust_prop_probability_multiplies_correctly(model):
    # healthy, rotation=2, mpg_recent<10 -> fallback returns 0.90
    feats = base_features(
        injury_status_severity=0,
        rotation_tier=2,
        minutes_per_game_last_5=8.0,
    )
    p_final = model.adjust_prop_probability(
        prop_prob_given_plays=0.70, dnp_features=feats
    )
    assert p_final == pytest.approx(0.70 * 0.90)


def test_adjust_prop_zero_when_out(model):
    feats = base_features(injury_status_severity=4)
    p_final = model.adjust_prop_probability(
        prop_prob_given_plays=0.70, dnp_features=feats
    )
    assert p_final == pytest.approx(0.70 * 0.05)


# =============== Artifact loading (17-18) ===============


def test_missing_artifact_falls_back_to_stub(tmp_path):
    m = DNPModel(artifact_path=tmp_path / "does_not_exist.pkl")
    assert m._loaded is False
    out = m.predict(make_input(injury_status_severity=4))
    assert out.calibrated_probability == pytest.approx(0.05)


def test_corrupt_artifact_logs_error_falls_back(tmp_path, caplog):
    bad = tmp_path / "corrupt.pkl"
    bad.write_bytes(b"not a real pickle")
    import logging
    with caplog.at_level(logging.ERROR):
        m = DNPModel(artifact_path=bad)
    assert m._loaded is False
    assert any("Failed to load DNP artifact" in r.message for r in caplog.records)
    # still works via stub
    out = m.predict(make_input(injury_status_severity=4))
    assert out.calibrated_probability == pytest.approx(0.05)
