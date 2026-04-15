# tools/ — War Machine Permanent Tools

Diagnostic and maintenance utilities. Run any time. Read-only unless noted.

## Diagnostic

### `health_check.py`
Verify system integrity. Checks 25+ items: critical files exist,
.env keys present, safety_state sanity, no hardcoded secrets,
.gitignore protections, PG password externalized, Python processes alive,
runner.log freshness, single CLAUDE.md source.

```
python tools\health_check.py
```

Exit code 0 = OK. Exit code 1 = errors found.

### `dependency_mapper.py`
AST-based import graph analysis. Generates 7-section dependency map:
files scanned, core execution chain, who-imports-whom, zombie detection,
shared/ usage, data flow, responsibility matrix.

```
python tools\dependency_mapper.py > docs\DEPENDENCY_MAP_YYYYMMDD.txt
```

### `verify_zombies.py`
Verify zombie module candidates by checking ALL import patterns
plus `__main__` guards. Eliminates grep false positives.

```
python tools\verify_zombies.py
```

### `detect_db_collision.py`
Detect duplicate or conflicting `db` module imports across project.

```
python tools\detect_db_collision.py
```

### `shared_reorg_advisor.py`
Analyze shared/ folder usage and recommend reorganization
(truly shared vs NBA-only vs MLB-only).

```
python tools\shared_reorg_advisor.py
```

### `phase1_diagnose.ps1`
Folder-level diagnostic (sizes, last-modified, root junk, .bak remnants,
__pycache__, 24h activity). Reusable any time the project layout drifts.

```
powershell -ExecutionPolicy Bypass -File tools\phase1_diagnose.ps1
```

## Maintenance

### `consolidate_db.py`
One-shot tool that converted `scripts/db.py` into a thin redirect
to `shared/db.py`. Run-once on 2026-04-15. Kept for reference.

## Usage Pattern

- Run `health_check.py` daily or after any code change.
- Run `dependency_mapper.py` weekly or after structural changes.
- Run `verify_zombies.py` before deleting any code "you think" is unused.

## Adding New Tools

1. Place `.py` file in `tools/`
2. Update this README
3. Make sure it's read-only or has clear opt-in for write actions
4. Use `if __name__ == "__main__":` guard
