---
paths:
  - "scripts/kalshi_client.py"
  - "scripts/kalshi_ws.py"
---

# Kalshi API Rules

These rules load when Claude touches the Kalshi API client or WebSocket code. These files handle real-money order flow.

## Authentication

- **Method**: RSA-PSS signature (not API key in header)
- **Key location**: `KALSHI_PRIVATE_KEY_PATH` env var → `./keys/private_key.pem`
- **NEVER** log, print, or commit the private key content
- **NEVER** set `KALSHI_USE_DEMO=true` in production. Current value: `false` (always prod)
- Client class: `KalshiAPIClient` with `RSASigner` helper (kalshi_client.py:185)

## Rate limits (Basic tier)

| Type | Limit | Notes |
|------|-------|-------|
| Read (GET) | 20 req/sec | Markets, orderbook, positions |
| Write (POST/PUT/DELETE) | 10 req/sec | Orders, cancels |
| Burst | Undocumented | Kalshi may throttle without warning |

- Client handles `429` responses via `retry_after` header (kalshi_client.py:266-269)
- **NEVER** add tight loops calling the API without `time.sleep()`. The `market_recorder` already pushes limits at 60s intervals.

## Critical trap: `expiration_time`

Kalshi `expiration_time` on NBA props is the **season end date** (~April 14), NOT the game date.

```python
# WRONG: using expiration_time to determine game date
game_date = market["expiration_time"]  # ← returns season end

# RIGHT: parse the ticker
# KXNBAREB-26APR19-HOMAWAY-PLAYER-THRESHOLD
ticker_date = parse_ticker_date(ticker)  # ← returns actual game day
```

This trap has caused silent signal outages. Always parse the ticker for game date.

## Position truth

- **Ground truth**: `KalshiAPIClient().get_positions()` — the Kalshi server
- **NOT ground truth**: `data/positions.json` — known sync-wipe bug
- Before any position-related decision, call the API:
  ```python
  from kalshi_client import create_client
  client = create_client()
  positions = client.get_positions()
  ```

## WebSocket (kalshi_ws.py)

- **Current tier**: Basic (limited channels)
- **URL**: `wss://api.elections.kalshi.com/trade-api/ws/v2`
- Supports: `subscribe` to ticker channels for real-time price updates
- `get_market_orderbook(ticker, depth=3)` for L2 data
- **Phase C upgrade**: Apply for Advanced tier at `kalshi.com/account/profile` for deeper orderbook + faster fills

## Order placement rules

1. All live orders flow through `trade_engine.execute_order()` — never call `kalshi_client` directly for order submission.
2. Kill switch check happens at `trade_engine.py:462` before every order.
3. Order timeout: `ORDER_TIMEOUT_SEC = 60` (auto_trader.py:155). Unfilled orders are cancelled.
4. Max bet sizes: `MAX_BET_REGULAR = $35`, `MAX_BET_ARB = $50` (from `SAFETY_CONFIG`).

## Before editing kalshi_client.py

1. **NEVER** change the auth method without testing against the demo environment first.
2. **NEVER** add new write endpoints (POST/PUT/DELETE) without Ikjun's approval — each one can move real money.
3. After any change, verify auth works: `python -c "from kalshi_client import create_client; c=create_client(); print(c.get_balance())"`
4. Check that rate-limit retry logic is preserved (grep for `retry_after`).
