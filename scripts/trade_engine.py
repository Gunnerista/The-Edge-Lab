#!/usr/bin/env python3
"""
Trade Engine - Phase 5: Order Execution & Position Management
==============================================================
Prediction Market War Machine

SAFETY FIRST: This engine does NOT auto-trade.
All signals require Ikjun's manual confirmation before execution.

Flow:
  1. Signal Engine detects opportunity
  2. Trade Engine formats the order details
  3. Alert sent to Discord/console
  4. Ikjun reviews and confirms
  5. Trade Engine executes the order via Kalshi API
  6. Position tracked until settlement

Features:
  - Limit orders only (never market orders)
  - Kelly-based position sizing with safety caps
  - Real-time P&L tracking
  - Partial and full exit support
  - Safety module integration (daily/weekly limits)
  - Full trade logging for future analysis

Usage:
    # Show current positions
    python scripts/trade_engine.py --positions

    # Execute a confirmed trade
    python scripts/trade_engine.py --execute TICKER --side yes --price 40 --size 5

    # Exit a position
    python scripts/trade_engine.py --exit TICKER --price 60 --count 5

    # Show trade history
    python scripts/trade_engine.py --history

    # Show P&L summary
    python scripts/trade_engine.py --pnl
"""

import sys
import json
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from kalshi_client import KalshiConfig, KalshiAPIClient, create_client
from safety import SafetyManager, SAFETY_CONFIG
from signal_engine import net_ev, breakeven_prob, KALSHI_FEE_RATE

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TRADES_FILE = DATA_DIR / "trade_log.jsonl"
POSITIONS_FILE = DATA_DIR / "positions.json"


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class TradeOrder:
    """A proposed or executed trade order."""
    ticker: str
    side: str                  # "yes" or "no"
    action: str                # "buy" or "sell"
    count: int                 # number of contracts
    price_cents: int           # limit price in cents (1-99)
    signal_type: str           # from SignalEngine
    reasoning: str
    model_prob: float
    edge: float
    net_ev_cents: float


@dataclass
class Position:
    """An open position."""
    ticker: str
    title: str
    side: str                  # "yes" or "no"
    avg_entry_price: float     # dollars (0-1)
    count: int                 # contracts held
    current_price: float       # latest mid-price
    unrealized_pnl: float      # dollars
    opened_at: str
    signal_type: str


@dataclass
class TradeRecord:
    """Completed trade for logging."""
    timestamp: str
    ticker: str
    title: str
    side: str
    action: str                # "buy" or "sell"
    count: int
    price_cents: int
    total_cost: float          # dollars
    signal_type: str
    reasoning: str
    model_prob: float
    edge: float
    order_id: str
    status: str                # "filled", "partial", "cancelled", "pending"
    source: str = "auto"       # "auto" or "manual"


# ============================================================================
# Position Manager
# ============================================================================

