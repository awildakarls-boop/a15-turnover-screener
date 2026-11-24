"""End-to-end daily collection, backfill, and atomic snapshot publisher."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .calendar_utils import (
    calendar_window,
    list_recent_closed_trading_days,
    normalize_date_yyyymmdd,
    resolve_default_target_date,
    to_dash_date,
)
from .calculator import evaluate_universe, prescreen_stock
from .compliance import LICENSE_AUDIT_SUMMARY, build_isolated_demo_snapshot
from .config import BJ_TZ, Settings, load_settings
from .minute_clients import BaoStockSession, IndependentMinuteClient, resolve_minute_window
from .pool import build_stock_pool
from .storage import StateStore, atomic_write_json, file_task_lock
from .summary_gen import build_business_summary
from .tushare_client import TushareClient, TushareError, build_daily_records


def determine_scan_status(funnels: dict[str, dict[str, int]]) -> tuple[str, str]:
    n = funnels["normal"]
    f = funnels["free"]
    unverified_total = n["unverified"] + f["unverified"]
    if n["pool_total"] == 0:
        return "waiting_update", "等待更新"
    if unverified_total == 0:
        if n["selected"] == 0 and f["selected"] == 0:
            return "complete_zero", "完整扫描后 0 只"
        return "complete", "完整覆盖"
    return "partial_coverage", "部分覆盖"


def collect_trade_date(
    trade_date: str | None = None,
    settings: Settings | None = None,
    force_refresh_minutes: bool = False,
    progress_every: int = 50,
) -> dict[str, Any]:
    """Run complete, resumable, idempotent collection for one trading date D.
    Always writes full verified snapshot to `data/private/days/YYYY-MM-DD.json`.
    Publishes to `web/data/` according to `settings.publish_mode` (`compliance_notice` vs `authorized_real`).
    """
    cfg = settings or load_settings()
    store = StateStore(cfg.db_path)

    with file_task_lock(cfg.lock_path):
        now_bj = datetime.now(BJ_TZ)
        ts_client = TushareClient(cfg.tushare_token)

        # 1. Fetch exchange calendar
        cal_start, cal_end = calendar_window(trade_date, lookback_days=90, lookahead_days=15)
        try:
            df_cal = ts_client.fetch_trade_cal(cal_start, cal_end)
            open_days = sorted(df_cal.loc[df_cal["is_open"] == 1, "cal_date"].astype(str).tolist())
            cal_source = "Tushare trade_cal (SSE)"
        except TushareError:
            with BaoStockSession(cfg.baostock_sleep_sec, cfg.baostock_max_retries) as bs_cal:
                open_days = bs_cal.fetch_trade_dates(cal_start, cal_end)
            cal_source = "BaoStock query_trade_dates"

        default_d = resolve_default_target_date(open_days, now_bj=now_bj)
        d = normalize_date_yyyymmdd(trade_date) if trade_date else default_d
        d_dash = to_dash_date(d)
        run_id = store.log_run_start(d, now_bj.isoformat())

        try:
            # 2. Build stock pool on target date D
            sb_df = ts_client.fetch_stock_basic_all()
            bse_map_df = ts_client.fetch_bse_mapping()
            pool, pool_excluded = build_stock_pool(sb_df, d, bse_mapping_df=bse_map_df)

            # 3. Fetch daily & daily_basic & suspend_d
            daily_df = ts_client.fetch_daily(d)
            daily_basic_df = ts_client.fetch_daily_basic(d)
            if len(daily_df) == 0 or len(daily_basic_df) == 0:
                msg = f"Target date {d_dash} daily data not yet available (daily={len(daily_df)}, daily_basic={len(daily_basic_df)})"
                store.log_run_finish(run_id, datetime.now(BJ_TZ).isoformat(), "waiting_update", len(pool), 0, 0, 0, 0, 0, msg)
                _update_manifests(cfg, open_days, default_d, store, pending_date=d_dash, pending_status="waiting_update")
                return {"trade_date": d_dash, "status": "waiting_update", "message": msg}

            suspended_codes = ts_client.fetch_suspend_d(d)
            daily_map = build_daily_records(d, daily_df, daily_basic_df, suspended_codes)

            # 4. Pre-screen union of normal & free calibers
            candidates = []
            for stock in pool:
                pre = prescreen_stock(stock, daily_map.get(stock.ts_code))
                if pre["needs_minute_fetch"]:
                    candidates.append(stock)

            print(
                f"[{d_dash}] Pool={len(pool)} (excluded={pool_excluded}), "
                f"daily={len(daily_df)}, daily_basic={len(daily_basic_df)}, "
                f"candidates_union={len(candidates)}"
            )

            # 5. Load or refresh company profile cache (refresh daily)
            today_dash = now_bj.strftime("%Y-%m-%d")
            company_cache, comp_fetched_date = store.load_company_cache()
            if not company_cache or comp_fetched_date != today_dash:
                company_cache = ts_client.fetch_stock_company_all()
                store.save_company_cache(company_cache, today_dash)
                comp_fetched_date = today_dash

            # 6. Check if official Tushare stk_auction_o is available
            tushare_auc_map = ts_client.fetch_open_auction_if_permitted(d)

            # 7. Resumable minute + opening auction fetch for candidates
            minute_map = {} if force_refresh_minutes else store.load_minute_cache(d)
            # Re-fetch entries that were missing_data previously
            to_fetch = [
                s
                for s in candidates
                if (s.ts_code not in minute_map) or (minute_map[s.ts_code].status == "missing_data")
            ]
            print(f"[{d_dash}] Cached minutes={len(minute_map)}, remaining to fetch={len(to_fetch)}")

            ind_client = IndependentMinuteClient(sleep_sec=cfg.alt_sleep_sec)
            t0 = time.time()
            with BaoStockSession(cfg.baostock_sleep_sec, cfg.baostock_max_retries) as bs_sess:
                for idx, stock in enumerate(to_fetch, start=1):
                    rec = daily_map[stock.ts_code]
                    res = resolve_minute_window(
                        stock=stock,
                        trade_date=d,
                        daily_rec=rec,
                        bs_session=bs_sess,
                        ind_client=ind_client,
                        tushare_auction_map=tushare_auc_map,
                    )
                    minute_map[stock.ts_code] = res
                    store.save_minute_result(res, datetime.now(BJ_TZ).isoformat())
                    if progress_every and (idx % progress_every == 0 or idx == len(to_fetch)):
                        elapsed = time.time() - t0
                        print(f"  [{d_dash}] Minute progress: {idx}/{len(to_fetch)} ({elapsed:.1f}s)")

            # 8. Build one-sentence summaries for candidates
            summary_map = {
                s.ts_code: build_business_summary(
                    company_cache.get(s.ts_code),
                    target_date_dash=d_dash,
                    fetched_date_dash=comp_fetched_date or today_dash,
                )
                for s in candidates
            }

            # 9. Evaluate full metrics & strict reconciliation
            evaluated = evaluate_universe(pool, daily_map, minute_map, summary_map)
            scan_status, scan_status_label = determine_scan_status(evaluated["funnels"])
            finished_iso = datetime.now(BJ_TZ).isoformat()

            snapshot = {
                "trade_date": d_dash,
                "trade_date_yyyymmdd": d,
                "is_synthetic_demo": False,
                "publish_mode": cfg.publish_mode,
                "scan_status": scan_status,
                "scan_status_label": scan_status_label,
                "generated_at": finished_iso,
                "calendar_source": cal_source,
                "pool_excluded_counts": pool_excluded,
                "candidates_union_count": len(candidates),
                "funnels": evaluated["funnels"],
                "board_funnels": evaluated["board_funnels"],
                "main_lists": evaluated["main_lists"],
                "raw_unverified_lists": evaluated["raw_unverified_lists"],
                "audit_not_selected_top": evaluated["audit_not_selected_top"],
                "api_stats": ts_client.api_stats,
                "methodology": {
                    "window": "09:30–09:45 连续竞价（不含 09:25 开盘集合竞价）",
                    "threshold": "全精度换手率 >= 10%（仅展示时四舍五入）",
                    "primary_minute_source_sh_sz": "BaoStock 15m (adjustflag=3, 结束时间 09:45)",
                    "auction_calibration_sh_sz": "Tushare stk_auction_o(若授权) -> 腾讯 m1 09:30 -> 东财 trends2 09:30",
                    "primary_minute_source_bse": "东财 trends2 09:31-09:45（直连，校验全天成交额）；历史兜底新浪 15m（入待核验）",
                    "tushare_stk_auction_o_available": tushare_auc_map is not None,
                },
            }

            # Always save full verified snapshot to private storage atomically
            priv_day_path = cfg.private_dir / "days" / f"{d_dash}.json"
            atomic_write_json(priv_day_path, snapshot)

            store.log_run_finish(
                run_id,
                finished_iso,
                scan_status,
                pool_total=len(pool),
                candidates_count=len(candidates),
                selected_normal=evaluated["funnels"]["normal"]["selected"],
                selected_free=evaluated["funnels"]["free"]["selected"],
                unverified_normal=evaluated["funnels"]["normal"]["unverified"],
                unverified_free=evaluated["funnels"]["free"]["unverified"],
                message=scan_status_label,
            )

            # Publish manifests & public files according to compliance mode
            _update_manifests(cfg, open_days, default_d, store)
            return snapshot

        except Exception as e:
            store.log_run_finish(run_id, datetime.now(BJ_TZ).isoformat(), "failed", message=str(e)[:400])
            raise


def _update_manifests(
    cfg: Settings,
    open_days: list[str],
    default_target_yyyymmdd: str,
    store: StateStore,
    pending_date: str | None = None,
    pending_status: str | None = None,
) -> None:
    """Update private manifest and publish web/data according to `cfg.publish_mode`."""
    now_iso = datetime.now(BJ_TZ).isoformat()
    default_dash = to_dash_date(default_target_yyyymmdd)
    recent_open_dash = [to_dash_date(x) for x in list_recent_closed_trading_days(open_days, count=20)]

    # Scan existing private day snapshots
    priv_days_dir = cfg.private_dir / "days"
    priv_days_dir.mkdir(parents=True, exist_ok=True)
    available_priv_dates = sorted(p.stem for p in priv_days_dir.glob("*.json"))
    latest_success_priv = available_priv_dates[-1] if available_priv_dates else None

    recent_runs = store.list_recent_runs(limit=10)
    priv_manifest = {
        "mode": "private_verified_real",
        "delivery_status": "私人环境真实验算完成",
        "generated_at": now_iso,
        "default_target_date": default_dash,
        "latest_success_date": latest_success_priv,
        "available_dates": list(reversed(available_priv_dates)),
        "recent_open_days": list(reversed(recent_open_dash)),
        "pending_date": pending_date,
        "pending_status": pending_status,
        "recent_runs": recent_runs,
        "license_audit": LICENSE_AUDIT_SUMMARY,
    }
    atomic_write_json(cfg.private_dir / "manifest.json", priv_manifest)

    # Now update web/data strictly according to compliance mode
    export_public_site_data(cfg, open_days=open_days, default_target_yyyymmdd=default_target_yyyymmdd, recent_runs=recent_runs)


def export_public_site_data(
    cfg: Settings,
    open_days: list[str] | None = None,
    default_target_yyyymmdd: str | None = None,
    recent_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Export `web/data/` strictly respecting `cfg.publish_mode`.
    - `compliance_notice`: NEVER writes unlicensed real market rows to `web/data/`;
      clears any stale real snapshots and publishes compliance notice + isolated 99xxxx demo fixture.
    - `authorized_real`: publishes verified real day snapshots to `web/data/days/`.
    """
    web_days_dir = cfg.web_data_dir / "days"
    web_days_dir.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(BJ_TZ).isoformat()

    priv_days_dir = cfg.private_dir / "days"
    priv_manifest_path = cfg.private_dir / "manifest.json"
    priv_manifest = {}
    if priv_manifest_path.exists():
        import json

        priv_manifest = json.loads(priv_manifest_path.read_text(encoding="utf-8"))

    default_dash = (
        to_dash_date(default_target_yyyymmdd)
        if default_target_yyyymmdd
        else priv_manifest.get("default_target_date", "2026-11-23")
    )

    if cfg.publish_mode == "authorized_real":
        # Copy validated real snapshots atomically to web/data/days/
        import json

        available: list[str] = []
        if priv_days_dir.exists():
            for p in sorted(priv_days_dir.glob("*.json")):
                data = json.loads(p.read_text(encoding="utf-8"))
                # Strip internal debug/audit fields not needed on public web
                data.pop("api_stats", None)
                atomic_write_json(web_days_dir / p.name, data)
                available.append(p.stem)
        manifest = {
            "mode": "authorized_real",
            "delivery_status": "已发布授权的真实榜单",
            "generated_at": now_iso,
            "default_target_date": default_dash,
            "latest_success_date": available[-1] if available else None,
            "available_dates": list(reversed(available)),
            "recent_runs": recent_runs or priv_manifest.get("recent_runs", []),
            "license_audit": LICENSE_AUDIT_SUMMARY,
        }
        atomic_write_json(cfg.web_data_dir / "manifest.json", manifest)
        return manifest

    # Default safe mode: compliance_notice
    # Remove any previously published files so zero unlicensed real data remains in web/data/days/
    for old_f in web_days_dir.glob("*.json"):
        old_f.unlink()

    demo_snap = build_isolated_demo_snapshot(demo_date_dash=default_dash)
    atomic_write_json(web_days_dir / f"{default_dash}.json", demo_snap)

    # Also sanitize recent_runs so no real stock details leak (run logs only have aggregate counts/status)
    safe_runs = [
        {
            "trade_date": r.get("trade_date"),
            "started_at": r.get("started_at"),
            "finished_at": r.get("finished_at"),
            "status": r.get("status"),
            "pool_total": r.get("pool_total"),
            "message": r.get("message"),
        }
        for r in (recent_runs or priv_manifest.get("recent_runs", []))[:5]
    ]

    manifest = {
        "mode": "compliance_notice",
        "delivery_status": "真实榜单待授权",
        "generated_at": now_iso,
        "default_target_date": default_dash,
        "latest_success_date": default_dash,
        "available_dates": [default_dash],
        "is_synthetic_demo": True,
        "recent_runs": safe_runs,
        "license_audit": LICENSE_AUDIT_SUMMARY,
    }
    atomic_write_json(cfg.web_data_dir / "manifest.json", manifest)
    return manifest
