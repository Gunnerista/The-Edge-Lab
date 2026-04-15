#!/usr/bin/env python3
"""
Database Configuration & Connection Manager
=============================================
Prediction Market War Machine

Provides a unified connection interface that supports both SQLite and PostgreSQL.
Uses connection pooling (ThreadedConnectionPool) for PostgreSQL.

Usage:
    from db_config import get_connection, release_connection, DB_TYPE

    conn = get_connection("market_data")
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM markets")
        print(cur.fetchone()[0])
    finally:
        release_connection(conn)

Toggle DB_TYPE between "sqlite" and "postgresql" to switch backends.
"""

import os
from pathlib import Path as _PathForEnv
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_PathForEnv(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass
import sqlite3
import threading
from pathlib import Path

# ============================================================================
# Configuration
# ============================================================================

# Toggle this to switch between SQLite and PostgreSQL
DB_TYPE = "postgresql"  # "sqlite" or "postgresql"

# PostgreSQL connection settings
PG_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "warmachine",
    "user": "postgres",
    "password": os.environ.get("WARMACHINE_PG_PASSWORD", ""),
}

# Pool settings
PG_POOL_MIN = 2
PG_POOL_MAX = 10

# SQLite paths (fallback)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATHS = {
    "market_data": PROJECT_ROOT / "data" / "market_data.db",
    "nba_data": PROJECT_ROOT / "data" / "nba_data.db",
    "scanner": PROJECT_ROOT / "data" / "scanner.db",
}

# ============================================================================
# PostgreSQL Connection Pool (lazy-initialized, thread-safe)
# ============================================================================

_pg_pool = None
_pg_pool_lock = threading.Lock()


def _get_pg_pool():
    """Get or create the PostgreSQL connection pool (singleton)."""
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                import psycopg2.pool
                _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=PG_POOL_MIN,
                    maxconn=PG_POOL_MAX,
                    host=PG_CONFIG["host"],
                    port=PG_CONFIG["port"],
                    dbname=PG_CONFIG["dbname"],
                    user=PG_CONFIG["user"],
                    password=PG_CONFIG["password"],
                )
    return _pg_pool


def get_connection(db_name: str = "market_data"):
    """
    Get a database connection.

    Args:
        db_name: Which database to connect to.
                 For PostgreSQL, all tables live in one DB so this is ignored.
                 For SQLite, maps to the appropriate .db file.

    Returns:
        A database connection (psycopg2 connection or sqlite3 connection).
    """
    if DB_TYPE == "postgresql":
        pool = _get_pg_pool()
        conn = pool.getconn()
        conn.autocommit = False
        return conn
    else:
        db_path = SQLITE_PATHS.get(db_name, SQLITE_PATHS["market_data"])
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn


def release_connection(conn):
    """
    Release a connection back to the pool (PostgreSQL) or close it (SQLite).
    """
    if conn is None:
        return
    if DB_TYPE == "postgresql":
        pool = _get_pg_pool()
        try:
            pool.putconn(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    else:
        try:
            conn.close()
        except Exception:
            pass


def close_all():
    """Close the connection pool. Call on shutdown."""
    global _pg_pool
    if _pg_pool is not None:
        with _pg_pool_lock:
            if _pg_pool is not None:
                _pg_pool.closeall()
                _pg_pool = None
