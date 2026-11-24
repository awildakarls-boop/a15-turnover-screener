"""Application configuration. Secrets are strictly read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

BJ_TZ = ZoneInfo("Asia/Shanghai")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    tushare_token: str
    publish_mode: str  # "compliance_notice" | "authorized_real"
    data_dir: Path
    private_dir: Path
    cache_dir: Path
    web_dir: Path
    web_data_dir: Path
    db_path: Path
    lock_path: Path
    baostock_sleep_sec: float
    baostock_max_retries: int
    alt_sleep_sec: float


def load_settings(root: Path | None = None) -> Settings:
    base = root or PROJECT_ROOT
    data_dir = base / "data"
    web_dir = base / "web"
    return Settings(
        tushare_token=os.environ.get("TUSHARE_TOKEN", "").strip(),
        publish_mode=os.environ.get("A15_PUBLISH_MODE", "compliance_notice").strip() or "compliance_notice",
        data_dir=data_dir,
        private_dir=data_dir / "private",
        cache_dir=data_dir / "cache",
        web_dir=web_dir,
        web_data_dir=web_dir / "data",
        db_path=data_dir / "state.db",
        lock_path=data_dir / "collector.lock",
        baostock_sleep_sec=float(os.environ.get("BAOSTOCK_SLEEP_SEC", "0.08")),
        baostock_max_retries=int(os.environ.get("BAOSTOCK_MAX_RETRIES", "3")),
        alt_sleep_sec=float(os.environ.get("ALT_SLEEP_SEC", "0.15")),
    )
