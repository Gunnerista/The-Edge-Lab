"""
Module: position_manager.py
Purpose: Broker-agnostic view of open positions + exposure helpers.
"""

from __future__ import annotations

from warmachine.trading.broker_adapter import BrokerAdapter


class PositionManager:
    def __init__(self, broker: BrokerAdapter):
        self.broker = broker

    def get_open_positions(self) -> list[dict]:
        return self.broker.get_positions()

    def get_total_open_exposure_cents(self) -> int:
        return sum(p["market_exposure_cents"] for p in self.get_open_positions())

    def get_exposure_for_ticker(self, ticker: str) -> int:
        return sum(
            p["market_exposure_cents"]
            for p in self.get_open_positions()
            if p["ticker"] == ticker
        )
