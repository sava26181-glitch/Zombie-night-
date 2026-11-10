"""Тонкий слой БД: PostgreSQL в проде (DATABASE_URL), SQLite для запуска на компьютере."""
import os
import sqlite3
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SQLITE_PATH = os.environ.get("SQLITE_PATH", "game.db")
IS_PG = DATABASE_URL.startswith("postgres")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id BIGINT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        coins BIGINT NOT NULL DEFAULT 0,
        best BIGINT NOT NULL DEFAULT 0,
        night INTEGER NOT NULL DEFAULT 1,
        upgrades TEXT NOT NULL DEFAULT '{}',
        vip INTEGER NOT NULL DEFAULT 0,
        referrer BIGINT,
        ref_paid INTEGER NOT NULL DEFAULT 0,
        friends INTEGER NOT NULL DEFAULT 0,
        ad_credits INTEGER NOT NULL DEFAULT 0,
        ad_day BIGINT NOT NULL DEFAULT 0,
        ad_today INTEGER NOT NULL DEFAULT 0,
        daily_day BIGINT NOT NULL DEFAULT 0,
        streak INTEGER NOT NULL DEFAULT 0,
        runs INTEGER NOT NULL DEFAULT 0,
        created BIGINT NOT NULL DEFAULT 0,
        seen BIGINT NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        started BIGINT NOT NULL,
        finished INTEGER NOT NULL DEFAULT 0,
        coins BIGINT NOT NULL DEFAULT 0,
        doubled INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS payments (
        charge_id TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        pack TEXT NOT NULL,
        stars INTEGER NOT NULL,
        refunded INTEGER NOT NULL DEFAULT 0,
        created BIGINT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS users_best ON users (best DESC)",
]


class Tx:
    def __init__(self, conn):
        self.conn = conn

    def run(self, sql, params=()):
        if IS_PG:
            sql = sql.replace("?", "%s")
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

    def one(self, sql, params=()):
        row = self.run(sql, params).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql, params=()):
        return [dict(r) for r in self.run(sql, params).fetchall()]


def _connect():
    if IS_PG:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    conn = sqlite3.connect(SQLITE_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def tx():
    conn = _connect()
    try:
        yield Tx(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# Новые колонки добавляются к уже работающей базе без потери данных.
MIGRATIONS = [
    ("users", "owned", "TEXT NOT NULL DEFAULT '[]'"),
    ("users", "equipped", "TEXT NOT NULL DEFAULT '{}'"),
    ("users", "weapons", "TEXT NOT NULL DEFAULT '[]'"),
    ("users", "weapon", "TEXT NOT NULL DEFAULT 'pistol'"),
    ("users", "story", "TEXT NOT NULL DEFAULT '{}'"),
    ("users", "story_done", "TEXT NOT NULL DEFAULT '[]'"),
    ("users", "story_pass", "INTEGER NOT NULL DEFAULT 0"),
]


def init():
    with tx() as t:
        for statement in SCHEMA:
            t.run(statement)
        for table, column, definition in MIGRATIONS:
            if IS_PG:
                t.run(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
            elif column not in {row["name"] for row in t.all(f"PRAGMA table_info({table})")}:
                t.run(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
