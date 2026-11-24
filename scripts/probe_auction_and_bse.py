"""Probe independent minute/auction sources (Eastmoney, Tencent, Sina) for auction calibration and BSE coverage."""

from __future__ import annotations

import json
from pathlib import Path

import requests


def eastmoney_secid(ts_code: str) -> str:
    sym, exch = ts_code.split(".")
    if exch == "SH":
        return f"1.{sym}"
    # SZ and BJ use market 0 in Eastmoney push2his
    return f"0.{sym}"


def probe_eastmoney_kline(ts_code: str, klt: int, beg: str, end: str) -> dict:
    """klt: 1=1min, 5=5min, 15=15min, 101=daily."""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": eastmoney_secid(ts_code),
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": str(klt),
        "fqt": "0",
        "beg": beg,
        "end": end,
        "lmt": "1000",
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        data = r.json()
        klines = (data.get("data") or {}).get("klines") or []
        return {
            "status": "ok",
            "count": len(klines),
            "name": (data.get("data") or {}).get("name"),
            "first_5": klines[:5],
            "last_2": klines[-2:] if klines else [],
            "all": klines,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def probe_eastmoney_trends2(ts_code: str, ndays: int = 5) -> dict:
    """Eastmoney trends2 gives 1-min intraday bars for recent 5 trading days including 09:30."""
    url = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
    params = {
        "secid": eastmoney_secid(ts_code),
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "1",
        "ndays": str(ndays),
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        data = r.json()
        trends = (data.get("data") or {}).get("trends") or []
        return {
            "status": "ok",
            "count": len(trends),
            "sample_2026_11_23": [t for t in trends if t.startswith("2026-11-23 09:")][:25],
            "all_2026_11_23_count": sum(1 for t in trends if t.startswith("2026-11-23")),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def probe_tencent_kline(ts_code: str, scale: str = "m15", count: int = 80) -> dict:
    sym, exch = ts_code.split(".")
    pref = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[exch]
    code = f"{pref}{sym}"
    url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
    params = {"param": f"{code},{scale},,{count}"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        data = r.json()
        bars = ((data.get("data") or {}).get(code) or {}).get(scale) or []
        day_bars = [b for b in bars if str(b[0]).startswith("20261123")]
        return {
            "status": "ok",
            "total_bars": len(bars),
            "day_20261123_count": len(day_bars),
            "day_20261123_first_4": day_bars[:4],
            "day_20261123_last_2": day_bars[-2:] if day_bars else [],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def probe_sina_kline(ts_code: str, scale: int = 15, datalen: int = 80) -> dict:
    sym, exch = ts_code.split(".")
    pref = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[exch]
    symbol = f"{pref}{sym}"
    url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData"
    params = {"symbol": symbol, "scale": str(scale), "ma": "no", "datalen": str(datalen)}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        txt = r.text
        start = txt.find("(")
        end = txt.rfind(")")
        if start == -1 or end == -1:
            return {"status": "parse_error", "raw_head": txt[:200]}
        arr = json.loads(txt[start + 1 : end])
        if not isinstance(arr, list):
            return {"status": "empty_or_null", "raw": arr}
        day_bars = [b for b in arr if str(b.get("day", "")).startswith("2026-11-23")]
        return {
            "status": "ok",
            "total_bars": len(arr),
            "day_20261123_count": len(day_bars),
            "day_20261123_first_4": day_bars[:4],
            "day_20261123_last_2": day_bars[-2:] if day_bars else [],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main() -> None:
    targets = ["600000.SH", "000001.SZ", "300750.SZ", "688981.SH", "920045.BJ", "920067.BJ", "920021.BJ"]
    out = {}
    for code in targets:
        print(f"Probing {code}...")
        em_15 = probe_eastmoney_kline(code, 15, "20261123", "20261123")
        em_1 = probe_eastmoney_kline(code, 1, "20261123", "20261123")
        em_t2 = probe_eastmoney_trends2(code, 5)
        tx_15 = probe_tencent_kline(code, "m15", 80)
        tx_1 = probe_tencent_kline(code, "m1", 600)
        sina_15 = probe_sina_kline(code, 15, 80)

        # Strip huge 'all' lists from saved json after computing sums
        em15_bars = em_15.pop("all", [])
        em1_bars = em_1.pop("all", [])
        em_15["sum_vol_lots"] = sum(int(float(x.split(",")[5])) for x in em15_bars) if em15_bars else 0
        em_15["sum_amount"] = sum(float(x.split(",")[6]) for x in em15_bars) if em15_bars else 0.0

        day_em1 = [x for x in em1_bars if x.startswith("2026-11-23")]
        em_1["day_count"] = len(day_em1)
        em_1["day_first_18"] = day_em1[:18]
        em_1["sum_vol_lots"] = sum(int(float(x.split(",")[5])) for x in day_em1) if day_em1 else 0
        em_1["sum_amount"] = sum(float(x.split(",")[6]) for x in day_em1) if day_em1 else 0.0

        out[code] = {
            "eastmoney_15m": em_15,
            "eastmoney_1m": em_1,
            "eastmoney_trends2": em_t2,
            "tencent_15m": tx_15,
            "tencent_1m": tx_1,
            "sina_15m": sina_15,
        }

    p = Path("data/cache/probe_alt_sources.json")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved to {p}")

    for code, res in out.items():
        print(f"\n=== {code} ===")
        print("  EM 15m:", res["eastmoney_15m"].get("status"), "cnt=", res["eastmoney_15m"].get("count"), "first=", res["eastmoney_15m"].get("first_5", [])[:1])
        print("  EM 1m :", res["eastmoney_1m"].get("status"), "day_cnt=", res["eastmoney_1m"].get("day_count"), "first3=", res["eastmoney_1m"].get("day_first_18", [])[:3])
        print("  EM t2 :", res["eastmoney_trends2"].get("status"), "day_cnt=", res["eastmoney_trends2"].get("all_2026_11_23_count"), "first3=", res["eastmoney_trends2"].get("sample_2026_11_23", [])[:3])
        print("  TX 15m:", res["tencent_15m"].get("status"), "day_cnt=", res["tencent_15m"].get("day_20261123_count"), "first=", res["tencent_15m"].get("day_20261123_first_4", [])[:1])
        print("  Sina15:", res["sina_15m"].get("status"), "day_cnt=", res["sina_15m"].get("day_20261123_count"), "first=", res["sina_15m"].get("day_20261123_first_4", [])[:1])


if __name__ == "__main__":
    main()
