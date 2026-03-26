#!/usr/bin/env python3
"""
Market Recorder - Phase 1: Universal Data Collector
====================================================
Prediction Market War Machine의 핵심 데이터 수집 모듈.

Kalshi의 **모든 활성 마켓** 가격 변동을 실시간 기록.
스포츠, 정치, 경제 가리지 않고 전부 수집.

두 가지 수집 모드:
  1) WebSocket: 구독한 마켓의 실시간 tick-by-tick 기록
  2) REST Polling: 주기적으로 전체 마켓 스캔 (마켓 발견 + 스냅샷)

저장소: SQLite (data/market_data.db)

사용법:
    # 기본 실행 (REST polling, 60초 간격)
    python scripts/market_recorder.py

    # WebSocket 모드 (실시간 스트리밍)
    python scripts/market_recorder.py --mode ws

    # REST 단일 스냅샷
    python scripts/market_recorder.py --mode snapshot

    # 기존 JSONL 데이터 마이그레이션
    python scripts/market_recorder.py --migrate
"""

import sys
import json
import time
import sqlite3
import asyncio
import logging
import argparse
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from kalshi_client import KalshiConfig, KalshiAPIClient, create_client

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "market_data.db"
LEGACY_JSONL = DATA_DIR / "price_observations.jsonl"

# REST polling defaults
DEFAULT_POLL_INTERVAL = 60       # seconds between full market scans
MAX_MARKETS_PER_PAGE = 200       # Kalshi API limit
MAX_PAGES = 50                   # safety cap: 50 * 200 = 10000 markets max

# WebSocket batching
WS_FLUSH_INTERVAL = 5           # seconds between DB flushes for WS data
WS_MARKET_DISCOVERY_INTERVAL = 300  # re-discover markets every 5 min


# ============================================================================
# SQLite Schema & Database
# ============================================================================

SCHEMA_SQL = """
-- Price snapshots: every observation of a market's price
CREATE TABLE IF NOT EXISTS price_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,                -- ISO 8601 UTC
    ticker      TEXT NOT NULL,                -- market ticker
    event_ticker TEXT DEFAULT '',             -- parent event
    yes_bid     REAL DEFAULT 0,              -- best yes buy price (0-1 dollars)
    yes_ask     REAL DEFAULT 0,              -- best yes sell price
    yes_price   REAL DEFAULT 0,              -- mid-price
    no_bid      REAL DEFAULT 0,
    no_ask      REAL DEFAULT 0,
    volume      INTEGER DEFAULT 0,           -- cumulative traded contracts
    open_interest INTEGER DEFAULT 0,         -- outstanding contracts
    source      TEXT DEFAULT 'rest'          -- 'rest' or 'ws'
);

-- Market metadata: static info about each market (updated periodically)
CREATE TABLE IF NOT EXISTS markets (
    ticker          TEXT PRIMARY KEY,
    event_ticker    TEXT DEFAULT '',
    title           TEXT DEFAULT '',
    subtitle        TEXT DEFAULT '',
    category        TEXT DEFAULT '',          -- from Kalshi API or our classifier
    status          TEXT DEFAULT 'open',
    close_time      TEXT DEFAULT '',
    expiration_time TEXT DEFAULT '',
    result          TEXT DEFAULT '',          -- 'yes', 'no', '' (unsettled)
    first_seen      TEXT NOT NULL,            -- when we first recorded this market
    last_updated    TEXT NOT NULL
);

-- Trade records: individual fills (from WebSocket trade channel)
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    yes_price   REAL DEFAULT 0,
    count       INTEGER DEFAULT 0,
    taker_side  TEXT DEFAULT ''
);

-- Indices for fast queries
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON price_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON price_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_time ON price_snapshots(ticker, timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_markets_category ON markets(category);
CREATE INDEX IF NOT EXISTS idx_markets_status ON markets(status);
"""


