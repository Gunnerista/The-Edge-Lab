"""
Module: kalshi_client.py
Purpose: Temporary shim to v1 client during v2 rebuild. Native impl later.
"""

import sys
from pathlib import Path

_V1_ROOT = Path(__file__).resolve().parents[4]
if str(_V1_ROOT) not in sys.path:
    sys.path.insert(0, str(_V1_ROOT))

# Load v2 .env FIRST so any absolute-path overrides (e.g. KALSHI_PRIVATE_KEY_PATH)
# win over v1's project-root .env (which uses a CWD-relative path).
try:
    from dotenv import load_dotenv as _load_dotenv  # noqa: E402

    _V2_ENV = Path(__file__).resolve().parents[3] / ".env"
    if _V2_ENV.exists():
        _load_dotenv(_V2_ENV, override=True)
except ImportError:
    pass

from shared.kalshi_client import create_client  # noqa: E402

try:
    from shared.kalshi_client import KalshiAPIClient as KalshiClient  # noqa: E402
except ImportError:
    try:
        from shared.kalshi_client import KalshiClient  # noqa: E402
    except ImportError:
        class KalshiClient:  # type: ignore[no-redef]
            """Fallback type used only for annotations."""
            pass

__all__ = ["create_client", "KalshiClient"]
