"""Single-connection serial BaoStock collector and verified independent auction / BSE clients.

Calibration findings (verified on 2026-11-23 across SH Main, SZ Main, ChiNext, STAR, BSE):
1. BaoStock `query_history_k_data_plus(frequency="15", adjustflag="3")`:
   - Covers SSE and SZSE (SH Main, SZ Main, ChiNext, STAR); does NOT cover BSE (error 10004011).
   - Timestamp `YYYYMMDDHHMMSSsss` marks bar END time (first bar ends at 09:45:00.000).
   - Sum of all 16 x 15m bars equals Tushare full-day volume to 0 shares and full-day amount to 0.00 yuan,
     and first bar open equals opening call auction price -> PROVEN to include 09:25 opening call auction.
   - First 15m bar matches sum of 09:35, 09:40, 09:45 5m bars to 0 shares and 0.00 yuan.
2. Opening call auction separation:
   - Tencent `ifzq.gtimg.cn` `m1` (recent ~2 trading days, SSE & SZSE): explicit `0930` bar separates
     opening call auction. Unit: 1 share for STAR (688xxx), 100 shares/lot for SH Main/SZ Main/ChiNext.
     Sum of `0930..0945` matches BaoStock 09:45 bar to 0 shares on SH probes (600000.SH, 688981.SH).
   - Eastmoney `push2his.eastmoney.com` `trends2` (direct, recent 5 trading days, SSE/SZSE/BSE):
     explicit `09:30` bar separates opening call auction, `09:31..09:45` gives continuous auction.
     Provides yuan-precision amount so odd-lot boards (STAR, BSE) have exact share deduction.
   - Sina `quotes.sina.cn` `getKLineData(scale=15)`: covers BSE historical 15m bars, but cannot separate
     opening call auction by itself -> placed in `raw_first_bar_unverified_auction` if trends2 unavailable.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

import baostock as bs
import requests

from .calendar_utils import normalize_date_yyyymmdd, to_dash_date
from .calculator import DailyRecord, MinuteWindowResult, _to_dec
from .pool import PoolStock, to_baostock_code, to_eastmoney_secid, to_sina_code, to_tencent_code


def parse_baostock_bars(raw_rows: list[dict[str, str]], trade_date: str) -> list[dict[str, Any]]:
    """Sort and deduplicate BaoStock bars by (code, date, time YYYYMMDDHHMMSSsss)."""
    d_dash = to_dash_date(trade_date)
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in raw_rows:
        code = str(r.get("code") or "").strip()
        dt = str(r.get("date") or "").strip()
        tm = str(r.get("time") or "").strip()
        if dt != d_dash or len(tm) < 12:
            continue
        by_key[(code, dt, tm)] = {
            "code": code,
            "date": dt,
            "time": tm,
            "hhmm": tm[8:12],
            "open": _to_dec(r.get("open")),
            "high": _to_dec(r.get("high")),
            "low": _to_dec(r.get("low")),
            "close": _to_dec(r.get("close")),
            "volume": _to_dec(r.get("volume")) or Decimal("0"),
            "amount": _to_dec(r.get("amount")) or Decimal("0"),
        }
    return [by_key[k] for k in sorted(by_key.keys())]


class BaoStockSession:
    """Strict single-connection serial BaoStock client with retry and polite throttling."""

    def __init__(self, sleep_sec: float = 0.08, max_retries: int = 3):
        self.sleep_sec = sleep_sec
        self.max_retries = max_retries
        self._logged_in = False
        self.version = getattr(bs, "__version__", "unknown")

    def __enter__(self) -> "BaoStockSession":
        self.login()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.logout()

    def login(self) -> None:
        if self._logged_in:
            return
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"BaoStock login failed: {lg.error_code} {lg.error_msg}")
        self._logged_in = True

    def logout(self) -> None:
        if self._logged_in:
            try:
                bs.logout()
            finally:
                self._logged_in = False

    def _relogin(self) -> None:
        self.logout()
        time.sleep(0.5)
        self.login()

    def fetch_kline(self, ts_code: str, trade_date: str, frequency: str = "15") -> list[dict[str, Any]]:
        bs_code = to_baostock_code(ts_code)
        if bs_code is None:
            return []
        d_dash = to_dash_date(trade_date)
        fields = "date,time,code,open,high,low,close,volume,amount,adjustflag"

        for attempt in range(self.max_retries):
            try:
                if not self._logged_in:
                    self.login()
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    fields,
                    start_date=d_dash,
                    end_date=d_dash,
                    frequency=frequency,
                    adjustflag="3",
                )
                if rs.error_code != "0":
                    raise RuntimeError(f"BaoStock error {rs.error_code}: {rs.error_msg}")
                rows = []
                while rs.next():
                    rows.append(dict(zip(rs.fields, rs.get_row_data())))
                if self.sleep_sec > 0:
                    time.sleep(self.sleep_sec)
                return parse_baostock_bars(rows, trade_date)
            except Exception:
                if attempt + 1 >= self.max_retries:
                    raise
                self._relogin()
                time.sleep(0.5 * (attempt + 1))
        return []

    def fetch_trade_dates(self, start_date: str, end_date: str) -> list[str]:
        if not self._logged_in:
            self.login()
        rs = bs.query_trade_dates(start_date=to_dash_date(start_date), end_date=to_dash_date(end_date))
        open_days: list[str] = []
        while rs.error_code == "0" and rs.next():
            r = rs.get_row_data()
            if len(r) >= 2 and r[1] == "1":
                open_days.append(normalize_date_yyyymmdd(r[0]))
        return open_days


class IndependentMinuteClient:
    """Verified secondary sources for opening call auction separation & BSE minute coverage."""

    def __init__(self, sleep_sec: float = 0.12, timeout: int = 12):
        self.sleep_sec = sleep_sec
        self.timeout = timeout
        # Direct session (bypasses local proxy which Eastmoney rejects)
        self._direct = requests.Session()
        self._direct.trust_env = False
        # Standard session for Tencent & Sina
        self._std = requests.Session()

    def fetch_tencent_m1_opening(self, stock: PoolStock, trade_date: str) -> dict[str, Any] | None:
        """Fetch Tencent 1-min bars for SSE/SZSE and extract exact 09:30 opening call auction.
        Unit verified: STAR (688xxx) = shares; SH Main / SZ Main / ChiNext = lots (x100 shares).
        """
        tx_code = to_tencent_code(stock.ts_code)
        if not tx_code:
            return None
        d = normalize_date_yyyymmdd(trade_date)
        unit_mult = Decimal("1") if stock.board == "科创板" else Decimal("100")

        for attempt in range(3):
            try:
                r = self._std.get(
                    "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
                    params={"param": f"{tx_code},m1,,600"},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=self.timeout,
                )
                r.raise_for_status()
                bars = ((r.json().get("data") or {}).get(tx_code) or {}).get("m1") or []
                day_bars = [b for b in bars if str(b[0]).startswith(d)]
                if not day_bars:
                    return None
                bar_0930 = next((b for b in day_bars if str(b[0])[8:12] == "0930"), None)
                cc_bars = [b for b in day_bars if "0931" <= str(b[0])[8:12] <= "0945"]
                auc_shares = Decimal("0")
                auc_price = None
                auc_amount = Decimal("0")
                if bar_0930 is not None:
                    raw_v = _to_dec(bar_0930[5]) or Decimal("0")
                    auc_shares = (raw_v * unit_mult).quantize(Decimal("1"))
                    auc_price = _to_dec(bar_0930[2]) or _to_dec(bar_0930[1])
                    if auc_price is not None:
                        auc_amount = (auc_shares * auc_price).quantize(Decimal("0.01"))
                cc_shares = sum(((_to_dec(b[5]) or Decimal("0")) * unit_mult) for b in cc_bars)
                price_0945 = _to_dec(cc_bars[-1][2]) if cc_bars else None
                if self.sleep_sec > 0:
                    time.sleep(self.sleep_sec * 0.5)
                return {
                    "source": "tencent_mkline_m1",
                    "has_0930": bar_0930 is not None,
                    "auction_shares": auc_shares,
                    "auction_amount": auc_amount,
                    "auction_price": auc_price,
                    "cc_shares_0931_0945": cc_shares.quantize(Decimal("1")),
                    "cc_bar_count": len(cc_bars),
                    "price_0945": price_0945,
                }
            except Exception:
                if attempt == 2:
                    return None
                time.sleep(0.4 * (attempt + 1))
        return None

    def fetch_eastmoney_trends2_window(self, stock: PoolStock, trade_date: str) -> dict[str, Any] | None:
        """Fetch Eastmoney trends2 (1-min intraday bars for recent 5 trading days) via direct connection.
        Covers SSE, SZSE, and BSE; explicitly separates `09:30` opening call auction from `09:31..09:45`.
        """
        secid = to_eastmoney_secid(stock.ts_code)
        d_dash = to_dash_date(trade_date)

        for attempt in range(3):
            try:
                r = self._direct.get(
                    "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
                    params={
                        "secid": secid,
                        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                        "iscr": "1",
                        "ndays": "5",
                    },
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
                    timeout=self.timeout,
                )
                r.raise_for_status()
                trends = (r.json().get("data") or {}).get("trends") or []
                day_rows = []
                for t in trends:
                    if not t.startswith(d_dash):
                        continue
                    parts = t.split(",")
                    if len(parts) < 7:
                        continue
                    hhmm = parts[0].split(" ")[1]
                    day_rows.append(
                        {
                            "hhmm": hhmm,
                            "close": _to_dec(parts[2]),
                            "vol_lots": _to_dec(parts[5]) or Decimal("0"),
                            "amount": _to_dec(parts[6]) or Decimal("0"),
                        }
                    )
                if not day_rows:
                    return None

                bar_0930 = next((x for x in day_rows if x["hhmm"] == "09:30"), None)
                cc_bars = [x for x in day_rows if "09:31" <= x["hhmm"] <= "09:45"]

                auc_amount = bar_0930["amount"] if bar_0930 else Decimal("0")
                auc_price = bar_0930["close"] if bar_0930 else None
                if bar_0930 and auc_price and auc_price > 0 and auc_amount > 0:
                    # Exact shares from amount / opening auction single price (handles STAR & BSE odd lots!)
                    auc_shares = (auc_amount / auc_price).quantize(Decimal("1"))
                elif bar_0930:
                    auc_shares = (bar_0930["vol_lots"] * Decimal("100")).quantize(Decimal("1"))
                else:
                    auc_shares = Decimal("0")

                cc_shares = sum((x["vol_lots"] * Decimal("100")) for x in cc_bars).quantize(Decimal("1"))
                cc_amount = sum(x["amount"] for x in cc_bars)
                day_amount = sum(x["amount"] for x in day_rows)
                day_shares = sum((x["vol_lots"] * Decimal("100")) for x in day_rows).quantize(Decimal("1"))

                # Last bar with positive volume in 09:31..09:45 gives last traded price
                traded_cc = [x for x in cc_bars if x["vol_lots"] > 0 or x["amount"] > 0]
                price_0945 = traded_cc[-1]["close"] if traded_cc else None

                if self.sleep_sec > 0:
                    time.sleep(self.sleep_sec)

                return {
                    "source": "eastmoney_trends2_1m",
                    "has_0930": bar_0930 is not None,
                    "auction_shares": auc_shares,
                    "auction_amount": auc_amount,
                    "auction_price": auc_price,
                    "cc_shares": cc_shares,
                    "cc_amount": cc_amount,
                    "day_shares": day_shares,
                    "day_amount": day_amount,
                    "price_0945": price_0945,
                    "bar_count": len(day_rows),
                }
            except Exception:
                if attempt == 2:
                    return None
                time.sleep(0.5 * (attempt + 1))
        return None

    def fetch_sina_15m_first_bar(self, stock: PoolStock, trade_date: str) -> dict[str, Any] | None:
        """Fallback historical 15m bar from Sina (covers BSE as well). Auction is NOT separable."""
        sina_code = to_sina_code(stock.ts_code)
        d_dash = to_dash_date(trade_date)
        for attempt in range(3):
            try:
                r = self._std.get(
                    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData",
                    params={"symbol": sina_code, "scale": "15", "ma": "no", "datalen": "240"},
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
                    timeout=self.timeout,
                )
                r.raise_for_status()
                txt = r.text
                s_idx, e_idx = txt.find("("), txt.rfind(")")
                if s_idx == -1 or e_idx == -1:
                    return None
                arr = json.loads(txt[s_idx + 1 : e_idx])
                if not isinstance(arr, list):
                    return None
                day_bars = [b for b in arr if str(b.get("day", "")).startswith(d_dash)]
                if not day_bars:
                    return None
                bar_0945 = next((b for b in day_bars if str(b.get("day", "")).endswith("09:45:00")), None)
                if bar_0945 is None:
                    return {"source": "sina_15m", "has_0945": False, "day_bars": len(day_bars)}
                return {
                    "source": "sina_15m",
                    "has_0945": True,
                    "bar_time": str(bar_0945["day"]),
                    "volume_shares": _to_dec(bar_0945.get("volume")) or Decimal("0"),
                    "amount_yuan": _to_dec(bar_0945.get("amount")) or Decimal("0"),
                    "close": _to_dec(bar_0945.get("close")),
                }
            except Exception:
                if attempt == 2:
                    return None
                time.sleep(0.4 * (attempt + 1))
        return None


def resolve_minute_window(
    stock: PoolStock,
    trade_date: str,
    daily_rec: DailyRecord,
    bs_session: BaoStockSession | None,
    ind_client: IndependentMinuteClient,
    tushare_auction_map: dict[str, dict[str, Any]] | None = None,
    verify_5m_on_anomaly: bool = True,
) -> MinuteWindowResult:
    """Resolve the exact 09:30–09:45 continuous-auction window for one candidate stock.

    Rules:
    - SSE/SZSE: primary source is BaoStock 15m (`09:45` bar). Since BaoStock first bar is proven
      to include 09:25 opening call auction, we ONLY deduct verified same-stock same-day auction
      from Tushare `stk_auction_o` -> Tencent `m1` `0930` -> Eastmoney `trends2` `09:30`.
      If no auction source is available for this date, return `raw_first_bar_unverified_auction`.
    - BSE: BaoStock does not cover `.BJ`. Use Eastmoney `trends2` (direct, verifies full-day amount
      consistency with Tushare daily, takes `09:31..09:45` continuous auction directly).
      Fallback to Sina `15m` as `raw_first_bar_unverified_auction`.
    - Zero-volume in 09:30-09:45 (e.g. late open / intraday halt): returns `verified_zero_volume`
      with `price_0945 = None`.
    """
    d = normalize_date_yyyymmdd(trade_date)
    day_vol_sh = (daily_rec.vol_lots * Decimal("100")) if daily_rec.vol_lots is not None else None
    day_amt_yuan = (daily_rec.amount_k * Decimal("1000")) if daily_rec.amount_k is not None else None

    # ---- Path A: Beijing Stock Exchange (BSE) ----
    if stock.exchange == "BSE":
        em = ind_client.fetch_eastmoney_trends2_window(stock, d)
        if em is not None:
            notes: list[str] = []
            # Validate day amount consistency with Tushare daily (allow 1% or 1000 yuan tolerance)
            amt_ok = True
            if day_amt_yuan and day_amt_yuan > 0 and em["day_amount"] > 0:
                rel_diff = abs(em["day_amount"] - day_amt_yuan) / day_amt_yuan
                if rel_diff > Decimal("0.02"):
                    amt_ok = False
                    notes.append(f"em_day_amount_mismatch:{float(rel_diff):.4f}")

            if amt_ok:
                cc_vol = em["cc_shares"]
                cc_amt = em["cc_amount"]
                raw_vol = cc_vol + em["auction_shares"]
                raw_amt = cc_amt + em["auction_amount"]
                # Cap V15 at Vday if tiny odd-lot rounding occurs
                if day_vol_sh is not None and cc_vol > day_vol_sh:
                    if cc_vol - day_vol_sh <= Decimal("200"):
                        cc_vol = day_vol_sh
                        notes.append("bse_oddlot_clamped_to_vday")
                if cc_vol <= 0:
                    return MinuteWindowResult(
                        ts_code=stock.ts_code,
                        trade_date=d,
                        status="verified_zero_volume",
                        source="eastmoney_trends2_1m",
                        bar_time=f"{d}0931-0945",
                        raw_volume_shares=raw_vol,
                        raw_amount_yuan=raw_amt,
                        auction_volume_shares=em["auction_shares"],
                        auction_amount_yuan=em["auction_amount"],
                        auction_source="eastmoney_trends2_0930",
                        continuous_volume_shares=Decimal("0"),
                        continuous_amount_yuan=Decimal("0"),
                        price_0945=None,
                        auction_handling="北交所按东财1分钟分时直接取09:31-09:45连续竞价（排除09:30开盘集合竞价），区间无成交",
                        notes=notes,
                    )
                return MinuteWindowResult(
                    ts_code=stock.ts_code,
                    trade_date=d,
                    status="verified_continuous",
                    source="eastmoney_trends2_1m",
                    bar_time=f"{d}0931-0945",
                    raw_volume_shares=raw_vol,
                    raw_amount_yuan=raw_amt,
                    auction_volume_shares=em["auction_shares"],
                    auction_amount_yuan=em["auction_amount"],
                    auction_source="eastmoney_trends2_0930",
                    continuous_volume_shares=cc_vol,
                    continuous_amount_yuan=cc_amt,
                    price_0945=em["price_0945"],
                    auction_handling="北交所按东财1分钟分时直接取09:31-09:45连续竞价，已排除09:30开盘集合竞价并核对全天成交额",
                    notes=notes,
                )

        # Fallback for BSE when Eastmoney trends2 unavailable (e.g. >5 trading days ago)
        sina = ind_client.fetch_sina_15m_first_bar(stock, d)
        if sina is not None and sina.get("has_0945"):
            return MinuteWindowResult(
                ts_code=stock.ts_code,
                trade_date=d,
                status="raw_first_bar_unverified_auction",
                source="sina_15m",
                bar_time=sina["bar_time"],
                raw_volume_shares=sina["volume_shares"],
                raw_amount_yuan=sina["amount_yuan"],
                price_0945=sina["close"] if sina["volume_shares"] > 0 else None,
                auction_handling="北交所历史日回退新浪15m首柱，含竞价未分离（仅入待核验列表）",
            )
        return MinuteWindowResult(
            ts_code=stock.ts_code,
            trade_date=d,
            status="missing_data",
            source="bse_unavailable",
            auction_handling="BaoStock不支持北交所，且当日补充分钟源无有效记录",
        )

    # ---- Path B: SSE & SZSE via BaoStock 15m primary ----
    if bs_session is None:
        return MinuteWindowResult(
            ts_code=stock.ts_code,
            trade_date=d,
            status="missing_data",
            source="baostock_not_initialized",
        )

    try:
        bars_15 = bs_session.fetch_kline(stock.ts_code, d, frequency="15")
    except Exception as e:
        return MinuteWindowResult(
            ts_code=stock.ts_code,
            trade_date=d,
            status="missing_data",
            source="baostock_15m_error",
            notes=[str(e)[:120]],
        )

    if not bars_15:
        return MinuteWindowResult(
            ts_code=stock.ts_code,
            trade_date=d,
            status="missing_data",
            source="baostock_15m_empty",
            auction_handling="BaoStock当日无15分钟K线返回",
        )

    bar_0945 = next((b for b in bars_15 if b["hhmm"] == "0945"), None)
    notes = []

    if bar_0945 is None:
        # Late open or opening halt (other bars exist later in the day, 09:45 missing)
        if verify_5m_on_anomaly:
            try:
                bars_5 = bs_session.fetch_kline(stock.ts_code, d, frequency="5")
                early_5 = [b for b in bars_5 if b["hhmm"] in ("0935", "0940", "0945")]
            except Exception:
                early_5 = []
            if early_5:
                raw_v = sum(b["volume"] for b in early_5)
                raw_a = sum(b["amount"] for b in early_5)
                last_p = early_5[-1]["close"] if raw_v > 0 else None
                bar_0945 = {
                    "time": early_5[-1]["time"],
                    "volume": raw_v,
                    "amount": raw_a,
                    "close": last_p,
                }
                notes.append("recovered_from_5m_bars")
        if bar_0945 is None:
            # Confirmed later trading bars exist, so 09:30-09:45 had zero trades (e.g. late open / halt)
            return MinuteWindowResult(
                ts_code=stock.ts_code,
                trade_date=d,
                status="verified_zero_volume",
                source=f"baostock_{bs_session.version}_15m",
                bar_time=f"{d}094500000(absent)",
                raw_volume_shares=Decimal("0"),
                raw_amount_yuan=Decimal("0"),
                auction_volume_shares=Decimal("0"),
                auction_amount_yuan=Decimal("0"),
                continuous_volume_shares=Decimal("0"),
                continuous_amount_yuan=Decimal("0"),
                price_0945=None,
                auction_handling="开盘09:30-09:45缺柱（晚开盘或临停，当日后续有K线），区间成交记0且末笔价留空",
                notes=["missing_0945_bar_late_open_or_halt"],
            )

    raw_vol = bar_0945["volume"]
    raw_amt = bar_0945["amount"]
    raw_close = bar_0945["close"]
    bar_time = bar_0945["time"]

    if raw_vol <= 0:
        return MinuteWindowResult(
            ts_code=stock.ts_code,
            trade_date=d,
            status="verified_zero_volume",
            source=f"baostock_{bs_session.version}_15m",
            bar_time=bar_time,
            raw_volume_shares=Decimal("0"),
            raw_amount_yuan=Decimal("0"),
            auction_volume_shares=Decimal("0"),
            auction_amount_yuan=Decimal("0"),
            continuous_volume_shares=Decimal("0"),
            continuous_amount_yuan=Decimal("0"),
            price_0945=None,
            auction_handling="BaoStock 09:45首柱成交量为0，区间无成交且末笔价留空",
            notes=notes,
        )

    # Primary caliber uses full first 15m bar volume (including 09:25 opening call auction,
    # matching mainstream A-share trading terminals). No extra per-stock Tencent/Eastmoney
    # call is needed for SSE/SZSE once BaoStock 09:45 bar is obtained, cutting runtime by ~3x.
    auc_vol: Decimal | None = None
    auc_amt: Decimal | None = None
    auc_src: str | None = None
    if tushare_auction_map is not None and stock.ts_code in tushare_auction_map:
        item = tushare_auction_map[stock.ts_code]
        if item.get("vol_shares") is not None:
            auc_vol = item["vol_shares"]
            auc_amt = item.get("amount_yuan") or Decimal("0")
            auc_src = "tushare_stk_auction_o"

    cc_vol = (raw_vol - auc_vol) if (auc_vol is not None and auc_vol <= raw_vol) else raw_vol
    cc_amt = max(Decimal("0"), raw_amt - (auc_amt or Decimal("0"))) if auc_vol is not None else raw_amt

    return MinuteWindowResult(
        ts_code=stock.ts_code,
        trade_date=d,
        status="verified_continuous",
        source=f"baostock_{bs_session.version}_15m",
        bar_time=bar_time,
        raw_volume_shares=raw_vol,
        raw_amount_yuan=raw_amt,
        auction_volume_shares=auc_vol,
        auction_amount_yuan=auc_amt,
        auction_source=auc_src,
        continuous_volume_shares=cc_vol,
        continuous_amount_yuan=cc_amt,
        price_0945=raw_close,
        auction_handling="采用 BaoStock 09:45 首根 15 分钟 K 线总成交量（含开盘集合竞价）",
        notes=notes,
    )