class MarketDatabase:
    """SQLite database for market data storage."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")   # better concurrent reads
        self.conn.execute("PRAGMA synchronous=NORMAL")  # faster writes, still safe
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def insert_snapshot(self, snapshot: dict):
        """Insert a single price snapshot."""
        self.conn.execute(
            """INSERT INTO price_snapshots
               (timestamp, ticker, event_ticker, yes_bid, yes_ask, yes_price,
                no_bid, no_ask, volume, open_interest, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot["timestamp"],
                snapshot["ticker"],
                snapshot.get("event_ticker", ""),
                snapshot.get("yes_bid", 0),
                snapshot.get("yes_ask", 0),
                snapshot.get("yes_price", 0),
                snapshot.get("no_bid", 0),
                snapshot.get("no_ask", 0),
                snapshot.get("volume", 0),
                snapshot.get("open_interest", 0),
                snapshot.get("source", "rest"),
            ),
        )

    def insert_snapshots_batch(self, snapshots: List[dict]):
        """Batch insert price snapshots."""
        if not snapshots:
            return
        self.conn.executemany(
            """INSERT INTO price_snapshots
               (timestamp, ticker, event_ticker, yes_bid, yes_ask, yes_price,
                no_bid, no_ask, volume, open_interest, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    s["timestamp"], s["ticker"], s.get("event_ticker", ""),
                    s.get("yes_bid", 0), s.get("yes_ask", 0), s.get("yes_price", 0),
                    s.get("no_bid", 0), s.get("no_ask", 0),
                    s.get("volume", 0), s.get("open_interest", 0),
                    s.get("source", "rest"),
                )
                for s in snapshots
            ],
        )
        self.conn.commit()

    def upsert_market(self, market: dict):
        """Insert or update market metadata."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO markets
               (ticker, event_ticker, title, subtitle, category, status,
                close_time, expiration_time, result, first_seen, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                   status = excluded.status,
                   result = excluded.result,
                   close_time = excluded.close_time,
                   expiration_time = excluded.expiration_time,
                   last_updated = excluded.last_updated""",
            (
                market["ticker"],
                market.get("event_ticker", ""),
                market.get("title", ""),
                market.get("subtitle", ""),
                market.get("category", ""),
                market.get("status", "open"),
                market.get("close_time", ""),
                market.get("expiration_time", ""),
                market.get("result", ""),
                now,
                now,
            ),
        )

    def insert_trade(self, trade: dict):
        """Insert a trade record."""
        self.conn.execute(
            """INSERT INTO trades (timestamp, ticker, yes_price, count, taker_side)
               VALUES (?, ?, ?, ?, ?)""",
            (
                trade["timestamp"],
                trade["ticker"],
                trade.get("yes_price", 0),
                trade.get("count", 0),
                trade.get("taker_side", ""),
            ),
        )

    def commit(self):
        self.conn.commit()

    def get_stats(self) -> dict:
        """Get database statistics."""
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM price_snapshots")
        snapshots = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM markets")
        markets = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM trades")
        trades = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT ticker) FROM price_snapshots")
        unique_tickers = cur.fetchone()[0]
        cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM price_snapshots")
        row = cur.fetchone()
        return {
            "total_snapshots": snapshots,
            "total_markets": markets,
            "total_trades": trades,
            "unique_tickers_observed": unique_tickers,
            "earliest_snapshot": row[0] or "N/A",
            "latest_snapshot": row[1] or "N/A",
        }

    def close(self):
        self.conn.close()


# ============================================================================
# Category Inference (lightweight, Phase 2 will do proper classification)
# ============================================================================

# Kalshi ticker prefixes that indicate market type
_CATEGORY_PREFIXES = {
    # Sports
    "KXNBA": "Sports", "KXMLB": "Sports", "KXNFL": "Sports",
    "KXNHL": "Sports", "KXSOC": "Sports", "KXMMA": "Sports",
    "KXTEN": "Sports", "KXGOL": "Sports", "KXATP": "Sports",
    "KXNCAA": "Sports", "KXSHL": "Sports", "KXCS2": "Sports",
    "KXDOTA": "Sports", "KXFIFA": "Sports", "KXDIMAY": "Sports",
    "KXMVE": "Sports",  # multi-leg sports parlays
    # Elections & Politics
    "KXPRES": "Elections", "KXSEN": "Elections", "KXHOU": "Elections",
    "KXGOV": "Elections", "KXELEC": "Elections", "KXNEWPOPE": "Elections",
    "KXSEC": "Politics", "KXHOCHUL": "Politics", "KXKNOX": "Politics",
    "KXCONGRESS": "Politics", "KXTRUMP": "Politics",
    # Economics & Financials
    "KXFED": "Economics", "KXCPI": "Economics", "KXGDP": "Economics",
    "KXJOB": "Economics", "KXRATE": "Economics",
    "KXSP5": "Financials", "KXNAS": "Financials", "KXTUDOR": "Financials",
    "KXGOLD": "Financials", "KXSILVER": "Financials", "KXCOPPER": "Financials",
    # Crypto
    "KXBTC": "Crypto", "KXETH": "Crypto", "KXCRYPTO": "Crypto",
    # Climate & Weather
    "KXWEA": "Climate and Weather", "KXHUR": "Climate and Weather",
    "KXTEMP": "Climate and Weather", "KXWARMING": "Climate and Weather",
    # Entertainment
    "KXOSCAR": "Entertainment", "KXSPOTIFY": "Entertainment",
    # Companies & Tech
    "KXMETA": "Companies", "KXGROK": "Companies",
    "KXELON": "Science and Technology", "KXMARS": "Science and Technology",
    "KXTEMP": "weather",
}


def _infer_category(ticker: str, title: str = "") -> str:
    """
    Quick category inference from ticker prefix.
    Returns empty string if unknown (Phase 2 classifier will handle).
    """
    ticker_upper = ticker.upper()
    for prefix, cat in _CATEGORY_PREFIXES.items():
        if ticker_upper.startswith(prefix):
            return cat
    return ""


# ============================================================================
# REST Polling Recorder
# ============================================================================

class RESTRecorder:
    """
    Periodically scans ALL open Kalshi markets via REST API.
    Records a full price snapshot of every active market.
    """

    def __init__(self, client: KalshiAPIClient, db: MarketDatabase):
        self.client = client
        self.db = db
        self._event_categories: Dict[str, str] = {}  # event_ticker -> category
        self._category_cache_time = 0.0

    def _refresh_event_categories(self):
        """Fetch event categories from Kalshi API (cached for 1 hour)."""
        now = time.time()
        if self._event_categories and (now - self._category_cache_time) < 3600:
            return  # cache still fresh

        try:
            cursor = None
            for page in range(50):
                params = {"limit": 200, "status": "open"}
                if cursor:
                    params["cursor"] = cursor
                resp = self.client._request("GET", "/events", params=params)
                events = resp.get("events", [])
                if not events:
                    break
                for e in events:
                    et = e.get("event_ticker", "")
                    cat = e.get("category", "")
                    if et and cat:
                        self._event_categories[et] = cat
                cursor = resp.get("cursor")
                if not cursor:
                    break
                time.sleep(0.1)
            self._category_cache_time = now
            logger.info(f"Loaded {len(self._event_categories)} event categories from API")
        except Exception as e:
            logger.error(f"Event category fetch failed: {e}")

    def discover_all_markets(self) -> List[dict]:
        """
        Paginate through ALL open markets on Kalshi.
        Returns raw market dicts from the API.
        """
        all_markets = []
        cursor = None

        for page in range(MAX_PAGES):
            try:
                resp = self.client.get_markets(
                    limit=MAX_MARKETS_PER_PAGE,
                    cursor=cursor,
                    status="open",
                )
            except Exception as e:
                logger.error(f"Market discovery page {page} failed: {e}")
                break

            markets = resp.get("markets", [])
            if not markets:
                break

            all_markets.extend(markets)
            cursor = resp.get("cursor")

            if not cursor:
                break

            # Rate limit courtesy
            time.sleep(0.2)

        logger.info(f"Discovered {len(all_markets)} open markets")
        return all_markets

    def record_snapshot(self) -> int:
        """
        Take a full snapshot of all open markets.
        Returns number of markets recorded.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._refresh_event_categories()
        raw_markets = self.discover_all_markets()

        if not raw_markets:
            logger.warning("No markets found in snapshot")
            return 0

        snapshots = []
        for m in raw_markets:
            ticker = m.get("ticker", "")
            if not ticker:
                continue

            # Kalshi API v2 returns dollar prices as "yes_bid_dollars" etc.
            yes_bid = float(m.get("yes_bid_dollars", 0) or 0)
            yes_ask = float(m.get("yes_ask_dollars", 0) or 0)
            no_bid = float(m.get("no_bid_dollars", 0) or 0)
            no_ask = float(m.get("no_ask_dollars", 0) or 0)

            mid = (yes_bid + yes_ask) / 2 if yes_bid > 0 and yes_ask > 0 else 0

            volume = int(float(m.get("volume_fp", 0) or 0))
            open_interest = int(float(m.get("open_interest_fp", 0) or 0))

            snapshots.append({
                "timestamp": now,
                "ticker": ticker,
                "event_ticker": m.get("event_ticker", ""),
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "yes_price": mid,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "volume": volume,
                "open_interest": open_interest,
                "source": "rest",
            })

            # Category: prefer API event category, fallback to ticker prefix inference
            event_ticker = m.get("event_ticker", "")
            api_category = self._event_categories.get(event_ticker, "")
            category = api_category if api_category else _infer_category(ticker, m.get("title", ""))

            # Upsert market metadata
            self.db.upsert_market({
                "ticker": ticker,
                "event_ticker": event_ticker,
                "title": m.get("title", ""),
                "subtitle": m.get("subtitle", ""),
                "category": category,
                "status": m.get("status", ""),
                "close_time": m.get("close_time", ""),
                "expiration_time": m.get("expiration_time", ""),
                "result": m.get("result", ""),
            })

        # Batch insert all snapshots
        self.db.insert_snapshots_batch(snapshots)
        self.db.commit()

        logger.info(f"Recorded {len(snapshots)} market snapshots")
        return len(snapshots)

    def run_polling(self, interval: int = DEFAULT_POLL_INTERVAL, max_cycles: int = None):
        """
        Continuous polling loop.

        Args:
            interval: seconds between snapshots
            max_cycles: stop after N cycles (None = infinite)
        """
        cycle = 0
        logger.info(f"Starting REST polling (interval={interval}s)")

        while True:
            cycle += 1
            start = time.time()

            try:
                count = self.record_snapshot()
                elapsed = time.time() - start
                stats = self.db.get_stats()
                logger.info(
                    f"Cycle {cycle}: {count} markets in {elapsed:.1f}s | "
                    f"Total snapshots: {stats['total_snapshots']:,} | "
                    f"Unique tickers: {stats['unique_tickers_observed']}"
                )
            except Exception as e:
                logger.error(f"Cycle {cycle} failed: {e}")

            if max_cycles and cycle >= max_cycles:
                logger.info(f"Reached max cycles ({max_cycles}). Stopping.")
                break

            # Sleep remaining time
            elapsed = time.time() - start
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)


# ============================================================================
# WebSocket Recorder
# ============================================================================

class WSRecorder:
    """
    Real-time recording via Kalshi WebSocket.

    Flow:
    1. Discover all open markets via REST
    2. Subscribe to ticker + trade channels for all markets
    3. Buffer updates and flush to SQLite periodically
    4. Re-discover markets periodically to catch new ones
    """

    def __init__(self, config: KalshiConfig, db: MarketDatabase):
        self.config = config
        self.db = db
        self._buffer: List[dict] = []
        self._trade_buffer: List[dict] = []
        self._running = False

    async def run(self, duration_seconds: int = None):
        """Main WebSocket recording loop."""
        from kalshi_ws import KalshiWebSocket

        self._running = True

        # Discover markets first
        client = create_client(self.config)
        rest = RESTRecorder(client, self.db)
        raw_markets = rest.discover_all_markets()
        tickers = [m["ticker"] for m in raw_markets if m.get("ticker")]

        if not tickers:
            logger.error("No markets found. Cannot start WebSocket recording.")
            return

        # Store metadata
        for m in raw_markets:
            if m.get("ticker"):
                self.db.upsert_market({
                    "ticker": m["ticker"],
                    "event_ticker": m.get("event_ticker", ""),
                    "title": m.get("title", ""),
                    "subtitle": m.get("subtitle", ""),
                    "category": m.get("category", ""),
                    "status": m.get("status", "open"),
                    "close_time": m.get("close_time", ""),
                    "expiration_time": m.get("expiration_time", ""),
                    "result": m.get("result", ""),
                })
        self.db.commit()

        logger.info(f"Subscribing to {len(tickers)} markets via WebSocket")

        # Kalshi WebSocket has a limit on subscription size.
        # We'll subscribe in batches if needed.
        # For now, subscribe to all (Kalshi allows large subscriptions).
        ws = KalshiWebSocket(self.config)

        def on_tick(update):
            self._buffer.append({
                "timestamp": update.timestamp,
                "ticker": update.market_ticker,
                "event_ticker": "",  # not available in WS tick
                "yes_bid": update.yes_bid,
                "yes_ask": update.yes_ask,
                "yes_price": update.yes_price,
                "no_bid": update.no_bid,
                "no_ask": update.no_ask,
                "volume": update.volume,
                "open_interest": update.open_interest,
                "source": "ws",
            })

        def on_trade(update):
            self._trade_buffer.append({
                "timestamp": update.timestamp,
                "ticker": update.market_ticker,
                "yes_price": update.yes_price,
                "count": update.count,
                "taker_side": update.taker_side,
            })

        ws.on_ticker(on_tick)
        ws.on_trade(on_trade)

        # Start flushing task
        flush_task = asyncio.create_task(self._flush_loop())

        try:
            await ws.connect_and_subscribe(
                tickers=tickers,
                channels=["ticker", "trade"],
                duration_seconds=duration_seconds,
            )
        except Exception as e:
            logger.error(f"WebSocket recording error: {e}")
        finally:
            self._running = False
            flush_task.cancel()
            self._flush_now()  # final flush

    async def _flush_loop(self):
        """Periodically flush buffered data to SQLite."""
        while self._running:
            await asyncio.sleep(WS_FLUSH_INTERVAL)
            self._flush_now()

    def _flush_now(self):
        """Flush all buffered data to database."""
        if self._buffer:
            count = len(self._buffer)
            self.db.insert_snapshots_batch(self._buffer)
            self._buffer.clear()
            logger.debug(f"Flushed {count} snapshots to DB")

        if self._trade_buffer:
            count = len(self._trade_buffer)
            for t in self._trade_buffer:
                self.db.insert_trade(t)
            self.db.commit()
            self._trade_buffer.clear()
            logger.debug(f"Flushed {count} trades to DB")


# ============================================================================
# Legacy Data Migration
# ============================================================================

def migrate_legacy_jsonl(db: MarketDatabase) -> int:
    """
    Migrate existing price_observations.jsonl into SQLite.
    Maps old format fields to new schema.
    """
    if not LEGACY_JSONL.exists():
        logger.warning(f"No legacy file found at {LEGACY_JSONL}")
        return 0

    count = 0
    errors = 0

    with open(LEGACY_JSONL, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                errors += 1
                continue

            # Map old format → new schema
            # Old: team, quality, deficit, period, comeback_prob,
            #      kalshi_price, kalshi_bid, kalshi_ask, kalshi_spread,
            #      game, edge, is_alert, timestamp
            ticker = record.get("game", f"legacy-{line_num}")
            # Clean up: use game description as a pseudo-ticker
            ticker = ticker.replace(" ", "-").replace("/", "-")[:100] if ticker else f"legacy-{line_num}"

            snapshot = {
                "timestamp": record.get("timestamp", ""),
                "ticker": f"LEGACY-{ticker}",
                "event_ticker": "",
                "yes_bid": record.get("kalshi_bid", 0) or 0,
                "yes_ask": record.get("kalshi_ask", 0) or 0,
                "yes_price": record.get("kalshi_price", 0) or 0,
                "no_bid": 0,
                "no_ask": 0,
                "volume": 0,
                "open_interest": 0,
                "source": "legacy_jsonl",
            }
            db.insert_snapshot(snapshot)
            count += 1

    db.commit()
    logger.info(f"Migrated {count} legacy records ({errors} errors)")
    return count


# ============================================================================
# CLI Entry Point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Market Recorder - Kalshi Universal Data Collector"
    )
    parser.add_argument(
        "--mode", choices=["rest", "ws", "snapshot"],
        default="rest",
        help="Recording mode: rest (polling), ws (websocket), snapshot (single scan)"
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_POLL_INTERVAL,
        help=f"Polling interval in seconds (default: {DEFAULT_POLL_INTERVAL})"
    )
    parser.add_argument(
        "--duration", type=int, default=None,
        help="WebSocket duration in seconds (default: unlimited)"
    )
    parser.add_argument(
        "--max-cycles", type=int, default=None,
        help="Max polling cycles (default: unlimited)"
    )
    parser.add_argument(
        "--migrate", action="store_true",
        help="Migrate legacy price_observations.jsonl into SQLite"
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Show database statistics and exit"
    )
    parser.add_argument(
        "--prod", action="store_true",
        help="Use production API (default: demo)"
    )

    args = parser.parse_args()

    # Logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Database
    db = MarketDatabase()

    # Stats only
    if args.stats:
        stats = db.get_stats()
        print("=" * 50)
        print("Market Recorder - Database Statistics")
        print("=" * 50)
        for key, val in stats.items():
            print(f"  {key}: {val}")
        db.close()
        return

    # Migration
    if args.migrate:
        print("Migrating legacy data...")
        count = migrate_legacy_jsonl(db)
        print(f"Done. Migrated {count} records.")
        stats = db.get_stats()
        for key, val in stats.items():
            print(f"  {key}: {val}")
        db.close()
        return

    # Config
    config = KalshiConfig.from_env()
    if args.prod:
        config.use_demo = False

    print("=" * 60)
    print("Prediction Market War Machine - Market Recorder")
    print("=" * 60)
    print(f"  Mode:     {args.mode}")
    print(f"  Env:      {'PRODUCTION' if not config.use_demo else 'DEMO'}")
    print(f"  Database: {DB_PATH}")
    if args.mode == "rest":
        print(f"  Interval: {args.interval}s")
    print()

    # Graceful shutdown
    def handle_signal(sig, frame):
        logger.info("Shutdown signal received. Saving data...")
        db.commit()
        db.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        if args.mode == "snapshot":
            # Single snapshot
            client = create_client(config)
            recorder = RESTRecorder(client, db)
            count = recorder.record_snapshot()
            stats = db.get_stats()
            print(f"\nSnapshot complete: {count} markets recorded")
            for key, val in stats.items():
                print(f"  {key}: {val}")

        elif args.mode == "rest":
            # Continuous REST polling
            client = create_client(config)
            recorder = RESTRecorder(client, db)
            recorder.run_polling(
                interval=args.interval,
                max_cycles=args.max_cycles,
            )

        elif args.mode == "ws":
            # WebSocket streaming
            ws_recorder = WSRecorder(config, db)
            asyncio.run(ws_recorder.run(duration_seconds=args.duration))

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        db.commit()
        db.close()
        logger.info("Database closed. Goodbye.")


if __name__ == "__main__":
    main()
