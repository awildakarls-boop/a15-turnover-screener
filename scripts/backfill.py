"""CLI: Backfill multiple recent or specified trading days sequentially with shared lock."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a15_screener.calendar_utils import calendar_window, list_recent_closed_trading_days, normalize_date_yyyymmdd
from a15_screener.config import load_settings
from a15_screener.pipeline import collect_trade_date
from a15_screener.tushare_client import TushareClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill multiple trading days.")
    parser.add_argument("--days", type=int, default=3, help="Number of recent closed trading days to backfill")
    parser.add_argument("--dates", nargs="*", default=None, help="Explicit list of dates YYYYMMDD or YYYY-MM-DD")
    args = parser.parse_args()

    cfg = load_settings()
    if args.dates:
        target_dates = [normalize_date_yyyymmdd(d) for d in args.dates]
    else:
        ts_client = TushareClient(cfg.tushare_token)
        start, end = calendar_window(lookback_days=60, lookahead_days=5)
        df_cal = ts_client.fetch_trade_cal(start, end)
        open_days = sorted(df_cal.loc[df_cal["is_open"] == 1, "cal_date"].astype(str).tolist())
        target_dates = list_recent_closed_trading_days(open_days, count=args.days)

    print(f"Backfilling {len(target_dates)} dates: {target_dates}")
    for d in target_dates:
        collect_trade_date(trade_date=d, settings=cfg)


if __name__ == "__main__":
    main()
