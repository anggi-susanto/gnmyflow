"""Where StudioWright keeps its data."""

from __future__ import annotations

import os
import re
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "studiowright"
DEFAULT_PROFILE = "default"
HOME_ENV = "STUDIOWRIGHT_HOME"

_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def home() -> Path:
    """The data directory: profiles, database, captures. ``STUDIOWRIGHT_HOME`` overrides it."""
    override = os.environ.get(HOME_ENV, "").strip()
    if override:
        return Path(override)
    return Path(user_data_dir(APP_NAME, appauthor=False, ensure_exists=False))


def profile_dir(name: str = DEFAULT_PROFILE) -> Path:
    """The Chrome user-data-dir for one signed-in profile."""
    if not _PROFILE_NAME.match(name):
        raise ValueError(f"invalid profile name {name!r}: use letters, digits, '-' or '_'")
    return home() / "profiles" / name


def output_dir() -> Path:
    """Where generated media is saved unless a command says otherwise."""
    override = os.environ.get("STUDIOWRIGHT_OUTPUT", "").strip()
    if override:
        return Path(override)
    return Path.home() / "Downloads" / APP_NAME


def staging_dir() -> Path:
    """Uniquely named copies of local files, made just before they are uploaded."""
    return home() / "staging"


def locks_dir() -> Path:
    return home() / "locks"


def captures_dir() -> Path:
    """Raw wire captures. They hold session material, so they never leave this directory."""
    return home() / "captures"
