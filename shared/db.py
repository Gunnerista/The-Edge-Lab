#!/usr/bin/env python3
"""
Central Database Connection Pool
==================================
Prediction Market War Machine

All PostgreSQL connections go through this module.
Usage:
    from db import get_connection, put_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM markets")
        print(cur.fetchone()[0])
        conn.commit()
    finally:
        put_connection(conn)

    # Dict cursor (rows as dicts):
    conn = get_connection(dict_cursor=True)
"""

import os
from pathlib import Path as _PathForEnv
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_PathForEnv(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass
import threading
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

_pool = pool.ThreadedConnectionPool(
    minconn=2,
    maxconn=10,
    host="localhost",
    port=5432,
    dbname="warmachine",
    user="postgres",
    password=os.environ.get("WARMACHINE_PG_PASSWORD", ""),
    options="-c statement_timeout=120000",
)

_lock = threading.Lock()


def get_connection(dict_cursor=False):
    with _lock:
        conn = _pool.getconn()
    if dict_cursor:
        conn.cursor_factory = RealDictCursor
    else:
        conn.cursor_factory = None
    return conn


def put_connection(conn):
    if conn is None:
        return
    try:
        if not conn.closed:
            conn.rollback()  # reset any uncommitted state before returning to pool
    except Exception:
        pass
    with _lock:
        _pool.putconn(conn)


def close_all():
    _pool.closeall()
