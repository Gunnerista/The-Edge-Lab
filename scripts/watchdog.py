#!/usr/bin/env python3
"""
Watchdog - Auto-restart runner.py on crash
============================================
Prediction Market War Machine

Launched by kalshi_watchdog.bat at Windows startup (via Startup folder).
Runs runner.py as a subprocess and restarts it if it exits unexpectedly.

Logs to data/watchdog.log for debugging.
"""

import sys
import time
import subprocess
import logging
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
LOG_DIR = PROJECT_ROOT / "data"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOG_DIR / "watchdog.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("watchdog")

# Config
RUNNER_SCRIPT = str(SCRIPTS_DIR / "runner.py")
RESTART_DELAY = 10          # seconds between restarts
MAX_RAPID_RESTARTS = 5      # max restarts within RAPID_WINDOW
RAPID_WINDOW = 300          # 5 minutes
COOLDOWN_DELAY = 600        # 10 min cooldown after too many rapid restarts


def run():
    """Main watchdog loop - launch and monitor runner.py."""
    logger.info("=" * 60)
    logger.info("Watchdog started")
    logger.info(f"  Runner: {RUNNER_SCRIPT}")
    logger.info(f"  Python: {sys.executable}")
    logger.info("=" * 60)

    restart_times = []

    while True:
        # Check for rapid restart loop
        now = time.time()
        restart_times = [t for t in restart_times if now - t < RAPID_WINDOW]

        if len(restart_times) >= MAX_RAPID_RESTARTS:
            logger.error(
                f"Too many restarts ({len(restart_times)} in {RAPID_WINDOW}s). "
                f"Cooling down for {COOLDOWN_DELAY}s..."
            )
            time.sleep(COOLDOWN_DELAY)
            restart_times.clear()

        # Launch runner.py
        logger.info("Launching runner.py...")
        restart_times.append(time.time())

        try:
            proc = subprocess.Popen(
                [sys.executable, RUNNER_SCRIPT],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            logger.info(f"runner.py started (PID {proc.pid})")

            # Stream output to log
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    logger.info(f"[runner] {line}")

            exit_code = proc.wait()
            logger.warning(f"runner.py exited with code {exit_code}")

        except KeyboardInterrupt:
            logger.info("Watchdog interrupted by user")
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
            break

        except Exception as e:
            logger.error(f"Failed to launch runner.py: {e}")

        logger.info(f"Restarting in {RESTART_DELAY}s...")
        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    run()
