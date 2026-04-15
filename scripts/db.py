"""DEPRECATED: Use 'from shared.db import ...' instead.

This shim exists only to avoid breaking existing 'from db import ...' calls.
It re-exports everything from shared/db.py. Single source of truth = shared/db.py.

Migrate callers to: from shared.db import get_connection, put_connection, close_all
"""
import sys
from pathlib import Path

# Add project root so 'shared.db' is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.db import *  # noqa: F401, F403
from shared.db import get_connection, put_connection, close_all  # noqa: F401
