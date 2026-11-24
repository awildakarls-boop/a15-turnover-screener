"""Probe Tushare and BaoStock permissions, board coverage, timestamps, and auction handling."""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import baostock as bs
import pandas as pd
import tushare as ts

BJ_TZ = ZoneInfo("Asia/Shanghai")


def classify_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if any(k in msg for k in ["权限", "积分", "没有接口访问权限", "抱歉，您没有"]):
        return "permission_denied"
    if any(k in msg for k in ["每分钟", "每天", "频次", "限额", "最多访问"]) or "rate limit" in low:
        return "rate_limited"
    if any(k in low for k in ["timeout", "connection", "proxy", "ssl", "max retries", "network", "unreachable"]):
        return "network_error"
    return "other_error"


def probe_tushare() -> dict:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    report: dict = {
        "token_present": bool(token),
        "token_length": len(token),
        "tushare_version": getattr(ts, "__version__", "unknown"),
        "interfaces": {},
    }
    if not token:
        report["error"] = "TUSHARE_TOKEN is not set"
        return report

    pro = ts.pro_api(token)

    now_bj = datetime.now(BJ_TZ)
    today_str = now_bj.strftime("%Y%m%d")
    start_cal = (now_bj - timedelta(days=45)).strftime("%Y%m%d")
    end_cal = (now_bj + timedelta(days=15)).strftime("%Y%m%d")
    report["system_time_bj"] = now_bj.isoformat()

    # 1. trade_cal
    target_date = None
    recent_open_days: list[str] = []
    try:
        df_cal = pro.trade_cal(exchange="SSE", start_date=start_cal, end_date=end_cal)
        open_days = sorted(df_cal.loc[df_cal["is_open"] == 1, "cal_date"].astype(str).tolist())
        strictly_before_today = [d for d in open_days if d < today_str]
        recent_open_days = strictly_before_today[-10:]
        target_date = strictly_before_today[-1] if strictly_before_today else (open_days[-1] if open_days else None)
        report["interfaces"]["trade_cal"] = {
            "status": "ok",
            "rows": len(df_cal),
            "columns": list(df_cal.columns),
            "min_date": str(df_cal["cal_date"].min()) if len(df_cal) else None,
            "max_date": str(df_cal["cal_date"].max()) if len(df_cal) else None,
            "strictly_before_today_latest": target_date,
            "recent_open_days": recent_open_days,
        }
    except Exception as e:
        report["interfaces"]["trade_cal"] = {
            "status": classify_error(e),
            "error": str(e),
        }

    # Verify latest actual daily data date (in case system clock is ahead of real world)
    actual_latest_daily_date = None
    try:
        df_daily_probe = pro.daily(ts_code="600000.SH")
        if df_daily_probe is not None and len(df_daily_probe) > 0:
            actual_latest_daily_date = str(df_daily_probe["trade_date"].max())
            report["actual_latest_daily_date_600000"] = actual_latest_daily_date
            report["recent_daily_dates_600000"] = df_daily_probe["trade_date"].astype(str).head(10).tolist()
    except Exception as e:
        report["actual_latest_daily_probe_error"] = str(e)

    # Pick completed trade date for probing
    probe_date = target_date
    if actual_latest_daily_date and (not probe_date or probe_date > actual_latest_daily_date):
        # Use second-to-latest or latest completed date where both Tushare & BaoStock should be settled
        probe_date = actual_latest_daily_date
    report["selected_probe_date"] = probe_date

    # 2. stock_basic (L, D, P)
    sb_all = []
    sb_info = {}
    fields_sb = "ts_code,symbol,name,area,industry,market,exchange,curr_type,list_status,list_date,delist_date"
    for st in ["L", "D", "P"]:
        try:
            df_sb = pro.stock_basic(list_status=st, fields=fields_sb)
            sb_info[st] = {
                "status": "ok",
                "rows": len(df_sb),
                "columns": list(df_sb.columns),
                "exchanges": df_sb["exchange"].value_counts(dropna=False).to_dict() if "exchange" in df_sb else {},
                "markets": df_sb["market"].value_counts(dropna=False).to_dict() if "market" in df_sb else {},
            }
            sb_all.append(df_sb)
        except Exception as e:
            sb_info[st] = {"status": classify_error(e), "error": str(e)}
    report["interfaces"]["stock_basic"] = sb_info
    df_sb_merged = pd.concat(sb_all, ignore_index=True) if sb_all else pd.DataFrame()

    # 3. daily_basic
    df_db = pd.DataFrame()
    if probe_date:
        try:
            df_db = pro.daily_basic(
                trade_date=probe_date,
                fields="ts_code,trade_date,close,turnover_rate,turnover_rate_f,float_share,free_share,circ_mv",
            )
            by_suffix = df_db["ts_code"].str.split(".").str[-1].value_counts().to_dict() if len(df_db) else {}
            null_counts = df_db.isna().sum().to_dict() if len(df_db) else {}
            report["interfaces"]["daily_basic"] = {
                "status": "ok",
                "trade_date": probe_date,
                "rows": len(df_db),
                "columns": list(df_db.columns),
                "by_suffix": by_suffix,
                "null_counts": {k: int(v) for k, v in null_counts.items()},
                "sample": df_db.head(3).to_dict(orient="records"),
            }
        except Exception as e:
            report["interfaces"]["daily_basic"] = {"status": classify_error(e), "error": str(e)}

    # 4. daily
    df_d = pd.DataFrame()
    if probe_date:
        try:
            df_d = pro.daily(trade_date=probe_date)
            by_suffix = df_d["ts_code"].str.split(".").str[-1].value_counts().to_dict() if len(df_d) else {}
            report["interfaces"]["daily"] = {
                "status": "ok",
                "trade_date": probe_date,
                "rows": len(df_d),
                "columns": list(df_d.columns),
                "by_suffix": by_suffix,
                "sample": df_d.head(3).to_dict(orient="records"),
            }
        except Exception as e:
            report["interfaces"]["daily"] = {"status": classify_error(e), "error": str(e)}

    # 5. stock_company (by exchange SSE, SZSE, BSE)
    sc_info = {}
    for exch in ["SSE", "SZSE", "BSE"]:
        try:
            df_sc = pro.stock_company(
                exchange=exch,
                fields="ts_code,exchange,main_business,introduction,website,business_scope",
            )
            non_empty_mb = int(df_sc["main_business"].fillna("").str.strip().ne("").sum()) if "main_business" in df_sc else 0
            non_empty_intro = int(df_sc["introduction"].fillna("").str.strip().ne("").sum()) if "introduction" in df_sc else 0
            sc_info[exch] = {
                "status": "ok",
                "rows": len(df_sc),
                "columns": list(df_sc.columns),
                "non_empty_main_business": non_empty_mb,
                "non_empty_introduction": non_empty_intro,
            }
        except Exception as e:
            sc_info[exch] = {"status": classify_error(e), "error": str(e)}
    report["interfaces"]["stock_company"] = sc_info

    # 6. Optional advanced interfaces: stk_auction_o, stk_mins, namechange, suspend_d
    for api_name, kwargs in [
        ("stk_auction_o", {"trade_date": probe_date}),
        ("stk_mins", {"ts_code": "600000.SH", "freq": "15min", "start_date": f"{probe_date[:4]}-{probe_date[4:6]}-{probe_date[6:]} 09:00:00", "end_date": f"{probe_date[:4]}-{probe_date[4:6]}-{probe_date[6:]} 10:00:00"} if probe_date else {}),
        ("suspend_d", {"trade_date": probe_date}),
        ("namechange", {"ts_code": "600000.SH"}),
        ("bse_mapping", {}),
    ]:
        try:
            fn = getattr(pro, api_name)
            df_x = fn(**kwargs)
            report["interfaces"][api_name] = {
                "status": "ok",
                "rows": len(df_x) if df_x is not None else 0,
                "columns": list(df_x.columns) if df_x is not None else [],
                "sample": df_x.head(3).to_dict(orient="records") if df_x is not None and len(df_x) else [],
            }
        except Exception as e:
            report["interfaces"][api_name] = {
                "status": classify_error(e),
                "error": str(e),
            }

    # Select BSE active stocks on probe_date
    bse_samples = []
    if len(df_d) > 0:
        bse_df = df_d[df_d["ts_code"].str.endswith(".BJ")].sort_values("amount", ascending=False)
        bse_samples = bse_df["ts_code"].head(5).tolist()
    report["bse_active_samples"] = bse_samples

    # Probe stocks details on probe_date
    probe_codes = ["600000.SH", "000001.SZ", "300750.SZ", "688981.SH"] + bse_samples[:3]
    probe_daily_rows = {}
    if len(df_d) > 0 and len(df_db) > 0:
        for c in probe_codes:
            r_d = df_d[df_d["ts_code"] == c].to_dict(orient="records")
            r_db = df_db[df_db["ts_code"] == c].to_dict(orient="records")
            probe_daily_rows[c] = {
                "daily": r_d[0] if r_d else None,
                "daily_basic": r_db[0] if r_db else None,
            }
    report["probe_daily_rows"] = probe_daily_rows
    return report


