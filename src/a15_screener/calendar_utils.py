"""Trading calendar utilities using Asia/Shanghai timezone."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable

from .config import BJ_TZ


def normalize_date_yyyymmdd(d: str | date | datetime) -> str:
    if isinstance(d, datetime):
        return d.strftime("%Y%m%d")
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    s = str(d).strip().replace("-", "").replace("/", "")
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"Invalid date format: {d!r}")
    return s


def to_dash_date(yyyymmdd: str) -> str:
    s = normalize_date_yyyymmdd(yyyymmdd)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def today_beijing_yyyymmdd(now_bj: datetime | None = None) -> str:
    dt = now_bj.astimezone(BJ_TZ) if now_bj is not None else datetime.now(BJ_TZ)
    return dt.strftime("%Y%m%d")


def resolve_default_target_date(
    open_dates: Iterable[str],
    now_bj: datetime | None = None,
) -> str:
    """Resolve default target trading date D:
    Strictly earlier than today in Asia/Shanghai (cal_date < today_bj),
    taking the latest open trading day from the exchange calendar.
    - Ordinary Monday -> last Friday.
    - First day after holiday -> last open trading day before holiday.
    - Even if today has already closed -> still strictly before today.
    """
    today_str = today_beijing_yyyymmdd(now_bj)
    normalized = sorted({normalize_date_yyyymmdd(d) for d in open_dates})
    candidates = [d for d in normalized if d < today_str]
    if not candidates:
        raise ValueError(f"No open trading day strictly earlier than {today_str}")
    return candidates[-1]


def list_recent_closed_trading_days(
    open_dates: Iterable[str],
    count: int = 10,
    now_bj: datetime | None = None,
) -> list[str]:
    today_str = today_beijing_yyyymmdd(now_bj)
    normalized = sorted({normalize_date_yyyymmdd(d) for d in open_dates if normalize_date_yyyymmdd(d) < today_str})
    return normalized[-count:]


def calendar_window(anchor_yyyymmdd: str | None = None, lookback_days: int = 60, lookahead_days: int = 15) -> tuple[str, str]:
    if anchor_yyyymmdd:
        s = normalize_date_yyyymmdd(anchor_yyyymmdd)
        anchor = date(int(s[:4]), int(s[4:6]), int(s[6:]))
    else:
        anchor = datetime.now(BJ_TZ).date()
    return (anchor - timedelta(days=lookback_days)).strftime("%Y%m%d"), (
        anchor + timedelta(days=lookahead_days)
    ).strftime("%Y%m%d")
