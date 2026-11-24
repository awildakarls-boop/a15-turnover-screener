"""SQLite persistence for resumable collection, run history, cross-process task lock,
and atomic JSON snapshot publishing.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from .calculator import MinuteWindowResult, _to_dec


@contextmanager
def file_task_lock(lock_path: Path, stale_seconds: int = 3600) -> Iterator[None]:
    """Cross-process exclusive file lock shared by scheduled jobs and manual backfills."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove stale lock if left over by crashed process
    if lock_path.exists():
        age = time.time() - lock_path.stat().st_mtime
        if age > stale_seconds:
            try:
                lock_path.unlink()
            except OSError:
                pass
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as e:
        raise RuntimeError(f"Another collection task is holding lock {lock_path}") from e
    try:
        os.write(fd, f"pid={os.getpid()} started={time.time()}\n".encode("utf-8"))
        os.close(fd)
        yield
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def atomic_write_json(path: Path, payload: Any) -> None:
    """Atomically write UTF-8 JSON using temp file + os.replace so interrupted runs keep last valid state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp.write_text(text, encoding="utf-8")
    # Validate JSON parses cleanly before swapping
    json.loads(tmp.read_text(encoding="utf-8"))
    os.replace(tmp, path)


class StateStore:
    """SQLite store for resumable per-stock minute cache, company profile cache, and run logs."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS minute_cache (
                    trade_date TEXT NOT NULL,
                    ts_code TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    bar_time TEXT,
                    raw_volume_shares TEXT,
                    raw_amount_yuan TEXT,
                    auction_volume_shares TEXT,
                    auction_amount_yuan TEXT,
                    auction_source TEXT,
                    continuous_volume_shares TEXT,
                    continuous_amount_yuan TEXT,
                    price_0945 TEXT,
                    auction_handling TEXT,
                    notes_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (trade_date, ts_code)
                );

                CREATE TABLE IF NOT EXISTS company_cache (
                    ts_code TEXT PRIMARY KEY,
                    exchange TEXT,
                    main_business TEXT,
                    introduction TEXT,
                    website TEXT,
                    fetched_date TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    pool_total INTEGER,
                    candidates_count INTEGER,
                    selected_normal INTEGER,
                    selected_free INTEGER,
                    unverified_normal INTEGER,
                    unverified_free INTEGER,
                    message TEXT
                );
                """
            )

    def load_minute_cache(self, trade_date: str) -> dict[str, MinuteWindowResult]:
        out: dict[str, MinuteWindowResult] = {}
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM minute_cache WHERE trade_date = ?", (trade_date,))
            for r in cur.fetchall():
                out[r["ts_code"]] = MinuteWindowResult(
                    ts_code=r["ts_code"],
                    trade_date=r["trade_date"],
                    status=r["status"],
                    source=r["source"],
                    bar_time=r["bar_time"],
                    raw_volume_shares=_to_dec(r["raw_volume_shares"]),
                    raw_amount_yuan=_to_dec(r["raw_amount_yuan"]),
                    auction_volume_shares=_to_dec(r["auction_volume_shares"]),
                    auction_amount_yuan=_to_dec(r["auction_amount_yuan"]),
                    auction_source=r["auction_source"],
                    continuous_volume_shares=_to_dec(r["continuous_volume_shares"]),
                    continuous_amount_yuan=_to_dec(r["continuous_amount_yuan"]),
                    price_0945=_to_dec(r["price_0945"]),
                    auction_handling=r["auction_handling"] or "",
                    notes=json.loads(r["notes_json"]) if r["notes_json"] else [],
                )
        return out

    def save_minute_result(self, res: MinuteWindowResult, updated_at: str) -> None:
        def _s(d: Decimal | None) -> str | None:
            return format(d, "f") if d is not None else None

        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO minute_cache (
                    trade_date, ts_code, status, source, bar_time,
                    raw_volume_shares, raw_amount_yuan,
                    auction_volume_shares, auction_amount_yuan, auction_source,
                    continuous_volume_shares, continuous_amount_yuan,
                    price_0945, auction_handling, notes_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    res.trade_date,
                    res.ts_code,
                    res.status,
                    res.source,
                    res.bar_time,
                    _s(res.raw_volume_shares),
                    _s(res.raw_amount_yuan),
                    _s(res.auction_volume_shares),
                    _s(res.auction_amount_yuan),
                    res.auction_source,
                    _s(res.continuous_volume_shares),
                    _s(res.continuous_amount_yuan),
                    _s(res.price_0945),
                    res.auction_handling,
                    json.dumps(res.notes, ensure_ascii=False),
                    updated_at,
                ),
            )

    def load_company_cache(self) -> tuple[dict[str, dict[str, Any]], str | None]:
        out: dict[str, dict[str, Any]] = {}
        latest_date: str | None = None
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM company_cache")
            for r in cur.fetchall():
                out[r["ts_code"]] = dict(r)
                fd = r["fetched_date"]
                if latest_date is None or fd > latest_date:
                    latest_date = fd
        return out, latest_date

    def save_company_cache(self, companies: dict[str, dict[str, Any]], fetched_date: str) -> None:
        rows = [
            (
                code,
                c.get("exchange"),
                c.get("main_business"),
                c.get("introduction"),
                c.get("website"),
                fetched_date,
            )
            for code, c in companies.items()
        ]
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO company_cache
                (ts_code, exchange, main_business, introduction, website, fetched_date)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def log_run_start(self, trade_date: str, started_at: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO run_logs (trade_date, started_at, status) VALUES (?, ?, 'running')",
                (trade_date, started_at),
            )
            return int(cur.lastrowid)

    def log_run_finish(
        self,
        run_id: int,
        finished_at: str,
        status: str,
        pool_total: int = 0,
        candidates_count: int = 0,
        selected_normal: int = 0,
        selected_free: int = 0,
        unverified_normal: int = 0,
        unverified_free: int = 0,
        message: str = "",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE run_logs SET
                    finished_at = ?, status = ?, pool_total = ?, candidates_count = ?,
                    selected_normal = ?, selected_free = ?,
                    unverified_normal = ?, unverified_free = ?, message = ?
                WHERE id = ?
                """,
                (
                    finished_at,
                    status,
                    pool_total,
                    candidates_count,
                    selected_normal,
                    selected_free,
                    unverified_normal,
                    unverified_free,
                    message,
                    run_id,
                ),
            )

    def list_recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM run_logs ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]
