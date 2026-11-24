"""Lightweight static read-only HTTP server for local preview & private real-data verification.
NEVER calls any market data API on page load or refresh.
"""

from __future__ import annotations

import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
PRIVATE_DATA_DIR = ROOT / "data" / "private"


class DualModeHandler(SimpleHTTPRequestHandler):
    data_override_dir: Path | None = None

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if self.data_override_dir is not None and clean.startswith("/data/"):
            rel = clean[len("/data/") :].lstrip("/")
            return str((self.data_override_dir / rel).resolve())
        return super().translate_path(path)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve static frontend locally (read-only snapshots).")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument(
        "--mode",
        choices=["public", "private"],
        default="public",
        help="'public' serves web/data/; 'private' serves data/private/ on localhost only for personal audit.",
    )
    args = parser.parse_args()

    class CustomHandler(DualModeHandler):
        data_override_dir = PRIVATE_DATA_DIR if args.mode == "private" else None

    handler = functools.partial(CustomHandler, directory=str(WEB_DIR))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving {WEB_DIR} on http://{args.host}:{args.port} (mode={args.mode}, read-only snapshots)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
