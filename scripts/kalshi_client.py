"""Shim forwarding to shared.kalshi_client — single source of truth.

Historical: scripts/kalshi_client.py existed as duplicate of shared/
(commit 66c6e41). Last divergence caused the 2026-04-20 STEP 4 bug:
check_settlements() called paginate() which existed only in shared/
(commit 4141bbf) but auto_trader resolved to scripts/ — AttributeError.

2026-04-20: converted to shim. All public symbols re-exported from
shared.kalshi_client. Do not add logic here. See CLAUDE.md.

Usage preserved: scripts/*.py continue to use `from kalshi_client import X`
unchanged. Resolution now forwards to shared/kalshi_client.py.
"""
from shared.kalshi_client import *  # noqa: F401,F403

# Explicit re-exports for IDE/linter clarity (all imported via * above)
from shared.kalshi_client import (  # noqa: F401
    KalshiAPIClient,
    KalshiConfig,
    KalshiMarket,
    KalshiEvent,
    KalshiSDKClient,
    RSASigner,
    SPORTS_KEYWORDS,
    create_client,
    parse_market,
    parse_markets_response,
    is_sports_market,
    filter_sports_markets,
    filter_open_sports_markets,
)
