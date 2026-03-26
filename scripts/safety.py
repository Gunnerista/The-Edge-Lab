#!/usr/bin/env python3
"""
Safety Guardrails
=================
$100 자본금 기준 안전 규칙.
모든 모듈이 공유하는 중앙 안전장치.

자동 중단 조건:
- 일일 손실 $10 → 당일 알림 중단
- 3연패 → 4시간 쿨다운
- 주간 손실 $20 → 주말까지 중단
"""

import sys
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ============================================================================
# Safety Config
# ============================================================================

SAFETY_CONFIG = {
    "starting_bankroll": 360,
    "max_single_bet": 35,
    "max_single_bet_arb": 50,       # arbitrage (confirmed profit) has higher limit
    "daily_max_loss": 75,
    "weekly_max_loss": 150,
    "min_edge_pregame": 0.05,
    "min_edge_live": 0.08,
    "min_volume": 0,                # allow low-volume markets (new strategy)
    "max_spread_cents": 15,
    "kelly_fraction": 0.25,
    "max_concurrent_bets": 5,
    "stop_after_consecutive_losses": 3,
    "cooldown_after_loss_streak_hours": 4,
    "max_settlement_hours": 72,     # only trade markets settling within 72h
}


# ============================================================================
# Safety Manager
# ============================================================================

