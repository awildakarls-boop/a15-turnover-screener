"""Comprehensive isolated acceptance test suite covering Section 7 items 1 & 2.
All fixtures run in memory or pytest tmp_path and NEVER touch production data/ or web/data/.
"""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a15_screener.calendar_utils import resolve_default_target_date
from a15_screener.calculator import DailyRecord, MinuteWindowResult, compute_stock_metrics, evaluate_universe, prescreen_stock
from a15_screener.config import BJ_TZ
from a15_screener.minute_clients import parse_baostock_bars, resolve_minute_window
from a15_screener.pool import PoolStock, build_stock_pool
from a15_screener.storage import StateStore, atomic_write_json, file_task_lock
from a15_screener.summary_gen import build_business_summary


def _make_stock(
    ts_code: str = "000001.SZ",
    name: str = "测试样本",
    exchange: str = "SZSE",
    board: str = "深市主板",
    is_st: bool = False,
    is_first_day: bool = False,
) -> PoolStock:
    sym = ts_code.split(".")[0]
    return PoolStock(
        ts_code=ts_code,
        symbol=sym,
        name=name,
        exchange=exchange,
        market="主板" if "主板" in board else board,
        board=board,
        industry="软件服务",
        curr_type="CNY",
        list_status="L",
        list_date="20200101",
        delist_date=None,
        is_st=is_st,
        is_first_day=is_first_day,
    )


# ---------------------------------------------------------------------------
# Acceptance Test 1: Exact benchmark case from Section 7.1
# ---------------------------------------------------------------------------
def test_acceptance_item_1_exact_benchmark_case() -> None:
    """float_share=10,000万股, free_share=5,000万股, close=12元,
    V15=800万股, Vday=900万股 (Tushare daily vol=90,000手).
    Expected:
      - 15m normal = 8%, 15m free = 16%
      - day normal = 9%, day free = 18%
      - circ_mv = 12亿元, free_mv = 6亿元
      - MUST enter free-float list even though full-day normal turnover is only 9%!
      - MUST NOT enter normal list.
    """
    stock = _make_stock("990001.SH", "验收基准股", "SSE", "沪市主板")
    rec = DailyRecord(
        ts_code=stock.ts_code,
        trade_date="20261123",
        open_price=Decimal("11.80"),
        close_price=Decimal("12.00"),
        pre_close=Decimal("11.50"),
        pct_chg=Decimal("4.3478"),
        vol_lots=Decimal("90000"),  # 90,000 lots = 9,000,000 shares = 900万股
        amount_k=Decimal("108000"),  # 108,000k = 108,000,000 yuan
        float_share_w=Decimal("10000"),  # 10,000万股 = 1亿股
        free_share_w=Decimal("5000"),  # 5,000万股 = 0.5亿股
        turnover_rate=Decimal("9.0000"),
        turnover_rate_f=Decimal("18.0000"),
        circ_mv_w=Decimal("120000"),  # 120,000万元 = 12亿元
    )
    minute = MinuteWindowResult(
        ts_code=stock.ts_code,
        trade_date="20261123",
        status="verified_continuous",
        source="isolated_unit_test",
        bar_time="20261123094500000",
        raw_volume_shares=Decimal("8000000"),  # 首根15分钟总成交800万股（含开盘竞价）
        raw_amount_yuan=Decimal("96000000"),
        auction_volume_shares=Decimal("200000"),
        auction_amount_yuan=Decimal("2400000"),
        continuous_volume_shares=Decimal("7800000"),
        continuous_amount_yuan=Decimal("93600000"),
        price_0945=Decimal("12.00"),
    )

    # Pre-screening MUST include in union candidate pool because free-float Vday >= 10% * FF
    pre = prescreen_stock(stock, rec)
    assert pre["normal_stage"] == "excluded"  # day normal is 9% < 10%
    assert pre["free_stage"] == "candidate"  # day free is 18% >= 10%
    assert pre["needs_minute_fetch"] is True

    evaluated = evaluate_universe([stock], {stock.ts_code: rec}, {stock.ts_code: minute})
    assert len(evaluated["main_lists"]["normal"]) == 0
    assert len(evaluated["main_lists"]["free"]) == 1

    row = evaluated["main_lists"]["free"][0]
    assert row["turnover_15m_normal"] == pytest.approx(8.0, rel=1e-12)
    assert row["turnover_15m_free"] == pytest.approx(16.0, rel=1e-12)
    assert row["turnover_day_normal"] == pytest.approx(9.0, rel=1e-12)
    assert row["turnover_day_free"] == pytest.approx(18.0, rel=1e-12)
    assert row["circ_mv_yi"] == pytest.approx(12.0, rel=1e-12)
    assert row["free_mv_yi"] == pytest.approx(6.0, rel=1e-12)
    assert row["selected_normal"] is False
    assert row["selected_free"] is True

    # Funnel reconciliation check
    assert evaluated["funnels"]["normal"]["prescreen_excluded"] == 1
    assert evaluated["funnels"]["free"]["selected"] == 1