class PositionManager:
    """Track open positions and P&L. Syncs with Kalshi API as source of truth."""

    def __init__(self, positions_file: Path = POSITIONS_FILE):
        self.file = positions_file
        self.positions: Dict[str, dict] = {}
        self._load()

    def _load(self):
        if self.file.exists():
            with open(self.file, "r") as f:
                self.positions = json.load(f)

    def _save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file, "w") as f:
            json.dump(self.positions, f, indent=2)

    def add_position(self, ticker: str, title: str, side: str,
                     price: float, count: int, signal_type: str):
        """Add or increase a position."""
        key = f"{ticker}_{side}"

        if key in self.positions:
            existing = self.positions[key]
            total_count = existing["count"] + count
            avg_price = (
                (existing["avg_entry_price"] * existing["count"] + price * count)
                / total_count
            )
            existing["avg_entry_price"] = round(avg_price, 4)
            existing["count"] = total_count
        else:
            self.positions[key] = {
                "ticker": ticker,
                "title": title,
                "side": side,
                "avg_entry_price": round(price, 4),
                "count": count,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "signal_type": signal_type,
            }

        self._save()

    def reduce_position(self, ticker: str, side: str, count: int) -> Optional[dict]:
        """Reduce or close a position. Returns the closed portion."""
        key = f"{ticker}_{side}"
        if key not in self.positions:
            return None

        pos = self.positions[key]
        closed_count = min(count, pos["count"])
        pos["count"] -= closed_count

        if pos["count"] <= 0:
            del self.positions[key]

        self._save()
        return {"count": closed_count, "avg_entry_price": pos["avg_entry_price"]}

    def get_positions(self) -> List[dict]:
        """Get all open positions."""
        return list(self.positions.values())

    def get_position(self, ticker: str, side: str) -> Optional[dict]:
        key = f"{ticker}_{side}"
        return self.positions.get(key)

    def get_tickers(self) -> set:
        """Get all tickers with open positions."""
        return {pos["ticker"] for pos in self.positions.values()}

    def sync_with_kalshi(self, client) -> dict:
        """
        Sync local positions with Kalshi API. Kalshi is the source of truth.

        Returns:
            dict with 'added', 'removed', 'updated' lists of tickers that changed.
        """
        changes = {"added": [], "removed": [], "updated": [], "mismatches": []}

        try:
            api_positions = client.get_positions()
            api_map = {}  # key -> {ticker, side, position_count}

            for p in api_positions.get("market_positions", []):
                pos_count = p.get("position", 0)
                if pos_count == 0:
                    continue

                ticker = p.get("ticker", "")
                side = "yes" if pos_count > 0 else "no"
                count = abs(pos_count)
                key = f"{ticker}_{side}"
                api_map[key] = {
                    "ticker": ticker,
                    "side": side,
                    "count": count,
                    "market_exposure": p.get("market_exposure", 0),
                }

            # Remove positions that no longer exist on Kalshi
            local_keys = set(self.positions.keys())
            for key in local_keys:
                if key not in api_map:
                    ticker = self.positions[key]["ticker"]
                    changes["removed"].append(ticker)
                    del self.positions[key]

            # Add/update positions from Kalshi
            for key, api_pos in api_map.items():
                if key in self.positions:
                    local = self.positions[key]
                    if local["count"] != api_pos["count"]:
                        changes["mismatches"].append({
                            "ticker": api_pos["ticker"],
                            "local_count": local["count"],
                            "kalshi_count": api_pos["count"],
                        })
                        changes["updated"].append(api_pos["ticker"])
                        local["count"] = api_pos["count"]
                else:
                    # Position exists on Kalshi but not locally
                    self.positions[key] = {
                        "ticker": api_pos["ticker"],
                        "title": "",
                        "side": api_pos["side"],
                        "avg_entry_price": 0,  # unknown from API
                        "count": api_pos["count"],
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "signal_type": "synced_from_kalshi",
                    }
                    changes["added"].append(api_pos["ticker"])

            self._save()

        except Exception as e:
            logger.error(f"[PositionManager] Kalshi sync error: {e}")

        return changes


# ============================================================================
# Trade Logger
# ============================================================================

