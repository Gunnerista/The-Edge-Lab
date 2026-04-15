"""db.py consolidation - convert scripts/db.py into a thin redirect to shared/db.py.

Run-once. After this:
  - shared/db.py is the single source of truth
  - scripts/db.py is a 13-line shim that re-exports from shared.db
  - Existing 'from db import ...' calls still work
"""
import sys
import io
from pathlib import Path

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

REDIRECT = '''"""DEPRECATED: Use 'from shared.db import ...' instead.

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
'''

target = Path('scripts/db.py')
old_size = target.stat().st_size
target.write_text(REDIRECT, encoding='utf-8')
new_size = target.stat().st_size
print(f"[OK] scripts/db.py converted to redirect shim")
print(f"     Old size: {old_size} bytes")
print(f"     New size: {new_size} bytes")