class SafetyManager:
    """중앙 안전장치 관리자"""

    def __init__(self, config: dict = None):
        self.config = config or SAFETY_CONFIG
        self.state_file = DATA_DIR / "safety_state.json"

        # 상태
        self.bankroll = self.config["starting_bankroll"]
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.consecutive_losses = 0
        self.active_bets = 0
        self.cooldown_until: Optional[datetime] = None
        self.daily_halted = False
        self.weekly_halted = False
        self.today = datetime.now(timezone.utc).date()
        self.week_start = self._get_week_start()

        # 기록
        self.bets_today = []

        self._load_state()

    def _get_week_start(self):
        today = datetime.now(timezone.utc).date()
        return today - timedelta(days=today.weekday())  # Monday

    def _load_state(self):
        """저장된 상태 로드"""
        if self.state_file.exists():
            try:
                data = json.load(open(self.state_file))
                self.bankroll = data.get("bankroll", self.config["starting_bankroll"])
                self.daily_pnl = data.get("daily_pnl", 0)
                self.weekly_pnl = data.get("weekly_pnl", 0)
                self.consecutive_losses = data.get("consecutive_losses", 0)
                self.daily_halted = data.get("daily_halted", False)
                self.weekly_halted = data.get("weekly_halted", False)

                saved_date = data.get("date", "")
                if saved_date != str(self.today):
                    # 새 날짜 → 일일 리셋
                    self.daily_pnl = 0
                    self.daily_halted = False
                    self.bets_today = []

                saved_week = data.get("week_start", "")
                if saved_week != str(self.week_start):
                    # 새 주 → 주간 리셋
                    self.weekly_pnl = 0
                    self.weekly_halted = False

                cd = data.get("cooldown_until")
                if cd:
                    self.cooldown_until = datetime.fromisoformat(cd)

            except Exception:
                pass

    def save_state(self):
        """상태 저장"""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "bankroll": self.bankroll,
            "daily_pnl": self.daily_pnl,
            "weekly_pnl": self.weekly_pnl,
            "consecutive_losses": self.consecutive_losses,
            "daily_halted": self.daily_halted,
            "weekly_halted": self.weekly_halted,
            "date": str(self.today),
            "week_start": str(self.week_start),
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "active_bets": self.active_bets,
        }
        json.dump(data, open(self.state_file, "w"), indent=2)

    # ── 검증 ────────────────────────────────────────────────────

    def can_bet(self) -> tuple:
        """배팅 가능 여부 + 사유"""
        # 일일 중단
        if self.daily_halted:
            return False, "일일 손실 한도 도달 — 오늘 배팅 중단"

        # 주간 중단
        if self.weekly_halted:
            return False, "주간 손실 한도 도달 — 이번 주 배팅 중단"

        # 쿨다운
        if self.cooldown_until and datetime.now(timezone.utc) < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now(timezone.utc)).total_seconds() / 60
            return False, f"연패 쿨다운 중 — {remaining:.0f}분 남음"

        # 동시 배팅 한도
        if self.active_bets >= self.config["max_concurrent_bets"]:
            return False, f"동시 배팅 한도 ({self.config['max_concurrent_bets']}건) 도달"

        return True, "배팅 가능"

    def validate_bet(self, amount: float, edge: float, strategy: str, spread_cents: float = 0) -> tuple:
        """개별 배팅 검증"""
        can, reason = self.can_bet()
        if not can:
            return False, reason

        # 배팅 금액 한도
        if amount > self.config["max_single_bet"]:
            return False, f"배팅 금액 ${amount:.2f} > 한도 ${self.config['max_single_bet']}"

        if amount > self.bankroll:
            return False, f"자금 부족 (잔액 ${self.bankroll:.2f})"

        # 엣지 최소 기준
        min_edge = self.config["min_edge_live"] if strategy == "live" else self.config["min_edge_pregame"]
        if edge < min_edge:
            return False, f"엣지 {edge:.1%} < 최소 기준 {min_edge:.0%} ({strategy})"

        # 스프레드
        if spread_cents > self.config["max_spread_cents"]:
            return False, f"스프레드 {spread_cents:.0f}c > 한도 {self.config['max_spread_cents']}c"

        return True, "검증 통과"

    def calculate_bet_size(self, model_prob: float, market_prob: float) -> float:
        """안전한 배팅 금액 계산"""
        from bankroll import KellyCriterion
        decimal_odds = 1.0 / market_prob if market_prob > 0 else 0
        kelly = KellyCriterion.calculate_kelly(
            model_prob, decimal_odds, self.config["kelly_fraction"]
        )
        raw = self.bankroll * kelly
        return min(raw, self.config["max_single_bet"], self.bankroll * 0.05)

    # ── 결과 기록 ───────────────────────────────────────────────

    def record_bet_placed(self, amount=None):
        """배팅 실행 기록 (amount는 optional — 호환성 유지)"""
        self.active_bets += 1
        self.save_state()

    def record_result(self, won: bool, pnl: float) -> Optional[str]:
        """
        결과 기록 + 안전장치 발동 체크

        Returns:
            None이면 정상, 문자열이면 안전장치 발동 사유
        """
        self.bankroll += pnl
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self.active_bets = max(0, self.active_bets - 1)

        if won:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

        alert_reason = None

        # 일일 손실 한도
        if self.daily_pnl <= -self.config["daily_max_loss"]:
            self.daily_halted = True
            alert_reason = f"일일 손실 한도 도달 (${self.daily_pnl:+.2f})"

        # 주간 손실 한도
        if self.weekly_pnl <= -self.config["weekly_max_loss"]:
            self.weekly_halted = True
            alert_reason = f"주간 손실 한도 도달 (${self.weekly_pnl:+.2f})"

        # 연패 쿨다운
        if self.consecutive_losses >= self.config["stop_after_consecutive_losses"]:
            hours = self.config["cooldown_after_loss_streak_hours"]
            self.cooldown_until = datetime.now(timezone.utc) + timedelta(hours=hours)
            alert_reason = f"{self.consecutive_losses}연패 — {hours}시간 쿨다운 시작"

        self.save_state()
        return alert_reason

    def get_status(self) -> Dict:
        """현재 안전 상태"""
        can, reason = self.can_bet()
        return {
            "bankroll": self.bankroll,
            "daily_pnl": self.daily_pnl,
            "weekly_pnl": self.weekly_pnl,
            "consecutive_losses": self.consecutive_losses,
            "active_bets": self.active_bets,
            "can_bet": can,
            "status": reason,
            "daily_halted": self.daily_halted,
            "weekly_halted": self.weekly_halted,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
        }


# ============================================================================
# 메인
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    sm = SafetyManager()
    status = sm.get_status()

    print("=" * 60)
    print("Safety Guardrails")
    print("=" * 60)
    print(f"  자금:          ${status['bankroll']:.2f}")
    print(f"  오늘 손익:     ${status['daily_pnl']:+.2f}")
    print(f"  주간 손익:     ${status['weekly_pnl']:+.2f}")
    print(f"  연속 패배:     {status['consecutive_losses']}")
    print(f"  활성 배팅:     {status['active_bets']}")
    print(f"  배팅 가능:     {'예' if status['can_bet'] else '아니오'}")
    print(f"  상태:          {status['status']}")
    print()
    print("  규칙:")
    for k, v in SAFETY_CONFIG.items():
        print(f"    {k}: {v}")