class TradeLogger:
    """Append-only trade log for analysis."""

    def __init__(self, log_file: Path = TRADES_FILE):
        self.file = log_file
        self.file.parent.mkdir(parents=True, exist_ok=True)

    def log_trade(self, record: TradeRecord):
        with open(self.file, "a") as f:
            f.write(json.dumps(asdict(record)) + "\n")

    def get_history(self, limit: int = 50) -> List[dict]:
        if not self.file.exists():
            return []
        records = []
        with open(self.file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records[-limit:]

    def get_pnl_summary(self) -> dict:
        """Calculate P&L from trade history."""
        history = self.get_history(limit=10000)
        if not history:
            return {"total_trades": 0, "total_cost": 0, "message": "No trades yet"}

        total_cost = sum(h.get("total_cost", 0) for h in history if h.get("action") == "buy")
        total_revenue = sum(h.get("total_cost", 0) for h in history if h.get("action") == "sell")
        buys = sum(1 for h in history if h.get("action") == "buy")
        sells = sum(1 for h in history if h.get("action") == "sell")

        return {
            "total_trades": len(history),
            "buys": buys,
            "sells": sells,
            "total_spent": round(total_cost, 2),
            "total_received": round(total_revenue, 2),
            "realized_pnl": round(total_revenue - total_cost, 2),
        }


# ============================================================================
# Trade Engine
# ============================================================================

class TradeEngine:
    """
    Order execution engine with safety checks.

    CRITICAL: No auto-execution. All trades require explicit confirmation.
    """

    def __init__(self, use_demo: bool = True):
        self.config = KalshiConfig.from_env()
        if not use_demo:
            self.config.use_demo = False
        self.client = create_client(self.config)
        self.safety = SafetyManager()
        self.positions = PositionManager()
        self.trade_log = TradeLogger()

    def size_order(self, model_prob: float, market_price: float,
                   side: str = "yes") -> int:
        """
        Calculate optimal position size using Kelly Criterion.

        Returns number of contracts (each $1 notional).
        """
        result = self.safety.calculate_bet_size(model_prob, market_price)
        if result is None:
            return 0

        bet_amount = result  # dollars
        price = market_price if side == "yes" else (1 - market_price)

        if price <= 0:
            return 0

        # Number of contracts = bet_amount / price_per_contract
        contracts = int(bet_amount / price)
        return max(0, min(contracts, 100))  # cap at 100 contracts

    def validate_trade(self, order: TradeOrder) -> tuple:
        """
        Run all safety checks on a proposed trade.
        Returns (is_valid: bool, reason: str).
        """
        # Safety manager check
        can_result = self.safety.can_bet()
        if isinstance(can_result, tuple):
            can_bet, reason = can_result
        else:
            can_bet, reason = can_result, ""
        if not can_bet:
            return False, f"Safety block: {reason}"

        price_dollars = order.price_cents / 100
        total_cost = price_dollars * order.count

        # Validate bet
        edge = order.edge
        strategy = "live" if order.signal_type in ("overreaction_fade",) else "pregame"
        spread = 10  # default, would need actual spread

        val_result = self.safety.validate_bet(total_cost, edge, strategy, spread)
        # validate_bet returns tuple (is_valid, message) or None
        if isinstance(val_result, tuple):
            is_valid, msg = val_result
            if not is_valid:
                return False, f"Safety rejection: {msg}"
        elif val_result is not None:
            return False, f"Safety rejection: {val_result}"

        # Additional checks
        if order.count <= 0:
            return False, "Count must be positive"
        if order.price_cents < 1 or order.price_cents > 99:
            return False, "Price must be 1-99 cents"
        if total_cost > SAFETY_CONFIG["max_single_bet"]:
            return False, f"Total cost ${total_cost:.2f} exceeds max bet ${SAFETY_CONFIG['max_single_bet']}"

        return True, "OK"

    def prepare_order(self, ticker: str, side: str, price_cents: int,
                      count: int, signal_type: str = "manual",
                      reasoning: str = "", model_prob: float = 0,
                      edge: float = 0) -> TradeOrder:
        """Create a TradeOrder object for review."""
        ev = net_ev(model_prob, price_cents / 100, side)
        return TradeOrder(
            ticker=ticker,
            side=side,
            action="buy",
            count=count,
            price_cents=price_cents,
            signal_type=signal_type,
            reasoning=reasoning,
            model_prob=model_prob,
            edge=edge,
            net_ev_cents=round(ev * 100, 2),
        )

    def execute_order(self, order: TradeOrder, dry_run: bool = True) -> dict:
        """
        Execute a trade order via Kalshi API.

        Args:
            order: The validated trade order
            dry_run: If True, simulate without actual execution

        Returns:
            Execution result dict
        """
        # Final safety check
        valid, reason = self.validate_trade(order)
        if not valid:
            return {"status": "rejected", "reason": reason}

        price_dollars = order.price_cents / 100
        total_cost = price_dollars * order.count

        if dry_run:
            result = {
                "status": "dry_run",
                "order": asdict(order),
                "total_cost": round(total_cost, 2),
                "message": "DRY RUN - not executed. Use --live to execute.",
            }
        else:
            # Step 1: API call (isolated try/except)
            api_success = False
            try:
                api_result = self.client.create_order(
                    ticker=order.ticker,
                    side=order.side,
                    action=order.action,
                    count=order.count,
                    type="limit",
                    yes_price=order.price_cents if order.side == "yes" else None,
                    no_price=order.price_cents if order.side == "no" else None,
                )
                order_id = api_result.get("order", {}).get("order_id", "unknown")
                result = {
                    "status": "submitted",
                    "order_id": order_id,
                    "api_response": api_result,
                    "total_cost": round(total_cost, 2),
                }
                api_success = True
            except Exception as e:
                result = {"status": "error", "error": str(e)}

            # Step 2: Post-API bookkeeping (errors here don't change result status)
            if api_success:
                try:
                    self.safety.record_bet_placed()
                except Exception as e:
                    logger.error(f"[TradeEngine] safety.record_bet_placed error: {e}")

        # Log the trade
        source = "manual" if order.signal_type == "manual" else "auto"
        record = TradeRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            ticker=order.ticker,
            title="",
            side=order.side,
            action=order.action,
            count=order.count,
            price_cents=order.price_cents,
            total_cost=round(total_cost, 2),
            signal_type=order.signal_type,
            reasoning=order.reasoning,
            model_prob=order.model_prob,
            edge=order.edge,
            order_id=result.get("order_id", "dry_run"),
            status=result["status"],
            source=source,
        )
        self.trade_log.log_trade(record)

        # Track position (even dry runs for paper trading)
        if result["status"] in ("submitted", "dry_run"):
            try:
                self.positions.add_position(
                    ticker=order.ticker,
                    title="",
                    side=order.side,
                    price=price_dollars,
                    count=order.count,
                    signal_type=order.signal_type,
                )
            except Exception as e:
                logger.error(f"[TradeEngine] position tracking error: {e}")

        return result

    def exit_position(self, ticker: str, side: str, price_cents: int,
                      count: int, dry_run: bool = True) -> dict:
        """Exit (sell) an existing position."""
        pos = self.positions.get_position(ticker, side)
        if not pos:
            return {"status": "error", "reason": f"No open {side} position for {ticker}"}

        exit_count = min(count, pos["count"])
        entry_price = pos["avg_entry_price"]
        exit_price = price_cents / 100

        # P&L calculation
        if side == "yes":
            gross_pnl = (exit_price - entry_price) * exit_count
        else:
            gross_pnl = (entry_price - exit_price) * exit_count

        # Fee on profit only
        fee = max(0, gross_pnl) * KALSHI_FEE_RATE
        net_pnl = gross_pnl - fee

        if dry_run:
            result = {
                "status": "dry_run",
                "exit_count": exit_count,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "gross_pnl": round(gross_pnl, 4),
                "fee": round(fee, 4),
                "net_pnl": round(net_pnl, 4),
                "message": "DRY RUN - not executed.",
            }
        else:
            try:
                api_result = self.client.create_order(
                    ticker=ticker,
                    side=side,
                    action="sell",
                    count=exit_count,
                    type="limit",
                    yes_price=price_cents if side == "yes" else None,
                    no_price=price_cents if side == "no" else None,
                )
                result = {
                    "status": "submitted",
                    "order_id": api_result.get("order", {}).get("order_id", "unknown"),
                    "exit_count": exit_count,
                    "net_pnl": round(net_pnl, 4),
                }

                self.safety.record_result(net_pnl > 0, net_pnl)

            except Exception as e:
                result = {"status": "error", "error": str(e)}

        # Reduce position
        if result["status"] in ("submitted", "dry_run"):
            self.positions.reduce_position(ticker, side, exit_count)

        # Log exit trade
        record = TradeRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            ticker=ticker, title="",
            side=side, action="sell",
            count=exit_count,
            price_cents=price_cents,
            total_cost=round(exit_price * exit_count, 2),
            signal_type="exit",
            reasoning=f"Exit at {price_cents}c, net P&L: ${net_pnl:.2f}",
            model_prob=0, edge=0,
            order_id=result.get("order_id", "dry_run"),
            status=result["status"],
        )
        self.trade_log.log_trade(record)

        return result


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Trade Engine - Order Execution & Position Management"
    )
    parser.add_argument("--positions", action="store_true", help="Show open positions")
    parser.add_argument("--history", action="store_true", help="Show trade history")
    parser.add_argument("--pnl", action="store_true", help="Show P&L summary")
    parser.add_argument("--execute", type=str, metavar="TICKER", help="Execute trade on TICKER")
    parser.add_argument("--exit", type=str, metavar="TICKER", help="Exit position on TICKER")
    parser.add_argument("--side", type=str, choices=["yes", "no"], default="yes")
    parser.add_argument("--price", type=int, help="Price in cents (1-99)")
    parser.add_argument("--size", type=int, default=1, help="Number of contracts")
    parser.add_argument("--live", action="store_true", help="Execute for real (default: dry run)")
    parser.add_argument("--prod", action="store_true", help="Use production API")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.positions:
        pm = PositionManager()
        positions = pm.get_positions()
        print("=" * 60)
        print(f"Open Positions ({len(positions)})")
        print("=" * 60)
        if not positions:
            print("  No open positions.")
        for p in positions:
            entry = p["avg_entry_price"]
            print(
                f"  {p['ticker'][:45]:45s} | "
                f"{p['side'].upper()} x{p['count']} @ {entry*100:.0f}c | "
                f"signal: {p['signal_type']}"
            )
        return

    if args.history:
        tl = TradeLogger()
        history = tl.get_history()
        print("=" * 60)
        print(f"Trade History ({len(history)} trades)")
        print("=" * 60)
        for h in history:
            print(
                f"  {h['timestamp'][:19]} | "
                f"{h['action'].upper():4s} {h['side'].upper():3s} x{h['count']} @ {h['price_cents']}c | "
                f"{h['ticker'][:35]:35s} | {h['status']}"
            )
        return

    if args.pnl:
        tl = TradeLogger()
        summary = tl.get_pnl_summary()
        print("=" * 60)
        print("P&L Summary")
        print("=" * 60)
        for k, v in summary.items():
            print(f"  {k}: {v}")
        return

    if args.execute:
        if not args.price:
            print("ERROR: --price required for execution")
            return

        # === PAPER TRADING MODE: block all live execution ===
        if args.live:
            print("=" * 60)
            print("  Paper trading mode active. Live trading disabled.")
            print("  All trades run as dry-run only.")
            print("=" * 60)

        use_demo = not args.prod
        engine = TradeEngine(use_demo=use_demo)

        order = engine.prepare_order(
            ticker=args.execute,
            side=args.side,
            price_cents=args.price,
            count=args.size,
            signal_type="manual",
            reasoning="Manual trade",
        )

        # Validate
        valid, reason = engine.validate_trade(order)
        if not valid:
            print(f"REJECTED: {reason}")
            return

        dry_run = True  # FORCED: paper trading mode
        env = "PRODUCTION" if args.prod else "DEMO"

        print("=" * 60)
        print(f"Trade Execution {'(DRY RUN)' if dry_run else '*** LIVE ***'}")
        print(f"Environment: {env}")
        print("=" * 60)
        print(f"  Ticker: {args.execute}")
        print(f"  Side:   {args.side.upper()}")
        print(f"  Price:  {args.price}c")
        print(f"  Count:  {args.size}")
        print(f"  Cost:   ${args.price / 100 * args.size:.2f}")

        if not dry_run:
            print("\n  *** THIS IS A LIVE TRADE ***")
            confirm = input("  Type 'CONFIRM' to proceed: ")
            if confirm != "CONFIRM":
                print("  Cancelled.")
                return

        result = engine.execute_order(order, dry_run=dry_run)
        print(f"\n  Result: {result['status']}")
        for k, v in result.items():
            if k != "status":
                print(f"  {k}: {v}")
        return

    if args.exit:
        if not args.price:
            print("ERROR: --price required for exit")
            return

        if args.live:
            print("Paper trading mode active. Live trading disabled.")

        use_demo = not args.prod
        engine = TradeEngine(use_demo=use_demo)

        dry_run = True  # FORCED: paper trading mode
        result = engine.exit_position(
            ticker=args.exit,
            side=args.side,
            price_cents=args.price,
            count=args.size,
            dry_run=dry_run,
        )
        print("=" * 60)
        print(f"Exit {'(DRY RUN)' if dry_run else '*** LIVE ***'}")
        print("=" * 60)
        for k, v in result.items():
            print(f"  {k}: {v}")
        return

    # Default: show help
    parser.print_help()


if __name__ == "__main__":
    main()