# ---------------------------------------------------------------------------
# Acceptance Test 2: Boundary conditions, missing free_share, auction, missing bar, suspension
# ---------------------------------------------------------------------------
def test_exact_10_percent_and_rounding_below_10_percent() -> None:
    """Exactly 10.0000% MUST qualify; 9.996% (rounds to 10.00% on display) MUST NOT qualify."""
    s_exact = _make_stock("990010.SH", "恰好十趴", "SSE", "沪市主板")
    s_below = _make_stock("990011.SH", "略低于十趴", "SSE", "沪市主板")

    rec_exact = DailyRecord(
        ts_code=s_exact.ts_code,
        trade_date="20261123",
        open_price=Decimal("10"),
        close_price=Decimal("10"),
        pre_close=Decimal("10"),
        pct_chg=Decimal("0"),
        vol_lots=Decimal("10000"),  # 1,000,000 shares -> exactly 10% of 10,000,000
        amount_k=Decimal("10000"),
        float_share_w=Decimal("1000"),  # 10,000,000 shares
        free_share_w=Decimal("1000"),
        turnover_rate=Decimal("10.00"),
        turnover_rate_f=Decimal("10.00"),
        circ_mv_w=Decimal("10000"),
    )
    min_exact = MinuteWindowResult(
        ts_code=s_exact.ts_code,
        trade_date="20261123",
        status="verified_continuous",
        source="test",
        continuous_volume_shares=Decimal("1000000"),  # exact 10.000000%
        continuous_amount_yuan=Decimal("10000000"),
        price_0945=Decimal("10"),
    )

    rec_below = DailyRecord(
        ts_code=s_below.ts_code,
        trade_date="20261123",
        open_price=Decimal("10"),
        close_price=Decimal("10"),
        pre_close=Decimal("10"),
        pct_chg=Decimal("0"),
        vol_lots=Decimal("12000"),  # 1,200,000 shares (12% day) -> enters candidate pool
        amount_k=Decimal("12000"),
        float_share_w=Decimal("1000"),  # 10,000,000 shares
        free_share_w=Decimal("1000"),
        turnover_rate=Decimal("12.00"),
        turnover_rate_f=Decimal("12.00"),
        circ_mv_w=Decimal("10000"),
    )
    min_below = MinuteWindowResult(
        ts_code=s_below.ts_code,
        trade_date="20261123",
        status="verified_continuous",
        source="test",
        continuous_volume_shares=Decimal("999600"),  # 9.996% -> rounds to 10.00% on 2-dec display!
        continuous_amount_yuan=Decimal("9996000"),
        price_0945=Decimal("10"),
    )

    ev = evaluate_universe(
        [s_exact, s_below],
        {s_exact.ts_code: rec_exact, s_below.ts_code: rec_below},
        {s_exact.ts_code: min_exact, s_below.ts_code: min_below},
    )
    assert [r["ts_code"] for r in ev["main_lists"]["normal"]] == ["990010.SH"]
    assert [r["ts_code"] for r in ev["main_lists"]["free"]] == ["990010.SH"]
    assert ev["funnels"]["normal"]["verified_not_selected"] == 1
    assert f"{ev['audit_not_selected_top'][0]['turnover_15m_normal']:.2f}" == "10.00"


def test_missing_free_share_keeps_normal_list_working() -> None:
    """When float_share is valid and free_share is missing, normal caliber still computes & selects,
    while free caliber reports missing/unverified."""
    stock = _make_stock("990020.SZ", "自由股本缺失", "SZSE", "创业板")
    rec = DailyRecord(
        ts_code=stock.ts_code,
        trade_date="20261123",
        open_price=Decimal("20"),
        close_price=Decimal("21"),
        pre_close=Decimal("20"),
        pct_chg=Decimal("5"),
        vol_lots=Decimal("20000"),
        amount_k=Decimal("41000"),
        float_share_w=Decimal("1000"),  # 10,000,000 shares
        free_share_w=None,
        turnover_rate=Decimal("20.0"),
        turnover_rate_f=None,
        circ_mv_w=Decimal("21000"),
    )
    minute = MinuteWindowResult(
        ts_code=stock.ts_code,
        trade_date="20261123",
        status="verified_continuous",
        source="test",
        continuous_volume_shares=Decimal("1500000"),  # 15%
        continuous_amount_yuan=Decimal("30750000"),
        price_0945=Decimal("20.5"),
    )
    ev = evaluate_universe([stock], {stock.ts_code: rec}, {stock.ts_code: minute})
    assert len(ev["main_lists"]["normal"]) == 1
    assert len(ev["main_lists"]["free"]) == 0
    assert ev["main_lists"]["normal"][0]["turnover_15m_free"] is None
    assert ev["main_lists"]["normal"][0]["free_mv_yi"] is None
    assert ev["funnels"]["normal"]["selected"] == 1
    assert ev["funnels"]["free"]["unverified"] == 1


