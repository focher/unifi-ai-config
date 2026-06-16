#!/usr/bin/env python3
"""Cross-platform launcher: starts the local server and opens the UI in a browser.

Usage:  python run.py            # launches on http://127.0.0.1:8765
        python run.py --port N   # custom port, --no-browser to skip auto-open
"""
from __future__ import annotations

import argparse
import threading
import time
import webbrowser


def main() -> None:
    parser = argparse.ArgumentParser(description="UniFi AI Config Auditor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    import uvicorn
    from backend.main import app

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        def _open() -> None:
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    print(f"\n  UniFi AI Config Auditor running at {url}\n  Press Ctrl+C to stop.\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
