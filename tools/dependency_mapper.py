"""War Machine dependency mapper - AST-based, accurate import graph."""
import ast
import sys
import io
from pathlib import Path
from collections import defaultdict

# Force UTF-8 output on Windows
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PROJECT_ROOT = Path('.').resolve()
SCAN_DIRS = ['scripts', 'shared', 'mlb/scripts', 'mlb/config', 'tools']


class ImportVisitor(ast.NodeVisitor):
    def __init__(self):
        self.imports = []

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ''
        for alias in node.names:
            full = f"{module}.{alias.name}" if module else alias.name
            self.imports.append(full)
        self.generic_visit(node)


def scan_file(path):
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        v = ImportVisitor()
        v.visit(tree)
        return v.imports
    except Exception as e:
        return [f"PARSE_ERROR: {e}"]


# Scan all .py files
files_data = {}
for sd in SCAN_DIRS:
    dir_path = PROJECT_ROOT / sd
    if not dir_path.exists():
        continue
    for py in dir_path.rglob('*.py'):
        spy = str(py)
        if '__pycache__' in spy or '_emergency' in spy or '_cleanup_archive' in spy:
            continue
        rel = py.relative_to(PROJECT_ROOT).as_posix()
        files_data[rel] = scan_file(py)

# Build reverse index
who_imports = defaultdict(list)
our_modules = set()
for fp in files_data:
    mod = Path(fp).stem
    our_modules.add(mod)

for fp, imps in files_data.items():
    for imp in imps:
        parts = imp.split('.')
        for part in parts:
            if part in our_modules:
                who_imports[part].append(fp)
                break

# === REPORT ===
print("=" * 70)
print("WAR MACHINE - DEPENDENCY MAP")
print("=" * 70)
print()

print("[1] FILES SCANNED")
print(f"    Total: {len(files_data)} files")
print()
for fp in sorted(files_data):
    print(f"    {fp}")
print()


print("[2] CORE EXECUTION CHAIN")
print("    runner.py = NBA entry point")
print("    runner_mlb.py = MLB entry point")
print()


def trace_chain(start_module, depth=0, visited=None):
    if visited is None:
        visited = set()
    if start_module in visited or depth > 4:
        return
    visited.add(start_module)
    matching = [fp for fp in files_data if Path(fp).stem == start_module]
    if not matching:
        return
    fp = matching[0]
    indent = "    " + ("  " * depth) + ("|- " if depth > 0 else "")
    print(f"{indent}{start_module}.py  ({fp})")
    for imp in files_data.get(fp, []):
        parts = imp.split('.')
        for part in parts:
            if part in our_modules and part != start_module and part not in visited:
                trace_chain(part, depth + 1, visited)
                break


print("--- NBA chain ---")
trace_chain('runner')
print()
print("--- MLB chain ---")
trace_chain('runner_mlb')
print()


print("[3] WHO IMPORTS WHOM (reverse index)")
print()
for mod in sorted(who_imports.keys()):
    importers = who_imports[mod]
    print(f"    {mod}.py  imported by {len(importers)} file(s):")
    for imp in sorted(set(importers)):
        print(f"        <- {imp}")
    print()


print("[4] ZOMBIE DETECTION (post-cleanup verification)")
print()
all_modules = our_modules
imported_modules = set(who_imports.keys())
entry_points = {'runner', 'runner_mlb', 'main'}
zombies = all_modules - imported_modules - entry_points
print(f"    Total modules: {len(all_modules)}")
print(f"    Imported: {len(imported_modules)}")
print(f"    Entry points: {len(entry_points)}")
print(f"    ZOMBIES: {len(zombies)}")
for z in sorted(zombies):
    print(f"        - {z}")
print()


print("[5] SHARED/ DETAILED USAGE - fixing Phase 1+2 ambiguity")
print()
shared_modules = [Path(fp).stem for fp in files_data if fp.startswith('shared/')]
print(f"    Modules in shared/: {shared_modules}")
print()
for sm in shared_modules:
    nba_users = [fp for fp in who_imports.get(sm, []) if fp.startswith('scripts/')]
    mlb_users = [fp for fp in who_imports.get(sm, []) if fp.startswith('mlb/')]
    print(f"    shared/{sm}.py:")
    print(f"        NBA scripts/ users: {len(nba_users)}")
    for u in nba_users:
        print(f"            - {u}")
    print(f"        MLB mlb/ users: {len(mlb_users)}")
    for u in mlb_users:
        print(f"            - {u}")
    print()


print("[6] DATA FLOW (file-based)")
print()
print("    INPUT sources:")
print("        - Kalshi REST API (kalshi_client)")
print("        - NBA API/scraping (nba_data_collector)")
print("        - MLB Stats API (mlb_data_collector)")
print()
print("    PERSISTENCE layer:")
print("        - PostgreSQL (db.py): markets, price_snapshots, mlb_*")
print("        - JSONL files (data/): prediction_log, trade_log, paper_trades")
print("        - JSON state (data/): positions, safety_state, tuned_params, equity_curve")
print()
print("    OUTPUT actions:")
print("        - Kalshi orders (auto_trader.execute_order)")
print("        - Logs (data/runner.log)")
print()


print("[7] RESPONSIBILITY MATRIX")
print()
responsibilities = {
    'runner': 'NBA main loop, scheduler, lifecycle',
    'auto_trader': 'NBA order execution, position sync, exit logic',
    'signal_engine': 'Edge calculation, signal generation, prop filters',
    'trade_engine': 'Kalshi API calls, sync_with_kalshi, kill_switch gate',
    'kalshi_client': 'Low-level Kalshi REST API wrapper',
    'kalshi_ws': 'Kalshi WebSocket real-time streams',
    'nba_data_collector': 'NBA stats fetch, feature engineering',
    'nba_model': 'NBA XGBoost + Platt scaling',
    'forward_test': 'Paper prediction generation',
    'settle_predictions': 'Box-score-based settlement',
    'safety': 'Drawdown, bankruptcy, circuit breaker logic',
    'kill_switch': 'File-based emergency halt',
    'bankroll': 'Kelly sizing, position size calc',
    'db': 'PG connection pool',
    'db_config': 'PG connection config (env-driven)',
    'tz': 'Timezone helpers',
    'price_analyzer': 'Price movement analysis',
    'market_classifier': 'Prop type routing',
    'market_recorder': 'Market data persistence',
    'self_learner': 'Auto-tune kelly fractions',
    'calibration': 'Calibration tracker',
    'learning_log': 'Signal/trade learning log',
    'risk_decomposition': 'Weekly loss attribution',
    'odds_collector': 'Sportsbook odds (auxiliary)',
    'active_scanner': 'Arbitrage + signal scanner',
    'filters': 'Settlement window filter',
    'main': 'Legacy entry point',
    'mlb_data_collector': 'MLB stats fetch',
    'mlb_features': 'MLB feature engineering',
    'mlb_model': 'MLB strikeouts model',
    'mlb_auto_trader': 'MLB paper-only trader',
    'mlb_signal_engine': 'MLB signal generation',
    'runner_mlb': 'MLB main loop',
}
for mod in sorted(responsibilities):
    if mod in our_modules:
        print(f"    {mod:25s} -> {responsibilities[mod]}")
print()

print("=" * 70)
print("END OF DEPENDENCY MAP")
print("=" * 70)
