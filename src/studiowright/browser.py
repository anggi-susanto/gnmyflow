"""Attach Playwright to the user's own Chrome running on a StudioWright profile.

Chrome is started as an ordinary process (see :mod:`studiowright.chrome`) and Playwright
connects over CDP with ``no_defaults``, so it adds no emulation of its own. Detaching only
disconnects: Chrome keeps running with its tabs, ready for the next job.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
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

    async def window_id(self, page: Page) -> int:
        cdp = await self.context.new_cdp_session(page)
        try:
            return int((await cdp.send("Browser.getWindowForTarget"))["windowId"])
        finally:
            await cdp.detach()

    async def _alone(self, page: Page) -> bool:
        """True when no other tab shares this tab's window."""
        mine = await self.window_id(page)
        for other in self.context.pages:
            if other is not page and await self.window_id(other) == mine:
                return False
        return True

    async def page_for(self, url_prefix: str) -> Page:
        """A tab for ``url_prefix`` in a window of its own.

        Chrome renders only the active tab of each window, and a page that does not render
        never settles, so clicks on it time out. Flow and Flow Music therefore each get
        their own window; a tab that shares a window is replaced by one in a new window.
        """
        for stale in [p for p in self.context.pages if p.url.startswith("chrome-error://")]:
            if len(self.context.pages) > 1:
                await stale.close()
        origin = "/".join(url_prefix.split("/")[:3])
        page = (
            self.find_page(url_prefix)
            or self.find_page(origin)
            or next((p for p in self.context.pages if p.url in ("about:blank", "")), None)
        )
        if page is not None:
            if await self._alone(page):
                return page
            await page.close()
        return await self._new_window()

    async def _new_window(self) -> Page:
        bounds: dict[str, Any] = {}
        if self.context.pages:
            cdp = await self.context.new_cdp_session(self.context.pages[0])
            try:
                bounds = (await cdp.send("Browser.getWindowForTarget"))["bounds"]
            finally:
                await cdp.detach()
        target: dict[str, Any] = {"url": "about:blank", "newWindow": True}
        if bounds.get("windowState", "normal") == "normal":
            # Open where the existing window is (off-screen when hidden), at its size.
            target.update({k: bounds[k] for k in ("left", "top", "width", "height") if k in bounds})
        browser_cdp = await self.browser.new_browser_cdp_session()
        try:
            async with self.context.expect_page() as created:
                await browser_cdp.send("Target.createTarget", target)
            return await created.value
        finally:
            await browser_cdp.detach()

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
    """Show StudioWright's windows on screen, or keep them running off-screen."""
    done: set[int] = set()
    for tab in [page, *[p for p in session.context.pages if p is not page]]:
        cdp, window_id, bounds = await _window_bounds(session, tab)
        try:
            if window_id in done:
                continue
            done.add(window_id)
            if bounds.get("windowState") != "normal":
                await cdp.send(
                    "Browser.setWindowBounds",
                    {"windowId": window_id, "bounds": {"windowState": "normal"}},
                )
            left = (ONSCREEN_LEFT + 40 * (len(done) - 1)) if visible else OFFSCREEN_LEFT
            await cdp.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"left": left, "top": 40}},
            )
        finally:
            await cdp.detach()


async def capture_download(
    session: ChromeSession,
    trigger: Callable[[], Awaitable[None]],
    folder: Path,
    timeout_s: float = 900,
) -> Path:
    """Run ``trigger`` and save the download it starts into ``folder``.

    Chrome was not launched by Playwright, so Playwright's download events do not fire.
    Chrome's own download events are used instead, for the duration of this call only.
    """
    folder.mkdir(parents=True, exist_ok=True)
    cdp = await session.browser.new_browser_cdp_session()
    names: dict[str, str] = {}
    finished: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    def on_begin(event: dict[str, Any]) -> None:
        names[event["guid"]] = event.get("suggestedFilename") or event["guid"]

    def on_progress(event: dict[str, Any]) -> None:
        if finished.done() or event.get("guid") not in names:
            return
        if event.get("state") == "completed":
            finished.set_result(event["guid"])
        elif event.get("state") == "canceled":
            finished.set_exception(RuntimeError("Chrome canceled the download"))

    cdp.on("Browser.downloadWillBegin", on_begin)
    cdp.on("Browser.downloadProgress", on_progress)
    await cdp.send(
        "Browser.setDownloadBehavior",
        {"behavior": "allowAndName", "downloadPath": str(folder), "eventsEnabled": True},
    )
    try:
        await trigger()
        guid = await asyncio.wait_for(finished, timeout=timeout_s)
        target = folder / names[guid]
        number = 2
        while target.exists():
            target = folder / f"{Path(names[guid]).stem}-{number}{Path(names[guid]).suffix}"
            number += 1
        (folder / guid).rename(target)
        return target
    finally:
        await cdp.send("Browser.setDownloadBehavior", {"behavior": "default"})
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
