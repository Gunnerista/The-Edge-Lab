"""Zombie verification - check why these were flagged."""
import sys
import io
from pathlib import Path

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PROJECT = Path('.').resolve()

ZOMBIES = [
    'backtest', 'backtest_oos', 'db_config', 'health_check',
    'mlb_data_collector', 'mlb_validate_model', 'odds_collector',
]


def find_files(name):
    found = []
    for c in PROJECT.rglob(f'{name}.py'):
        s = str(c)
        if '__pycache__' in s or 'cleanup_archive' in s or 'historical_patches' in s:
            continue
        found.append(c)
    return found


def search_imports(name):
    """Search every Python file for any import form of `name`."""
    patterns = [
        f"from {name} import",
        f"import {name}",
        f"from .{name}",
        f"from scripts.{name}",
        f"from shared.{name}",
        f"from mlb.{name}",
        f"from mlb.scripts.{name}",
        f"from tools.{name}",
    ]
    found = []
    for sd in ['scripts', 'shared', 'mlb', 'tools']:
        d = PROJECT / sd
        if not d.exists():
            continue
        for py in d.rglob('*.py'):
            spy = str(py)
            if '__pycache__' in spy or 'cleanup_archive' in spy or 'historical_patches' in spy:
                continue
            try:
                text = py.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            for p in patterns:
                if p in text:
                    found.append((py.relative_to(PROJECT).as_posix(), p))
    return found


def is_entry_point(path):
    try:
        text = path.read_text(encoding='utf-8', errors='ignore')
        return ('if __name__' in text) and ('__main__' in text)
    except Exception:
        return False


for z in ZOMBIES:
    files = find_files(z)
    print(f"\n=== {z}.py ===")
    if not files:
        print("  Not found on disk")
        continue
    for c in files:
        rel = c.relative_to(PROJECT).as_posix()
        size = c.stat().st_size
        ep = " (ENTRY POINT - has __main__ guard)" if is_entry_point(c) else ""
        print(f"  Location: {rel} ({size} bytes){ep}")

    imps = search_imports(z)
    if imps:
        print(f"  Imported by:")
        for f, p in sorted(set(imps)):
            print(f"    - {f}  ['{p}']")
    else:
        print(f"  WARNING: NO imports found - true zombie OR standalone tool")

    # Final classification
    has_main = any(is_entry_point(c) for c in files)
    if has_main and not imps:
        print(f"  -> CLASSIFY: standalone tool (CLI), keep")
    elif imps:
        print(f"  -> CLASSIFY: ALIVE (false positive in earlier scan)")
    else:
        print(f"  -> CLASSIFY: TRUE ZOMBIE candidate for archive")
