"""
Module: config.py
Purpose: Centralized settings via pydantic-settings.
         Priority: env > .env > TOML > defaults.
         Env prefix: WM_, nested delimiter: __ (e.g. WM_RUNTIME__MODE).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class RuntimeConfig(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    poll_interval_seconds: int = 60
    max_markets_per_cycle: int = 50


class KellyTOMLConfig(BaseModel):
    fractional_multiplier: float = 0.25
    single_trade_cap_pct: float = 0.03
    min_contracts: int = 1


class TradingConfig(BaseModel):
    global_min_edge_pp: float = 15.0
    yes_edge_premium_pp: float = 5.0
    max_edge_cap_pp: float = 60.0
    kelly: KellyTOMLConfig = Field(default_factory=KellyTOMLConfig)


class PaperConfig(BaseModel):
    initial_balance_cents: int = 28061
    slippage_cents: int = 1


class ExposureTOMLConfig(BaseModel):
    per_ticker_pct: float = 0.03
    per_game_pct: float = 0.08
    per_player_pct: float = 0.05
    per_prop_type_daily_pct: float = 0.15
    total_open_pct: float = 0.30


class BankruptcyTOMLConfig(BaseModel):
    floor_dollars: float = 100.0
    cooldown_days: int = 14


class RiskTOMLConfig(BaseModel):
    exposure: ExposureTOMLConfig = Field(default_factory=ExposureTOMLConfig)
    bankruptcy: BankruptcyTOMLConfig = Field(default_factory=BankruptcyTOMLConfig)


class PathsConfig(BaseModel):
    data_dir: str = "data"
    state_file: str = "data/v2_session_state.json"
    clv_log: str = "data/v2_clv_log.jsonl"
    paper_state: str = "data/v2_paper_state.json"
    kill_switch: str = "data/STOP"
    heartbeat: str = "data/v2_heartbeat.txt"


class DatabaseConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    dbname: str = "warmachine"
    user: str = "postgres"
    password: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        toml_file="config/settings.toml",
        env_file=".env",
        env_prefix="WM_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    risk: RiskTOMLConfig = Field(default_factory=RiskTOMLConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

    kalshi_key_id: str = ""
    kalshi_private_key_path: str = ""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


def load_settings(toml_path: Optional[Path] = None) -> Settings:
    """Factory. Optional TOML path override for tests."""
    if toml_path is not None:
        class _S(Settings):
            model_config = SettingsConfigDict(
                toml_file=str(toml_path),
                env_file=".env",
                env_prefix="WM_",
                env_nested_delimiter="__",
                extra="ignore",
            )

        return _S()
    return Settings()
