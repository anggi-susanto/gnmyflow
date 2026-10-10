"""Locate Google Chrome and run it as an ordinary process on a StudioWright profile.

Chrome is never launched by Playwright. Playwright's own launch adds dozens of switches
and a debugging pipe that marks the page as automated (``navigator.webdriver``). Instead
StudioWright starts the user's installed Chrome with nothing but a profile directory and,
for automation, a fixed loopback debugging port, then attaches to it over CDP. A fixed
non-zero port does not set ``navigator.webdriver``.

Sign-in runs without the debugging port: Google refuses sign-in in browsers it considers
controlled by software.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

FLOW_URL = "https://flow.google.com/"
PORT_BASE = 9300
PORT_SPAN = 90


class ChromeError(RuntimeError):
    """Chrome could not be started or reached."""


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


def require_chrome() -> Path:
    exe = find_chrome()
    if exe is None:
        raise ChromeError("Google Chrome was not found; install it first.")
    return exe


def debug_port(profile_name: str) -> int:
    """A stable loopback port per profile, so several profiles can run side by side."""
    return PORT_BASE + zlib.crc32(profile_name.encode("utf-8")) % PORT_SPAN


def _base_args(chrome: Path, profile: Path) -> list[str]:
    return [
        str(chrome),
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def sign_in_command(chrome: Path, profile: Path, url: str = FLOW_URL) -> list[str]:
    """Arguments for a plain Chrome window on ``profile`` that opens ``url``."""
    return [*_base_args(chrome, profile), url]


#: Keep the page running at full speed while its window sits behind other windows or is
#: minimized. Without these, Windows' occlusion tracking marks the page hidden: animation
#: frames stop (so UI actions stall) and the page's own timers, such as Flow's 5 s video
#: status poll, are throttled. They change scheduling only; pages cannot read them.
KEEP_RUNNING_FLAGS = (
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
)


def automation_command(chrome: Path, profile: Path, port: int, url: str = FLOW_URL) -> list[str]:
    """Chrome on ``profile`` with a loopback debugging port and the keep-running flags."""
    return [
        *_base_args(chrome, profile),
        f"--remote-debugging-port={port}",
        *KEEP_RUNNING_FLAGS,
        url,
    ]


def run_sign_in(chrome: Path, profile: Path, url: str = FLOW_URL) -> int:
    """Open Chrome for a manual sign-in and wait until the user closes it."""
    profile.mkdir(parents=True, exist_ok=True)
    return subprocess.run(sign_in_command(chrome, profile, url), check=False).returncode


def endpoint_version(port: int, timeout_s: float = 1.0) -> dict[str, str] | None:
    """Chrome's ``/json/version`` on ``port``, or None when nothing answers there."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout_s
        ) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def ensure_running(profile: Path, port: int, *, timeout_s: float = 20.0) -> dict[str, str]:
    """Reuse the Chrome listening on ``port`` or start one on ``profile``; return its version."""
    version = endpoint_version(port)
    if version:
        return version
    if not profile.is_dir():
        raise ChromeError(f"profile {profile} does not exist; run `sw auth login` first")
    subprocess.Popen(
        automation_command(require_chrome(), profile, port),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        version = endpoint_version(port)
        if version:
            return version
        time.sleep(0.25)
    raise ChromeError(
        "Chrome did not open its debugging port. If a StudioWright Chrome window is already "
        "open (for example from `sw auth login`), close it and try again."
    )
