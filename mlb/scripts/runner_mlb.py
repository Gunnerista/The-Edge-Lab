#!/usr/bin/env python3
"""
MLB Runner - Daily Automation
================================
War Machine MLB

Runs MLB data pipeline on game days:
- 2h before first pitch: forward test + paper trading
- Every 15 min: market price updates
- 11:59 PM ET: settlement

Completely independent from NBA runner (scripts/runner.py).

Usage:
    python mlb/scripts/runner_mlb.py
    python mlb/scripts/runner_mlb.py --once   # single cycle then exit
"""

import sys
import os
import io
import time
import logging
import argparse
from datetime import datetime, timedelta

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

from tz import ET

LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, 'runner_mlb.log'), encoding='utf-8'),
    ],
)
logger = logging.getLogger(__name__)

# Safety
FORCE_DRY_RUN = True


def _now_et():
    return datetime.now(ET)


def run_once():
    """Run a single cycle of all MLB tasks."""
    from mlb_schedule import get_today_games, is_game_day
    from mlb_market_collector import collect_markets
    from mlb_forward_test import run_mlb_forward_test
    from mlb_auto_trader import run_mlb_paper_trading
    from mlb_settle import settle_mlb_predictions

    now = _now_et()
    today = now.date()
    logger.info(f"[MLB Runner] Cycle start: {now.strftime('%Y-%m-%d %I:%M %p ET')}")

    # 1. Check schedule
    if not is_game_day():
        logger.info("[MLB Runner] No MLB games today. Skipping.")
        return

    games = get_today_games()
    logger.info(f"[MLB Runner] {len(games)} games today")

    # 2. Sync market data
    try:
        collect_markets()
    except Exception as e:
        logger.error(f"[MLB Runner] Market collection error: {e}")

    # 3. Forward test
    try:
        run_mlb_forward_test(today)
    except Exception as e:
        logger.error(f"[MLB Runner] Forward test error: {e}")

    # 4. Paper trading
    try:
        run_mlb_paper_trading(today)
    except Exception as e:
        logger.error(f"[MLB Runner] Paper trading error: {e}")

    # 5. Settlement (always try)
    try:
        settle_mlb_predictions()
    except Exception as e:
        logger.error(f"[MLB Runner] Settlement error: {e}")

    logger.info("[MLB Runner] Cycle complete.")


def run_loop():
    """Main loop: run tasks at appropriate times."""
    from mlb_schedule import get_today_games, is_game_day

    logger.info("[MLB Runner] Starting main loop...")

    _forward_done_today = False
    _settle_done_today = False
    _last_market_sync = 0

    while True:
        now = _now_et()
        today = now.date()

        # Reset daily flags at midnight
        if now.hour == 0 and now.minute < 2:
            _forward_done_today = False
            _settle_done_today = False

        # Check if game day
        try:
            if not is_game_day():
                logger.info(f"[MLB Runner] No games today. Sleeping 1h...")
                time.sleep(3600)
                continue
        except Exception as e:
            logger.error(f"[MLB Runner] Schedule check error: {e}")
            time.sleep(600)
            continue

        # Market sync every 15 min
        if time.time() - _last_market_sync >= 900:
            try:
                from mlb_market_collector import collect_markets
                collect_markets()
            except Exception as e:
                logger.error(f"[MLB Runner] Market sync error: {e}")
                # Reset any poisoned DB connections in the pool
                try:
                    from db import close_all
                    close_all()
                except Exception:
                    pass
                logger.info("[MLB Runner] Sleeping 60s before next iteration after sync error...")
                time.sleep(60)
            finally:
                # Always advance the sync timer so we back off 15 min, even on failure
                _last_market_sync = time.time()
            continue

        # Forward test: 2h before first game, once per day
        if not _forward_done_today and 10 <= now.hour <= 18:
            try:
                from mlb_forward_test import run_mlb_forward_test
                from mlb_auto_trader import run_mlb_paper_trading
                run_mlb_forward_test(today)
                run_mlb_paper_trading(today)
                _forward_done_today = True
            except Exception as e:
                logger.error(f"[MLB Runner] Forward test/trading error: {e}")

        # Settlement: after 11 PM ET, once per day
        if not _settle_done_today and now.hour >= 23:
            try:
                from mlb_settle import settle_mlb_predictions
                settle_mlb_predictions()
                _settle_done_today = True
            except Exception as e:
                logger.error(f"[MLB Runner] Settlement error: {e}")

        # Sleep 5 min between checks
        time.sleep(300)


def main():
    parser = argparse.ArgumentParser(description="MLB Runner")
    parser.add_argument("--once", action="store_true", help="Single cycle then exit")
    args = parser.parse_args()

    print()
    print("  ====================================================")
    print("  WAR MACHINE MLB RUNNER")
    print("  ====================================================")
    print(f"  Mode:      {'PAPER' if FORCE_DRY_RUN else 'LIVE'}")
    print(f"  Time:      {_now_et().strftime('%I:%M %p ET')}")
    print("  ====================================================")
    print()

    if args.once:
        run_once()
    else:
        run_loop()


if __name__ == "__main__":
    main()
