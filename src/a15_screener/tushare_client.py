"""Tushare Pro client with pagination, error classification, and safe token handling."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pandas as pd
import tushare as ts

from .calendar_utils import normalize_date_yyyymmdd
from .calculator import DailyRecord, _to_dec


class TushareError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(f"[{category}] {message}")
        self.category = category
        self.raw_message = message


def classify_tushare_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if any(k in msg for k in ["权限", "积分", "没有接口访问权限", "抱歉，您没有"]):
        return "permission_denied"
    if any(k in msg for k in ["每分钟", "每天", "频次", "限额", "最多访问"]) or "rate limit" in low:
        return "rate_limited"
    if any(k in low for k in ["timeout", "connection", "proxy", "ssl", "max retries", "network", "unreachable"]):
        return "network_error"
    return "other_error"


class TushareClient:
    def __init__(self, token: str, max_retries: int = 3):
        if not token:
            raise TushareError("missing_token", "TUSHARE_TOKEN environment variable is required")
        self._pro = ts.pro_api(token)
        self.max_retries = max_retries
        self.api_stats: dict[str, dict[str, Any]] = {}

    def _call(self, api_name: str, **kwargs: Any) -> pd.DataFrame:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                fn = getattr(self._pro, api_name)
                df = fn(**kwargs)
                if df is None:
                    df = pd.DataFrame()
                self.api_stats[api_name] = {
                    "status": "ok" if len(df) > 0 else "empty_record",
                    "last_rows": int(len(df)),
                }
                return df
            except Exception as e:
                cat = classify_tushare_error(e)
                self.api_stats[api_name] = {"status": cat, "error": str(e)[:200]}
                if cat == "permission_denied":
                    raise TushareError(cat, str(e)) from e
                last_exc = e
                if attempt + 1 < self.max_retries:
                    time.sleep(1.0 * (attempt + 1))
        assert last_exc is not None
        raise TushareError(classify_tushare_error(last_exc), str(last_exc)) from last_exc

    def _call_paginated(self, api_name: str, page_size: int = 5000, **kwargs: Any) -> pd.DataFrame:
        """Fetch all rows across Tushare single-request row limits using limit/offset."""
        frames: list[pd.DataFrame] = []
        offset = 0
        while True:
            df = self._call(api_name, limit=page_size, offset=offset, **kwargs)
            if len(df) == 0:
                break
            frames.append(df)
            if len(df) < page_size:
                break
            offset += page_size
            time.sleep(0.15)
        if not frames:
            return pd.DataFrame()
        merged = pd.concat(frames, ignore_index=True)
        if "ts_code" in merged.columns and "trade_date" in merged.columns:
            merged = merged.drop_duplicates(subset=["ts_code", "trade_date"], keep="first")
        elif "ts_code" in merged.columns:
            merged = merged.drop_duplicates(subset=["ts_code"], keep="first")
        return merged

    def fetch_trade_cal(self, start_date: str, end_date: str) -> pd.DataFrame:
        return self._call(
            "trade_cal",
            exchange="SSE",
            start_date=normalize_date_yyyymmdd(start_date),
            end_date=normalize_date_yyyymmdd(end_date),
        )

    def fetch_stock_basic_all(self) -> pd.DataFrame:
        fields = "ts_code,symbol,name,exchange,market,industry,curr_type,list_status,list_date,delist_date"
        frames = []
        for st in ("L", "D", "P"):
            df = self._call_paginated("stock_basic", page_size=5000, list_status=st, fields=fields)
            if len(df) > 0:
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        merged = pd.concat(frames, ignore_index=True)
        return merged.drop_duplicates(subset=["ts_code"], keep="first")

    def fetch_bse_mapping(self) -> pd.DataFrame:
        try:
            return self._call("bse_mapping")
        except TushareError:
            return pd.DataFrame()

    def fetch_daily(self, trade_date: str) -> pd.DataFrame:
        d = normalize_date_yyyymmdd(trade_date)
        return self._call_paginated("daily", page_size=5000, trade_date=d)

    def fetch_daily_basic(self, trade_date: str) -> pd.DataFrame:
        d = normalize_date_yyyymmdd(trade_date)
        fields = "ts_code,trade_date,close,turnover_rate,turnover_rate_f,float_share,free_share,circ_mv"
        return self._call_paginated("daily_basic", page_size=5000, trade_date=d, fields=fields)

    def fetch_suspend_d(self, trade_date: str) -> set[str]:
        d = normalize_date_yyyymmdd(trade_date)
        try:
            df = self._call("suspend_d", trade_date=d)
            if len(df) == 0 or "ts_code" not in df.columns:
                return set()
            # suspend_type == 'S' indicates suspension
            if "suspend_type" in df.columns:
                df = df[df["suspend_type"].astype(str).str.upper() == "S"]
            return set(df["ts_code"].astype(str).str.strip().tolist())
        except TushareError:
            return set()

    def fetch_stock_company_all(self) -> dict[str, dict[str, Any]]:
        fields = "ts_code,exchange,main_business,introduction,website"
        result: dict[str, dict[str, Any]] = {}
        for exch in ("SSE", "SZSE", "BSE"):
            df = self._call_paginated("stock_company", page_size=5000, exchange=exch, fields=fields)
            for _, row in df.iterrows():
                code = str(row.get("ts_code") or "").strip()
                if code:
                    result[code] = {
                        "ts_code": code,
                        "exchange": exch,
                        "main_business": None if pd.isna(row.get("main_business")) else str(row.get("main_business")),
                        "introduction": None if pd.isna(row.get("introduction")) else str(row.get("introduction")),
                        "website": None if pd.isna(row.get("website")) else str(row.get("website")),
                    }
        return result

    def fetch_open_auction_if_permitted(self, trade_date: str) -> dict[str, dict[str, Any]] | None:
        """If the Tushare account has `stk_auction_o` permission, fetch official opening auction
        volume and amount for all stocks on `trade_date`. Returns None if permission denied.
        """
        d = normalize_date_yyyymmdd(trade_date)
        try:
            df = self._call_paginated("stk_auction_o", page_size=5000, trade_date=d)
        except TushareError as e:
            if e.category == "permission_denied":
                return None
            return None
        if len(df) == 0:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for _, r in df.iterrows():
            code = str(r.get("ts_code") or "").strip()
            if not code:
                continue
            # Tushare stk_auction_o: vol is in shares (verify per doc_id=353), amount in yuan
            out[code] = {
                "vol_shares": _to_dec(r.get("vol")),
                "amount_yuan": _to_dec(r.get("amount")),
                "price": _to_dec(r.get("close")) or _to_dec(r.get("open")),
            }
        return out


def build_daily_records(
    trade_date: str,
    daily_df: pd.DataFrame,
    daily_basic_df: pd.DataFrame,
    suspended_codes: set[str],
) -> dict[str, DailyRecord]:
    d = normalize_date_yyyymmdd(trade_date)
    d_map: dict[str, dict[str, Any]] = {}
    if len(daily_df) > 0:
        for _, r in daily_df.iterrows():
            c = str(r.get("ts_code") or "").strip()
            if c:
                d_map[c] = r.to_dict()

    db_map: dict[str, dict[str, Any]] = {}
    if len(daily_basic_df) > 0:
        for _, r in daily_basic_df.iterrows():
            c = str(r.get("ts_code") or "").strip()
            if c:
                db_map[c] = r.to_dict()

    all_codes = set(d_map.keys()) | set(db_map.keys()) | set(suspended_codes)
    records: dict[str, DailyRecord] = {}
    for code in all_codes:
        dr = d_map.get(code, {})
        dbr = db_map.get(code, {})
        vol_lots = _to_dec(dr.get("vol"))
        is_susp = (code in suspended_codes) and (vol_lots is None or vol_lots <= 0)
        if not dr and not dbr and not is_susp:
            continue
        records[code] = DailyRecord(
            ts_code=code,
            trade_date=d,
            open_price=_to_dec(dr.get("open")),
            close_price=_to_dec(dr.get("close")) or _to_dec(dbr.get("close")),
            pre_close=_to_dec(dr.get("pre_close")),
            pct_chg=_to_dec(dr.get("pct_chg")),
            vol_lots=vol_lots,
            amount_k=_to_dec(dr.get("amount")),
            float_share_w=_to_dec(dbr.get("float_share")),
            free_share_w=_to_dec(dbr.get("free_share")),
            turnover_rate=_to_dec(dbr.get("turnover_rate")),
            turnover_rate_f=_to_dec(dbr.get("turnover_rate_f")),
            circ_mv_w=_to_dec(dbr.get("circ_mv")),
            is_suspended_confirmed=is_susp,
        )
    return records
