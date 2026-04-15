"""Append Phase 1-5 summary to docs/SPRINT1_DAY2_RESULTS.md (idempotent)."""
import sys
import io
from pathlib import Path

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

MARKER = "## Late Day 2 — Foundation Cleanup (Phase 1-5)"

ADDENDUM = """

---

## Late Day 2 — Foundation Cleanup (Phase 1-5)

Discovered system was carrying significant technical debt from 5 versions over 3 weeks:

- **8GB SQLite legacy** in data/ (3/29 PG migration left orphan files)
- **Duplicate db.py** (scripts/ and shared/ byte-identical, drift risk)
- **Zombie code** (5 modules archived after AST verification)
- **Backup of backups** (102MB across 2 folders)
- **Test artifacts** (.test_write, runner_test*.log, .fuse_hidden* etc)

### Resolution

- All cruft moved to `_cleanup_archive_20260415/` (safety net, delete in 1 week)
- data/ folder reduced 8086MB → 116MB (98.6% reduction)
- scripts/db.py converted to redirect shim, single source of truth = shared/db.py
- Created tools/ with 5 permanent diagnostic utilities + README
- Health check passes 24 OK / 0 ERR

### Lesson

Tech debt accumulates silently across version transitions. Each migration (SQLite→PG, v3→v4→v5_kelly) left orphans because no one ran cleanup. Solution: tools/health_check.py runs daily, dependency_mapper.py weekly, prevents drift recurrence.
"""

path = Path('docs/SPRINT1_DAY2_RESULTS.md')
if not path.exists():
    print(f"[ERR] {path} not found")
    sys.exit(1)

content = path.read_text(encoding='utf-8')
if MARKER in content:
    print(f"[SKIP] Phase 1-5 section already present in {path.name}")
else:
    path.write_text(content + ADDENDUM, encoding='utf-8')
    print(f"[OK] Phase 1-5 section appended to {path}")
