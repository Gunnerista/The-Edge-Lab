# PREDICTION MARKET WAR MACHINE — CLAUDE.md
> Claude Code: 이 파일을 먼저 읽고 작업을 시작하세요.
> 마지막 업데이트: 2026-04-04

---

## 1. 프로젝트 개요

**EDGE — NBA Player Props 예측 시스템 (Kalshi 기반)**

전통 스포츠북이 아닌 **Kalshi Prediction Market** 전용 NBA props 예측 시스템.
NBA 박스스코어 통계로 선수별 득점/리바운드 Over/Under 확률을 계산하고,
Kalshi 시장 가격과 비교해 엣지를 찾아 베팅하는 시스템.

현재 상태: **PAPER TRADING MODE** (`runner.py:549` `PAPER_TRADING = True`)
현재 config: **`v4_edge_optimized`** (2026-04-04 배포)

---

## 2. 현재 config — v4_edge_optimized

### 진입 기준

| Prop Type | YES 최소 Edge | NO 최소 Edge | 상태 |
|-----------|--------------|-------------|------|
| Points | ≥ 30% | ≥ 25% | Active |
| Rebounds | ≥ 25% | ≥ 20% | Active |
| Assists | — | — | **Disabled** |
| Threes/기타 | ≥ 30% | ≥ 25% | Active |

### 필터 파라미터

| Parameter | 값 | 위치 |
|-----------|---|------|
| `YES_EDGE_PREMIUM` | +0.05 | `signal_engine.py` |
| `GLOBAL_MIN_EDGE` | 0.15 | `signal_engine.py` |
| `PROP_MIN_EDGE` (points) | 0.25 | `signal_engine.py` |
| `PROP_MIN_EDGE` (rebounds) | 0.20 | `signal_engine.py` |
| Platt A | 0.8976 | `nba_model.py:99` |
| Platt B | -0.4242 | `nba_model.py:100` |
| 2x Spread Rule | YES only | `auto_trader.py` |

### 진입 로직 (auto_trader.py → `should_place_bet()`)

```python
def should_place_bet(prop_type, side, calculated_edge):
    # 1) prop type 활성화 체크
    if not PROP_CONFIG[prop_type]["enabled"]:
        return False
    # 2) prop별 MIN_EDGE
    min_edge = PROP_CONFIG[prop_type]["MIN_EDGE"]
    # 3) YES side premium 추가
    if side == "YES":
        min_edge += YES_EDGE_PREMIUM
    # 4) global floor
    min_edge = max(min_edge, GLOBAL_MIN_EDGE)
    return calculated_edge >= min_edge
```

### Entry Price 계산 (signal_engine.py)

```python
# YES bets: buy at the ask
entry_price = market_data["yes_ask"]
# NO bets: inverse of yes bid
entry_price = 1 - market_data["yes_bid"]
```

> ⚠️ v3 이전에는 YES bet에서 bid를 사용 — edge가 체계적으로 과대 평가됨. 4/3에 수정됨.

---

## 3. Config 버전 이력

| Config | 기간 | 핵심 변경 |
|--------|------|-----------|
| `legacy` | ~3/29 | Rule-based, 단일 MIN_EDGE |
| `v2_platt_edge20` | 3/29~3/31 | Platt scaling 적용, MIN_EDGE 0.20 |
| `v3_prop_edge` | 3/31~4/3 | Prop별 차등 MIN_EDGE (assists 0.15, pts/reb 0.25) |
| **`v4_edge_optimized`** | **4/4~현재** | **Assists 비활성화, rebounds 0.20, YES premium +0.05, global floor 0.15** |

prediction_log의 `config_version` 필드로 구분. 분석 시 반드시 config별로 분리할 것.

---

## 4. 디렉토리 구조

