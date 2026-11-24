"""Cross-verify BaoStock 15m/5m first bar against Eastmoney trends2 (1m) and Tencent m1 (1m)
to prove:
1. Timestamp convention (end-time vs start-time) and 09:45:00 boundary attribution.
2. Whether BaoStock/Sina first 15m bar includes 09:25 opening call auction.
3. Exact unit and precision of auction deduction across all 5 boards (SH Main, SZ Main, ChiNext, STAR, BSE).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests


def _get_json(session: requests.Session, url: str, params: dict, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            r = session.get(
                url,
                params=params,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://quote.eastmoney.com/",
                },
                timeout=12,
            )
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(0.8 * (attempt + 1))
    return None


def fetch_em_trends2(session: requests.Session, secid: str, date_dash: str) -> list[dict]:
    data = _get_json(
        session,
        "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "iscr": "1",
            "ndays": "5",
        },
    )
    if not data:
        return []
    trends = (data.get("data") or {}).get("trends") or []
    rows = []
    for t in trends:
        if t.startswith(date_dash):
            parts = t.split(",")
            rows.append(
                {
                    "time": parts[0].split(" ")[1],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "vol_lots": int(parts[5]),
                    "amount": float(parts[6]),
                }
            )
    return rows


def fetch_em_kline_15m(session: requests.Session, secid: str, date_yyyymmdd: str) -> list[dict]:
    data = _get_json(
        session,
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        {
            "secid": secid,
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "15",
            "fqt": "0",
            "beg": date_yyyymmdd,
            "end": date_yyyymmdd,
            "lmt": "100",
        },
    )
    if not data:
        return []
    klines = (data.get("data") or {}).get("klines") or []
    rows = []
    for k in klines:
        p = k.split(",")
        rows.append(
            {
                "time": p[0],
                "open": float(p[1]),
                "close": float(p[2]),
                "high": float(p[3]),
                "low": float(p[4]),
                "vol_lots": int(p[5]),
                "amount": float(p[6]),
            }
        )
    return rows


def fetch_tx_m1(code: str, date_yyyymmdd: str) -> list[dict]:
    r = requests.get(
        "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
        params={"param": f"{code},m1,,600"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    r.raise_for_status()
    bars = ((r.json().get("data") or {}).get(code) or {}).get("m1") or []
    rows = []
    for b in bars:
        if str(b[0]).startswith(date_yyyymmdd):
            rows.append({"time": str(b[0])[8:], "open": float(b[1]), "close": float(b[2]), "vol": float(b[5])})
    return rows


def main() -> None:
    probe = json.loads(Path("data/cache/probe_result.json").read_text(encoding="utf-8"))
    alt = json.loads(Path("data/cache/probe_alt_sources.json").read_text(encoding="utf-8"))
    date_yyyymmdd = "20261123"
    date_dash = "2026-11-23"

    s = requests.Session()
    s.trust_env = False

    cases = [
        ("600000.SH", "沪市主板", "1.600000", "sh600000", 100),
        ("000001.SZ", "深市主板", "0.000001", "sz000001", 100),
        ("300750.SZ", "创业板", "0.300750", "sz300750", 100),
        ("688981.SH", "科创板", "1.688981", "sh688981", 1),
        ("920045.BJ", "北交所", "0.920045", None, 100),
        ("920067.BJ", "北交所", "0.920067", None, 100),
        ("920021.BJ", "北交所", "0.920021", None, 100),
    ]

    results = []
    for ts_code, board, secid, tx_code, tx_mult in cases:
        em = fetch_em_trends2(s, secid, date_dash)
        em15 = fetch_em_kline_15m(s, secid, date_yyyymmdd)
        em_0930 = next((r for r in em if r["time"] == "09:30"), None)
        em_0931_0945 = [r for r in em if "09:31" <= r["time"] <= "09:45"]
        em_0930_0945 = [r for r in em if "09:30" <= r["time"] <= "09:45"]
        em_0930_0944 = [r for r in em if "09:30" <= r["time"] <= "09:44"]

        ts_d = probe["tushare"]["probe_daily_rows"][ts_code]["daily"]
        day_vol_sh = int(round(ts_d["vol"] * 100))
        day_amt = round(ts_d["amount"] * 1000, 2)

        em_auc_amt = em_0930["amount"] if em_0930 else 0.0
        em_auc_vol_sh_from_lots = (em_0930["vol_lots"] * 100) if em_0930 else 0
        # Exact shares from auction amount / opening price (works for STAR & BSE odd lots!)
        em_auc_vol_sh_exact = (
            int(round(em_auc_amt / em_0930["close"])) if (em_0930 and em_0930["close"] > 0) else 0
        )

        em_0930_45_vol = sum(r["vol_lots"] for r in em_0930_0945) * 100
        em_0930_45_amt = sum(r["amount"] for r in em_0930_0945)
        em_0931_45_vol = sum(r["vol_lots"] for r in em_0931_0945) * 100
        em_0931_45_amt = sum(r["amount"] for r in em_0931_0945)
        em_0930_44_vol = sum(r["vol_lots"] for r in em_0930_0944) * 100
        em_0930_44_amt = sum(r["amount"] for r in em_0930_0944)

        bs15_list = probe["baostock"]["kline_15m"].get(ts_code, {}).get("first_3", [])
        bs_vol = int(bs15_list[0]["volume"]) if bs15_list else None
        bs_amt = float(bs15_list[0]["amount"]) if bs15_list else None
        bs_close = float(bs15_list[0]["close"]) if bs15_list else None

        sina15_list = alt[ts_code]["sina_15m"].get("day_20261123_first_4", [])
        sina_vol = int(sina15_list[0]["volume"]) if sina15_list else None
        sina_amt = float(sina15_list[0]["amount"]) if sina15_list else None

        tx_info = {}
        if tx_code:
            tx = fetch_tx_m1(tx_code, date_yyyymmdd)
            tx_0930 = int(round(sum(r["vol"] for r in tx if r["time"] == "0930") * tx_mult))
            tx_0930_45 = int(round(sum(r["vol"] for r in tx if "0930" <= r["time"] <= "0945") * tx_mult))
            tx_0930_44 = int(round(sum(r["vol"] for r in tx if "0930" <= r["time"] <= "0944") * tx_mult))
            tx_0931_45 = int(round(sum(r["vol"] for r in tx if "0931" <= r["time"] <= "0945") * tx_mult))
            tx_info = {
                "tx_0930_sh": tx_0930,
                "tx_0930_0945_sh": tx_0930_45,
                "tx_0930_0944_sh": tx_0930_44,
                "tx_0931_0945_sh": tx_0931_45,
                "diff_bs_vs_tx_0930_0945": (bs_vol - tx_0930_45) if bs_vol is not None else None,
                "diff_bs_vs_tx_0930_0944": (bs_vol - tx_0930_44) if bs_vol is not None else None,
            }

        row = {
            "ts_code": ts_code,
            "board": board,
            "tushare_day_vol_sh": day_vol_sh,
            "tushare_day_amt": day_amt,
            "baostock_0945_vol_sh": bs_vol,
            "baostock_0945_amt": bs_amt,
            "baostock_0945_close": bs_close,
            "em15_first": em15[0] if em15 else None,
            "em_trends2_0930_price": em_0930["close"] if em_0930 else None,
            "em_trends2_0930_vol_lots": em_0930["vol_lots"] if em_0930 else None,
            "em_trends2_0930_vol_sh_exact": em_auc_vol_sh_exact,
            "em_trends2_0930_amt": em_auc_amt,
            "em_trends2_0930_0945_vol_sh": em_0930_45_vol,
            "em_trends2_0930_0945_amt": em_0930_45_amt,
            "em_trends2_0931_0945_vol_sh": em_0931_45_vol,
            "em_trends2_0931_0945_amt": em_0931_45_amt,
            "em_trends2_0945_close": em_0931_0945[-1]["close"] if em_0931_0945 else None,
            "diff_bs_amt_vs_em_0930_0945": round(bs_amt - em_0930_45_amt, 2) if bs_amt is not None else None,
            "diff_bs_amt_vs_em_0930_0944": round(bs_amt - em_0930_44_amt, 2) if bs_amt is not None else None,
            "sina_0945_vol_sh": sina_vol,
            "sina_0945_amt": sina_amt,
            "tx_m1": tx_info,
        }
        results.append(row)

        print(f"\n=== {ts_code} ({board}) ===")
        print(f"  EM15 first bar      : {em15[0] if em15 else None}")
        print(f"  BaoStock 09:45      : vol={bs_vol}, amt={bs_amt}, close={bs_close}")
        print(f"  EM trends2 09:30    : price={row['em_trends2_0930_price']}, lots={row['em_trends2_0930_vol_lots']}, exact_sh={em_auc_vol_sh_exact}, amt={em_auc_amt:,.2f}")
        print(f"  EM 09:30~09:45      : vol_sh={em_0930_45_vol}, amt={em_0930_45_amt:,.2f} (diff BS-EM amt={row['diff_bs_amt_vs_em_0930_0945']})")
        print(f"  EM 09:30~09:44      : vol_sh={em_0930_44_vol}, amt={em_0930_44_amt:,.2f} (diff BS-EM amt={row['diff_bs_amt_vs_em_0930_0944']})")
        print(f"  EM 09:31~09:45 (CC) : vol_sh={em_0931_45_vol}, amt={em_0931_45_amt:,.2f}, close_0945={row['em_trends2_0945_close']}")
        print(f"  Sina 09:45          : vol={sina_vol}, amt={sina_amt}")
        if tx_info:
            print(f"  TX m1               : {tx_info}")

    out_path = Path("data/cache/auction_calibration.json")
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved calibration report to {out_path}")


if __name__ == "__main__":
    main()
