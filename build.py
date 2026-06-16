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
    # On macOS, set BUILD_ARCH=arm64|x86_64 to force a single-architecture build.
    # The x86_64 slice is produced by running PyInstaller under Rosetta (`arch
    # -x86_64`) so its bootloader and bundled deps are x86_64, even on Apple Silicon.
    build_arch = os.environ.get("BUILD_ARCH", "").strip()

    launcher: list[str] = []
    arch_opts: list[str] = []
    if sys.platform == "darwin" and build_arch:
        launcher = ["arch", f"-{build_arch}"]
        arch_opts = ["--target-architecture", build_arch]

    opts = [
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
        *arch_opts,
    ]
    args = [*launcher, sys.executable, "-m", "PyInstaller", *opts, "run.py"]

    target = build_arch or platform.machine()
    print(f"Building {name} for {platform.system()} {target}…")
    subprocess.check_call(args)
    print("\nDone. Binary is in ./dist/")


if __name__ == "__main__":
    main()