```
C:\Users\Dell\Desktop\Projects\sports-betting-system\
├── .env                        # API 키 (KALSHI, DISCORD, ODDS_API)
├── keys/
│   └── private_key.pem         # Kalshi RSA-PSS 인증 키
├── scripts/                    # 모든 실행 파일
│   ├── runner.py               # 메인 오케스트레이터 (상시 실행, 부모-자식 프로세스)
│   ├── market_recorder.py      # Kalshi 마켓 데이터 수집 → PostgreSQL
│   ├── forward_test.py         # NBA props 예측 생성 → prediction_log
│   ├── settle_predictions.py   # 박스스코어로 예측 정산
│   ├── nba_model.py            # NBA 확률 모델 (XGBoost + Platt scaling)
│   ├── kalshi_client.py        # Kalshi REST API (RSA-PSS 인증)
│   ├── kalshi_ws.py            # Kalshi WebSocket 실시간 스트리밍
│   ├── auto_trader.py          # 자동 트레이딩 엔진 + should_place_bet()
│   ├── active_scanner.py       # 차익거래 + 시그널 스캔
│   ├── signal_engine.py        # 시그널 생성 엔진 (PROP_MIN_EDGE, YES_PREMIUM, GLOBAL)
│   ├── market_classifier.py    # 마켓 자동 분류
│   ├── trade_engine.py         # 주문 실행 + 포지션 관리
│   ├── bankroll.py             # Kelly Criterion 자금 관리
│   ├── safety.py               # 일일/주간 손실 한도 안전장치
│   ├── calibration.py          # 모델 캘리브레이션 추적
│   ├── self_learner.py         # 파라미터 자동 튜닝 (일요일 10pm)
│   ├── learning_log.py         # 시그널/트레이드 학습 로그
│   ├── risk_decomposition.py   # 주간 손실 분해 분석
│   ├── backtest.py / backtest_oos.py / backtest_platt.py
│   ├── tz.py                   # ET 타임존 유틸리티
│   └── watchdog.py             # 프로세스 모니터링
└── data/
    ├── prediction_log.jsonl    # 예측 로그 (append-only, 무결성 최우선)
    ├── trade_log.jsonl         # 실행 트레이드 로그
    ├── paper_trades.jsonl      # 페이퍼 트레이딩 기록
    ├── learning_log.jsonl      # 시그널 감지 이력
    ├── equity_curve.json       # 날짜별 bankroll 추적
    ├── runner.log              # runner.py 실행 로그
    ├── calibration_state.json  # 캘리브레이션 상태
    ├── tuned_params.json       # self_learner 자동 튜닝 파라미터
    └── safety_state.json       # 안전장치 상태
```

> ⚠️ 프로젝트 경로는 반드시 `C:\Users\Dell\Desktop\Projects\sports-betting-system`. **D드라이브 아님.**

---

## 5. 데이터베이스 — PostgreSQL

### 연결 정보
- DB: `warmachine`
- Host: `localhost:5432`
- 드라이버: `psycopg2` with `ThreadedConnectionPool`

### 테이블

| 테이블 | 설명 | 규모 |
|--------|------|------|
| `markets` | Kalshi 마켓 메타데이터 | ~769만 건 |
| `price_snapshots` | 마켓 가격 스냅샷 (60s 간격) | ~428만 건 |
| `nba_players` | NBA 선수 정보 | 445명 |
| `nba_teams` | NBA 팀 정보 | 30팀 |
| `nba_games` | NBA 경기 정보 | ~23+ 건 |

> ⚠️ SQLite에서 마이그레이션 완료 (3/29). `data/market_data.db`는 레거시 — 새 코드에서 참조하지 말 것.

---

## 6. 핵심 파일별 역할

### `runner.py` — 메인 오케스트레이터

**부모-자식 프로세스 구조**로 실행 (2개 PID).

두 개 스레드를 동시 운영:
- **Observer 스레드** (60s 간격): `market_recorder.RESTRecorder.record_snapshot()` → PostgreSQL
- **Scanner 스레드** (120s 간격): `AutoTrader.run_cycle()` + `ActiveScanner` + `SignalEngine`

