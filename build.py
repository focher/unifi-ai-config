#!/usr/bin/env python3
"""Build a standalone single-file executable with PyInstaller.

Run on each target OS to produce its native binary:
    python build.py

Output lands in dist/ as `unifi-ai-auditor` (or .exe on Windows).
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys

SEP = ";" if os.name == "nt" else ":"  # PyInstaller --add-data separator


def main() -> None:
    name = "unifi-ai-auditor"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", name,
        "--add-data", f"frontend{SEP}frontend",
        # FastAPI/uvicorn pull these in dynamically; pin them so the bundle is complete.
        "--collect-submodules", "uvicorn",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        "run.py",
    ]
    print(f"Building {name} for {platform.system()} {platform.machine()}…")
    subprocess.check_call(args)
    print("\nDone. Binary is in ./dist/")


if __name__ == "__main__":
    main()
