"""
Module: broker_adapter.py
Purpose: BrokerAdapter ABC + Paper/Kalshi implementations.
         Paper and Live must be interchangeable behind this interface.
         Kalshi fee: fee_cents = max(1, ceil(0.07 * qty * p * (1-p) * 100)).
"""

from __future__ import annotations

import json
import math
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict

from warmachine.risk.types import OrderIntent, OrderSide
from warmachine.utils.logger import get_logger

log = get_logger(__name__)


def kalshi_fee_cents(quantity: int, price_cents: int) -> int:
    """Official Kalshi fee: 7% * qty * p * (1-p), rounded up, min 1c."""
    p = price_cents / 100.0
    raw = 0.07 * quantity * p * (1.0 - p)
    return max(1, math.ceil(raw * 100))


class FillResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str
    ticker: str
    side: OrderSide
    filled_quantity: int
    fill_price_cents: int
    fee_cents: int
    total_cost_cents: int
    filled_at: datetime
    status: str
    reason: Optional[str] = None


class BrokerAdapter(ABC):
    @property
    @abstractmethod
    def mode(self) -> str: ...

    @abstractmethod
    def place_order(self, intent: OrderIntent) -> FillResult: ...

    @abstractmethod
    def get_balance_cents(self) -> int: ...

    @abstractmethod
    def get_cash_cents(self) -> int: ...

    @abstractmethod
    def get_positions(self) -> list[dict]: ...

    @abstractmethod
    def get_current_price_cents(self, ticker: str, side: str) -> int: ...


class PaperAdapter(BrokerAdapter):
    SLIPPAGE_CENTS = 1

    def __init__(
        self,
        state_path: Path,
        initial_balance_cents: int,
        kalshi_price_source: Callable[[str, str], int],
    ):
        self.state_path = Path(state_path)
        self.price_source = kalshi_price_source
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.state_path.exists():
            self._save(
                {
                    "cash_cents": initial_balance_cents,
                    "positions": {},
                    "fill_history": [],
                }
            )

    @property
    def mode(self) -> str:
        return "paper"

    def place_order(self, intent: OrderIntent) -> FillResult:
        state = self._load()
        order_id = f"paper_{uuid.uuid4().hex[:10]}"

        try:
            market_price = self.price_source(intent.ticker, intent.side.value)
        except Exception as e:
            return FillResult(
                order_id=order_id,
                ticker=intent.ticker,
                side=intent.side,
                filled_quantity=0,
                fill_price_cents=0,
                fee_cents=0,
                total_cost_cents=0,
                filled_at=datetime.now(timezone.utc),
                status="rejected",
                reason=f"price lookup failed: {e}",
            )

        if market_price > intent.limit_price_cents:
            return FillResult(
                order_id=order_id,
                ticker=intent.ticker,
                side=intent.side,
                filled_quantity=0,
                fill_price_cents=0,
                fee_cents=0,
                total_cost_cents=0,
                filled_at=datetime.now(timezone.utc),
                status="rejected",
                reason=f"market {market_price}c > limit {intent.limit_price_cents}c",
            )

        fill_price = min(
            intent.limit_price_cents, market_price + self.SLIPPAGE_CENTS
        )
        fee = kalshi_fee_cents(intent.quantity, fill_price)
        total_cost = intent.quantity * fill_price + fee

        if total_cost > state["cash_cents"]:
            return FillResult(
                order_id=order_id,
                ticker=intent.ticker,
                side=intent.side,
                filled_quantity=0,
                fill_price_cents=0,
                fee_cents=0,
                total_cost_cents=0,
                filled_at=datetime.now(timezone.utc),
                status="rejected",
                reason=(
                    f"insufficient cash: need {total_cost}c, "
                    f"have {state['cash_cents']}c"
                ),
            )

        state["cash_cents"] -= total_cost
        pos = state["positions"].get(
            intent.ticker, {"quantity": 0, "total_cost_cents": 0}
        )
        new_qty = pos["quantity"] + intent.quantity
        new_total_cost = pos["total_cost_cents"] + (intent.quantity * fill_price)
        state["positions"][intent.ticker] = {
            "quantity": new_qty,
            "total_cost_cents": new_total_cost,
            "avg_price_cents": new_total_cost // new_qty if new_qty > 0 else 0,
            "side": intent.side.value,
        }

        fill = FillResult(
            order_id=order_id,
            ticker=intent.ticker,
            side=intent.side,
            filled_quantity=intent.quantity,
            fill_price_cents=fill_price,
            fee_cents=fee,
            total_cost_cents=total_cost,
            filled_at=datetime.now(timezone.utc),
            status="filled",
        )
        state["fill_history"].append(fill.model_dump(mode="json"))
        self._save(state)
        log.info(
            f"[PAPER] filled {intent.ticker} {intent.side.value} "
            f"{intent.quantity}@{fill_price}c fee={fee}c"
        )
        return fill

    def get_balance_cents(self) -> int:
        state = self._load()
        cash = state["cash_cents"]
        mtm = 0
        for ticker, pos in state["positions"].items():
            if pos["quantity"] == 0:
                continue
            side = pos.get("side", "YES")
            try:
                p = self.price_source(ticker, side)
                mtm += pos["quantity"] * p
            except Exception:
                mtm += pos["total_cost_cents"]
        return cash + mtm

    def get_cash_cents(self) -> int:
        return self._load()["cash_cents"]

    def get_positions(self) -> list[dict]:
        state = self._load()
        result = []
        for ticker, pos in state["positions"].items():
            if pos["quantity"] == 0:
                continue
            side = pos.get("side", "YES")
            try:
                current = self.price_source(ticker, side)
                market_exposure = pos["quantity"] * current
            except Exception:
                market_exposure = pos["total_cost_cents"]
            result.append(
                {
                    "ticker": ticker,
                    "quantity": pos["quantity"],
                    "market_exposure_cents": market_exposure,
                    "avg_price_cents": pos.get("avg_price_cents", 0),
                }
            )
        return result

    def get_current_price_cents(self, ticker: str, side: str) -> int:
        return self.price_source(ticker, side)

    def settle_position(self, ticker: str, outcome_yes_wins: bool) -> int:
        state = self._load()
        pos = state["positions"].get(ticker)
        if not pos or pos["quantity"] == 0:
            return 0
        side = pos.get("side", "YES")
        won = (side == "YES" and outcome_yes_wins) or (
            side == "NO" and not outcome_yes_wins
        )
        payout = pos["quantity"] * 100 if won else 0
        state["cash_cents"] += payout
        pnl = payout - pos["total_cost_cents"]
        state["positions"][ticker] = {
            "quantity": 0,
            "total_cost_cents": 0,
            "avg_price_cents": 0,
            "side": side,
        }
        self._save(state)
        log.info(
            f"[PAPER] settled {ticker}: {'WIN' if won else 'LOSS'} pnl={pnl}c"
        )
        return pnl

    def _load(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _save(self, state: dict) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(state, indent=2, default=str), encoding="utf-8"
        )
        tmp.replace(self.state_path)


