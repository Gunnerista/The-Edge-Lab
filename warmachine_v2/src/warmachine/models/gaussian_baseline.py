"""
Module: gaussian_baseline.py
Purpose: Gaussian baseline - direct port of v1 nba_model.py with Platt v2 calibration.

Formula:
  projected = 0.20*season + 0.30*recent10 + 0.35*recent5 + 0.15*ha_split
  projected *= (1 + matchup_adj + b2b_adj + pace_adj + blowout_adj)
  std = max(recent_std, floor) * (1.15 if b2b) * (1.20 if gp<20)
  P(X > line) = 1 - PHI((line - projected) / std)
  P_cal = sigmoid(0.6759 * logit(P) - 0.3489)   # Platt v2
  if |P_cal - market| > 0.20: shrink 5% toward market
"""

from __future__ import annotations

import math
from typing import Optional

from pydantic import BaseModel, ConfigDict

from warmachine.models.base import BaseProbModel, ModelInput, ModelOutput
from warmachine.utils.logger import get_logger

log = get_logger(__name__)


PLATT_A = 0.6759
PLATT_B = -0.3489

WEIGHT_SEASON = 0.20
WEIGHT_RECENT_10 = 0.30
WEIGHT_RECENT_5 = 0.35
WEIGHT_HOME_AWAY = 0.15

STD_FLOOR = {"points": 4.0, "rebounds": 2.0, "assists": 1.5, "threes": 1.2}

B2B_PENALTY = -0.08
BLOWOUT_BASE = -0.07
BLOWOUT_PER_POINT = -0.005
BLOWOUT_MAX = -0.15
MATCHUP_MAX = 0.20
PACE_MULTIPLIER = 0.5

EDGE_SHRINK_THRESHOLD = 0.20
EDGE_SHRINK_FACTOR = 0.05


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float, eps: float = 1e-6) -> float:
    p = max(eps, min(1 - eps, p))
    return math.log(p / (1 - p))


def _norm_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2))


GAUSSIAN_FEATURES = [
    "season_avg",
    "recent_10_avg",
    "recent_5_avg",
    "home_away_avg",
    "recent_std",
    "opp_allowed",
    "league_avg",
    "opp_pace",
    "league_pace",
    "is_b2b",
    "games_played",
    "spread",
    "line",
]


class GaussianConfig(BaseModel):
    model_config = ConfigDict(frozen=True, protected_namespaces=())
    version: str = "gaussian_v1_platt_v2"
    prop_type: str
    use_platt: bool = True
    use_edge_shrinkage: bool = True


class GaussianBaseline(BaseProbModel):
    def __init__(self, config: GaussianConfig):
        self.config = config

    @property
    def model_version(self) -> str:
        return f"{self.config.version}_{self.config.prop_type}"

    def predict(
        self,
        input: ModelInput,
        market_prob_yes: Optional[float] = None,
    ) -> ModelOutput:
        self._validate_features(input.features)
        f = input.features

        base = self._compute_base_projection(f)
        adj = self._compute_adjustments(f)
        projected = base * (1 + adj["total"])
        std = self._compute_std(f)

        line = f["line"]
        if std <= 0:
            raw = 0.5
        else:
            z = (line - projected) / std
            raw = 1.0 - _norm_cdf(z)
        raw = max(0.01, min(0.99, raw))

        if self.config.use_platt:
            calibrated = _sigmoid(PLATT_A * _logit(raw) + PLATT_B)
        else:
            calibrated = raw

        final = calibrated
        if (
            self.config.use_edge_shrinkage
            and market_prob_yes is not None
            and abs(calibrated - market_prob_yes) > EDGE_SHRINK_THRESHOLD
        ):
            final = calibrated - EDGE_SHRINK_FACTOR * (calibrated - market_prob_yes)

        final = max(0.01, min(0.99, final))

        return ModelOutput(
            raw_probability=raw,
            calibrated_probability=final,
            model_version=self.model_version,
            features_used={k: f[k] for k in GAUSSIAN_FEATURES if k in f},
        )

    def batch_predict(self, inputs: list[ModelInput]) -> list[ModelOutput]:
        return [self.predict(i) for i in inputs]

    def _validate_features(self, features: dict) -> None:
        missing = set(GAUSSIAN_FEATURES) - set(features.keys())
        if missing:
            raise ValueError(f"Missing Gaussian features: {missing}")

    def _compute_base_projection(self, f: dict) -> float:
        return (
            WEIGHT_SEASON * f["season_avg"]
            + WEIGHT_RECENT_10 * f["recent_10_avg"]
            + WEIGHT_RECENT_5 * f["recent_5_avg"]
            + WEIGHT_HOME_AWAY * f["home_away_avg"]
        )

    def _compute_adjustments(self, f: dict) -> dict:
        league = f["league_avg"] if f["league_avg"] > 0 else 1.0
        matchup_raw = (f["opp_allowed"] / league) - 1.0
        matchup_adj = max(-MATCHUP_MAX, min(MATCHUP_MAX, matchup_raw))

        b2b_adj = B2B_PENALTY if f["is_b2b"] >= 1 else 0.0

        pace_league = f["league_pace"] if f["league_pace"] > 0 else 1.0
        pace_adj = ((f["opp_pace"] / pace_league) - 1.0) * PACE_MULTIPLIER

        spread = abs(f["spread"])
        if spread >= 10:
            blow = BLOWOUT_BASE + (spread - 10) * BLOWOUT_PER_POINT
            blowout_adj = max(BLOWOUT_MAX, blow)
        else:
            blowout_adj = 0.0

        return {
            "matchup": matchup_adj,
            "b2b": b2b_adj,
            "pace": pace_adj,
            "blowout": blowout_adj,
            "total": matchup_adj + b2b_adj + pace_adj + blowout_adj,
        }

    def _compute_std(self, f: dict) -> float:
        floor = STD_FLOOR.get(self.config.prop_type, 2.0)
        std = max(f["recent_std"], floor)
        if f["is_b2b"] >= 1:
            std *= 1.15
        if f["games_played"] < 20:
            std *= 1.20
        return std
