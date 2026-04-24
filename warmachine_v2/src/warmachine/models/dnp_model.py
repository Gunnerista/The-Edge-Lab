"""
Module: dnp_model.py
Purpose: DNP (Did Not Play) probability model.
         Target: P(player plays >= 5 minutes).
         Research: Cohan 2021 METIC (features), Sarlis 2021 (DNP definition), XGBoost.

Kalshi-specific:
- DNP -> NO settles at $1.00
- Market prices DNP risk into YES
- Our edge: faster/more accurate DNP prob than the market
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict

from warmachine.models.base import BaseProbModel, ModelInput, ModelOutput
from warmachine.utils.logger import get_logger

log = get_logger(__name__)


DNP_FEATURES = [
    "minutes_per_game_last_5",
    "minutes_per_game_last_10",
    "minutes_per_game_season",
    "games_played_pct_last_10",
    "consecutive_games_played",
    "consecutive_games_missed",
    "is_back_to_back",
    "days_since_last_game",
    "age",
    "has_injury_report_flag",
    "injury_status_severity",
    "season_dnp_rate",
    "rotation_tier",
    "is_rest_candidate",
    "playoff_elimination_game",
]


class DNPModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    version: str = "v1_stub"
    target_definition: str = "minutes_played >= 5"
    feature_names: tuple[str, ...] = tuple(DNP_FEATURES)

    fallback_plays_probability: float = 0.95
    fallback_plays_probability_injured: float = 0.40


class DNPModel(BaseProbModel):
    """DNP probability model. Loads XGBoost+Platt artifact when present, else rule-based stub."""

    def __init__(
        self,
        artifact_path: Optional[Path] = None,
        config: Optional[DNPModelConfig] = None,
    ):
        self.config = config or DNPModelConfig()
        self.artifact_path = artifact_path
        self._model = None
        self._calibrator = None
        self._loaded = False

        if artifact_path and Path(artifact_path).exists():
            self._load_artifact()
        else:
            log.warning(
                f"DNP model artifact not found at {artifact_path}. "
                f"Using rule-based stub."
            )

    def _load_artifact(self) -> None:
        try:
            with Path(self.artifact_path).open("rb") as f:
                artifact = pickle.load(f)
            self._model = artifact["model"]
            self._calibrator = artifact["calibrator"]
            self._loaded = True
            log.info(f"Loaded DNP model artifact: {Path(self.artifact_path).name}")
        except Exception as e:
            log.error(f"Failed to load DNP artifact: {e}")

    @property
    def model_version(self) -> str:
        return self.config.version

    def predict(self, input: ModelInput) -> ModelOutput:
        self._validate_features(input)

        if self._loaded:
            feature_vec = [input.features[k] for k in self.config.feature_names]
            raw = float(self._model.predict_proba([feature_vec])[0][1])
            calibrated = float(self._calibrator.predict([raw])[0])
        else:
            raw = self._rule_based_fallback(input.features)
            calibrated = raw

        raw = max(0.01, min(0.99, raw))
        calibrated = max(0.01, min(0.99, calibrated))

        return ModelOutput(
            raw_probability=raw,
            calibrated_probability=calibrated,
            model_version=self.config.version,
            features_used={k: input.features[k] for k in self.config.feature_names},
        )

    def batch_predict(self, inputs: list[ModelInput]) -> list[ModelOutput]:
        return [self.predict(i) for i in inputs]

    def _validate_features(self, input: ModelInput) -> None:
        missing = set(self.config.feature_names) - set(input.features.keys())
        if missing:
            raise ValueError(f"Missing features: {missing}")

    def _rule_based_fallback(self, f: dict[str, float]) -> float:
        """Conservative rule-based stub until trained XGBoost replaces this."""
        injury = f["injury_status_severity"]
        rotation = f["rotation_tier"]
        is_b2b = f["is_back_to_back"]
        age = f["age"]
        consecutive_missed = f["consecutive_games_missed"]
        mpg_recent = f["minutes_per_game_last_5"]
        rest_candidate = f["is_rest_candidate"]

        if injury >= 4:
            return 0.05
        if injury >= 3:
            return 0.30

        if consecutive_missed >= 3 and injury >= 1:
            return 0.50

        if rest_candidate >= 1 and is_b2b >= 1 and age >= 34:
            return 0.55

        if rotation <= 0:
            return 0.60 if mpg_recent >= 8 else 0.35

        if injury >= 2:
            return 0.65

        if injury >= 1:
            return 0.85

        if rotation >= 2:
            return 0.96 if mpg_recent >= 10 else 0.90
        return 0.75

    def adjust_prop_probability(
        self,
        prop_prob_given_plays: float,
        dnp_features: dict[str, float],
    ) -> float:
        """P_final(prop) = P_prop(prop | plays) * P(plays)."""
        model_input = ModelInput(
            player_id="",
            game_id="",
            features=dnp_features,
        )
        p_plays = self.predict(model_input).calibrated_probability
        return prop_prob_given_plays * p_plays
