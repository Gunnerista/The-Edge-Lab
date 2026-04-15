"""War Machine system health checker - run anytime to verify integrity."""
import json
import subprocess
import sys
import io
from pathlib import Path
from datetime import datetime, timezone

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PROJECT = Path(__file__).resolve().parent.parent


class HealthCheck:
    def __init__(self):
        self.checks = []
        self.warnings = []
        self.errors = []

    def ok(self, name, msg=""):
        self.checks.append((name, "OK", msg))

    def warn(self, name, msg):
        self.warnings.append((name, msg))

    def err(self, name, msg):
        self.errors.append((name, msg))

    def report(self):
        print("=" * 60)
        print(f"WAR MACHINE HEALTH CHECK - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)
        for n, _s, m in self.checks:
            print(f"  [OK]   {n:35s} {m}")
        for n, m in self.warnings:
            print(f"  [WARN] {n:35s} {m}")
        for n, m in self.errors:
            print(f"  [ERR]  {n:35s} {m}")
        print()
        print(f"Result: {len(self.checks)} OK, {len(self.warnings)} warn, {len(self.errors)} err")
        return len(self.errors) == 0


h = HealthCheck()

# 1. Critical files exist
critical_files = [
    'scripts/runner.py', 'scripts/auto_trader.py', 'scripts/signal_engine.py',
    'scripts/trade_engine.py', 'scripts/kill_switch.py',
    'data/positions.json', 'data/tuned_params.json', 'data/safety_state.json',
    '.env', 'CLAUDE.md',
]
for f in critical_files:
    if (PROJECT / f).exists():
        h.ok(f"file_exists:{f}")
    else:
        h.err(f"file_missing:{f}", "REQUIRED")

# 2. .env has critical keys
env_path = PROJECT / '.env'
if env_path.exists():
    env = env_path.read_text(encoding='utf-8', errors='ignore')
    for k in ['KALSHI_API_KEY_ID', 'KALSHI_PRIVATE_KEY_PATH', 'WARMACHINE_PG_PASSWORD']:
        if k in env:
            h.ok(f"env_key:{k}")
        else:
            h.warn(f"env_key:{k}", "MISSING")

# 3. safety_state sanity
ss_path = PROJECT / 'data/safety_state.json'
if ss_path.exists():
    try:
        ss = json.loads(ss_path.read_text(encoding='utf-8'))
        if isinstance(ss.get('bankroll'), (int, float)) and ss['bankroll'] >= 0:
            h.ok("safety_state.bankroll", f"${ss['bankroll']}")
        else:
            h.err("safety_state.bankroll", "invalid")
        if 'active_bets' in ss:
            h.ok("safety_state.active_bets", str(ss['active_bets']))
    except Exception as e:
        h.err("safety_state.parse", str(e))

# 4. positions.json sanity
pos_path = PROJECT / 'data/positions.json'
if pos_path.exists():
    try:
        pos = json.loads(pos_path.read_text(encoding='utf-8'))
        if isinstance(pos, dict):
            h.ok("positions.json", f"{len(pos)} active position(s)")
        elif isinstance(pos, list):
            h.ok("positions.json", f"{len(pos)} active position(s) (list form)")
        else:
            h.warn("positions.json", "unexpected structure")
    except Exception as e:
        h.err("positions.json", str(e))

# 5. No hardcoded secrets in tracked code (generic pattern, no literal values)
import re as _re
_hardcoded_pw_re = _re.compile(r'''password\s*=\s*["'][^"']+["']''')
_skip_pw_markers = ('os.environ', 'getenv', 'get(', 'PASSWORD', '.env', 'environ[')
for sd in ['scripts', 'shared', 'mlb/scripts']:
    d = PROJECT / sd
    if not d.exists():
        continue
    for py in d.rglob('*.py'):
        spy = str(py)
        if '__pycache__' in spy or '_emergency' in spy or 'historical_patches' in spy:
            continue
        try:
            text = py.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        for line in text.splitlines():
            if _hardcoded_pw_re.search(line) and not any(m in line for m in _skip_pw_markers):
                h.err(
                    f"hardcoded_secret:{py.relative_to(PROJECT)}",
                    "line appears to hardcode a password",
                )
                break

# 6. .gitignore protects secrets
gi = PROJECT / '.gitignore'
if gi.exists():
    g = gi.read_text(encoding='utf-8', errors='ignore')
    for must in ['.env', 'keys/', '*.pem', '_cleanup_archive']:
        if must in g:
            h.ok(f"gitignore:{must}")
        else:
            h.warn(f"gitignore:{must}", "not protected")

# 7. PG password externalized (generic check - no literal values)
_pg_hardcode_re = _re.compile(r'''password\s*=\s*["'][^"']+["']''')
_pg_skip = ('os.environ', 'getenv', 'environ[')
for f in ['scripts/db.py', 'shared/db.py', 'scripts/db_config.py']:
    fp = PROJECT / f
    if fp.exists():
        text = fp.read_text(encoding='utf-8', errors='ignore')
        if 'WARMACHINE_PG_PASSWORD' in text:
            h.ok(f"pg_externalized:{f}")
        else:
            hardcoded = False
            for line in text.splitlines():
                if _pg_hardcode_re.search(line) and not any(s in line for s in _pg_skip):
                    hardcoded = True
                    break
            if hardcoded:
                h.err(f"pg_hardcoded:{f}", "appears to hardcode a password")

# 8. Python processes alive
try:
    out = subprocess.check_output(['tasklist'], shell=True, text=True)
    count = out.count('python.exe')
    if count >= 2:
        h.ok("python_processes", f"{count} running")
    elif count >= 1:
        h.warn("python_processes", f"only {count} running")
    else:
        h.err("python_processes", "NO RUNNERS")
except Exception as e:
    h.warn("python_processes", str(e))

# 9. Recent runner.log activity
log = PROJECT / 'data/runner.log'
if log.exists():
    mtime = datetime.fromtimestamp(log.stat().st_mtime, timezone.utc)
    age_h = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600
    if age_h < 1:
        h.ok("runner.log_freshness", f"{age_h:.1f}h ago")
    elif age_h < 24:
        h.warn("runner.log_freshness", f"{age_h:.1f}h ago")
    else:
        h.err("runner.log_freshness", f"{age_h:.1f}h ago - STALE")

# 10. CLAUDE.md single source of truth
claude_mds = list(PROJECT.glob('CLAUDE.md'))
if len(claude_mds) == 1:
    h.ok("claude_md_single_source", "only project root")
else:
    h.warn("claude_md_count", f"{len(claude_mds)} found")

success = h.report()
sys.exit(0 if success else 1)
