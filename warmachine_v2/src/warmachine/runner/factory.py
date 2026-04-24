"""
Module: factory.py
Purpose: build_runner(mode) -> fully wired WarMachineRunner.
         Single place to change any wiring decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import psycopg2

from warmachine.core.config import Settings, load_settings
from warmachine.core.kalshi_client import create_client as create_v1_kalshi_client
from warmachine.core.session_state import SessionState
from warmachine.core.state import WarMachineState
from warmachine.data.kalshi_market_parser import KalshiMarketFetcher
from warmachine.data.player_resolver import PlayerResolver
from warmachine.data.v1_bridge import V1DataBridge
from warmachine.edge.clv_tracker import CLVTracker
from warmachine.edge.kelly_sizer import KellyConfig as KellySizerConfig
from warmachine.edge.kelly_sizer import KellySizer
from warmachine.models.dnp_model import DNPModel
from warmachine.models.gaussian_baseline import GaussianBaseline, GaussianConfig
from warmachine.risk.engine import RiskEngine
from warmachine.risk.gate1_kill_switch import KillSwitchGate
from warmachine.risk.gate2_exposure import ExposureGate, ExposureLimits
from warmachine.risk.gate3_liquidity import LiquidityGate
from warmachine.risk.gate4_edge_decay import EdgeDecayGate
from warmachine.risk.gate5_bankruptcy import BankruptcyGate
from warmachine.runner.lifecycle import LifecycleManager
from warmachine.runner.main import WarMachineRunner
from warmachine.trading.broker_adapter import KalshiAdapter, PaperAdapter
from warmachine.trading.order_router import OrderRouter
from warmachine.trading.signal_engine import SignalEngine, SignalEngineConfig
from warmachine.utils.logger import get_logger

log = get_logger(__name__)


def _build_db(settings: Settings):
    return psycopg2.connect(
        host=settings.database.host,
        port=settings.database.port,
        dbname=settings.database.dbname,
        user=settings.database.user,
        password=settings.database.password,
    )


def _build_prop_models() -> dict[str, GaussianBaseline]:
    return {
        prop: GaussianBaseline(GaussianConfig(prop_type=prop))
        for prop in ("points", "rebounds", "assists", "threes")
    }


def _cents_from_any(v) -> int:
    if v is None:
        return 0
    if isinstance(v, str):
        try:
            return int(round(float(v) * 100))
        except ValueError:
            return 0
    if isinstance(v, (int, float)):
        return int(v) if v >= 1 else int(round(v * 100))
    return 0


def _make_market_data_provider(kalshi_client):
    class _MDProvider:
        def get_market_snapshot(self, ticker: str) -> dict:
            # Use signed client method (session.get is unauthenticated).
            resp = kalshi_client.get_market(ticker)
            m = resp.get("market", {})
            return {
                "yes_bid": _cents_from_any(
                    m.get("yes_bid_dollars") or m.get("yes_bid")
                ),
                "yes_ask": _cents_from_any(
                    m.get("yes_ask_dollars") or m.get("yes_ask")
                ),
                "no_bid": _cents_from_any(
                    m.get("no_bid_dollars") or m.get("no_bid")
                ),
                "no_ask": _cents_from_any(
                    m.get("no_ask_dollars") or m.get("no_ask")
                ),
                "yes_ask_depth": int(float(m.get("yes_ask_size_fp", 0) or 0)),
                "no_ask_depth": int(float(m.get("no_ask_size_fp", 0) or 0)),
                "last_trade_time": m.get("last_trade_time"),
            }

    return _MDProvider()


def _make_price_provider(broker):
    class _PriceProvider:
        def get_current_price_cents(self, ticker: str, side: str) -> int:
            return broker.get_current_price_cents(ticker, side)

    return _PriceProvider()


def build_runner(
    mode: Optional[Literal["paper", "live"]] = None,
    toml_path: Optional[Path] = None,
) -> WarMachineRunner:
    settings = load_settings(toml_path)
    effective_mode = mode or settings.runtime.mode
    log.info(f"Building runner in {effective_mode} mode")

    # ----- Infrastructure -----
    db = _build_db(settings)
    kalshi_client = create_v1_kalshi_client()

    # ----- State -----
    state_path = Path(settings.paths.state_file)
    session = SessionState(path=state_path)
    state = WarMachineState(kalshi=kalshi_client, session=session)

    # ----- Data layer -----
    resolver = PlayerResolver(db)
    v1_bridge = V1DataBridge(db)
    market_fetcher = KalshiMarketFetcher(kalshi_client)
    clv_tracker = CLVTracker(Path(settings.paths.clv_log))

    # ----- Models -----
    prop_models = _build_prop_models()
    dnp_model = DNPModel()

    # ----- Edge + sizing -----
    kelly = KellySizer(
        KellySizerConfig(
            fractional_multiplier=settings.trading.kelly.fractional_multiplier,
            single_trade_cap_pct=settings.trading.kelly.single_trade_cap_pct,
            min_contracts=settings.trading.kelly.min_contracts,
        )
    )
    signal_engine = SignalEngine(
        config=SignalEngineConfig(
            min_edge_pp=settings.trading.global_min_edge_pp,
            yes_edge_premium_pp=settings.trading.yes_edge_premium_pp,
            max_edge_cap_pp=settings.trading.max_edge_cap_pp,
        ),
        kelly_sizer=kelly,
    )

    # ----- Broker (before liquidity gate since it needs a price source) -----
    if effective_mode == "paper":
        live_for_prices = KalshiAdapter(kalshi_client)
        broker = PaperAdapter(
            state_path=Path(settings.paths.paper_state),
            initial_balance_cents=settings.paper.initial_balance_cents,
            kalshi_price_source=live_for_prices.get_current_price_cents,
        )
    else:
        broker = KalshiAdapter(kalshi_client)

    # ----- Risk -----
    exposure_limits = ExposureLimits(
        per_ticker_pct=settings.risk.exposure.per_ticker_pct,
        per_game_pct=settings.risk.exposure.per_game_pct,
        per_player_pct=settings.risk.exposure.per_player_pct,
        per_prop_type_daily_pct=settings.risk.exposure.per_prop_type_daily_pct,
        total_open_pct=settings.risk.exposure.total_open_pct,
    )
    kill = KillSwitchGate(stop_file=Path(settings.paths.kill_switch))
    bankruptcy = BankruptcyGate(state=state)
    exposure = ExposureGate(state=state, limits=exposure_limits)
    liquidity = LiquidityGate(market_data=_make_market_data_provider(kalshi_client))
    edge_decay = EdgeDecayGate(price_provider=_make_price_provider(broker))
    risk_engine = RiskEngine(
        kill=kill,
        bankruptcy=bankruptcy,
        exposure=exposure,
        liquidity=liquidity,
        edge_decay=edge_decay,
    )

    # ----- Router -----
    router = OrderRouter(risk_engine=risk_engine, broker=broker)

    # ----- Lifecycle + Runner -----
    lifecycle = LifecycleManager()
    runner = WarMachineRunner(
        lifecycle=lifecycle,
        state=state,
        kalshi_client=kalshi_client,
        signal_engine=signal_engine,
        risk_engine=risk_engine,
        broker=broker,
        order_router=router,
        v1_bridge=v1_bridge,
        prop_models=prop_models,
        clv_tracker=clv_tracker,
    )
    runner.market_fetcher = market_fetcher
    runner.resolver = resolver
    runner.dnp_model = dnp_model
    runner.settings = settings
    runner.db_connection = db

    log.info(
        f"Runner built: broker={broker.mode}, "
        f"models={list(prop_models.keys())}, "
        f"resolver_cached={resolver.cache_stats()}"
    )
    return runner
