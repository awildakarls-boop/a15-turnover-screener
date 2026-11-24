"""CLI: Collect one trading day (default: strictly previous open day in Asia/Shanghai)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a15_screener.config import load_settings
from a15_screener.pipeline import collect_trade_date


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect A-share opening 15-min turnover screening data.")
    parser.add_argument("--date", type=str, default=None, help="Target trade date YYYYMMDD or YYYY-MM-DD (default: D)")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cached minute bars and re-fetch")
    parser.add_argument(
        "--publish-mode",
        type=str,
        default=None,
        choices=["compliance_notice", "authorized_real"],
        help="Override A15_PUBLISH_MODE",
    )
    args = parser.parse_args()

    cfg = load_settings()
    if args.publish_mode:
        cfg = replace(cfg, publish_mode=args.publish_mode)

    snap = collect_trade_date(trade_date=args.date, settings=cfg, force_refresh_minutes=args.force_refresh)
    if "funnels" in snap:
        fn = snap["funnels"]
        print(
            f"\nDone [{snap['trade_date']}] status={snap['scan_status']} ({snap['scan_status_label']})\n"
            f"  Normal: pool={fn['normal']['pool_total']}, susp={fn['normal']['suspended']}, "
            f"excl={fn['normal']['prescreen_excluded']}, not_sel={fn['normal']['verified_not_selected']}, "
            f"selected={fn['normal']['selected']}, unverified={fn['normal']['unverified']}\n"
            f"  Free  : pool={fn['free']['pool_total']}, susp={fn['free']['suspended']}, "
            f"excl={fn['free']['prescreen_excluded']}, not_sel={fn['free']['verified_not_selected']}, "
            f"selected={fn['free']['selected']}, unverified={fn['free']['unverified']}"
        )
    else:
        print(snap)


if __name__ == "__main__":
    main()
