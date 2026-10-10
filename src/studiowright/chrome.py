"""Locate Google Chrome and run it as a plain, unautomated process for sign-in.

Google refuses sign-in in browsers driven by automation, so the profile is signed in
through an ordinary Chrome process. Afterwards Playwright opens the same profile with the
same Chrome binary, which can read the session that sign-in stored.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

FLOW_URL = "https://flow.google.com/"


def find_chrome() -> Path | None:
    """The installed Google Chrome executable, or None."""
    candidates: list[Path] = []
    if sys.platform == "win32":
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    else:
        for name in ("google-chrome", "google-chrome-stable"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    return next((path for path in candidates if path.is_file()), None)


def sign_in_command(chrome: Path, profile: Path, url: str = FLOW_URL) -> list[str]:
    """Arguments for a plain Chrome window on ``profile`` that opens ``url``."""
    return [
        str(chrome),
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]


def run_sign_in(chrome: Path, profile: Path, url: str = FLOW_URL) -> int:
    """Open Chrome for a manual sign-in and wait until the user closes it."""
    profile.mkdir(parents=True, exist_ok=True)
    return subprocess.run(sign_in_command(chrome, profile, url), check=False).returncode