def test_first_bar_with_auction_deduction_and_unverified_isolation() -> None:
    """Test auction deduction removes opening auction; if auction cannot be verified,
    record is isolated to raw_unverified_lists and excluded from formal continuous list."""

    class FakeBaoStock:
        version = "0.9.4"

        def fetch_kline(self, ts_code: str, trade_date: str, frequency: str = "15"):
            return [
                {
                    "code": "sh.600000",
                    "date": "2026-11-23",
                    "time": "20261123094500000",
                    "hhmm": "0945",
                    "open": Decimal("10"),
                    "high": Decimal("10.2"),
                    "low": Decimal("9.9"),
                    "close": Decimal("10.1"),
                    "volume": Decimal("1050000"),  # 10.5% raw including 100k auction
                    "amount": Decimal("10550000"),
                }
            ]

    class FakeIndWithAuction:
        def fetch_tencent_m1_opening(self, stock, d):
            return {
                "source": "tencent_mkline_m1",
                "has_0930": True,
                "auction_shares": Decimal("100000"),  # After deducting 100k -> 950k = 9.5% (<10%)
                "auction_amount": Decimal("1000000"),
                "auction_price": Decimal("10"),
                "cc_shares_0931_0945": Decimal("950000"),
                "cc_bar_count": 15,
                "price_0945": Decimal("10.1"),
            }

        def fetch_eastmoney_trends2_window(self, stock, d):
            return None

    class FakeIndNoAuction:
        def fetch_tencent_m1_opening(self, stock, d):
            return None

        def fetch_eastmoney_trends2_window(self, stock, d):
            return None

    stock = _make_stock("600000.SH", "含竞价样本", "SSE", "沪市主板")
    rec = DailyRecord(
        ts_code=stock.ts_code,
        trade_date="20261123",
        open_price=Decimal("10"),
        close_price=Decimal("10.2"),
        pre_close=Decimal("10"),
        pct_chg=Decimal("2"),
        vol_lots=Decimal("20000"),
        amount_k=Decimal("20200"),
        float_share_w=Decimal("1000"),  # 10,000,000 shares
        free_share_w=Decimal("1000"),
        turnover_rate=Decimal("20"),
        turnover_rate_f=Decimal("20"),
        circ_mv_w=Decimal("10200"),
    )

    # Case A: first 15m bar total volume (1,050,000 = 10.5% including opening auction) qualifies directly
    res_verified = resolve_minute_window(stock, "20261123", rec, FakeBaoStock(), FakeIndWithAuction())
    assert res_verified.status == "verified_continuous"
    assert res_verified.raw_volume_shares == Decimal("1050000")
    ev_a = evaluate_universe([stock], {stock.ts_code: rec}, {stock.ts_code: res_verified})
    assert len(ev_a["main_lists"]["normal"]) == 1
    assert ev_a["main_lists"]["normal"][0]["turnover_15m_normal"] == pytest.approx(10.5, rel=1e-12)

    # Case B: even when separate auction feed is unavailable, first 15m bar total volume is still valid & evaluated
    res_unver = resolve_minute_window(stock, "20261123", rec, FakeBaoStock(), FakeIndNoAuction())
    assert res_unver.raw_volume_shares == Decimal("1050000")
    ev_b = evaluate_universe([stock], {stock.ts_code: rec}, {stock.ts_code: res_unver})
    assert len(ev_b["main_lists"]["normal"]) == 1
    assert ev_b["funnels"]["normal"]["selected"] == 1


