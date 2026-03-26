# PREDICTION MARKET WAR MACHINE — Master Instructions
> Claude Code: 이 파일을 먼저 읽고 작업을 시작하세요.
> 마지막 업데이트: 2026-03-25

---

## 1. 프로젝트 비전

**"제2의 Tony Bloom — Prediction Market 버전"**

전통 스포츠북이 아닌, **Kalshi (Prediction Market)** 전용 시스템.
스포츠뿐 아니라 정치, 경제, 사회 이벤트까지 — 세상에서 일어나는 모든 측정 가능한 사건에 가격이 매겨지는 곳에서 엣지를 찾는 시스템.

핵심 철학:
- 감정 없는 냉철한 확률 판단
- Prediction market 자체의 특성을 이용 (중간 exit, cross-market 상관관계, 시장 비효율)
- 작은 통계적 우위를 일관되게 적용
- 데이터를 직접 수집하며 동시에 수익도 추구

---

## 2. 현재 상태

### 유지된 인프라 (scripts/)
| 파일 | 역할 | 상태 |
|------|------|------|
| `kalshi_client.py` | Kalshi REST API 클라이언트 (RSA-PSS 인증) | 작동 확인됨 |
| `kalshi_ws.py` | Kalshi WebSocket 실시간 가격 스트리밍 | 작동 확인됨 |
| `bankroll.py` | Kelly Criterion 자금 관리 | 작동, 리팩토링 필요 |
| `safety.py` | 안전장치 (일일/주간 손실 한도) | 작동 |
| `alerts.py` | Discord/Email 알림 | 작동 |
| `error_tracker.py` | 에러 트래킹 & 학습 | 작동 |
| `observer.py` | Kalshi 마켓 가격 관찰/기록 | 작동 |
| `odds_collector.py` | Kalshi 마켓 스캔 | 작동, 리팩토링 필요 |

### 삭제된 것 (전통 스포츠북 프레임)
analyzer.py, comeback_db.py, live_trading_engine.py, live_scores.py,
consensus_filter.py, mega_analyzer.py, advanced_stats.py, auto_learning.py,
auto_trader.py, data_pipeline.py, multi_platform.py, odds_api.py,
verify_efficiency.py, main.py, watchdog.py, 모든 backtest 데이터

### 보존된 데이터
- `data/price_observations.jsonl` — 이미 수집된 Kalshi 가격 데이터 (귀중)
- `.env` — API 키 설정
- `keys/private_key.pem` — Kalshi RSA 키

---

## 3. 새로 만들어야 할 모듈

### Phase 1: 데이터 수집기 (최우선)
```
scripts/market_recorder.py
```
- Kalshi의 **모든 활성 마켓** 가격 변동을 실시간 기록
- 스포츠, 정치, 경제 가리지 않고 전부 수집
- DB: SQLite 또는 JSONL (나중에 분석용)
- 기록할 것: timestamp, market_ticker, yes_bid, yes_ask, volume, open_interest
- kalshi_ws.py 기반으로 구축

### Phase 2: 마켓 분류기
```
scripts/market_classifier.py
```
- 수집된 마켓을 자동 분류: sports / politics / economics / other
- 각 마켓의 메타데이터 파싱 (이벤트 시간, 결산 조건 등)
- 유동성 수준 평가 (너무 얇은 마켓 필터링)

### Phase 3: 가격 패턴 분석기
```
scripts/price_analyzer.py
```
- 수집된 데이터에서 패턴 발견
- 스포츠: 경기 중 가격 변동 곡선, 과잉 반응 지점 식별
- 정치: 뉴스 발표 전후 가격 움직임
- 경제: 지표 발표 전 가격 수렴 패턴
- "진입 최적 시점"과 "exit 최적 시점" 모델링

### Phase 4: 시그널 엔진
```
scripts/signal_engine.py
```
- 실시간으로 +EV 기회 탐지
- 스포츠: 인게임 확률 vs Kalshi 가격 괴리
- 정치/경제: 뉴스 분석 → 마켓 반영 속도 차이
- Cross-market 상관관계 시그널
- 수수료(Kalshi fee) 반영한 순 EV 계산

### Phase 5: 트레이딩 엔진
```
scripts/trade_engine.py
```
- 시그널 → 주문 실행 (limit order only)
- Position 관리 (부분 exit, 전체 exit)
- 실시간 P&L 추적
- safety.py 연동

### Phase 6: 통합 실행
```
scripts/main.py
```
- 모든 모듈 통합
- 모드: observe (관찰만) / signal (알림만) / live (실제 트레이딩)
- Discord 알림 연동

---

## 4. 아키텍처 원칙

### Prediction Market 전용 전략
1. **가격 수렴 트레이딩**: 이벤트가 다가올수록 가격이 수렴 → 초기 비효율 포착
2. **인게임 과잉반응**: 스포츠 경기 중 Kalshi 가격의 과잉 반응 → 저점 매수
3. **Cross-market 아비트라지**: 관련 마켓 간 가격 불일치 이용
4. **뉴스 속도 차이**: 뉴스 반영이 느린 마켓에서 선제 포지션
5. **유동성 비효율**: 참여자가 적은 변방 마켓에서 엣지 확보

### 코드 원칙
- 모든 판단에 감정 배제, 확률과 EV만
- 수수료 항상 반영
- 모든 거래 기록 (학습 데이터)
- 안전장치 절대 우회 불가
- Demo 모드 먼저, Ikjun 확인 후 Production

### 안전 규칙
- 자동 배팅 금지 (알림 → Ikjun 확인 → 수동 실행)
- 일일 손실 한도 $10 / 주간 $20 (초기)
- API 키 커밋 절대 금지
- 검증 없는 배팅 금지

---

## 5. 시작 도메인: 스포츠 (NBA/MLB)

첫 번째 검증 대상. 이유:
- Kalshi 스포츠 마켓이 가장 활발
- MLB 시즌 진행 중 (2026-03-25 개막)
- NBA 플레이오프 임박
- 실시간 데이터 수집 즉시 가능

이후 확장: 정치 → 경제 → 기타

---

## 6. GitHub
- Repo: https://github.com/Gunnerista/Here-We-Bet-.git
- Branch: main
- API 키, private_key.pem 절대 커밋 금지

---

## 7. 기술 스택
- Python 3.14+
- Kalshi REST API + WebSocket
- SQLite (로컬 데이터)
- Discord Webhook (알림)
- 의존성: requirements.txt 참조

---

## 8. Claude Code 작업 시작 방법

1. 이 파일을 먼저 읽는다
2. `scripts/` 내 유지된 8개 파일을 확인한다
3. Phase 1 (market_recorder.py)부터 구현 시작
4. 각 Phase 완료 후 테스트 → 다음 Phase 진행
5. 항상 기존 인프라(kalshi_client, kalshi_ws 등)를 활용한다

---

## 9. 향후 리마인드

- **NFL 시즌 (2026-09)**: Pressure-based Over/Under 모델 적용 검토
  - Bet Angel 영상 참조: 압박 지표 기반 regression model
  - 유럽 축구 10개 리그, 12시즌, 68K 베팅 분석 학술 논문 기반
  - 자세한 내용: .auto-memory/project_pressure_model_future.md

---

*"You don't need to outsmart the entire market. You just need a slight statistical advantage, applied consistently."*
