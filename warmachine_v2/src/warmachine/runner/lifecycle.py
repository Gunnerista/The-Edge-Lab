"""
Module: lifecycle.py
Purpose: Graceful shutdown - threading.Event + SIGINT/SIGTERM handlers.
"""

from __future__ import annotations

import signal
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from warmachine.utils.logger import get_logger

log = get_logger(__name__)


class LifecycleManager:
    def __init__(self):
        self._stop_event = threading.Event()
        self._shutdown_hooks: list[Callable[[], None]] = []
        self.started_at: Optional[datetime] = None

    def register_shutdown_hook(self, callback: Callable[[], None]) -> None:
        self._shutdown_hooks.append(callback)

    def install_signal_handlers(self) -> None:
        def handler(signum, frame):
            log.critical(f"Signal {signum} received. Initiating graceful shutdown.")
            self._stop_event.set()

        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
            log.info("Signal handlers installed (SIGINT, SIGTERM)")
        except ValueError as e:
            log.warning(f"Signal handler registration failed (not main thread?): {e}")

    def start(self) -> None:
        self.started_at = datetime.now(timezone.utc)
        self.install_signal_handlers()
        log.info("Lifecycle started")

    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def wait_or_stop(self, seconds: float) -> bool:
        return self._stop_event.wait(timeout=seconds)

    def request_stop(self) -> None:
        self._stop_event.set()

    def run_shutdown_hooks(self) -> None:
        log.info(f"Running {len(self._shutdown_hooks)} shutdown hooks")
        for hook in self._shutdown_hooks:
            try:
                hook()
            except Exception as e:
                log.error(f"Shutdown hook failed: {e}")