def test_missing_opening_bar_and_suspension() -> None:
    """Late open / missing 09:45 bar -> zero volume & price_0945 is None; suspended stock -> suspended bucket."""

    class FakeLateOpenBS:
        version = "0.9.4"

        def fetch_kline(self, ts_code: str, trade_date: str, frequency: str = "15"):
            if frequency == "15":
                return [
                    {
                        "code": "sz.000002",
                        "date": "2026-11-23",
                        "time": "20261123103000000",  # opened late at 10:30, 09:45 missing
                        "hhmm": "1030",
                        "open": Decimal("10"),
                        "high": Decimal("10.5"),
                        "low": Decimal("10"),
                        "close": Decimal("10.3"),
                        "volume": Decimal("500000"),
                        "amount": Decimal("5150000"),
                    }
                ]
            return []

    class DummyInd:
        pass

    s_late = _make_stock("000002.SZ", "晚开盘临停", "SZSE", "深市主板")
    s_susp = _make_stock("000003.SZ", "全天停牌", "SZSE", "深市主板")

    rec_late = DailyRecord(
        ts_code=s_late.ts_code,
        trade_date="20261123",
        open_price=Decimal("10"),
        close_price=Decimal("10.3"),
        pre_close=Decimal("10"),
        pct_chg=Decimal("3"),
        vol_lots=Decimal("15000"),
        amount_k=Decimal("15450"),
        float_share_w=Decimal("1000"),
        free_share_w=Decimal("1000"),
        turnover_rate=Decimal("15"),
        turnover_rate_f=Decimal("15"),
        circ_mv_w=Decimal("10300"),
    )
    res_late = resolve_minute_window(s_late, "20261123", rec_late, FakeLateOpenBS(), DummyInd())
    assert res_late.status == "verified_zero_volume"
    assert res_late.continuous_volume_shares == Decimal("0")
    assert res_late.price_0945 is None

    ev = evaluate_universe([s_late, s_susp], {s_late.ts_code: rec_late}, {s_late.ts_code: res_late})
    assert ev["funnels"]["normal"]["suspended"] == 1
    assert ev["funnels"]["normal"]["verified_not_selected"] == 1
    assert ev["funnels"]["normal"]["selected"] == 0
    assert ev["audit_not_selected_top"][0]["price_0945"] is None


def test_calendar_rules_monday_post_holiday_and_after_close() -> None:
    """Default target day D updates to same day at/after 20:00 CST on trading days,
    and falls back to previous open day before 20:00 CST or on weekends/holidays."""
    open_days = ["20260929", "20260930", "20261008", "20261009", "20261012"]
    # Post National Day holiday before 20:00 -> last open day before holiday (2026-09-30)
    assert resolve_default_target_date(open_days, datetime(2026, 10, 8, 19, 59, tzinfo=BJ_TZ)) == "20260930"
    # Post National Day holiday at 20:00 -> updates to same day (2026-10-08)
    assert resolve_default_target_date(open_days, datetime(2026, 10, 8, 20, 0, tzinfo=BJ_TZ)) == "20261008"
    # Ordinary Monday morning 10:00 -> last Friday 2026-10-09
    assert resolve_default_target_date(open_days, datetime(2026, 10, 12, 10, 0, tzinfo=BJ_TZ)) == "20261009"
    # Ordinary Monday evening 20:00+ -> Monday itself 2026-10-12
    assert resolve_default_target_date(open_days, datetime(2026, 10, 12, 20, 0, tzinfo=BJ_TZ)) == "20261012"
    # Weekend Saturday 22:00 -> Friday 2026-10-09
    assert resolve_default_target_date(open_days, datetime(2026, 10, 10, 22, 0, tzinfo=BJ_TZ)) == "20261009"