**NBA 스케줄 자동 감지:**
- 기동 시 NBA API로 오늘 경기 유무 확인
- 경기 있으면: 첫 tipoff 2시간 전부터 가동, 마지막 경기 종료 +1시간 후 shutdown
- 경기 없으면: 다음 게임데이까지 sleep
- `shutdown_at` 최소: 12:30 AM ET (경기가 일찍 끝나도 최소 이 시각까지 가동)
- 게임 윈도우 밖에서 기동되면 4 PM ET까지 sleep (restart loop 방지)

스케줄러 (메인 스레드, 60s 루프):
- 12:00 PM ET → `forward_test.run_forward_test(source="auto")`
- 11:59 PM ET → `settle_predictions.settle()`
- 10:00 PM ET → Discord 데일리 요약 (한국어)
- 기동 시 catch-up: 12pm 이후 기동 + 오늘 auto 예측 없음 + active props 있으면 즉시 forward test 실행

### `market_recorder.py` — 데이터 수집
- `RESTRecorder.record_snapshot()`: Kalshi 전체 마켓 스냅샷을 PostgreSQL에 저장
- 페이지당 1000건, 최대 200페이지 (=200K 마켓 처리 가능)

### `forward_test.py` — 예측 생성
- `get_active_props()`: PostgreSQL에서 오늘/내일 활성 NBA props 조회
- `NBAModel.process_kalshi_nba_ticker()`: 박스스코어 통계 → XGBoost → Platt scaling → 확률
- 결과를 `data/prediction_log.jsonl`에 append
- `CONFIG_VERSION = "v4_edge_optimized"` 태깅
- 대상 ticker prefix: `KXNBAPTS` (득점), `KXNBAREB` (리바운드)
- ~~`KXNBAAST` (어시스트)~~ → v4에서 제거됨
- ticker 날짜 형식: `KXNBAPTS-26APR04-...` (yy+MON+dd, **반드시 zero-padding**: `04` not `4`)

> ⚠️ Kalshi `expiration_time`은 **시즌 종료일** (~4/14)이지 경기 날짜가 아님. 날짜 판단은 반드시 ticker 파싱으로.

### `settle_predictions.py` — 정산
- `fetch_box_scores()`: nba_api로 박스스코어 조회 (완료된 경기만 status=3)
- `settle()`: 미정산 예측 → 실제 결과 비교 → `prediction_log.jsonl` 업데이트
- `print_report()`: 정확도/캘리브레이션/Brier Score 출력

### `signal_engine.py` — 시그널 엔진
- `PROP_MIN_EDGE`: prop type별 최소 edge 딕셔너리
- `YES_EDGE_PREMIUM = 0.05`: YES bet 시 추가 premium
- `GLOBAL_MIN_EDGE = 0.15`: 절대 하한선
- entry price: YES=ask, NO=1-yes_bid

### `auto_trader.py` — 자동 트레이딩
- `should_place_bet()`: prop type 활성화 → prop MIN_EDGE → YES premium → global floor 순서로 필터
- 2x spread rule: YES only (NO는 면제)

---

## 7. 데이터 흐름

```
[Kalshi API]
     │
     ▼ (60s 간격)
market_recorder.py  ──────►  PostgreSQL (warmachine DB)
     │                         (markets + price_snapshots)
     │
     ▼ (12:00 PM ET)
forward_test.py  ────────────►  data/prediction_log.jsonl
  PostgreSQL에서                   (unsettled 예측 append)
  active props 조회                config_version 태깅
  XGBoost + Platt scaling
     │
     ▼ (11:59 PM ET)
settle_predictions.py  ──────►  data/prediction_log.jsonl
  nba_api 박스스코어 조회           (settled=True, actual_result 업데이트)
  예측 정산
```

---

## 8. Source 태깅 체계

`prediction_log.jsonl`의 `"source"` 필드:

| source | 설명 | 사용 시점 |
|--------|------|-----------|
| `"auto"` | runner.py 스케줄러 자동 실행 | 12pm cron + startup catch-up |
| `"auto-cli"` | CLI 직접 실행 | `python scripts/forward_test.py` (기본값) |
| `"manual"` | 수동 입력 | 사용자가 Kalshi에 직접 베팅한 내역 |

캘리브레이션 리포트: `auto` + `auto-cli`만 사용. `manual`은 제외.

---

## 9. 스케줄

모든 시간 기준: **US/Eastern (ET)**

| 시간 | 작업 |
|------|------|
| NBA 스케줄 기반 | runner.py 자동 기동/shutdown (tipoff-2h ~ 종료+1h) |
| 12:00 PM ET | NBA props forward test (예측 생성) |
| 10:00 PM ET | Discord 데일리 요약 (한국어, 잔고/PnL/시그널/캘리브레이션) |
| 11:59 PM ET | 예측 정산 (settle) |
| 매주 일요일 10PM ET | 주간 손실 분해 + self_learner 파라미터 자동 튜닝 |

기동 catch-up 로직:
1. 현재 시각 > 12pm ET
2. 오늘 `source="auto"` 예측이 없음
3. PostgreSQL에 오늘 날짜 active props 존재
→ 세 조건 모두 충족 시 즉시 forward test 실행

> ⚠️ 게임 윈도우 밖에서 기동 시 4 PM ET까지 sleep. restart loop 방지 (4/1~4/2 버그에서 학습).

---

## 10. 가격 필터

`forward_test.py`:
```python
MIN_KALSHI_PRICE = 0.05   # 5c 미만 → 유동성 없음 (스킵)
MAX_KALSHI_PRICE = 0.95   # 95c 초과 → 확실 결과 (엣지 없음, 스킵)
```

---

## 11. 최근 수정 이력

| 날짜 | 변경 내용 |
|------|-----------|
| **2026-04-04** | **v4_edge_optimized 배포**: assists 비활성화, rebounds MIN_EDGE 0.25→0.20, YES_EDGE_PREMIUM +0.05, GLOBAL_MIN_EDGE 0.15 |
| 2026-04-04 | signal_engine.py: PROP_MIN_EDGE + YES_PREMIUM + GLOBAL 클래스 변수 추가 |
| 2026-04-04 | forward_test.py: assists(KXNBAAST) 수집 제거, CONFIG_VERSION = "v4_edge_optimized" |
| 2026-04-04 | auto_trader.py: should_place_bet() 함수 추가 (legacy tuned_min_edge 대체) |
| 2026-04-04 | GitHub Chapter 3 push: "The Price Was Wrong" |
| 2026-04-03 | Entry price fix: YES=ask, NO=1-yes_bid (이전: YES=bid → edge 과대평가) |
| 2026-04-03 | NO betting 활성화: 2x spread rule → YES only (NO 면제) |
| 2026-04-03 | v3_prop_edge 156건 정산 완료 → ROI -4.4%, assists -27.8%, rebounds +17.6% |
| 2026-04-02 | runner.py: 게임 윈도우 밖 기동 시 4PM까지 sleep (restart loop fix) |
| 2026-04-02 | ticker zero-padding fix: `f"{d.day:02d}"` (KXNBAPTS-26APR04, not APR4) |
| 2026-04-02 | 4/1~4/2 데이터 유실 확인: restart loop + zero-padding 버그 겹침 |
| 2026-04-02 | GitHub Chapter 2 push: Phase 1 Validation (999 predictions) |
| 2026-03-31 | v3_prop_edge config 배포: assists MIN_EDGE 0.15, points/rebounds 0.25 |
| 2026-03-30 | Platt scaling refit: A=0.8976, B=-0.4242 (Brier 0.1693→0.1590) |
| 2026-03-29 | SQLite → PostgreSQL 전체 마이그레이션 (warmachine DB, psycopg2 ThreadedConnectionPool) |
| 2026-03-29 | GitHub Chapter 1 push: Day 1 (XGBoost transition) |
| 2026-03-28 | PAPER_TRADING = True 설정, forward test 시작 |