class KalshiAdapter(BrokerAdapter):
    def __init__(self, kalshi_client):
        self.client = kalshi_client

    @property
    def mode(self) -> str:
        return "live"

    POLL_ATTEMPTS = 3
    POLL_INTERVAL_SECONDS = 0.7

    def place_order(self, intent: OrderIntent) -> FillResult:
        order_id_local = f"live_{uuid.uuid4().hex[:10]}"

        kwargs = {
            "ticker": intent.ticker,
            "side": intent.side.value.lower(),
            "action": "buy",
            "count": intent.quantity,
            "type": "limit",
        }
        if intent.side.value == "YES":
            kwargs["yes_price"] = intent.limit_price_cents
        else:
            kwargs["no_price"] = intent.limit_price_cents

        try:
            resp = self.client.create_order(**kwargs)
        except Exception as e:
            log.error(f"[LIVE] create_order failed: {e}")
            return FillResult(
                order_id=order_id_local,
                ticker=intent.ticker,
                side=intent.side,
                filled_quantity=0,
                fill_price_cents=0,
                fee_cents=0,
                total_cost_cents=0,
                filled_at=datetime.now(timezone.utc),
                status="rejected",
                reason=str(e),
            )

        submitted_order = resp.get("order") or {}
        kalshi_order_id = submitted_order.get("order_id")
        if not kalshi_order_id:
            return FillResult(
                order_id=order_id_local,
                ticker=intent.ticker,
                side=intent.side,
                filled_quantity=0,
                fill_price_cents=0,
                fee_cents=0,
                total_cost_cents=0,
                filled_at=datetime.now(timezone.utc),
                status="rejected",
                reason=f"no order_id in response: {submitted_order}",
            )

        return self._hydrate_fill(
            kalshi_order_id=kalshi_order_id,
            local_id=order_id_local,
            intent=intent,
        )

    def _hydrate_fill(
        self,
        kalshi_order_id: str,
        local_id: str,
        intent: OrderIntent,
    ) -> FillResult:
        """Poll /portfolio/fills to build FillResult. Best-effort."""
        for attempt in range(self.POLL_ATTEMPTS):
            try:
                fills_resp = self.client.get_fills(order_id=kalshi_order_id)
                fills = fills_resp.get("fills", []) if fills_resp else []
                if fills:
                    total_qty = 0
                    total_cents = 0
                    total_fee_cents = 0
                    for f in fills:
                        qty = f.get("count_fp") or f.get("count") or 0
                        qty = int(qty) if qty else 0
                        if qty <= 0:
                            continue
                        price = self._extract_price_cents(f, intent.side.value)
                        total_qty += qty
                        total_cents += qty * price
                        total_fee_cents += int(f.get("fee_cost") or 0)
                    if total_qty > 0:
                        avg_price = total_cents // total_qty
                        return FillResult(
                            order_id=local_id,
                            ticker=intent.ticker,
                            side=intent.side,
                            filled_quantity=total_qty,
                            fill_price_cents=avg_price,
                            fee_cents=total_fee_cents,
                            total_cost_cents=total_cents + total_fee_cents,
                            filled_at=datetime.now(timezone.utc),
                            status="filled"
                            if total_qty == intent.quantity
                            else "partial",
                        )
            except Exception as e:
                log.warning(f"[LIVE] get_fills attempt {attempt + 1} failed: {e}")
            time.sleep(self.POLL_INTERVAL_SECONDS)

        return FillResult(
            order_id=local_id,
            ticker=intent.ticker,
            side=intent.side,
            filled_quantity=0,
            fill_price_cents=0,
            fee_cents=0,
            total_cost_cents=0,
            filled_at=datetime.now(timezone.utc),
            status="rejected",
            reason=(
                f"order submitted (kalshi_id={kalshi_order_id}) "
                f"but no fills after {self.POLL_ATTEMPTS} polls"
            ),
        )

    def _extract_price_cents(self, fill: dict, side: str) -> int:
        """Kalshi mixed-unit defense: portfolio endpoints use int cents, markets use $ strings."""
        for k in ("yes_price", "no_price", "price"):
            v = fill.get(k)
            if isinstance(v, int) and 1 <= v <= 99:
                return v
        for k in ("yes_price_dollars", "no_price_dollars"):
            v = fill.get(k)
            if isinstance(v, str):
                try:
                    return int(round(float(v) * 100))
                except (ValueError, TypeError):
                    pass
        if side.upper() == "YES":
            v = fill.get("yes_price_dollars") or fill.get("yes_price")
        else:
            v = fill.get("no_price_dollars") or fill.get("no_price")
        if isinstance(v, (int, float)):
            return int(v) if v >= 1 else int(round(v * 100))
        if isinstance(v, str):
            try:
                return int(round(float(v) * 100))
            except (ValueError, TypeError):
                pass
        return 0

    def get_balance_cents(self) -> int:
        bal = self.client.get_balance()
        return int(bal["balance"]) + int(bal.get("portfolio_value", 0))

    def get_cash_cents(self) -> int:
        return int(self.client.get_balance()["balance"])

    def get_positions(self) -> list[dict]:
        resp = self.client.get_positions_raw()
        result = []
        for p in resp.get("market_positions", []):
            if p.get("position", 0) == 0:
                continue
            result.append(
                {
                    "ticker": p["ticker"],
                    "quantity": p["position"],
                    "market_exposure_cents": p.get("market_exposure", 0),
                    "avg_price_cents": p.get("average_cost", 0),
                }
            )
        return result

    def get_current_price_cents(self, ticker: str, side: str) -> int:
        # Use signed get_market() instead of session.get (which is unauthenticated).
        try:
            m = self.client.get_market(ticker).get("market", {})
        except Exception:
            m = {}

        if side.upper() == "YES":
            v = m.get("yes_ask_dollars") or m.get("yes_ask")
        else:
            v = m.get("no_ask_dollars") or m.get("no_ask")

        if v is None:
            return 50
        if isinstance(v, str):
            try:
                return int(round(float(v) * 100))
            except ValueError:
                return 50
        if isinstance(v, (int, float)):
            if v >= 1:
                return int(v)
            return int(round(v * 100))
        return 50
