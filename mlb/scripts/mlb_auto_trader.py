#!/usr/bin/env python3
"""
MLB Auto Trader (Paper Mode)
===============================
War Machine MLB

Takes predictions from mlb_predictions and executes paper trades.
MLB bankroll is separate from NBA ($100 paper start).

Usage:
    python mlb/scripts/mlb_auto_trader.py
"""

import sys
import os
import io
import logging
from datetime import datetime, date, timezone

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
from tz import ET
from bankroll import kelly_bet_size

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# MLB-specific config (separate from NBA)
MLB_BANKROLL_START = 100.0
KELLY_FRACTION = 0.25
MIN_BET = 1.0
MAX_BET = 5.0
MAX_OPEN_POSITIONS = 10
DAILY_MAX_LOSS = 10.0


def _get_mlb_bankroll(cur):
    """Calculate current MLB virtual bankroll from trades."""
    cur.execute("SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM mlb_trades WHERE pnl IS NOT NULL")
    row = cur.fetchone()
    return MLB_BANKROLL_START + (row['total_pnl'] if row else 0)


def _get_open_positions(cur):
    """Count open (unsettled) positions."""
    cur.execute("SELECT COUNT(*) as cnt FROM mlb_trades WHERE status = 'open' AND is_paper = true")
    return cur.fetchone()['cnt']


def _get_daily_pnl(cur, target_date):
    """Get today's realized PnL."""
    cur.execute(
        "SELECT COALESCE(SUM(pnl), 0) as pnl FROM mlb_trades WHERE DATE(created_at) = %s AND pnl IS NOT NULL",
        (target_date,)
    )
    return cur.fetchone()['pnl']


def run_mlb_paper_trading(target_date=None):
    """Execute paper trades for pending predictions."""
    if target_date is None:
        target_date = datetime.now(ET).date()

    conn = get_connection(dict_cursor=True)
    cur = conn.cursor()

    bankroll = _get_mlb_bankroll(cur)
    open_pos = _get_open_positions(cur)
    daily_pnl = _get_daily_pnl(cur, target_date)

    logger.info(f"[MLB Paper] Bankroll: ${bankroll:.2f}, Open: {open_pos}, Daily PnL: ${daily_pnl:.2f}")

    # Daily loss limit
    if daily_pnl <= -DAILY_MAX_LOSS:
        logger.info(f"[MLB Paper] Daily loss limit reached (${daily_pnl:.2f})")
        put_connection(conn)
        return []

    # Get unplaced predictions for today
    cur.execute("""
        SELECT id, ticker, prop_type, player_name, line,
               calibrated_prob, market_price, edge, side
        FROM mlb_predictions
        WHERE game_date = %s AND bet_placed = false AND result IS DISTINCT FROM 'INVALID'
        ORDER BY ABS(edge) DESC
    """, (target_date,))
    predictions = cur.fetchall()

    if not predictions:
        logger.info("[MLB Paper] No pending predictions")
        put_connection(conn)
        return []

    trades = []
    for pred in predictions:
        # Position limit
        if open_pos >= MAX_OPEN_POSITIONS:
            logger.info(f"[MLB Paper] Max positions ({MAX_OPEN_POSITIONS}) reached")
            break

        entry_price = pred['market_price']
        edge = abs(pred['edge'])

        # Kelly sizing
        odds = (1 - entry_price) / entry_price if 0 < entry_price < 1 else 0
        if odds <= 0:
            continue

        bet_dollars = kelly_bet_size(edge, odds, bankroll, KELLY_FRACTION)
        bet_dollars = max(MIN_BET, min(bet_dollars, MAX_BET))

        # Contracts
        contracts = max(1, int(bet_dollars / entry_price))

        # Record trade
        conn2 = get_connection()
        cur2 = conn2.cursor()
        try:
            cur2.execute("""
                INSERT INTO mlb_trades
                    (ticker, side, action, price, quantity, order_id, status,
                     edge_at_entry, kelly_fraction, is_paper, pnl)
                VALUES (%s, %s, 'BUY', %s, %s, %s, 'open', %s, %s, true, NULL)
            """, (
                pred['ticker'], pred['side'], round(entry_price, 4),
                contracts, f"paper-{pred['id']}", round(edge, 4), KELLY_FRACTION,
            ))
            # Mark prediction as placed
            cur2.execute("UPDATE mlb_predictions SET bet_placed = true WHERE id = %s", (pred['id'],))
            conn2.commit()

            trades.append({
                'ticker': pred['ticker'],
                'player': pred['player_name'],
                'side': pred['side'],
                'price': entry_price,
                'contracts': contracts,
                'bet_size': round(bet_dollars, 2),
                'edge': edge,
            })
            open_pos += 1

        except Exception as e:
            conn2.rollback()
            logger.debug(f"[MLB Paper] Trade error: {e}")
        finally:
            put_connection(conn2)

    put_connection(conn)

    # Summary
    print()
    print("=" * 60)
    print(f"  MLB PAPER TRADING - {target_date}")
    print("=" * 60)
    print(f"  Bankroll:     ${bankroll:.2f}")
    print(f"  Trades placed: {len(trades)}")
    if trades:
        print()
        print(f"  {'Player':20s} {'Side':>4s} {'Price':>6s} {'Qty':>4s} {'Bet$':>6s} {'Edge':>6s}")
        for t in trades:
            print(f"  {t['player']:20s} {t['side']:>4s} {t['price']:5.1%} {t['contracts']:4d} ${t['bet_size']:5.2f} {t['edge']:+5.1%}")
    print("=" * 60)

    return trades


if __name__ == "__main__":
    run_mlb_paper_trading()