---

## 12. 코딩 규칙

### 수정 전 반드시 현재 코드 읽기
파일을 수정하기 전에 항상 `Read` 도구로 현재 상태를 확인한다.
기억이나 이전 대화를 믿지 말 것 — 코드는 바뀌어 있을 수 있다.

### runner.py 수정 시
1. 수정 후 runner.py를 재시작해야 변경사항이 적용됨
2. 부모-자식 프로세스 구조 (2개 PID) — 둘 다 확인/보고
3. 재시작 후 PID를 확인하고 사용자에게 보고

### prediction_log.jsonl 무결성 최우선
- **절대 덮어쓰기 금지** — append-only 파일
- 수정 전 반드시 백업 (`.bak.YYYYMMDD_HHMMSS`)
- `save_predictions()` 함수는 전체 파일을 재작성함 → 호출 전 신중히 검토
- 이미 `settled=True`인 항목은 건드리지 말 것

### config 수정 시
- `config_version` 태그를 반드시 업데이트 (prediction_log에 기록됨)
- Platt scaling 파라미터 (A, B)는 별도 지시 없이 변경 금지
- `self_learner`가 수동 설정을 덮어쓸 수 있음 → param 우선순위 확인 필수

### GitHub 작업
- **Claude Code는 글(prose/narrative)을 쓰지 않는다** — Chat에서 작성 후 파일만 push
- 과거 Chapter는 수정 불가 — 시점별 기록 유지
- logs/ 폴더에 날짜별 .md 파일 추가 방식

### 일반 원칙
- 수정 요청 범위를 벗어난 "개선"은 하지 않는다
- 타입 어노테이션, docstring, 불필요한 에러 처리 추가 금지
- PostgreSQL 사용 (SQLite 아님)
- 모든 시간 계산은 `tz.py`의 `ET` (US/Eastern) 기준
- ticker 날짜는 반드시 zero-padding (`%02d`)

---

## 13. 환경 변수 (.env)

```
KALSHI_API_KEY_ID=...            # Kalshi API 키
KALSHI_PRIVATE_KEY_PATH=./keys/private_key.pem
KALSHI_USE_DEMO=false            # 항상 prod 사용
DISCORD_WEBHOOK_URL=...          # 알림 webhook
ALERT_MAX_PER_DAY=10
ODDS_API_KEY=...                 # The Odds API (보조용)
```

---

## 14. 실행 방법

```bash
# 메인 시스템 시작 (상시 실행, 부모-자식 프로세스)
python scripts/runner.py

# 옵션
python scripts/runner.py --observe-only     # 데이터 수집만
python scripts/runner.py --scan-only        # 스캔만 (데이터 수집 없음)
python scripts/runner.py --no-auto-trade    # 시그널 감지만 (트레이딩 없음)
python scripts/runner.py --dry-run          # 드라이런 (실주문 없음)

# 수동 forward test
python scripts/forward_test.py              # 오늘+내일 (source=auto-cli)
python scripts/forward_test.py --date 2026-04-04

# 수동 settle
python scripts/settle_predictions.py       # 전체 미정산
python scripts/settle_predictions.py --report  # 리포트만

# 데이터 수집 단독 실행
python scripts/market_recorder.py
```

---

## 15. 현재 성과 요약 (참고용)

| 구분 | 건수 | Brier | ROI |
|------|------|-------|-----|
| 전체 (all configs) | 1,111 settled | 0.1788 | +$14.56 |
| v3_prop_edge | 156 settled | 0.1732 | -4.4% |
| v4_edge_optimized | 0 (4/4 시작) | — | — |

**v4 목표**: 100건 정산 후 ROI +10% 이상 → real money 전환 판단

> ⚠️ 현재 PAPER TRADING. 실 자금 투입 없음.
