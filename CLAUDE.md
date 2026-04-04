# PREDICTION MARKET WAR MACHINE — CLAUDE.md
> Claude Code: 이 파일을 먼저 읽고 작업을 시작하세요.
> 마지막 업데이트: 2026-03-29

---

## 1. 프로젝트 개요

**EDGE — NBA Player Props 예측 시스템 (Kalshi 기반)**

전통 스포츠북이 아닌 **Kalshi Prediction Market** 전용 NBA props 예측 시스템.
NBA 박스스코어 통계로 선수별 득점/리바운드/어시스트 Over/Under 확률을 계산하고,
Kalshi 시장 가격과 비교해 엣지를 찾아 베팅하는 시스템.

현재 상태: **PAPER TRADING MODE** (`runner.py:549` `PAPER_TRADING = True`)

---

## 2. 디렉토리 구조

```
sports-betting-system/
├── .env                        # API 키 (KALSHI, DISCORD, ODDS_API)
├── keys/
│   └── private_key.pem         # Kalshi RSA-PSS 인증 키
├── scripts/                    # 모든 실행 파일
│   ├── runner.py               # 메인 오케스트레이터 (상시 실행)
│   ├── market_recorder.py      # Kalshi 마켓 데이터 수집 → SQLite
│   ├── forward_test.py         # NBA props 예측 생성 → prediction_log.jsonl
│   ├── settle_predictions.py   # 박스스코어로 예측 정산
│   ├── nba_model.py            # NBA 확률 모델 (핵심 알고리즘)
│   ├── kalshi_client.py        # Kalshi REST API (RSA-PSS 인증)
│   ├── kalshi_ws.py            # Kalshi WebSocket 실시간 스트리밍
│   ├── auto_trader.py          # 자동 트레이딩 엔진
│   ├── active_scanner.py       # 차익거래 + 시그널 스캔
│   ├── signal_engine.py        # 시그널 생성 엔진
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
    ├── market_data.db          # SQLite: markets + price_snapshots
    ├── prediction_log.jsonl    # 예측 로그 (append-only, 무결성 최우선)
    ├── trade_log.jsonl         # 실행 트레이드 로그
    ├── paper_trades.jsonl      # 페이퍼 트레이딩 기록
    ├── learning_log.jsonl      # 시그널 감지 이력
    ├── runner.log              # runner.py 실행 로그
    ├── calibration_state.json  # 캘리브레이션 상태
    ├── tuned_params.json       # self_learner 자동 튜닝 파라미터
    └── safety_state.json       # 안전장치 상태
```

---

## 3. 핵심 파일별 역할

### `runner.py` — 메인 오케스트레이터
상시 실행. 두 개 스레드를 동시에 운영:
- **Observer 스레드** (60s 간격): `market_recorder.RESTRecorder.record_snapshot()` → SQLite 저장
- **Scanner 스레드** (120s 간격): `AutoTrader.run_cycle()` + `ActiveScanner` + `SignalEngine`

스케줄러 (메인 스레드, 60s 루프):
- 12:00 PM ET → `forward_test.run_forward_test(source="auto")`
- 11:59 PM ET → `settle_predictions.settle()`
- 10:00 PM ET → Discord 데일리 요약
- 기동 시 catch-up: 12pm 이후 기동 + 오늘 auto 예측 없음 + active props 있으면 즉시 forward test 실행

### `market_recorder.py` — 데이터 수집
- `RESTRecorder.record_snapshot()`: Kalshi 전체 마켓 스냅샷을 SQLite에 저장
- 페이지당 1000건, 최대 200페이지 (=200K 마켓 처리 가능)
- DB: `data/market_data.db` (테이블: `markets`, `price_snapshots`)
- SQLite 연결 timeout=30초

### `forward_test.py` — 예측 생성
- `get_active_props()`: `market_data.db`에서 오늘/내일 활성 NBA props 조회
- `NBAModel.process_kalshi_nba_ticker()`: 박스스코어 통계 → 확률 계산
- 결과를 `data/prediction_log.jsonl`에 append
- 대상 ticker prefix: `KXNBAPTS` (득점), `KXNBAREB` (리바운드), `KXNBAAST` (어시스트)
- ticker 날짜 형식: `KXNBAPTS-26MAR28-...` (yy+MON+dd)

### `settle_predictions.py` — 정산
- `fetch_box_scores()`: nba_api로 박스스코어 조회 (완료된 경기만 status=3)
- `settle()`: 미정산 예측 → 실제 결과 비교 → `prediction_log.jsonl` 업데이트
- `print_report()`: 정확도/캘리브레이션/Brier Score 출력

---

## 4. 데이터 흐름

```
[Kalshi API]
     │
     ▼ (60s 간격)
market_recorder.py  ──────►  data/market_data.db
     │                         (markets + price_snapshots)
     │
     ▼ (12:00 PM ET)
forward_test.py  ────────────►  data/prediction_log.jsonl
  market_data.db에서               (unsettled 예측 append)
  active props 조회
  NBAModel 확률 계산
     │
     ▼ (11:59 PM ET)
settle_predictions.py  ──────►  data/prediction_log.jsonl
  nba_api 박스스코어 조회           (settled=True, actual_result 업데이트)
  예측 정산
```

---

## 5. Source 태깅 체계

`prediction_log.jsonl`의 `"source"` 필드:

| source | 설명 | 사용 시점 |
|--------|------|-----------|
| `"auto"` | runner.py 스케줄러 자동 실행 | 12pm cron + startup catch-up |
| `"auto-cli"` | CLI 직접 실행 | `python scripts/forward_test.py` (기본값) |
| `"manual"` | 수동 입력 | 사용자가 Kalshi에 직접 베팅한 내역 |

캘리브레이션 리포트: `auto` + `auto-cli`만 사용. `manual`은 제외.
(`settle_predictions.py:212` `AUTO_SOURCES = {"auto", "auto-cli"}`)

---

## 6. 스케줄

모든 시간 기준: **US/Eastern (ET)**

| 시간 | 작업 |
|------|------|
| 12:00 PM ET | NBA props forward test (예측 생성) |
| 10:00 PM ET | Discord 데일리 요약 (잔고, PnL, 시그널 수, 캘리브레이션) |
| 11:59 PM ET | 예측 정산 (settle) |
| 매주 일요일 10PM ET | 주간 손실 분해 + self_learner 파라미터 자동 튜닝 |

기동 catch-up 로직 (`runner.py:429`):
1. 현재 시각 > 12pm ET
2. 오늘 `source="auto"` 예측이 없음
3. `market_data.db`에 오늘 날짜 active props 존재
→ 세 조건 모두 충족 시 즉시 forward test 실행

---

## 7. DB 설정

### SQLite 설정
- `timeout=30` (모든 SQLite 연결에 적용)
- WAL 모드 사용 (`market_data.db-shm`, `market_data.db-wal` 파일 존재)

### Settle 재시도
```python
# runner.py:339
max_retries = 3
for attempt in range(1, max_retries + 1):
    try:
        settle(target_date=today_str)
        break
    except Exception as e:
        if "locked" in str(e).lower() and attempt < max_retries:
            time.sleep(60)  # 60초 대기 후 재시도
```
DB 잠금 시 60초 간격으로 최대 3회 재시도.

---

## 8. 가격 필터

`forward_test.py:121`:
```python
MIN_KALSHI_PRICE = 0.05   # 5c 미만 → 유동성 없음 (스킵)
MAX_KALSHI_PRICE = 0.95   # 95c 초과 → 확실 결과 (엣지 없음, 스킵)
```
`kalshi_price`가 0이거나 범위 밖이면 예측 생성하지 않음.

---

## 9. 최근 수정 이력

| 날짜 | 변경 내용 |
|------|-----------|
| 2026-04-03 | 프로젝트 경로 확정: `C:\Users\Dell\Desktop\Projects\sports-betting-system` (D드라이브 참조 제거) |
| 2026-03-29 | `prediction_log.jsonl` 백업: `.bak.20260329_005700` |
| 2026-03-29 | SQLite timeout=30 (전체 적용) |
| 2026-03-29 | settle 재시도: 60초×3회 (DB 잠금 대응) |
| 2026-03-29 | pagination MAX_PAGES=200 (200K 마켓 처리) |
| 2026-03-28 | PAPER_TRADING = True (실매매 중단, 전략 검증 중) |

---

## 10. 코딩 규칙

### 수정 전 반드시 현재 코드 읽기
파일을 수정하기 전에 항상 `Read` 도구로 현재 상태를 확인한다.
기억이나 이전 대화를 믿지 말 것 — 코드는 바뀌어 있을 수 있다.

### runner.py 수정 시
1. 수정 후 runner.py를 재시작해야 변경사항이 적용됨
2. 재시작 후 PID를 확인하고 사용자에게 보고
3. 재시작 명령: `python scripts/runner.py` (또는 Task Scheduler를 통해)

### prediction_log.jsonl 무결성 최우선
- **절대 덮어쓰기 금지** — append-only 파일
- 수정 전 반드시 백업 (`.bak.YYYYMMDD_HHMMSS`)
- `save_predictions()` 함수는 전체 파일을 재작성함 → 호출 전 신중히 검토
- 이미 `settled=True`인 항목은 건드리지 말 것

### 일반 원칙
- 수정 요청 범위를 벗어난 "개선"은 하지 않는다
- 타입 어노테이션, docstring, 불필요한 에러 처리 추가 금지
- SQLite 연결 시 항상 `timeout=30` 명시
- 모든 시간 계산은 `tz.py`의 `ET` (US/Eastern) 기준

---

## 11. 환경 변수 (.env)

```
KALSHI_API_KEY_ID=...            # Kalshi API 키
KALSHI_PRIVATE_KEY_PATH=./keys/private_key.pem
KALSHI_USE_DEMO=false            # 항상 prod 사용
DISCORD_WEBHOOK_URL=...          # 알림 webhook
ALERT_MAX_PER_DAY=10
ODDS_API_KEY=...                 # The Odds API (보조용)
```

---

## 12. 실행 방법

```bash
# 메인 시스템 시작 (상시 실행)
python scripts/runner.py

# 옵션
python scripts/runner.py --observe-only     # 데이터 수집만
python scripts/runner.py --scan-only        # 스캔만 (데이터 수집 없음)
python scripts/runner.py --no-auto-trade    # 시그널 감지만 (트레이딩 없음)
python scripts/runner.py --dry-run          # 드라이런 (실주문 없음)

# 수동 forward test
python scripts/forward_test.py              # 오늘+내일 (source=auto-cli)
python scripts/forward_test.py --date 2026-03-28

# 수동 settle
python scripts/settle_predictions.py       # 전체 미정산
python scripts/settle_predictions.py --report  # 리포트만

# 데이터 수집 단독 실행
python scripts/market_recorder.py
```