def probe_baostock(probe_date_yyyymmdd: str, bse_samples: list[str]) -> dict:
    date_dash = f"{probe_date_yyyymmdd[:4]}-{probe_date_yyyymmdd[4:6]}-{probe_date_yyyymmdd[6:]}"
    report: dict = {
        "baostock_version": getattr(bs, "__version__", "unknown"),
        "probe_date": date_dash,
        "login": {},
        "trade_dates": {},
        "industry": {},
        "kline_15m": {},
        "kline_5m": {},
        "bse_tests": {},
    }
    lg = bs.login()
    report["login"] = {"error_code": lg.error_code, "error_msg": lg.error_msg}
    if lg.error_code != "0":
        return report

    try:
        # Check trade_dates
        rs_td = bs.query_trade_dates(start_date="2026-01-01", end_date=date_dash)
        td_rows = []
        while rs_td.error_code == "0" and rs_td.next():
            td_rows.append(rs_td.get_row_data())
        open_tds = [r[0] for r in td_rows if len(r) >= 2 and r[1] == "1"]
        report["trade_dates"] = {
            "error_code": rs_td.error_code,
            "total_rows": len(td_rows),
            "latest_open_days": open_tds[-5:],
        }

        # Check industry
        rs_ind = bs.query_stock_industry(code="sh.600000")
        ind_rows = []
        while rs_ind.error_code == "0" and rs_ind.next():
            ind_rows.append(rs_ind.get_row_data())
        report["industry"] = {
            "error_code": rs_ind.error_code,
            "fields": rs_ind.fields,
            "rows": ind_rows,
        }

        # Standard 4 probes
        probes = [
            ("sh.600000", "600000.SH"),
            ("sz.000001", "000001.SZ"),
            ("sz.300750", "300750.SZ"),
            ("sh.688981", "688981.SH"),
        ]
        fields = "date,time,code,open,high,low,close,volume,amount,adjustflag"
        for bs_code, ts_code in probes:
            rs15 = bs.query_history_k_data_plus(
                bs_code, fields, start_date=date_dash, end_date=date_dash, frequency="15", adjustflag="3"
            )
            rows15 = []
            while rs15.error_code == "0" and rs15.next():
                rows15.append(dict(zip(rs15.fields, rs15.get_row_data())))
            report["kline_15m"][ts_code] = {
                "error_code": rs15.error_code,
                "error_msg": rs15.error_msg,
                "count": len(rows15),
                "first_3": rows15[:3],
                "last_1": rows15[-1:] if rows15 else [],
                "sum_volume": sum(int(float(r["volume"])) for r in rows15 if r.get("volume")),
                "sum_amount": sum(float(r["amount"]) for r in rows15 if r.get("amount")),
            }

            rs5 = bs.query_history_k_data_plus(
                bs_code, fields, start_date=date_dash, end_date=date_dash, frequency="5", adjustflag="3"
            )
            rows5 = []
            while rs5.error_code == "0" and rs5.next():
                rows5.append(dict(zip(rs5.fields, rs5.get_row_data())))
            first_3_5m = rows5[:3]
            report["kline_5m"][ts_code] = {
                "error_code": rs5.error_code,
                "error_msg": rs5.error_msg,
                "count": len(rows5),
                "first_3": first_3_5m,
                "first_3_sum_volume": sum(int(float(r["volume"])) for r in first_3_5m if r.get("volume")),
                "first_3_sum_amount": sum(float(r["amount"]) for r in first_3_5m if r.get("amount")),
            }

        # Test BSE codes with different prefixes (bj., sh., sz.)
        for bse_ts in bse_samples[:3]:
            sym = bse_ts.split(".")[0]
            bse_res = {}
            for pref in ["bj", "sz", "sh"]:
                code = f"{pref}.{sym}"
                rs_b = bs.query_history_k_data_plus(
                    code, fields, start_date=date_dash, end_date=date_dash, frequency="15", adjustflag="3"
                )
                rows_b = []
                while rs_b.error_code == "0" and rs_b.next():
                    rows_b.append(dict(zip(rs_b.fields, rs_b.get_row_data())))
                bse_res[code] = {
                    "error_code": rs_b.error_code,
                    "error_msg": rs_b.error_msg,
                    "count": len(rows_b),
                    "first_1": rows_b[:1],
                }
            report["bse_tests"][bse_ts] = bse_res

        # Also check query_all_stock on probe_date to see prefixes BaoStock knows
        rs_all = bs.query_all_stock(day=date_dash)
        prefixes: dict[str, int] = {}
        total_all = 0
        while rs_all.error_code == "0" and rs_all.next():
            r = rs_all.get_row_data()
            total_all += 1
            p = r[0].split(".")[0] if r and "." in r[0] else "unknown"
            prefixes[p] = prefixes.get(p, 0) + 1
        report["query_all_stock"] = {
            "error_code": rs_all.error_code,
            "total": total_all,
            "prefixes": prefixes,
        }
    finally:
        bs.logout()

    return report


