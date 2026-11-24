"""Audit and cross-verify a collected private day snapshot against Section 7.3 requirements."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    date_dash = sys.argv[1] if len(sys.argv) > 1 else "2026-11-23"
    p = Path("data/private/days") / f"{date_dash}.json"
    d = json.loads(p.read_text(encoding="utf-8"))

    print(f"=== Snapshot Audit: {d['trade_date']} ({d['scan_status_label']}) ===")
    print("Funnels:")
    for cal in ("normal", "free"):
        f = d["funnels"][cal]
        s = f["suspended"] + f["prescreen_excluded"] + f["verified_not_selected"] + f["selected"] + f["unverified"]
        print(f"  {cal:6s}: {f} (sum={s}, ok={s == f['pool_total']})")

    print("\nBoard Coverage:")
    for b, bf in d["board_funnels"].items():
        n = bf["normal"]
        fr = bf["free"]
        n_proc = n["suspended"] + n["prescreen_excluded"] + n["verified_not_selected"] + n["selected"]
        f_proc = fr["suspended"] + fr["prescreen_excluded"] + fr["verified_not_selected"] + fr["selected"]
        print(
            f"  {b:6s} | pool={n['pool_total']:4d} | "
            f"普通: 处理={n_proc:4d} 未核验={n['unverified']:2d} 入选={n['selected']:2d} | "
            f"自由: 处理={f_proc:4d} 未核验={fr['unverified']:2d} 入选={fr['selected']:2d}"
        )

    print(f"\nVerified Main Lists: normal={len(d['main_lists']['normal'])}, free={len(d['main_lists']['free'])}")
    print(f"Raw Unverified Lists: normal={len(d['raw_unverified_lists']['normal'])}, free={len(d['raw_unverified_lists']['free'])}")

    print("\n--- Top 5 Normal Selected (Detailed Audit) ---")
    for r in d["main_lists"]["normal"][:5]:
        print(
            f"  {r['ts_code']} {r['name']} [{r['board']}/{r['industry']}] "
            f"bar={r['minute_bar_time']} src={r['minute_source']} | {r['auction_handling']}\n"
            f"    raw_v15={r['v15_raw_shares']:,} auc={r['auction_vol_shares']:,} V15={r['v15_shares']:,} Vday={r['vday_shares']:,} "
            f"(15m/day={r['vol_ratio_15m_to_day']:.2f}%)\n"
            f"    float={r['float_share_wan']:.4f}万股({r['float_share_yi']:.4f}亿) "
            f"free={r['free_share_wan']:.4f}万股({r['free_share_yi']:.4f}亿)\n"
            f"    tr15_norm={r['turnover_15m_normal']:.4f}% tr15_free={r['turnover_15m_free']:.4f}% | "
            f"trday_norm={r['turnover_day_normal']:.4f}% trday_free={r['turnover_day_free']:.4f}%\n"
            f"    P0945={r['price_0945']} close={r['close_price']} (09:45->close={r['pct_0945_to_close']:+.2f}%) "
            f"circ_mv={r['circ_mv_yi']:.2f}亿 free_mv={r['free_mv_yi']:.2f}亿\n"
            f"    主营: {r['business_summary']} [出处: {r['business_source']}]"
        )

    print("\n--- Top 3 Free-Only Selected ---")
    norm_codes = {r["ts_code"] for r in d["main_lists"]["normal"]}
    free_only = [r for r in d["main_lists"]["free"] if r["ts_code"] not in norm_codes]
    for r in free_only[:3]:
        print(
            f"  {r['ts_code']} {r['name']} [{r['board']}] tr15_norm={r['turnover_15m_normal']:.4f}% "
            f"tr15_free={r['turnover_15m_free']:.4f}% circ_mv={r['circ_mv_yi']:.2f}亿 free_mv={r['free_mv_yi']:.2f}亿"
        )

    if d["raw_unverified_lists"]["free"]:
        print("\n--- Raw Unverified Sample ---")
        for r in d["raw_unverified_lists"]["free"][:5]:
            print(f"  {r['ts_code']} {r['name']} [{r['board']}] src={r['minute_source']} {r['auction_handling']}")


if __name__ == "__main__":
    main()
