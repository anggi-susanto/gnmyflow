"""One real, headed Chrome on a signed-in StudioWright profile, driven through Playwright."""

from __future__ import annotations

from pathlib import Path

from playwright.async_api import BrowserContext, Playwright

#: Keep Chrome looking like the ordinary browser the profile was signed in with.
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]
IGNORE_DEFAULT_ARGS = ["--enable-automation"]


async def open_profile(playwright: Playwright, profile: Path) -> BrowserContext:
    """Launch system Chrome (headed) on ``profile``. Fails if the profile was never signed in."""
    if not profile.is_dir():
        raise FileNotFoundError(f"profile {profile} does not exist; run `sw auth login` first")
    return await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        channel="chrome",
        headless=False,
        args=LAUNCH_ARGS,
        ignore_default_args=IGNORE_DEFAULT_ARGS,
        no_viewport=True,
    )
