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
    # BUILD_ARCH=arm64 | x86_64 | universal2 (macOS only). universal2 requires a
    # universal2 Python and universal2 builds of every compiled dependency.
    build_arch = os.environ.get("BUILD_ARCH", "").strip()

    launcher: list[str] = []
    arch_opts: list[str] = []
    if sys.platform == "darwin" and build_arch:
        arch_opts = ["--target-architecture", build_arch]
        # Pin the build process to the target slice for single-arch builds; not
        # applicable to universal2 (no such `arch` slice).
        if build_arch in ("arm64", "x86_64"):
            launcher = ["arch", f"-{build_arch}"]

    # Platform-native app icon (ignored by PyInstaller on Linux).
    icon_opts: list[str] = []
    if sys.platform == "darwin" and os.path.exists("assets/icon.icns"):
        icon_opts = ["--icon", "assets/icon.icns"]
    elif os.name == "nt" and os.path.exists("assets/icon.ico"):
        icon_opts = ["--icon", "assets/icon.ico"]

    opts = [
        "--noconfirm", "--clean", "--onefile",
        "--name", name,
        *icon_opts,
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
