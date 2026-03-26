#!/usr/bin/env python3
"""
Prediction Market War Machine - Main Entry Point
==================================================
Phase 6: Unified runner integrating all modules.

Three operating modes:
  observe  - Record data + classify + analyze (no trading)
  signal   - Everything in observe + signal detection + alerts
  live     - Everything in signal + order execution (with confirmation)

Usage:
    # Observation mode (default, safe)
    python scripts/main.py observe

    # Signal mode (alerts when opportunities detected)
    python scripts/main.py signal

    # Live mode (can execute trades after confirmation)
    python scripts/main.py live --prod

    # Quick status check
    python scripts/main.py status

    # One-shot scan (no loop)
    python scripts/main.py scan
"""

import sys
import time
import signal
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from kalshi_client import KalshiConfig, create_client
from market_recorder import MarketDatabase, RESTRecorder, DB_PATH
from market_classifier import MarketClassifier
from price_analyzer import PriceAnalyzer
from signal_engine import SignalEngine
from trade_engine import TradeEngine, PositionManager, TradeLogger
from safety import SafetyManager

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ============================================================================
# War Machine
# ============================================================================

class WarMachine:
    """
    The Prediction Market War Machine.
    Integrates all modules into a unified system.
    """

    def __init__(self, mode: str = "observe", use_prod: bool = False, interval: int = 60):
        self.mode = mode
        self.interval = interval
        self.running = False

        # Config
        self.config = KalshiConfig.from_env()
        if use_prod:
            self.config.use_demo = False

        env = "PRODUCTION" if not self.config.use_demo else "DEMO"
        logger.info(f"War Machine initializing | mode={mode} | env={env}")

        # Core modules
        self.db = MarketDatabase()
        self.client = create_client(self.config)
        self.recorder = RESTRecorder(self.client, self.db)
        self.classifier = MarketClassifier()
        self.analyzer = PriceAnalyzer()
        self.signal_engine = SignalEngine()
        self.safety = SafetyManager()
        self.positions = PositionManager()
        self.trade_log = TradeLogger()

        # Trade engine only in live mode
        self.trade_engine = None
        if mode == "live":
            self.trade_engine = TradeEngine(use_demo=self.config.use_demo)

        # Alert integration (optional)
        self.alert_manager = None
        try:
            from alerts import AlertManager
            self.alert_manager = AlertManager()
            logger.info("Alert manager loaded")
        except Exception as e:
            logger.debug(f"Alert manager not available: {e}")

    def run_cycle(self) -> dict:
        """
        Execute one full cycle of the War Machine.
        Returns cycle results summary.
        """
        cycle_start = time.time()
        results = {"timestamp": datetime.now(timezone.utc).isoformat()}

        # Step 1: Record market data
        try:
            market_count = self.recorder.record_snapshot()
            results["markets_recorded"] = market_count
        except Exception as e:
            logger.error(f"Recording failed: {e}")
            results["recording_error"] = str(e)

        # Step 2: Classify new markets
        try:
            class_counts = self.classifier.classify_all(force=False)
            results["newly_classified"] = sum(class_counts.values()) if class_counts else 0
        except Exception as e:
            logger.error(f"Classification failed: {e}")

        # Step 3: Detect signals (signal + live modes)
        signals = []
        if self.mode in ("signal", "live"):
            try:
                signals = self.signal_engine.scan_all()
                results["signals_detected"] = len(signals)

                # Filter to actionable signals
                actionable = [
                    s for s in signals
                    if s.confidence in ("high", "medium") and s.net_ev_cents > 1
                ]
                results["actionable_signals"] = len(actionable)

                # Alert for actionable signals (skip overreaction_fade — too noisy)
                if actionable and self.alert_manager:
                    for sig in actionable[:5]:
                        if sig.signal_type == "overreaction_fade":
                            continue  # learn only, no Discord spam
                        try:
                            self._send_signal_alert(sig)
                        except Exception as e:
                            logger.error(f"Alert failed: {e}")

            except Exception as e:
                logger.error(f"Signal detection failed: {e}")

        # Step 4: Report
        elapsed = time.time() - cycle_start
        results["cycle_time_sec"] = round(elapsed, 1)

        return results

    def _send_signal_alert(self, signal):
        """Format and send a signal alert."""
        if not self.alert_manager:
            return

        message = (
            f"**{signal.signal_type}** | {signal.confidence.upper()}\n"
            f"Market: {signal.title[:60]}\n"
            f"Side: {signal.side.upper()} at {signal.entry_price*100:.0f}c\n"
            f"Edge: {signal.edge*100:.1f}% | EV: {signal.net_ev_cents:.1f}c\n"
            f"Reasoning: {signal.reasoning}"
        )

        try:
            self.alert_manager.send_alert({"message": message, "title": signal.signal_type})
        except Exception:
            pass  # alert failure is not critical

    def run_loop(self, max_cycles: int = None):
        """
        Main execution loop.
        """
        self.running = True
        cycle = 0

        logger.info(f"Starting War Machine loop | interval={self.interval}s | mode={self.mode}")

        while self.running:
            cycle += 1
            logger.info(f"=== Cycle {cycle} ===")

            try:
                results = self.run_cycle()
                self._print_cycle_summary(cycle, results)
            except Exception as e:
                logger.error(f"Cycle {cycle} failed: {e}")

            if max_cycles and cycle >= max_cycles:
                logger.info(f"Reached max cycles ({max_cycles})")
                break

            # Sleep
            sleep_time = max(0, self.interval - results.get("cycle_time_sec", 0))
            if sleep_time > 0 and self.running:
                time.sleep(sleep_time)

    def _print_cycle_summary(self, cycle: int, results: dict):
        """Print a concise cycle summary."""
        parts = [f"Cycle {cycle}:"]

        if "markets_recorded" in results:
            parts.append(f"{results['markets_recorded']} markets")

        if "signals_detected" in results:
            parts.append(f"{results['signals_detected']} signals")
            if results.get("actionable_signals", 0) > 0:
                parts.append(f"*** {results['actionable_signals']} ACTIONABLE ***")

        parts.append(f"({results.get('cycle_time_sec', 0)}s)")

        logger.info(" | ".join(parts))

    def status(self) -> dict:
        """Get full system status."""
        status = {
            "mode": self.mode,
            "env": "PRODUCTION" if not self.config.use_demo else "DEMO",
        }

        # Database stats
        status["db"] = self.db.get_stats()

        # Safety status
        can_bet, reason = self.safety.can_bet()
        status["safety"] = {
            "can_bet": can_bet,
            "reason": reason if not can_bet else "OK",
        }

        # Positions
        positions = self.positions.get_positions()
        status["open_positions"] = len(positions)

        # P&L
        status["pnl"] = self.trade_log.get_pnl_summary()

        # Classification summary
        try:
            summary = self.classifier.get_summary()
            status["total_classified"] = summary.get("total_classified", 0)
            status["total_tradeable"] = summary.get("total_tradeable", 0)
        except Exception:
            pass

        return status

    def shutdown(self):
        """Graceful shutdown."""
        self.running = False
        logger.info("Shutting down War Machine...")
        self.db.commit()
        self.db.close()
        self.classifier.close()
        self.analyzer.close()
        self.signal_engine.close()
        logger.info("Shutdown complete.")


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Prediction Market War Machine"
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["observe", "signal", "live", "status", "scan"],
        default="live",
        help="Operating mode"
    )
    parser.add_argument("--prod", action="store_true", help="Use production API")
    parser.add_argument("--interval", type=int, default=60, help="Cycle interval (seconds)")
    parser.add_argument("--max-cycles", type=int, default=None, help="Max cycles before exit")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    print()
    print("  ====================================================")
    print("  PREDICTION MARKET WAR MACHINE")
    print("  ====================================================")
    print(f"  Mode:     {args.mode}")
    print(f"  Env:      {'PRODUCTION' if args.prod else 'DEMO'}")
    print(f"  Interval: {args.interval}s")
    print("  ====================================================")
    print()

    machine = WarMachine(
        mode=args.mode if args.mode not in ("status", "scan") else "observe",
        use_prod=args.prod,
        interval=args.interval,
    )

    # Graceful shutdown
    def handle_signal(sig, frame):
        machine.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if args.mode == "status":
        status = machine.status()
        for section, data in status.items():
            if isinstance(data, dict):
                print(f"  {section}:")
                for k, v in data.items():
                    print(f"    {k}: {v}")
            else:
                print(f"  {section}: {data}")
        machine.shutdown()

    elif args.mode == "scan":
        results = machine.run_cycle()
        print("\nScan results:")
        for k, v in results.items():
            print(f"  {k}: {v}")
        machine.shutdown()

    else:
        try:
            machine.run_loop(max_cycles=args.max_cycles)
        finally:
            machine.shutdown()


if __name__ == "__main__":
    main()
