"""Kalshi API direct query - emergency reconciliation 2026-04-15

Read-only: queries balance, positions, orders, trades, specific markets.
No order creation, no cancellation, no modification.
"""
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from kalshi_client import create_client, KalshiConfig  # noqa: E402


def safe(label, fn):
    print(f"\n[{label}]")
    try:
        r = fn()
        print(json.dumps(r, indent=2, default=str))
        return r
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return None


def main():
    cfg = KalshiConfig.from_env()
    print("=" * 70)
    print("KALSHI API DIRECT RECONCILIATION")
    print(f"Run at:   {datetime.now(timezone.utc).isoformat()}")
    print(f"Env:      {'DEMO' if cfg.use_demo else 'PROD'}")
    print(f"Key ID:   {cfg.api_key_id[:12]}***")
    print(f"Key path: {cfg.private_key_path}")
    print("=" * 70)

    client = create_client(cfg)

    # 1. Balance
    safe("BALANCE", lambda: client.get_balance())

    # 2. Open positions (all)
    safe("ALL POSITIONS (limit=100)", lambda: client.get_positions(limit=100))

    # 3. Recent orders (no filter - all statuses)
    safe("ALL RECENT ORDERS", lambda: client.get_orders())

    # 4. Orders by status
    for st in ("resting", "canceled", "executed"):
        safe(f"ORDERS status={st}", lambda s=st: client.get_orders(status=s))

    # 5. Specific tickers of interest (from runner.log evidence)
    tickers = [
        # Vucevic 4/7 CHA@BOS (LIVE per runner.log)
        "KXNBAREB-26APR07CHABOS-BOSNVUCEVIC4-6",
        "KXNBAREB-26APR07CHABOS-BOSNVUCEVIC4-8",
        # Clingan 4/8 POR@SAS (LIVE per runner.log 4/9 06:56)
        "KXNBAREB-26APR08PORSAS-PORDCLINGAN23-12",
        # Clingan 4/14 POR@PHX (LIVE per runner.log 4/15 06:38)
        "KXNBAREB-26APR14PORPHX-PORDCLINGAN23-12",
    ]
    print("\n[SPECIFIC TICKER STATUS]")
    for t in tickers:
        try:
            m = client.get_market(t)
            mk = m.get("market", m) if isinstance(m, dict) else {}
            print(
                f"  {t}: "
                f"status={mk.get('status', '?')} "
                f"result={mk.get('result', '-')} "
                f"yes_bid={mk.get('yes_bid', '-')} "
                f"yes_ask={mk.get('yes_ask', '-')} "
                f"last={mk.get('last_price', '-')}"
            )
        except Exception as e:
            print(f"  {t}: ERROR {type(e).__name__}: {e}")

    # 6. Per-ticker orders (to find 4/15 Clingan order)
    print("\n[ORDERS per Clingan/Vucevic ticker]")
    for t in tickers:
        try:
            o = client.get_orders(ticker=t)
            orders = o.get("orders", []) if isinstance(o, dict) else []
            print(f"  {t}: {len(orders)} order(s)")
            for od in orders[:5]:
                print(
                    f"    - id={od.get('order_id','?')[:12]} "
                    f"side={od.get('side')} "
                    f"action={od.get('action')} "
                    f"count={od.get('count')} "
                    f"price={od.get('yes_price') or od.get('no_price') or '-'} "
                    f"status={od.get('status')} "
                    f"created={od.get('created_ts')}"
                )
        except Exception as e:
            print(f"  {t}: ERROR {e}")

    # 7. Available client methods
    print("\n[CLIENT METHODS]")
    print(sorted(m for m in dir(client) if not m.startswith("_") and callable(getattr(client, m))))


if __name__ == "__main__":
    main()