def main() -> None:
    out_dir = Path("data/cache")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=== Probing Tushare ===")
    ts_rep = probe_tushare()
    probe_date = ts_rep.get("selected_probe_date")
    bse_samples = ts_rep.get("bse_active_samples", [])
    print(f"Selected probe_date: {probe_date}, BSE samples: {bse_samples}")

    print("=== Probing BaoStock ===")
    bs_rep = probe_baostock(probe_date, bse_samples) if probe_date else {"error": "no probe_date"}

    full = {"tushare": ts_rep, "baostock": bs_rep}
    out_path = out_dir / "probe_result.json"
    out_path.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved probe report to {out_path}")

    # Compact summary to stdout
    print("\n--- Tushare Interfaces ---")
    for k, v in ts_rep.get("interfaces", {}).items():
        if isinstance(v, dict) and "status" in v:
            print(f"  {k}: status={v['status']}, rows={v.get('rows')}, err={v.get('error', '')[:120]}")
        else:
            print(f"  {k}: { {sk: (sv.get('status'), sv.get('rows')) for sk, sv in v.items()} }")
    print("\n--- BaoStock Summary ---")
    print("  login:", bs_rep.get("login"))
    print("  query_all_stock:", bs_rep.get("query_all_stock"))
    for k, v in bs_rep.get("kline_15m", {}).items():
        r5 = bs_rep.get("kline_5m", {}).get(k, {})
        first15 = v["first_3"][0] if v.get("first_3") else {}
        print(
            f"  {k}: 15m_cnt={v.get('count')}, first15_time={first15.get('time')}, "
            f"first15_vol={first15.get('volume')}, sum3x5m_vol={r5.get('first_3_sum_volume')}, "
            f"day15m_sum_vol={v.get('sum_volume')}"
        )
    print("  bse_tests:", json.dumps(bs_rep.get("bse_tests"), ensure_ascii=False))


if __name__ == "__main__":
    main()
