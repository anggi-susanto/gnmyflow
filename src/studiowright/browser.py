"""Attach Playwright to the user's own Chrome running on a StudioWright profile.

Chrome is started as an ordinary process (see :mod:`studiowright.chrome`) and Playwright
connects over CDP with ``no_defaults``, so it adds no emulation of its own. Detaching only
disconnects: Chrome keeps running with its tabs, ready for the next job.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

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


async def attach(playwright: Playwright, profile: str = paths.DEFAULT_PROFILE) -> ChromeSession:
    """Start (or reuse) Chrome on ``profile`` and connect to it."""
    port = chrome.debug_port(profile)
    await asyncio.to_thread(chrome.ensure_running, paths.profile_dir(profile), port)
    browser = await playwright.chromium.connect_over_cdp(
        f"http://127.0.0.1:{port}", no_defaults=True
    )
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    return ChromeSession(browser=browser, context=context, port=port)
