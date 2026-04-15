"""shared/ reorganization recommendation."""
import re
import sys
import io
from pathlib import Path

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

report_path = Path('docs/DEPENDENCY_MAP_20260415.txt')
if not report_path.exists():
    print("Cannot find dependency map. Run dependency_mapper.py first.")
    sys.exit(1)

content = report_path.read_text(encoding='utf-8')

# Parse [5] section - SHARED/ DETAILED USAGE
m = re.search(r'\[5\] SHARED/.*?(?=\[6\])', content, re.DOTALL)
if not m:
    print("Cannot parse SHARED section")
    sys.exit(1)

section = m.group(0)
modules = re.findall(
    r'shared/(\w+)\.py:\s*\n\s*NBA scripts/ users: (\d+).*?MLB mlb/ users: (\d+)',
    section,
    re.DOTALL,
)

print("=" * 60)
print("SHARED/ MODULE CLASSIFICATION")
print("=" * 60)
print()

classifications = {
    'truly_shared': [],
    'mlb_only': [],
    'nba_only': [],
    'unused': [],
}
for mod, nba, mlb in modules:
    nba_n = int(nba)
    mlb_n = int(mlb)
    if nba_n > 0 and mlb_n > 0:
        classifications['truly_shared'].append(mod)
    elif mlb_n > 0:
        classifications['mlb_only'].append(mod)
    elif nba_n > 0:
        classifications['nba_only'].append(mod)
    else:
        classifications['unused'].append(mod)

print(f"Truly shared (stays in shared/):   {classifications['truly_shared']}")
print(f"MLB-only  (move to mlb/lib/):      {classifications['mlb_only']}")
print(f"NBA-only  (move to scripts/):      {classifications['nba_only']}")
print(f"Unused    (archive):               {classifications['unused']}")
print()
print("RECOMMENDATION:")
if not classifications['truly_shared']:
    print("  shared/ folder name is misleading - nothing is truly shared.")
    print("  Action: rename shared/ -> mlb/lib/ (or distribute to consumers)")
elif len(classifications['truly_shared']) >= 4:
    print("  shared/ name is justified - keep as is.")
else:
    print("  Mixed usage - case-by-case decision needed.")
