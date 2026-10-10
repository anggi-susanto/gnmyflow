"""Attach Playwright to the user's own Chrome running on a StudioWright profile.

Chrome is started as an ordinary process (see :mod:`studiowright.chrome`) and Playwright
connects over CDP with ``no_defaults``, so it adds no emulation of its own. Detaching only
disconnects: Chrome keeps running with its tabs, ready for the next job.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright

from studiowright import chrome, paths


@dataclass
class ChromeSession:
    browser: Browser
    context: BrowserContext
    port: int

    def find_page(self, url_prefix: str) -> Page | None:
        return next((p for p in self.context.pages if p.url.startswith(url_prefix)), None)

    async def page_for(self, url_prefix: str) -> Page:
        """A tab whose URL starts with ``url_prefix``, else a blank or new tab."""
        page = self.find_page(url_prefix)
        if page is not None:
            return page
        blank = next((p for p in self.context.pages if p.url in ("about:blank", "")), None)
        return blank or await self.context.new_page()

    async def detach(self) -> None:
        """Disconnect Playwright. Chrome and its tabs stay open."""
        await self.browser.close()


#: Off-screen but not minimized: Chrome keeps rendering the page there.
OFFSCREEN_LEFT = -3000
ONSCREEN_LEFT = 60


async def _window_bounds(session: ChromeSession, page: Page) -> tuple[Any, int, dict[str, Any]]:
    cdp = await session.context.new_cdp_session(page)
    window = await cdp.send("Browser.getWindowForTarget")
    return cdp, window["windowId"], window["bounds"]


async def ensure_rendering(session: ChromeSession, page: Page) -> None:
    """A minimized window freezes the page; un-minimize it off-screen instead."""
    cdp, window_id, bounds = await _window_bounds(session, page)
    try:
        if bounds.get("windowState") == "minimized":
            await cdp.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "normal"}},
            )
            await cdp.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"left": OFFSCREEN_LEFT, "top": 40}},
            )
    finally:
        await cdp.detach()


async def place_window(session: ChromeSession, page: Page, *, visible: bool) -> None:
    """Show the StudioWright window on screen, or keep it running off-screen."""
    cdp, window_id, bounds = await _window_bounds(session, page)
    try:
        if bounds.get("windowState") != "normal":
            await cdp.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "normal"}},
            )
        left = ONSCREEN_LEFT if visible else OFFSCREEN_LEFT
        await cdp.send(
            "Browser.setWindowBounds", {"windowId": window_id, "bounds": {"left": left, "top": 40}}
        )
    finally:
        await cdp.detach()


async def attach(playwright: Playwright, profile: str = paths.DEFAULT_PROFILE) -> ChromeSession:
    """Start (or reuse) Chrome on ``profile`` and connect to it."""
    port = chrome.debug_port(profile)
    await asyncio.to_thread(chrome.ensure_running, paths.profile_dir(profile), port)
    browser = await playwright.chromium.connect_over_cdp(
        f"http://127.0.0.1:{port}", no_defaults=True
    )
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    return ChromeSession(browser=browser, context=context, port=port)
