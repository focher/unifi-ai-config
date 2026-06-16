#!/usr/bin/env python3
"""Self-contained launcher for the UniFi AI Config Auditor.

By default it starts the bundled server and shows the UI in a NATIVE desktop
window (WKWebView on macOS, WebView2 on Windows, WebKitGTK on Linux) — no browser
tab. If no native webview backend is available it falls back to the default browser.

Flags:
  --no-browser   headless: just serve and block (used by CI / remote use)
  --browser      force the browser instead of a native window
  --port N       preferred port (falls back to a free one if busy)
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

APP_TITLE = "UniFi AI Config Auditor"


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
    return desired


def _wait_ready(url: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _our_app_at(*_split(url)):
            return True
        time.sleep(0.2)
    return False


def _split(url: str) -> tuple[str, int]:
    rest = url.split("://", 1)[-1]
    host, _, port = rest.partition(":")
    return host, int(port or 80)


def _serve_in_thread(host: str, port: int):
    """Run uvicorn in a daemon thread; return the Server so it can be stopped."""
    import uvicorn
    from backend.main import app

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    return server


def _report_fatal(exc: BaseException) -> None:
    msg = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        _log_path().write_text(msg)
    except Exception:
        pass
    short = f"{type(exc).__name__}: {exc}"
    print("\n  FATAL: " + short + "\n  See " + str(_log_path()) + "\n")
    import sys
    if sys.platform == "darwin":
        try:
            import subprocess
            body = (f"{APP_TITLE} could not start.\n\n{short}\n\n"
                    f"Details: {_log_path()}").replace('"', "'")
            subprocess.run(
                ["osascript", "-e",
                 f'display dialog "{body}" with title "{APP_TITLE}" '
                 f'buttons {{"OK"}} with icon stop'],
                timeout=60, check=False,
            )
        except Exception:
            pass


def _run_windowed(url: str) -> bool:
    """Show the UI in a native window. Returns False if no backend is available."""
    try:
        import webview
    except Exception:
        return False
    try:
        webview.create_window(APP_TITLE, url, width=1240, height=880, min_size=(960, 660))
        # http_server stays in our uvicorn thread; this blocks until the window closes.
        webview.start()
        return True
    except Exception as exc:  # backend present but failed to start (e.g. no display)
        try:
            _log_path().write_text("".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)))
        except Exception:
            pass
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true",
                        help="headless: serve only, no window or browser")
    parser.add_argument("--browser", action="store_true",
                        help="use the default browser instead of a native window")
    args = parser.parse_args()

    # Already running? Just focus the existing instance and exit.
    if _our_app_at(args.host, args.port):
        url = f"http://{args.host}:{args.port}"
        print(f"\n  Already running at {url}.\n")
        if not args.no_browser:
            webbrowser.open(url)
        return

    port = _pick_port(args.host, args.port)
    url = f"http://{args.host}:{port}"

    # Headless mode (CI / remote): serve on the main thread and block.
    if args.no_browser:
        import uvicorn
        from backend.main import app
        print(f"\n  {APP_TITLE} running at {url}\n  Press Ctrl+C to stop.\n")
        uvicorn.run(app, host=args.host, port=port, log_level="warning")
        return

    server = _serve_in_thread(args.host, port)
    if not _wait_ready(url):
        raise RuntimeError("Server did not become ready in time.")
    print(f"\n  {APP_TITLE} running at {url}\n")

    shown = False if args.browser else _run_windowed(url)
    if not shown:
        # Fall back to a browser tab and keep the server alive.
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    server.should_exit = True


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except BaseException as exc:  # noqa: BLE001 - last-resort guard for double-click launches
        _report_fatal(exc)
        raise SystemExit(1)
