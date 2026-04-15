"""Force resync positions.json from Kalshi API - one-shot recovery.

Reads Kalshi portfolio via REST, rebuilds local positions.json using
the post-fix schema understanding (position_fp, market_exposure_dollars,
fees_paid_dollars).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from kalshi_client import create_client  # noqa: E402


def main():
    client = create_client()
    api_pos = client.get_positions()
    market_positions = api_pos.get("market_positions", []) or []

    print(f"[Kalshi API] {len(market_positions)} market_positions entries returned")
    for p in market_positions:
        pf = p.get("position_fp", "?")
        t = p.get("ticker", "?")
        exp = p.get("market_exposure_dollars", "?")
        print(f"  raw: ticker={t} position_fp={pf} market_exposure_dollars={exp}")

    positions = {}
    for p in market_positions:
        try:
            pos_f = float(p.get("position_fp", "0"))
        except (TypeError, ValueError):
            pos_f = 0.0
        if pos_f == 0:
            continue

        ticker = p.get("ticker", "")
        side = "yes" if pos_f > 0 else "no"
        count = int(round(abs(pos_f)))
        exposure = float(p.get("market_exposure_dollars", "0") or 0)
        fees = float(p.get("fees_paid_dollars", "0") or 0)
        traded = float(p.get("total_traded_dollars", "0") or 0)

        # Avg entry ≈ total_traded / count × 100 (cents). exposure reflects current mark,
        # traded reflects actual cost at entry.
        avg_price_cents = int(round((traded / count) * 100)) if count > 0 else 0

        key = f"{ticker}_{side}"
        positions[key] = {
            "ticker": ticker,
            "title": "",
            "side": side,
            "avg_entry_price": avg_price_cents / 100.0,
            "count": count,
            "opened_at": p.get("last_updated_ts", datetime.now(timezone.utc).isoformat()),
            "signal_type": "recovered_from_kalshi_20260415",
            "market_exposure": exposure,
            "fees_paid": fees,
            "total_traded": traded,
        }
        print(
            f"  recovered: {ticker} side={side} count={count} "
            f"avg={avg_price_cents}c exposure=${exposure:.2f} fees=${fees:.2f}"
        )

    out = REPO / "data" / "positions.json"
    out.write_text(json.dumps(positions, indent=2), encoding="utf-8")
    print(f"\n[DONE] {out} written with {len(positions)} open position(s)")


if __name__ == "__main__":
    main()
