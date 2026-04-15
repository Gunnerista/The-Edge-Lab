#!/usr/bin/env python3
"""
MLB Prediction Settlement
============================
War Machine MLB

Checks Kalshi API for settled MLB markets and updates predictions + trades.

Usage:
    python mlb/scripts/mlb_settle.py
    python mlb/scripts/mlb_settle.py --report   # report only, no updates
"""

import sys
import os
import io
import logging
import argparse
from datetime import datetime, timezone

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

from db import get_connection, put_connection
from kalshi_client import KalshiConfig, create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

KALSHI_FEE = 0.07


def settle_mlb_predictions(report_only=False):
    """Check and settle MLB predictions against Kalshi results."""
    config = KalshiConfig.from_env()
    config.use_demo = False
    client = create_client(config)

    conn = get_connection(dict_cursor=True)
    cur = conn.cursor()

    # Get unsettled predictions
    cur.execute("SELECT id, ticker, side, market_price, edge FROM mlb_predictions WHERE result IS NULL")
    unsettled = cur.fetchall()
    logger.info(f"[MLB Settle] {len(unsettled)} unsettled predictions")

    if not unsettled:
        put_connection(conn)
        return

    settled = 0
    wins = 0
    losses = 0
    total_pnl = 0

    for pred in unsettled:
        ticker = pred['ticker']
        try:
            mkt_data = client.get_market(ticker)
            market = mkt_data.get('market', mkt_data)
            result = market.get('result', '')

            if result not in ('yes', 'no'):
                continue

            side = pred['side']
            won = (side == 'YES' and result == 'yes') or (side == 'NO' and result == 'no')
            result_str = result.upper()

            if not report_only:
                # Update prediction
                conn2 = get_connection()
                cur2 = conn2.cursor()
                try:
                    cur2.execute(
                        "UPDATE mlb_predictions SET result = %s WHERE id = %s",
                        (result_str, pred['id'])
                    )

                    # Update matching trade
                    cur2.execute(
                        "SELECT id, price, quantity FROM mlb_trades WHERE ticker = %s AND status = 'open' LIMIT 1",
                        (ticker,)
                    )
                    trade = cur2.fetchone()
                    if trade:
                        entry_price = trade['price']
                        qty = trade['quantity']
                        if won:
                            gross = (1 - entry_price) * qty
                            fee = gross * KALSHI_FEE
                            pnl = gross - fee
                        else:
                            pnl = -entry_price * qty

                        cur2.execute(
                            "UPDATE mlb_trades SET status = 'settled', pnl = %s WHERE id = %s",
                            (round(pnl, 4), trade['id'])
                        )
                        total_pnl += pnl

                    conn2.commit()
                except Exception as e:
                    conn2.rollback()
                    logger.debug(f"[MLB Settle] Update error for {ticker}: {e}")
                finally:
                    put_connection(conn2)

            settled += 1
            if won:
                wins += 1
            else:
                losses += 1

        except Exception as e:
            logger.debug(f"[MLB Settle] Error checking {ticker}: {e}")

    put_connection(conn)

    # Report
    print()
    print("=" * 60)
    print(f"  MLB SETTLEMENT REPORT")
    print("=" * 60)
    print(f"  Unsettled checked:  {len(unsettled)}")
    print(f"  Newly settled:      {settled}")
    if settled > 0:
        print(f"  Wins:               {wins}")
        print(f"  Losses:             {losses}")
        print(f"  Win rate:           {wins/settled*100:.1f}%")
        print(f"  PnL:                ${total_pnl:+.2f}")

    # Cumulative stats
    conn3 = get_connection(dict_cursor=True)
    cur3 = conn3.cursor()
    cur3.execute("SELECT COUNT(*) as cnt FROM mlb_predictions WHERE result IS NOT NULL")
    total_settled = cur3.fetchone()['cnt']
    cur3.execute("SELECT COALESCE(SUM(pnl), 0) as pnl FROM mlb_trades WHERE pnl IS NOT NULL")
    cum_pnl = cur3.fetchone()['pnl']
    print()
    print(f"  Cumulative settled: {total_settled}")
    print(f"  Cumulative PnL:     ${cum_pnl:+.2f}")
    print(f"  Virtual bankroll:   ${100 + cum_pnl:.2f}")
    print("=" * 60)
    put_connection(conn3)


def main():
    parser = argparse.ArgumentParser(description="MLB Settlement")
    parser.add_argument("--report", action="store_true", help="Report only")
    args = parser.parse_args()
    settle_mlb_predictions(report_only=args.report)


if __name__ == "__main__":
    main()
