"""db.py import collision detector - AST-based, picks up all import forms."""
import ast
import sys
import io
from pathlib import Path
from collections import defaultdict

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PROJECT = Path('.').resolve()
SCAN = ['scripts', 'shared', 'mlb/scripts']
EXCLUDE = ['__pycache__', '_emergency', '_cleanup_archive', 'historical_patches']


class V(ast.NodeVisitor):
    def __init__(self):
        self.imports = []

    def visit_Import(self, n):
        for a in n.names:
            self.imports.append(('import', a.name))

    def visit_ImportFrom(self, n):
        m = n.module or ''
        for a in n.names:
            self.imports.append(('from', f"{m}::{a.name}"))


# Map: import-pattern -> list of files using it
db_users = defaultdict(list)

for sd in SCAN:
    base = PROJECT / sd
    if not base.exists():
        continue
    for py in base.rglob('*.py'):
        if any(e in str(py) for e in EXCLUDE):
            continue
        try:
            tree = ast.parse(py.read_text(encoding='utf-8'))
        except Exception:
            continue
        v = V()
        v.visit(tree)
        rel = py.relative_to(PROJECT).as_posix()
        for kind, name in v.imports:
            # Match anything that resolves to a 'db' module
            if kind == 'import':
                if name == 'db' or name.endswith('.db'):
                    db_users[f"import {name}"].append(rel)
            else:  # from
                module, symbol = name.split('::', 1) if '::' in name else (name, '')
                if module == 'db' or module.endswith('.db') or module == 'shared' and symbol == 'db':
                    db_users[f"from {module} import {symbol}"].append(rel)

print("Files importing 'db' module (any form):")
print()
for pattern, files in sorted(db_users.items()):
    print(f"  '{pattern}' used by {len(files)} file(s):")
    for f in sorted(set(files)):
        print(f"    - {f}")
    print()

print("=" * 60)
unique_modules = set()
for pattern in db_users:
    if pattern.startswith('import '):
        unique_modules.add(pattern.split(' ', 1)[1])
    elif pattern.startswith('from '):
        # 'from X import Y' -> X
        unique_modules.add(pattern.split(' ', 2)[1])

print(f"Unique db module sources resolved: {sorted(unique_modules)}")
if len(unique_modules) > 1:
    print("WARNING: Multiple db sources detected - check sys.path resolution")
else:
    print("OK: Single db source - no collision at import name level")
