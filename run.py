#!/usr/bin/env python3
"""Cross-platform launcher: starts the local server and opens the UI in a browser.

Usage:  python run.py            # launches on http://127.0.0.1:8765 (or next free port)
        python run.py --port N   # custom port, --no-browser to skip auto-open

Designed to never die silently when double-clicked: if the chosen port is busy it
falls back to a free one, if our app is already running it just reopens the browser,
and any fatal error is written to a log file and (on macOS) shown in a dialog.
"""
from __future__ import annotations

import argparse
import socket
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from urllib.request import urlopen


def _log_path() -> Path:
    base = Path.home() / ".unifi-ai-config"
    base.mkdir(parents=True, exist_ok=True)
    return base / "launch.log"


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _our_app_at(host: str, port: int) -> bool:
    """True if THIS app is already serving on host:port (so we don't double-launch)."""
    try:
        with urlopen(f"http://{host}:{port}/api/version", timeout=1.5) as r:
            return r.status == 200 and b"version" in r.read()
    except Exception:
        return False


def _pick_port(host: str, desired: int) -> int:
    if _port_free(host, desired):
        return desired
    for p in range(desired + 1, desired + 50):
        if _port_free(host, p):
            return p
    return desired  # let uvicorn surface the bind error


def _report_fatal(exc: BaseException) -> None:
    msg = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        _log_path().write_text(msg)
    except Exception:
        pass
    short = f"{type(exc).__name__}: {exc}"
    print("\n  FATAL: " + short + "\n  See " + str(_log_path()) + "\n")
    # On macOS, surface a dialog so a double-clicked app doesn't just vanish.
    import sys
    if sys.platform == "darwin":
        try:
            import subprocess
            body = (f"UniFi AI Config Auditor could not start.\n\n{short}\n\n"
                    f"Details: {_log_path()}").replace('"', "'")
            subprocess.run(
                ["osascript", "-e",
                 f'display dialog "{body}" with title "UniFi AI Config Auditor" '
                 f'buttons {{"OK"}} with icon stop'],
                timeout=60, check=False,
            )
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="UniFi AI Config Auditor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    # If our app is already running on the desired port, just reopen the UI and exit
    # cleanly rather than crashing on a port conflict (common on a second launch).
    if _our_app_at(args.host, args.port):
        url = f"http://{args.host}:{args.port}"
        print(f"\n  Already running at {url} — reopening browser.\n")
        if not args.no_browser:
            webbrowser.open(url)
        return

    port = _pick_port(args.host, args.port)
    url = f"http://{args.host}:{port}"

    if not args.no_browser:
        def _open() -> None:
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    import uvicorn
    from backend.main import app

    print(f"\n  UniFi AI Config Auditor running at {url}\n  Press Ctrl+C to stop.\n")
    uvicorn.run(app, host=args.host, port=port, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except BaseException as exc:  # noqa: BLE001 - last-resort guard for double-click launches
        _report_fatal(exc)
        raise SystemExit(1)
