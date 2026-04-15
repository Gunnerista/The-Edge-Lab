"""CLAUDE.md ERRATA append - Day 2 cleanup summary (Phase 1-5).

Idempotent: only appends if the marker is not already present.
"""
import sys
import io
from pathlib import Path

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

MARKER = "ADDENDUM — 2026-04-15 Day 2 Late Cleanup"

ADDENDUM = """

## 🧹 ADDENDUM — 2026-04-15 Day 2 Late Cleanup (Phase 1-5)

Sprint 1 Day 2 후반에 토대 클린업 5단계 진행:

**Phase 1 — 진단:** 누더기 35-40% 측정. data/ 8GB SQLite 잔재가 99% 차지.

**Phase 2 — 안전 정리:** 모든 잔재를 `_cleanup_archive_20260415/`로 이동 (삭제 X).
- SQLite 8GB (market_data.db + WAL + SHM)
- 백업의 백업 102MB (backups/, backup_before_cleanup/)
- .bak 파일 11개 산재
- 테스트 로그 11개 (runner_test/clean/final, .test_write 등)
- Zombie 5개 (watchdog, migrate_*, backfill_settled, backtest_platt)
- 결과: data/ 8086 MB → 116 MB (98.6% 감소)

**Phase 3 — AST 의존성 지도:** Phase 1+2의 grep 한계 정정.
- shared/ 진짜 분류: bankroll/db/kalshi_client/tz는 TRULY-SHARED, filters/safety는 NBA-only
- 진짜 zombie 0개 (Phase 3 7개는 false positive: backtest, mlb_*, db_config 등 모두 entry point)
- 영구 도구 5개 생성: health_check, dependency_mapper, verify_zombies, detect_db_collision, shared_reorg_advisor

**Phase 4 — db.py 통합:** scripts/db.py와 shared/db.py가 byte-identical 중복임 확인.
- scripts/db.py를 redirect shim으로 변환 (shared.db 재export)
- sys.path 메커니즘 해독: NBA는 scripts/ cwd, MLB는 3개 path 모두 등록
- PG roundtrip 검증: redirect 통해 정상 작동

**Phase 5 — Task Scheduler 정리 + GitHub push.**
- WarMachine task: 한글 경로 가리키며 0x800710D 매번 실패 (CLAUDE.md §17 정체) → 삭제 시도
- 정리 결과 GitHub push (Option B: 코드만, CLAUDE.md/docs/ 제외)

**Mystery Writer 19:42 미스터리:** 외부 프로세스 아님. 오늘 아침 fix_safety_state_clingan.py가 UTC/KST 시차로 19:42 KST(=10:42 UTC)에 last_manual_correction 필드 갱신한 것.

**Health check (Phase 5 후):** 24 OK / 1 WARN (runner.log freshness — sleep 중 정상) / 0 ERR.

**누더기 점수:** 35-40% → 약 10% (남은 것은 archive 폴더 1주일 보존분).

---
"""

path = Path('CLAUDE.md')
md = path.read_text(encoding='utf-8')

if MARKER in md:
    print("[SKIP] ADDENDUM already exists in CLAUDE.md")
else:
    new_md = md + ADDENDUM
    path.write_text(new_md, encoding='utf-8')
    old_lines = len(md.splitlines())
    new_lines = len(new_md.splitlines())
    print(f"[OK] CLAUDE.md ADDENDUM appended")
    print(f"     Lines: {old_lines} -> {new_lines} (+{new_lines - old_lines})")
