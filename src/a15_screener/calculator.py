"""Exact share/yuan metric computation, dual-caliber union pre-screening,
threshold evaluation (full precision >= 10%), and strict per-caliber reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from .pool import BOARD_ORDER, PoolStock

THRESHOLD_PCT = Decimal("10")
THRESHOLD_RATIO = Decimal("0.10")
TEN_THOUSAND = Decimal("10000")
HUNDRED_MILLION = Decimal("100000000")
HUNDRED = Decimal("100")
THOUSAND = Decimal("1000")


def _to_dec(val: Any) -> Decimal | None:
    if val is None:
        return None
    if isinstance(val, Decimal):
        return None if val.is_nan() or val.is_infinite() else val
    try:
        s = str(val).strip()
        if not s or s.lower() in {"nan", "none", "null", "inf", "-inf"}:
            return None
        d = Decimal(s)
        return None if d.is_nan() or d.is_infinite() else d
    except (InvalidOperation, ValueError):
        return None


@dataclass
class DailyRecord:
    ts_code: str
    trade_date: str
    open_price: Decimal | None
    close_price: Decimal | None
    pre_close: Decimal | None
    pct_chg: Decimal | None
    vol_lots: Decimal | None  # Tushare daily.vol in lots (1 lot = 100 shares)
    amount_k: Decimal | None  # Tushare daily.amount in thousand yuan
    float_share_w: Decimal | None  # daily_basic.float_share in 10k shares
    free_share_w: Decimal | None  # daily_basic.free_share in 10k shares
    turnover_rate: Decimal | None  # %
    turnover_rate_f: Decimal | None  # %
    circ_mv_w: Decimal | None  # daily_basic.circ_mv in 10k yuan
    is_suspended_confirmed: bool = False


@dataclass
class MinuteWindowResult:
    """Result of resolving the 09:30–09:45 continuous-auction window for a stock."""

    ts_code: str
    trade_date: str
    # Status:
    # - verified_continuous : continuous auction 09:30-09:45 verified (auction excluded or separated)
    # - verified_zero_volume: confirmed zero trades during 09:30-09:45 (close_0945 must be None)
    # - raw_first_bar_unverified_auction: raw 09:45 bar available, but auction deduction unavailable
    # - missing_data        : source missing/error/unsupported
    status: str
    source: str
    bar_time: str | None = None  # e.g. "20261123094500000" or "09:31-09:45"
    raw_volume_shares: Decimal | None = None
    raw_amount_yuan: Decimal | None = None
    auction_volume_shares: Decimal | None = None
    auction_amount_yuan: Decimal | None = None
    auction_source: str | None = None
    continuous_volume_shares: Decimal | None = None
    continuous_amount_yuan: Decimal | None = None
    price_0945: Decimal | None = None  # Last traded price in 09:30-09:45; None if zero trades
    auction_handling: str = ""
    notes: list[str] = field(default_factory=list)


def prescreen_stock(stock: PoolStock, rec: DailyRecord | None) -> dict[str, Any]:
    """Evaluate day-level pre-screening for both normal and free-float calibers.
    Returns per-caliber disposition: 'suspended' | 'excluded' | 'candidate' | 'unverified_missing_denom'
    and whether the stock enters the union minute-fetch candidate pool.
    """
    if rec is None:
        return {
            "ts_code": stock.ts_code,
            "normal_stage": "suspended",
            "free_stage": "suspended",
            "needs_minute_fetch": False,
            "reason": "no_daily_record_suspended",
        }

    v_day = (rec.vol_lots * HUNDRED) if rec.vol_lots is not None else None
    if rec.is_suspended_confirmed or v_day is None or v_day <= 0:
        return {
            "ts_code": stock.ts_code,
            "normal_stage": "suspended",
            "free_stage": "suspended",
            "needs_minute_fetch": False,
            "reason": "suspended_or_zero_daily_volume",
        }

    f_shares = (rec.float_share_w * TEN_THOUSAND) if (rec.float_share_w is not None and rec.float_share_w > 0) else None
    ff_shares = (rec.free_share_w * TEN_THOUSAND) if (rec.free_share_w is not None and rec.free_share_w > 0) else None

    if f_shares is None:
        normal_stage = "unverified_missing_denom"
    elif v_day >= THRESHOLD_RATIO * f_shares:
        normal_stage = "candidate"
    else:
        normal_stage = "excluded"

    if ff_shares is None:
        free_stage = "unverified_missing_denom"
    elif v_day >= THRESHOLD_RATIO * ff_shares:
        free_stage = "candidate"
    else:
        free_stage = "excluded"

    # Union of both calibers enters minute-fetch pool
    needs_minute = (normal_stage == "candidate") or (free_stage == "candidate")
    return {
        "ts_code": stock.ts_code,
        "normal_stage": normal_stage,
        "free_stage": free_stage,
        "needs_minute_fetch": needs_minute,
        "v_day_shares": v_day,
        "f_shares": f_shares,
        "ff_shares": ff_shares,
    }


def compute_stock_metrics(
    stock: PoolStock,
    rec: DailyRecord,
    minute: MinuteWindowResult,
    use_raw_unverified: bool = False,
) -> dict[str, Any]:
    """Compute exact full-precision metrics in shares & yuan.
    Uses first 15m bar total volume (09:30-09:45 including 09:25 opening call auction,
    matching mainstream A-share trading terminals). Falls back to continuous_volume_shares
    only if raw_volume_shares is absent.
    Selection uses exact Decimal >= 10 (no rounding before threshold test).
    """
    v_day = rec.vol_lots * HUNDRED if rec.vol_lots is not None else None
    a_day = rec.amount_k * THOUSAND if rec.amount_k is not None else None
    close_p = rec.close_price

    f_shares = (rec.float_share_w * TEN_THOUSAND) if (rec.float_share_w is not None and rec.float_share_w > 0) else None
    ff_shares = (rec.free_share_w * TEN_THOUSAND) if (rec.free_share_w is not None and rec.free_share_w > 0) else None

    v15 = minute.raw_volume_shares if minute.raw_volume_shares is not None else minute.continuous_volume_shares
    a15 = minute.raw_amount_yuan if minute.raw_amount_yuan is not None else minute.continuous_amount_yuan

    warnings: list[str] = list(minute.notes)

    # Check V15 <= Vday consistency
    if v15 is not None and v_day is not None and v15 > v_day:
        warnings.append(f"v15_exceeds_vday: v15={v15} > vday={v_day}")

    # Turnover rates (exact Decimal)
    tr15_norm = (HUNDRED * v15 / f_shares) if (v15 is not None and f_shares is not None) else None
    tr15_free = (HUNDRED * v15 / ff_shares) if (v15 is not None and ff_shares is not None) else None

    # Day turnover verification
    trday_norm_calc = (HUNDRED * v_day / f_shares) if (v_day is not None and f_shares is not None) else None
    trday_free_calc = (HUNDRED * v_day / ff_shares) if (v_day is not None and ff_shares is not None) else None

    trday_norm = rec.turnover_rate if rec.turnover_rate is not None else trday_norm_calc
    trday_free = rec.turnover_rate_f if rec.turnover_rate_f is not None else trday_free_calc

    # Verify reported day turnover vs Vday / F (allow source rounding tolerance 0.05 pct pt)
    if rec.turnover_rate is not None and trday_norm_calc is not None:
        diff_n = abs(rec.turnover_rate - trday_norm_calc)
        if diff_n > Decimal("0.05"):
            warnings.append(f"day_turnover_norm_diff:{float(diff_n):.4f}")
    if rec.turnover_rate_f is not None and trday_free_calc is not None:
        diff_f = abs(rec.turnover_rate_f - trday_free_calc)
        if diff_f > Decimal("0.05"):
            warnings.append(f"day_turnover_free_diff:{float(diff_f):.4f}")

    # Shares in 亿股 and 万股
    float_share_yi = (f_shares / HUNDRED_MILLION) if f_shares is not None else None
    free_share_yi = (ff_shares / HUNDRED_MILLION) if ff_shares is not None else None

    # Market caps in 亿元
    # circ_mv is in 万元 -> / 10,000 = 亿元
    circ_mv_yi = (rec.circ_mv_w / TEN_THOUSAND) if rec.circ_mv_w is not None else (
        (close_p * f_shares / HUNDRED_MILLION) if (close_p is not None and f_shares is not None) else None
    )
    # Free-float market cap = P * FF / 1e8 (calculated by close price)
    free_mv_yi = (close_p * ff_shares / HUNDRED_MILLION) if (close_p is not None and ff_shares is not None) else None

    # Concentration: 100 * V15 / Vday
    vol_ratio_15m_to_day = (HUNDRED * v15 / v_day) if (v15 is not None and v_day is not None and v_day > 0) else None

    # 09:45 to close pct change: 100 * (P / P_0945 - 1)
    price_0945 = minute.price_0945 if (v15 is not None and v15 > 0) else None
    pct_0945_to_close = (
        HUNDRED * (close_p / price_0945 - Decimal("1"))
        if (close_p is not None and price_0945 is not None and price_0945 > 0)
        else None
    )

    # Full-precision threshold checks (>= 10%)
    selected_normal = (tr15_norm is not None) and (tr15_norm >= THRESHOLD_PCT)
    selected_free = (tr15_free is not None) and (tr15_free >= THRESHOLD_PCT)

    def _flt(d: Decimal | None) -> float | None:
        return float(d) if d is not None else None

    def _str_exact(d: Decimal | None) -> str | None:
        return format(d, "f") if d is not None else None

    return {
        "ts_code": stock.ts_code,
        "symbol": stock.symbol,
        "name": stock.name,
        "exchange": stock.exchange,
        "board": stock.board,
        "industry": stock.industry,
        "industry_source": "Tushare stock_basic.industry",
        "is_st": stock.is_st,
        "is_first_day": stock.is_first_day,
        "list_date": stock.list_date,
        "old_codes": list(stock.old_codes),
        # Prices (yuan)
        "open_price": _flt(rec.open_price),
        "price_0945": _flt(price_0945),
        "close_price": _flt(close_p),
        "pre_close": _flt(rec.pre_close),
        "pct_chg": _flt(rec.pct_chg),
        "pct_0945_to_close": _flt(pct_0945_to_close),
        # Turnover rates (%) - full precision float + exact string for audit
        "turnover_15m_normal": _flt(tr15_norm),
        "turnover_15m_normal_exact": _str_exact(tr15_norm),
        "turnover_15m_free": _flt(tr15_free),
        "turnover_15m_free_exact": _str_exact(tr15_free),
        "turnover_day_normal": _flt(trday_norm),
        "turnover_day_normal_calc": _flt(trday_norm_calc),
        "turnover_day_free": _flt(trday_free),
        "turnover_day_free_calc": _flt(trday_free_calc),
        # Volumes & Amounts (shares, yuan)
        "v15_shares": int(v15) if v15 is not None else None,
        "a15_yuan": _flt(a15),
        "v15_raw_shares": int(minute.raw_volume_shares) if minute.raw_volume_shares is not None else None,
        "a15_raw_yuan": _flt(minute.raw_amount_yuan),
        "auction_vol_shares": int(minute.auction_volume_shares) if minute.auction_volume_shares is not None else None,
        "auction_amt_yuan": _flt(minute.auction_amount_yuan),
        "vday_shares": int(v_day) if v_day is not None else None,
        "aday_yuan": _flt(a_day),
        "vol_ratio_15m_to_day": _flt(vol_ratio_15m_to_day),
        # Shares & Market Cap
        "float_share_wan": _flt(rec.float_share_w),
        "float_share_yi": _flt(float_share_yi),
        "free_share_wan": _flt(rec.free_share_w),
        "free_share_yi": _flt(free_share_yi),
        "circ_mv_yi": _flt(circ_mv_yi),
        "free_mv_yi": _flt(free_mv_yi),
        "free_mv_note": "按当日收盘价计算",
        # Selection flags
        "selected_normal": selected_normal,
        "selected_free": selected_free,
        # Lineage
        "minute_status": minute.status,
        "minute_source": minute.source,
        "minute_bar_time": minute.bar_time,
        "auction_source": minute.auction_source,
        "auction_handling": minute.auction_handling,
        "warnings": warnings,
    }


def evaluate_universe(
    pool: list[PoolStock],
    daily_map: dict[str, DailyRecord],
    minute_map: dict[str, MinuteWindowResult],
    summary_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run complete dual-caliber screening and exact funnel reconciliation across the stock pool.
    Separates verified continuous-auction main lists from unverified raw-first-bar lists.
    """
    summary_map = summary_map or {}

    def _empty_funnel() -> dict[str, int]:
        return {
            "pool_total": 0,
            "suspended": 0,
            "prescreen_excluded": 0,
            "verified_not_selected": 0,
            "selected": 0,
            "unverified": 0,
        }

    funnels = {
        "normal": _empty_funnel(),
        "free": _empty_funnel(),
    }
    board_funnels: dict[str, dict[str, dict[str, int]]] = {
        b: {"normal": _empty_funnel(), "free": _empty_funnel()} for b in BOARD_ORDER
    }

    main_normal: list[dict[str, Any]] = []
    main_free: list[dict[str, Any]] = []
    raw_unverified_normal: list[dict[str, Any]] = []
    raw_unverified_free: list[dict[str, Any]] = []
    audit_samples_not_selected: list[dict[str, Any]] = []

    def _inc(caliber: str, board: str, key: str) -> None:
        funnels[caliber][key] += 1
        board_funnels.setdefault(board, {"normal": _empty_funnel(), "free": _empty_funnel()})[caliber][key] += 1

    for stock in pool:
        board = stock.board
        _inc("normal", board, "pool_total")
        _inc("free", board, "pool_total")

        rec = daily_map.get(stock.ts_code)
        pre = prescreen_stock(stock, rec)
        n_stage = pre["normal_stage"]
        f_stage = pre["free_stage"]

        # Handle non-candidate dispositions first
        if n_stage == "suspended":
            _inc("normal", board, "suspended")
        elif n_stage == "excluded":
            _inc("normal", board, "prescreen_excluded")
        elif n_stage == "unverified_missing_denom":
            _inc("normal", board, "unverified")

        if f_stage == "suspended":
            _inc("free", board, "suspended")
        elif f_stage == "excluded":
            _inc("free", board, "prescreen_excluded")
        elif f_stage == "unverified_missing_denom":
            _inc("free", board, "unverified")

        if not pre["needs_minute_fetch"]:
            continue

        assert rec is not None
        minute = minute_map.get(stock.ts_code)
        if minute is None or minute.status == "missing_data":
            if n_stage == "candidate":
                _inc("normal", board, "unverified")
            if f_stage == "candidate":
                _inc("free", board, "unverified")
            continue

        comp_info = summary_map.get(
            stock.ts_code,
            {
                "summary": "待补充",
                "source": "缺失",
                "source_date": None,
                "is_latest_fallback": False,
                "raw_excerpt": "",
                "website": "",
            },
        )

        if minute.status in ("verified_continuous", "verified_zero_volume", "raw_first_bar_unverified_auction"):
            row = compute_stock_metrics(stock, rec, minute, use_raw_unverified=False)
            row["business_summary"] = comp_info["summary"]
            row["business_source"] = comp_info["source"]
            row["business_source_date"] = comp_info.get("source_date")
            row["business_is_latest_fallback"] = comp_info.get("is_latest_fallback", False)
            row["business_raw_excerpt"] = comp_info.get("raw_excerpt", "")
            row["website"] = comp_info.get("website", "")

            if n_stage == "candidate":
                if row["selected_normal"]:
                    _inc("normal", board, "selected")
                    main_normal.append(row)
                else:
                    _inc("normal", board, "verified_not_selected")
            if f_stage == "candidate":
                if row["selected_free"]:
                    _inc("free", board, "selected")
                    main_free.append(row)
                else:
                    _inc("free", board, "verified_not_selected")

            if (not row["selected_normal"]) and (not row["selected_free"]):
                audit_samples_not_selected.append(row)

    # Sort descending by full-precision exact turnover rate, tie-break by ts_code
    main_normal.sort(
        key=lambda r: (Decimal(r["turnover_15m_normal_exact"] or "0"), r["ts_code"]),
        reverse=True,
    )
    main_free.sort(
        key=lambda r: (Decimal(r["turnover_15m_free_exact"] or "0"), r["ts_code"]),
        reverse=True,
    )
    raw_unverified_normal.sort(
        key=lambda r: (Decimal(r["turnover_15m_normal_exact"] or "0"), r["ts_code"]),
        reverse=True,
    )
    raw_unverified_free.sort(
        key=lambda r: (Decimal(r["turnover_15m_free_exact"] or "0"), r["ts_code"]),
        reverse=True,
    )
    audit_samples_not_selected.sort(
        key=lambda r: (Decimal(r["turnover_15m_free_exact"] or r["turnover_15m_normal_exact"] or "0")),
        reverse=True,
    )

    # Strict reconciliation invariant check
    for cal in ("normal", "free"):
        f = funnels[cal]
        accounted = (
            f["suspended"]
            + f["prescreen_excluded"]
            + f["verified_not_selected"]
            + f["selected"]
            + f["unverified"]
        )
        assert accounted == f["pool_total"], f"Reconciliation mismatch for {cal}: {accounted} != {f['pool_total']}"

    return {
        "funnels": funnels,
        "board_funnels": board_funnels,
        "main_lists": {
            "normal": main_normal,
            "free": main_free,
        },
        "raw_unverified_lists": {
            "normal": raw_unverified_normal,
            "free": raw_unverified_free,
        },
        "audit_not_selected_top": audit_samples_not_selected[:10],
    }
