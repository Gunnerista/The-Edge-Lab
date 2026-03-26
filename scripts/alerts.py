#!/usr/bin/env python3
"""
Alert System
============
+EV 배팅 기회 발견 시 Discord/Email 알림 발송.
Rate limiting으로 스팸 방지.

사용법:
    python scripts/alerts.py --test     # 테스트 알림 발송
"""

import os
import sys
import json
import logging
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class AlertConfig:
    """알림 시스템 설정"""
    # Discord
    discord_webhook_url: str = ""
    # Gmail
    gmail_user: str = ""
    gmail_app_password: str = ""
    gmail_recipient: str = ""
    # Rate limiting
    max_alerts_per_day: int = 10
    min_edge_for_alert: float = 0.05
    min_confidence: str = "medium"  # "low", "medium", "high"
    cooldown_minutes: int = 30

    @classmethod
    def from_env(cls) -> "AlertConfig":
        return cls(
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", ""),
            gmail_user=os.environ.get("GMAIL_USER", ""),
            gmail_app_password=os.environ.get("GMAIL_APP_PASSWORD", ""),
            gmail_recipient=os.environ.get("GMAIL_RECIPIENT", ""),
            max_alerts_per_day=int(os.environ.get("ALERT_MAX_PER_DAY", "10")),
        )

    @property
    def has_discord(self) -> bool:
        return bool(self.discord_webhook_url)

    @property
    def has_email(self) -> bool:
        return bool(self.gmail_user and self.gmail_app_password and self.gmail_recipient)


# ============================================================================
# Alert Manager
# ============================================================================

