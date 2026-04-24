"""
Module: main.py
Purpose: WarMachine v2 runner - Kalshi-driven 60s polling loop.
         scan -> predict -> signal -> risk -> route, each cycle exception-isolated.
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Optional

from warmachine.models.base import ModelInput
from warmachine.runner.lifecycle import LifecycleManager
from warmachine.utils.logger import get_logger

log = get_logger(__name__)


class WarMachineRunner:
    POLL_INTERVAL_SECONDS = 60
    MAX_MARKETS_PER_CYCLE = 50

    def __init__(
        self,
        lifecycle: LifecycleManager,
        state,
        kalshi_client,
        signal_engine,
        risk_engine,
        broker,
        order_router,
        v1_bridge,
        prop_models: dict,
        clv_tracker,
    ):
        self.lifecycle = lifecycle
        self.state = state
        self.kalshi = kalshi_client
        self.signal_engine = signal_engine
        self.risk_engine = risk_engine
        self.broker = broker
        self.router = order_router
        self.bridge = v1_bridge
        self.models = prop_models
        self.clv = clv_tracker

        self.cycle_count = 0
        self.last_cycle_start: Optional[datetime] = None

    def run(self) -> None:
        log.info(f"WarMachine v2 runner starting. Mode={self.broker.mode}")
        self.lifecycle.start()

        try:
            while not self.lifecycle.should_stop():
                self.last_cycle_start = datetime.now(timezone.utc)
                self.cycle_count += 1

                try:
                    self._run_one_cycle()
                except Exception as e:
                    log.error(
                        f"Cycle {self.cycle_count} failed: {e}\n{traceback.format_exc()}"
                    )

                if self.lifecycle.wait_or_stop(self.POLL_INTERVAL_SECONDS):
                    break
        finally:
            log.info("Runner shutting down")
            self.lifecycle.run_shutdown_hooks()
            log.info(f"Runner stopped. Completed {self.cycle_count} cycles.")

    def _run_one_cycle(self) -> None:
        self._write_heartbeat()

        if self.state.is_stale:
            log.warning("State is stale, skipping cycle")
            return

        prop_markets = self._scan_nba_prop_markets()
        if not prop_markets:
            log.info(f"Cycle {self.cycle_count}: no open markets")
            return

        log.info(
            f"Cycle {self.cycle_count}: {len(prop_markets)} markets to evaluate"
        )

        orders_routed = 0
        signals_generated = 0
        gate_rejections = 0

        for market in prop_markets[: self.MAX_MARKETS_PER_CYCLE]:
            try:
                result = self._evaluate_market(market)
                if result:
                    if result.get("signal"):
                        signals_generated += 1
                    if result.get("routed"):
                        orders_routed += 1
                    if result.get("gate_reject"):
                        gate_rejections += 1
            except Exception as e:
                ticker = getattr(market, "ticker", "?")
                log.error(f"Market {ticker} failed: {e}", exc_info=True)

        log.info(
            f"Cycle {self.cycle_count} done: scanned={len(prop_markets)}, "
            f"signals={signals_generated}, routed={orders_routed}, "
            f"rejects={gate_rejections}"
        )

    def _scan_nba_prop_markets(self) -> list:
        """Returns list of PropMarket objects from KalshiMarketFetcher."""
        try:
            return self.market_fetcher.fetch_all_open_nba_prop_markets()
        except Exception as e:
            log.error(f"Market scan failed: {e}")
            return []

    def _evaluate_market(self, market) -> Optional[dict]:
        """Full pipeline: resolve -> features -> predict -> DNP -> signal -> CLV -> route."""
        from datetime import datetime, timezone

        from warmachine.edge.clv_tracker import CLVEntry
        from warmachine.trading.signal_engine import Prediction

        ticker = market.ticker

        # 0. Skip markets with degenerate pricing (100c = no sellers, 0c = no buyers)
        if not (1 <= market.yes_ask_cents <= 99) or not (1 <= market.no_ask_cents <= 99):
            return None

        # 1. Resolve player UUID -> v1 player_id
        player_res = self.resolver.resolve_player(
            market.player_uuid, market.player_name or ""
        )
        if not player_res:
            log.debug(f"{ticker}: player UUID unresolved, skip")
            return None

        # 2. Resolve opponent team (best-effort; deferred full impl)
        opp_team_id_v1 = None
        is_home = False

        # 3. Get features from v1 bridge
        features = self.bridge.get_player_features(
            player_id=str(player_res.nba_player_id),
            prop_type=market.prop_type,
            is_home=is_home,
            opp_team_id=str(opp_team_id_v1) if opp_team_id_v1 else "",
            is_b2b=False,
            spread=0.0,
            line=market.line,
        )
        if not features:
            log.debug(
                f"{ticker}: features unavailable for player_id={player_res.nba_player_id}"
            )
            return None

        # 4. Run Gaussian model
        model = self.models.get(market.prop_type)
        if not model:
            return None

        market_yes_prob = market.yes_ask_cents / 100.0
        model_input = ModelInput(
            player_id=str(player_res.nba_player_id),
            game_id=market.event_ticker,
            features=features,
        )
        output = model.predict(model_input, market_prob_yes=market_yes_prob)

        # 5. DNP adjustment
        final_prob = output.calibrated_probability
        dnp_features = self._build_dnp_features(features, market)
        if dnp_features:
            try:
                final_prob = self.dnp_model.adjust_prop_probability(
                    prop_prob_given_plays=output.calibrated_probability,
                    dnp_features=dnp_features,
                )
            except Exception as e:
                log.debug(f"{ticker}: DNP adjust failed ({e}), using raw model prob")
                final_prob = output.calibrated_probability

        # 6. Build Prediction -> SignalEngine
        pred = Prediction(
            ticker=ticker,
            prop_type=market.prop_type,
            player_id=str(player_res.nba_player_id),
            game_id=market.event_ticker,
            model_prob_yes=output.calibrated_probability,
            model_version=model.model_version,
            market_yes_ask_cents=market.yes_ask_cents,
            market_no_ask_cents=market.no_ask_cents,
            predicted_at=datetime.now(timezone.utc),
            dnp_adjusted_prob_yes=final_prob,
        )

        intent = self.signal_engine.generate(
            pred, bankroll_dollars=self.state.bankroll
        )
        if not intent:
            return {"ticker": ticker, "signal": False, "routed": False}

        log.info(
            f"{ticker}: SIGNAL edge={intent.signal_edge_pp:.1f}pp "
            f"size={intent.quantity}@{intent.limit_price_cents}c"
        )

        # 7. Route
        routing = self.router.route(intent)

        # 8. CLV on successful route
        if routing.routed and routing.fill:
            try:
                clv_entry = CLVEntry(
                    ticker=ticker,
                    entry_time_utc=routing.fill.filled_at.isoformat(),
                    entry_price_cents=routing.fill.fill_price_cents,
                    entry_price_dollars=routing.fill.fill_price_cents / 100.0,
                    side=intent.side.value,
                    quantity=routing.fill.filled_quantity,
                    model_prob=final_prob,
                    edge_at_entry_pp=intent.signal_edge_pp,
                    prop_type=market.prop_type,
                    player=player_res.player_name,
                    line=market.line,
                    game_id=market.event_ticker,
                    game_tip_off_utc=market.tip_off_utc.isoformat(),
                )
                self.clv.record_entry(clv_entry)
            except Exception as e:
                log.error(f"CLV record_entry failed: {e}")

        return {
            "ticker": ticker,
            "signal": True,
            "routed": routing.routed,
            "gate_reject": (
                not routing.routed
                and routing.risk_decision is not None
                and not routing.risk_decision.allowed
            ),
            "model_prob": final_prob,
            "edge_pp": intent.signal_edge_pp,
            "fill": routing.fill.model_dump(mode="json") if routing.fill else None,
        }

    def _build_dnp_features(self, gaussian_features: dict, market) -> Optional[dict]:
        """Best-effort DNP feature dict. None -> skip DNP adjustment."""
        try:
            season_avg = gaussian_features.get("season_avg", 0) or 0
            return {
                "minutes_per_game_last_5": (gaussian_features.get("recent_5_avg", 0) or 0) * 1.5,
                "minutes_per_game_last_10": (gaussian_features.get("recent_10_avg", 0) or 0) * 1.5,
                "minutes_per_game_season": season_avg * 1.5,
                "games_played_pct_last_10": min(
                    1.0, (gaussian_features.get("games_played", 0) or 0) / 82.0
                ),
                "consecutive_games_played": 1,
                "consecutive_games_missed": 0,
                "is_back_to_back": gaussian_features.get("is_b2b", 0) or 0,
                "days_since_last_game": 1,
                "age": 27,
                "has_injury_report_flag": 0,
                "injury_status_severity": 0,
                "season_dnp_rate": 0.05,
                "rotation_tier": 3 if season_avg >= 10 else 1,
                "is_rest_candidate": 0,
                "playoff_elimination_game": 0,
            }
        except Exception:
            return None

    def _write_heartbeat(self) -> None:
        try:
            from datetime import datetime, timezone
            from pathlib import Path
            if hasattr(self, "settings") and getattr(self.settings, "paths", None):
                hb_path = getattr(self.settings.paths, "heartbeat", None)
                if hb_path:
                    p = Path(hb_path)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(
                        datetime.now(timezone.utc).isoformat(), encoding="utf-8"
                    )
        except Exception as e:
            log.debug(f"Heartbeat write failed: {e}")


def main():
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="WarMachine v2 runner")
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default=None,
        help="Override runtime.mode from settings.toml",
    )
    parser.add_argument(
        "--config", type=str, default=None, help="TOML path override"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Run 1 cycle then exit"
    )
    args = parser.parse_args()

    from warmachine.runner.factory import build_runner

    runner = build_runner(
        mode=args.mode,
        toml_path=Path(args.config) if args.config else None,
    )

    if args.dry_run:
        log.info("Dry run: single cycle")
        runner.lifecycle.start()
        try:
            runner._run_one_cycle()
        finally:
            runner.lifecycle.request_stop()
        log.info("Dry run complete")
        return

    runner.run()


if __name__ == "__main__":
    main()