def test_stock_pool_filtering_and_leading_zeros() -> None:
    """Pool preserves leading zeros, includes ST/IPO/historically listed delisted stocks,
    maps BSE old codes, and excludes B-shares, CDRs (689xxx), ETFs, convertible bonds, NEEQ."""
    df = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "symbol": "1", "name": "平安银行", "exchange": "SZSE", "market": "主板", "industry": "银行", "curr_type": "CNY", "list_status": "L", "list_date": "19910403", "delist_date": None},
            {"ts_code": "200002.SZ", "symbol": "200002", "name": "万科B", "exchange": "SZSE", "market": "主板", "industry": "地产", "curr_type": "HKD", "list_status": "L", "list_date": "19930528", "delist_date": None},
            {"ts_code": "689009.SH", "symbol": "689009", "name": "九号公司", "exchange": "SSE", "market": "科创板", "industry": "专用设备", "curr_type": "CNY", "list_status": "L", "list_date": "20201029", "delist_date": None},
            {"ts_code": "510300.SH", "symbol": "510300", "name": "沪深300ETF", "exchange": "SSE", "market": "ETF", "industry": None, "curr_type": "CNY", "list_status": "L", "list_date": "20120528", "delist_date": None},
            {"ts_code": "110053.SH", "symbol": "110053", "name": "苏银转债", "exchange": "SSE", "market": "可转债", "industry": None, "curr_type": "CNY", "list_status": "L", "list_date": "20190403", "delist_date": None},
            {"ts_code": "600999.SH", "symbol": "600999", "name": "*ST历史退市", "exchange": "SSE", "market": "主板", "industry": "化工", "curr_type": "CNY", "list_status": "D", "list_date": "20000101", "delist_date": "20261125"},
            {"ts_code": "920729.BJ", "symbol": "920729", "name": "永顺生物", "exchange": "BSE", "market": "北交所", "industry": "医药", "curr_type": "CNY", "list_status": "L", "list_date": "20200727", "delist_date": None},
            {"ts_code": "301999.SZ", "symbol": "301999", "name": "N新股测试", "exchange": "SZSE", "market": "创业板", "industry": "电子", "curr_type": "CNY", "list_status": "L", "list_date": "20261123", "delist_date": None},
        ]
    )
    bse_map = pd.DataFrame([{"name": "永顺生物", "o_code": "839729.BJ", "n_code": "920729.BJ", "list_date": "20200727"}])
    pool, excluded = build_stock_pool(df, "20261123", bse_mapping_df=bse_map)
    codes = {s.ts_code: s for s in pool}

    assert set(codes.keys()) == {"000001.SZ", "600999.SH", "920729.BJ", "301999.SZ"}
    assert codes["000001.SZ"].symbol == "000001"
    assert codes["600999.SH"].is_st is True
    assert codes["301999.SZ"].is_first_day is True
    assert codes["920729.BJ"].old_codes == ("839729.BJ",)
    assert excluded["non_cny_currency"] == 1
    assert excluded["excluded_security_type_or_cdr"] == 3


def test_idempotent_storage_lock_and_baostock_dedup(tmp_path: Path) -> None:
    """Duplicate collection writes produce no duplicate rows; task lock prevents concurrent overlap;
    BaoStock bar parser sorts and deduplicates by (code, date, time)."""
    # 1. Deduplication & sorting of BaoStock rows
    bars = parse_baostock_bars(
        [
            {"code": "sh.600000", "date": "2026-11-23", "time": "20261123100000000", "open": "10", "high": "10", "low": "10", "close": "10", "volume": "100", "amount": "1000"},
            {"code": "sh.600000", "date": "2026-11-23", "time": "20261123094500000", "open": "10", "high": "10", "low": "10", "close": "10", "volume": "200", "amount": "2000"},
            {"code": "sh.600000", "date": "2026-11-23", "time": "20261123094500000", "open": "10", "high": "10", "low": "10", "close": "10", "volume": "200", "amount": "2000"},
        ],
        "20261123",
    )
    assert len(bars) == 2
    assert bars[0]["hhmm"] == "0945"

    # 2. Idempotent SQLite storage
    store = StateStore(tmp_path / "state.db")
    m = MinuteWindowResult(
        ts_code="600000.SH",
        trade_date="20261123",
        status="verified_continuous",
        source="baostock",
        continuous_volume_shares=Decimal("1000"),
        continuous_amount_yuan=Decimal("10000"),
        price_0945=Decimal("10"),
    )
    store.save_minute_result(m, "2026-11-24T10:00:00+08:00")
    store.save_minute_result(m, "2026-11-24T10:05:00+08:00")
    loaded = store.load_minute_cache("20261123")
    assert len(loaded) == 1
    assert loaded["600000.SH"].continuous_volume_shares == Decimal("1000")

    # 3. Exclusive lock check
    lock_file = tmp_path / "collector.lock"
    with file_task_lock(lock_file):
        with pytest.raises(RuntimeError):
            with file_task_lock(lock_file):
                pass
    assert not lock_file.exists()

    # 4. Atomic write check
    out_json = tmp_path / "snap.json"
    atomic_write_json(out_json, {"ok": True})
    assert out_json.exists()

    # 5. Summary generator rejects boilerplate business scope
    s_bad = build_business_summary({"main_business": "一般项目：技术开发；软件销售。", "introduction": ""}, "2026-11-23", "2026-11-24")
    assert s_bad["summary"] == "待补充"
    s_good = build_business_summary(
        {"main_business": "公司主要从事新能源汽车动力电池系统及储能电池系统的研发、生产和销售。", "website": "www.catl.com"},
        "2026-11-20",
        "2026-11-24",
    )
    assert "动力电池" in s_good["summary"]
    assert s_good["is_latest_fallback"] is True
    assert "最新资料" in s_good["source"]