class AlertManager:
    """알림 발송 + Rate Limiting 관리"""

    CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}

    def __init__(self, config: AlertConfig = None):
        self.config = config or AlertConfig.from_env()
        self.daily_count = 0
        self.daily_reset_date = datetime.now(timezone.utc).date()
        self.last_alert_times: Dict[str, datetime] = {}

    def should_alert(self, scan_result) -> bool:
        """알림 발송 여부 판단 (rate limiting + 필터)"""
        # 일일 리셋
        today = datetime.now(timezone.utc).date()
        if today != self.daily_reset_date:
            self.daily_count = 0
            self.daily_reset_date = today

        # 일일 한도 체크
        if self.daily_count >= self.config.max_alerts_per_day:
            return False

        # Edge 필터
        edge = getattr(scan_result, "edge", None)
        if edge is not None and edge < self.config.min_edge_for_alert:
            return False

        # Confidence 필터
        confidence = getattr(scan_result, "confidence", "low")
        if self.CONFIDENCE_ORDER.get(confidence, 0) < self.CONFIDENCE_ORDER.get(self.config.min_confidence, 1):
            return False

        # 쿨다운 체크 (같은 ticker)
        ticker = getattr(scan_result, "ticker", "")
        if ticker in self.last_alert_times:
            elapsed = datetime.now(timezone.utc) - self.last_alert_times[ticker]
            if elapsed < timedelta(minutes=self.config.cooldown_minutes):
                return False

        return True

    def send_alert(self, scan_result) -> bool:
        """알림 발송 (Discord + Email)"""
        sent = False

        if self.config.has_discord:
            try:
                self._send_discord(scan_result)
                sent = True
            except Exception as e:
                logger.error(f"Discord alert failed: {e}")

        if self.config.has_email:
            try:
                self._send_email(scan_result)
                sent = True
            except Exception as e:
                logger.error(f"Email alert failed: {e}")

        if sent:
            self.daily_count += 1
            ticker = getattr(scan_result, "ticker", "")
            self.last_alert_times[ticker] = datetime.now(timezone.utc)
            logger.info(f"Alert sent [{self.daily_count}/{self.config.max_alerts_per_day}]: {ticker}")

        return sent

    def _send_discord(self, scan_result):
        """Discord webhook 알림"""
        embed = self._format_discord_embed(scan_result)
        payload = {"embeds": [embed]}

        resp = requests.post(
            self.config.discord_webhook_url,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()

    def _send_email(self, scan_result):
        """Gmail SMTP 알림"""
        title = getattr(scan_result, "title", "Unknown")
        edge = getattr(scan_result, "edge", 0)

        body = self._format_email_body(scan_result)
        msg = MIMEText(body)
        msg["Subject"] = f"[BET ALERT] {title} - Edge {edge:.1%}"
        msg["From"] = self.config.gmail_user
        msg["To"] = self.config.gmail_recipient

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(self.config.gmail_user, self.config.gmail_app_password)
            server.send_message(msg)

    @staticmethod
    def _format_discord_embed(scan_result) -> dict:
        """Discord embed 포맷 (한국어)"""
        title = getattr(scan_result, "title", "Unknown Market")
        sport = getattr(scan_result, "sport", "")
        edge = getattr(scan_result, "edge", 0)
        ev = getattr(scan_result, "ev", 0)
        model_prob = getattr(scan_result, "model_prob", 0)
        market_prob = getattr(scan_result, "kalshi_implied_prob", 0)
        recommended = getattr(scan_result, "recommended_bet", 0)
        confidence = getattr(scan_result, "confidence", "low")

        color_map = {"high": 0x4CAF50, "medium": 0xFF9800, "low": 0xF44336}
        conf_kr = {"high": "높음", "medium": "보통", "low": "낮음"}

        return {
            "title": f"+EV 기회 발견: {title}",
            "color": color_map.get(confidence, 0x9E9E9E),
            "fields": [
                {"name": "종목", "value": sport or "N/A", "inline": True},
                {"name": "엣지", "value": f"{edge:.1%}" if edge else "N/A", "inline": True},
                {"name": "기대값(EV)", "value": f"{ev:+.1%}" if ev else "N/A", "inline": True},
                {"name": "모델 확률", "value": f"{model_prob:.0%}" if model_prob else "N/A", "inline": True},
                {"name": "시장 확률", "value": f"{market_prob:.0%}" if market_prob else "N/A", "inline": True},
                {"name": "추천 배팅", "value": f"${recommended:.2f}" if recommended else "N/A", "inline": True},
                {"name": "신뢰도", "value": conf_kr.get(confidence, confidence).upper(), "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "스포츠 배팅 시스템 | Kalshi"},
        }

    @staticmethod
    def _format_email_body(scan_result) -> str:
        """Email 본문 포맷"""
        title = getattr(scan_result, "title", "Unknown")
        sport = getattr(scan_result, "sport", "")
        edge = getattr(scan_result, "edge", 0)
        ev = getattr(scan_result, "ev", 0)
        model_prob = getattr(scan_result, "model_prob", 0)
        market_prob = getattr(scan_result, "kalshi_implied_prob", 0)
        recommended = getattr(scan_result, "recommended_bet", 0)
        confidence = getattr(scan_result, "confidence", "low")

        conf_kr = {"high": "높음", "medium": "보통", "low": "낮음"}
        return f"""
+EV 배팅 기회 발견!
{'='*40}

마켓:       {title}
종목:       {sport}
신뢰도:     {conf_kr.get(confidence, confidence)}

엣지:       {edge:.1%}
기대값(EV): {ev:+.1%}
모델 확률:  {model_prob:.0%}
시장 확률:  {market_prob:.0%}

추천 배팅:  ${recommended:.2f}

{'='*40}
생성: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
스포츠 배팅 시스템 | Kalshi
        """.strip()


    # ── Phase 8: 추가 알림 유형 ──────────────────────────────────

    def send_pregame_alert(self, game_info: dict):
        """🟡 프리게임 알림"""
        if not self.config.has_discord:
            return
        embed = {
            "title": f"🟡 프리게임 분석: {game_info.get('title', '')}",
            "color": 0xFFEB3B,
            "fields": [
                {"name": "경기", "value": f"{game_info.get('away', '')} @ {game_info.get('home', '')}", "inline": False},
                {"name": "시작 시간", "value": game_info.get("start_time", ""), "inline": True},
                {"name": "종목", "value": game_info.get("sport", ""), "inline": True},
                {"name": "모델 확률", "value": f"{game_info.get('model_prob', 0):.0%}", "inline": True},
                {"name": "시장 확률", "value": f"{game_info.get('market_prob', 0):.0%}", "inline": True},
                {"name": "엣지", "value": f"{game_info.get('edge', 0):.1%}", "inline": True},
                {"name": "추천 배팅", "value": f"${game_info.get('bet', 0):.2f}", "inline": True},
                {"name": "합의", "value": game_info.get("consensus", "N/A"), "inline": True},
                {"name": "신뢰도", "value": game_info.get("confidence", ""), "inline": True},
            ],
            "footer": {"text": "프리게임 분석 | 경기 전 검토하세요"},
        }
        try:
            requests.post(self.config.discord_webhook_url, json={"embeds": [embed]}, timeout=10)
        except Exception as e:
            logger.error(f"프리게임 알림 실패: {e}")

    def send_daily_summary(self, summary: dict):
        """📊 일일 요약"""
        if not self.config.has_discord:
            return

        pnl = summary.get("total_pnl", 0)
        color = 0x4CAF50 if pnl >= 0 else 0xF44336

        embed = {
            "title": "📊 오늘 요약",
            "color": color,
            "fields": [
                {"name": "발견한 기회", "value": f"{summary.get('opportunities', 0)}건", "inline": True},
                {"name": "배팅", "value": f"{summary.get('bets', 0)}건 ({summary.get('wins', 0)}W/{summary.get('losses', 0)}L)", "inline": True},
                {"name": "대기중", "value": f"{summary.get('pending', 0)}건", "inline": True},
                {"name": "수익", "value": f"${pnl:+.2f}", "inline": True},
                {"name": "자금", "value": f"${summary.get('bankroll', 0):.2f}", "inline": True},
                {"name": "ROI", "value": f"{summary.get('roi', 0):+.1%}", "inline": True},
            ],
            "footer": {"text": f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')} | 일일 요약"},
        }
        try:
            requests.post(self.config.discord_webhook_url, json={"embeds": [embed]}, timeout=10)
        except Exception as e:
            logger.error(f"일일 요약 실패: {e}")

    def send_risk_alert(self, reason: str, details: dict = None):
        """⚠️ 리스크 알림 (안전장치 발동)"""
        if not self.config.has_discord:
            return

        fields = [{"name": "사유", "value": reason, "inline": False}]
        if details:
            if "daily_pnl" in details:
                fields.append({"name": "오늘 손익", "value": f"${details['daily_pnl']:+.2f}", "inline": True})
            if "consecutive_losses" in details:
                fields.append({"name": "연속 패배", "value": f"{details['consecutive_losses']}연패", "inline": True})
            if "action" in details:
                fields.append({"name": "조치", "value": details["action"], "inline": False})

        embed = {
            "title": "⚠️ 리스크 알림",
            "color": 0xF44336,
            "fields": fields,
            "footer": {"text": "안전장치 발동 | 배팅 일시 중단"},
        }
        try:
            requests.post(self.config.discord_webhook_url, json={"embeds": [embed]}, timeout=10)
        except Exception as e:
            logger.error(f"리스크 알림 실패: {e}")


# ============================================================================
# 메인 실행 (테스트)
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=" * 60)
    print("Alert System Test")
    print("=" * 60)

    config = AlertConfig.from_env()
    print(f"  Discord: {'configured' if config.has_discord else 'not configured'}")
    print(f"  Email:   {'configured' if config.has_email else 'not configured'}")

    manager = AlertManager(config)

    # 테스트용 가짜 ScanResult
    class MockResult:
        ticker = "TEST-NBA-001"
        title = "Celtics vs Lakers - Test Alert"
        sport = "NBA"
        edge = 0.08
        ev = 0.065
        model_prob = 0.70
        kalshi_implied_prob = 0.62
        recommended_bet = 45.00
        confidence = "high"
        action = "BET"

    mock = MockResult()

    if "--test" in sys.argv:
        print("\nSending test alert...")
        if manager.should_alert(mock):
            sent = manager.send_alert(mock)
            if sent:
                print("Test alert sent successfully!")
            else:
                print("Failed to send alert. Check your Discord/Email configuration.")
        else:
            print("Alert filtered by rate limiting or confidence rules.")
    else:
        print("\nRun with --test to send a test alert")
        print()
        print("To configure, add to .env:")
        print("  DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...")
        print("  GMAIL_USER=your@gmail.com")
        print("  GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx")
        print("  GMAIL_RECIPIENT=your@gmail.com")

    # should_alert 로직 테스트
    print()
    print("--- Rate Limiting Test ---")
    print(f"  should_alert (1st): {manager.should_alert(mock)}")
    manager.send_alert = lambda x: True  # mock send
    manager.daily_count += 1
    manager.last_alert_times["TEST-NBA-001"] = datetime.now(timezone.utc)
    print(f"  should_alert (cooldown): {manager.should_alert(mock)}")

    mock2 = MockResult()
    mock2.ticker = "TEST-NBA-002"
    mock2.edge = 0.01  # 낮은 엣지
    print(f"  should_alert (low edge): {manager.should_alert(mock2)}")
