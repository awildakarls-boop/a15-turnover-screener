"""CLI: Export web/data/ snapshots according to publish mode (compliance_notice vs authorized_real)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a15_screener.config import load_settings
from a15_screener.pipeline import export_public_site_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Export static site JSON snapshots according to license mode.")
    parser.add_argument(
        "--mode",
        type=str,
        default=None,
        choices=["compliance_notice", "authorized_real"],
        help="Publish mode",
    )
    args = parser.parse_args()
    cfg = load_settings()
    if args.mode:
        cfg = replace(cfg, publish_mode=args.mode)
    manifest = export_public_site_data(cfg)
    print(f"Exported web/data in mode={manifest['mode']} ({manifest['delivery_status']}), dates={manifest['available_dates']}")


if __name__ == "__main__":
    main()
