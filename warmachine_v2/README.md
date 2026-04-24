# War Machine v2 - 110pt Rebuild

2026-04-23 Ikjun decision: parallel rebuild after v1 corruption (-$5899 bankroll bug).

## v1 vs v2

| Area | v1 | v2 |
|---|---|---|
| State source | JSON-first | Kalshi API-first |
| Features | 2 (season/recent avg) | 20+ (player/opponent/game/market) |
| Models | Gaussian only | Gaussian + XGBoost per-prop + DNP + Ensemble |
| Risk | 1 kill switch | 5-gate engine |
| Monitoring | None | Discord 4-tier + silent-failure detection |
| CLV tracking | None | Full |
| Injury feed | None | ESPN + Reddit + mxufc29 |

## Cutover plan

1. v2 scaffold -> implement
2. Paper shadow mode (v1 live, v2 records only)
3. Cutover (v2 wins -> switch)
