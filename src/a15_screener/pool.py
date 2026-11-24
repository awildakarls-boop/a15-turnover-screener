"""A-share stock universe construction with strict security-type filtering,
historical listing/delisting window checks, board normalization, and code mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .calendar_utils import normalize_date_yyyymmdd

VALID_EXCHANGES = {"SSE", "SZSE", "BSE"}
BOARD_ORDER = ["沪市主板", "深市主板", "创业板", "科创板", "北交所"]

# Valid A-share ordinary equity prefixes by exchange (explicitly excluding B-shares 200/900,
# CDRs 689, funds/ETFs 50/51/56/58/15/16/18, convertible bonds 11/12, indices, NEEQ).
ALLOWED_PREFIXES: dict[str, tuple[str, ...]] = {
    "SSE": ("600", "601", "603", "605", "688"),
    "SZSE": ("000", "001", "002", "003", "300", "301", "302"),
    "BSE": ("920", "83", "87", "88", "43"),
}


@dataclass(frozen=True)
class PoolStock:
    ts_code: str
    symbol: str  # string preserving leading zeros e.g. "000001"
    name: str
    exchange: str
    market: str
    board: str  # 沪市主板 / 深市主板 / 创业板 / 科创板 / 北交所
    industry: str
    curr_type: str
    list_status: str
    list_date: str
    delist_date: str | None
    is_st: bool
    is_first_day: bool
    old_codes: tuple[str, ...] = field(default_factory=tuple)


def normalize_board(exchange: str, market: str, symbol: str) -> str | None:
    ex = (exchange or "").strip().upper()
    mk = (market or "").strip()
    sym = str(symbol or "").strip()

    # Explicitly exclude CDRs (689xxx.SH)
    if ex == "SSE" and sym.startswith("689"):
        return None
    # Explicitly exclude B-shares
    if (ex == "SSE" and sym.startswith("900")) or (ex == "SZSE" and sym.startswith("200")):
        return None

    if ex == "SSE":
        if sym.startswith("688") or mk == "科创板":
            return "科创板" if sym.startswith("688") else None
        if sym.startswith(("600", "601", "603", "605")):
            return "沪市主板"
        return None

    if ex == "SZSE":
        if sym.startswith(("300", "301", "302")) or mk == "创业板":
            return "创业板" if sym.startswith(("300", "301", "302")) else None
        if sym.startswith(("000", "001", "002", "003")):
            return "深市主板"
        return None

    if ex == "BSE":
        if mk == "北交所" or sym.startswith(("920", "83", "87", "88", "43")):
            return "北交所"
        return None

    return None


def detect_st_flag(name: str) -> bool:
    u = (name or "").upper()
    return "ST" in u or u.startswith("S*ST") or u.startswith("*ST")


def build_stock_pool(
    stock_basic_df: pd.DataFrame,
    target_date: str,
    bse_mapping_df: pd.DataFrame | None = None,
    historical_names: dict[str, str] | None = None,
) -> tuple[list[PoolStock], dict[str, int]]:
    """Build exact A-share CNY ordinary equity pool active on `target_date`.
    Includes ST stocks and newly listed stocks (list_date == target_date),
    includes subsequently delisted stocks when backfilling (delist_date > target_date),
    and excludes B-shares, funds, ETFs, convertible bonds, indices, CDRs, and NEEQ securities.
    """
    d = normalize_date_yyyymmdd(target_date)
    excluded_reasons: dict[str, int] = {
        "non_cny_currency": 0,
        "invalid_exchange": 0,
        "excluded_security_type_or_cdr": 0,
        "not_yet_listed": 0,
        "already_delisted": 0,
        "duplicate_ts_code": 0,
    }

    old_code_map: dict[str, list[str]] = {}
    if bse_mapping_df is not None and len(bse_mapping_df) > 0:
        for _, r in bse_mapping_df.iterrows():
            nc = str(r.get("n_code") or "").strip()
            oc = str(r.get("o_code") or "").strip()
            if nc and oc:
                old_code_map.setdefault(nc, []).append(oc)

    pool: list[PoolStock] = []
    seen: set[str] = set()

    for _, row in stock_basic_df.iterrows():
        ts_code = str(row.get("ts_code") or "").strip()
        if not ts_code:
            continue
        if ts_code in seen:
            excluded_reasons["duplicate_ts_code"] += 1
            continue

        curr_type = str(row.get("curr_type") or "").strip().upper()
        if curr_type != "CNY":
            excluded_reasons["non_cny_currency"] += 1
            continue

        exchange = str(row.get("exchange") or "").strip().upper()
        if exchange not in VALID_EXCHANGES:
            excluded_reasons["invalid_exchange"] += 1
            continue

        # Preserve leading zeros in symbol
        raw_sym = row.get("symbol")
        symbol = str(raw_sym).strip() if pd.notna(raw_sym) and str(raw_sym).strip() else ts_code.split(".")[0]
        if symbol.isdigit() and len(symbol) < 6:
            symbol = symbol.zfill(6)

        market = "" if pd.isna(row.get("market")) else str(row.get("market")).strip()
        board = normalize_board(exchange, market, symbol)
        if board is None:
            excluded_reasons["excluded_security_type_or_cdr"] += 1
            continue

        if not symbol.startswith(ALLOWED_PREFIXES[exchange]):
            excluded_reasons["excluded_security_type_or_cdr"] += 1
            continue

        raw_list = row.get("list_date")
        if pd.isna(raw_list) or not str(raw_list).strip():
            excluded_reasons["not_yet_listed"] += 1
            continue
        list_date = normalize_date_yyyymmdd(str(raw_list))
        if list_date > d:
            excluded_reasons["not_yet_listed"] += 1
            continue

        raw_delist = row.get("delist_date")
        delist_date: str | None = None
        if pd.notna(raw_delist) and str(raw_delist).strip() and str(raw_delist).strip().lower() != "none":
            delist_date = normalize_date_yyyymmdd(str(raw_delist))
            # If delisted on or before D, no longer listed on D
            if delist_date <= d:
                excluded_reasons["already_delisted"] += 1
                continue

        seen.add(ts_code)
        base_name = "" if pd.isna(row.get("name")) else str(row.get("name")).strip()
        eff_name = (historical_names or {}).get(ts_code, base_name)
        industry = "" if pd.isna(row.get("industry")) else str(row.get("industry")).strip()
        list_status = "" if pd.isna(row.get("list_status")) else str(row.get("list_status")).strip()

        pool.append(
            PoolStock(
                ts_code=ts_code,
                symbol=symbol,
                name=eff_name,
                exchange=exchange,
                market=market or board,
                board=board,
                industry=industry or "未分类",
                curr_type=curr_type,
                list_status=list_status,
                list_date=list_date,
                delist_date=delist_date,
                is_st=detect_st_flag(eff_name),
                is_first_day=(list_date == d),
                old_codes=tuple(sorted(set(old_code_map.get(ts_code, [])))),
            )
        )

    pool.sort(key=lambda x: x.ts_code)
    return pool, excluded_reasons


def pool_board_counts(pool: list[PoolStock]) -> dict[str, int]:
    counts: dict[str, int] = {b: 0 for b in BOARD_ORDER}
    for s in pool:
        counts[s.board] = counts.get(s.board, 0) + 1
    return counts


def to_baostock_code(ts_code: str) -> str | None:
    sym, exch = ts_code.split(".")
    if exch == "SH":
        return f"sh.{sym}"
    if exch == "SZ":
        return f"sz.{sym}"
    return None


def to_tencent_code(ts_code: str) -> str | None:
    sym, exch = ts_code.split(".")
    if exch == "SH":
        return f"sh{sym}"
    if exch == "SZ":
        return f"sz{sym}"
    return None


def to_eastmoney_secid(ts_code: str) -> str:
    sym, exch = ts_code.split(".")
    return f"1.{sym}" if exch == "SH" else f"0.{sym}"


def to_sina_code(ts_code: str) -> str:
    sym, exch = ts_code.split(".")
    pref = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[exch]
    return f"{pref}{sym}"
