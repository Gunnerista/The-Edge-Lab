"""
Module: base.py
Purpose: Abstract base interface for probability models.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict


class ModelInput(BaseModel):
    """Per-player-per-game feature vector."""

    model_config = ConfigDict(protected_namespaces=())

    player_id: str
    game_id: str
    features: dict[str, float]


class ModelOutput(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    raw_probability: float
    calibrated_probability: float
    model_version: str
    features_used: dict[str, float]


class BaseProbModel(ABC):
    """All probability models implement this interface."""

    @abstractmethod
    def predict(self, input: ModelInput) -> ModelOutput: ...

    @abstractmethod
    def batch_predict(self, inputs: list[ModelInput]) -> list[ModelOutput]: ...

    @property
    @abstractmethod
    def model_version(self) -> str: ...
