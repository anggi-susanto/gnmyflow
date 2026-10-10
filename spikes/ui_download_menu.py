"""Spike: list the download options of an image tile and a video tile, at zero cost.

Usage:
    uv run python spikes/ui_download_menu.py PROJECT_ID [--profile NAME]

Opens each tile's more_vert menu, hovers the download entry and dumps the submenu:
icon, text, and disabled state. Nothing is clicked, so nothing is downloaded or upscaled.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from playwright.async_api import Page, async_playwright
from ui_attach2 import fresh

from studiowright import paths
from studiowright.browser import attach

OVERLAY = ".cdk-overlay-pane"

ITEMS_JS = r"""
() => [...document.querySelectorAll('.cdk-overlay-pane [role="menuitem"], .cdk-overlay-pane button')]
  .filter((el) => el.getBoundingClientRect().width > 0)
  .map((el) => ({
    icons: [...el.querySelectorAll('mat-icon')].map((i) => i.textContent.trim()),
    text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60),
    disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
  }))
"""


async def download_menu(page: Page, tile_selector: str) -> dict[str, Any]:
    tile = page.locator(tile_selector).filter(has=page.locator("img, video")).first
    if not await tile.count():
        return {"error": f"no {tile_selector}"}
    media = tile.locator("img, video").first
    await media.scroll_into_view_if_needed()
    await media.hover(force=True)
    await page.wait_for_timeout(300)
    await tile.locator("button:has(mat-icon:text-is('more_vert'))").first.click(force=True)
    entry = page.locator(f"{OVERLAY} [role='menuitem']:has(mat-icon:text-is('download'))").first
    await entry.wait_for(state="visible", timeout=5_000)
    await entry.hover()
    await page.wait_for_timeout(800)
    items = await page.evaluate(ITEMS_JS)
    await page.keyboard.press("Escape")
    await page.keyboard.press("Escape")
    return {"items": items}


async def run(project_id: str, profile: str) -> None:
    report: dict[str, Any] = {}
    url = f"https://flow.google.com/project/{project_id}"
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(url)
        try:
            await fresh(page, url)
            report["image"] = await download_menu(page, "flow-image-tile")
            await fresh(page, url)
            report["video"] = await download_menu(page, "flow-video-tile")
        finally:
            await session.detach()
    sys.stdout.reconfigure(encoding="utf-8")
    for kind in ("image", "video"):
        print(f"== {kind}")
        for item in report[kind].get("items", []):
            flag = " (disabled)" if item["disabled"] else ""
            print(f"   {item['icons']} {item['text']}{flag}")
        if "error" in report[kind]:
            print("   ", report[kind]["error"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.profile))


if __name__ == "__main__":
    main()
