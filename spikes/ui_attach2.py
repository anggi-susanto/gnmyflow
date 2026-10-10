"""Spike: time each reference-attach technique precisely, at zero cost.

Usage:
    uv run python spikes/ui_attach2.py PROJECT_ID ASSET_NAME [--profile NAME]

ASSET_NAME is the caption of an image already in the project (e.g. ``probe-red.png``).
Before each technique the page is reloaded, so the prompt box starts empty. A screenshot
of the prompt box is taken after each technique. Bound reference chips are counted with
``flow-prompt-box button.chip-container:has(img)`` and inline mentions with
``.mention-chip``.

Nothing is submitted; only passive listeners are used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from playwright.async_api import Page, async_playwright

from studiowright import paths
from studiowright.browser import attach

READY = ".settings-trigger-button"
BOX = "flow-prompt-box"
COMPOSER = f"{BOX} [contenteditable='true']"
BOUND = f"{BOX} button.chip-container:has(img)"
MENTION = f"{BOX} .mention-chip"


async def counts(page: Page) -> dict[str, int]:
    return {
        "bound": await page.locator(BOUND).count(),
        "mention": await page.locator(MENTION).count(),
    }


async def wait_for_new(page: Page, before: dict[str, int], timeout_s: float = 8.0) -> float | None:
    started = time.time()
    while time.time() - started < timeout_s:
        now = await counts(page)
        if now["bound"] > before["bound"] or now["mention"] > before["mention"]:
            return round(time.time() - started, 2)
        await asyncio.sleep(0.03)
    return None


async def fresh(page: Page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    await page.locator(READY).first.wait_for(state="visible", timeout=30_000)
    await page.wait_for_timeout(300)


async def shot(page: Page, name: str) -> str:
    path = paths.captures_dir() / f"attach-{name}-{time.strftime('%H%M%S')}.png"
    await page.locator(BOX).first.screenshot(path=str(path))
    return str(path)


async def plus(page: Page, asset: str) -> dict[str, Any]:
    before = await counts(page)
    started = time.time()
    await page.locator(f"{BOX} button:has(mat-icon:text-is('add'))").first.click()
    item = page.locator(".cdk-overlay-pane [role='option'], .cdk-overlay-pane button").filter(
        has_text=asset
    )
    await item.first.wait_for(state="visible", timeout=8_000)
    listed = round(time.time() - started, 2)
    await item.first.click()
    add = page.locator(".cdk-overlay-pane button:has(mat-icon:text-is('add_2'))")
    used_add_button = False
    if await add.count():
        await add.first.click()
        used_add_button = True
    attached = await wait_for_new(page, before)
    return {
        "listed_s": listed,
        "used_add_button": used_add_button,
        "attached_s": attached,
        "total_s": round(time.time() - started, 2),
        "after": await counts(page),
    }


async def tile_menu(page: Page, asset: str) -> dict[str, Any]:
    before = await counts(page)
    tile = page.locator("flow-image-tile").filter(has_text=asset).first
    if not await tile.count():
        tile = page.locator("flow-image-tile").last
    started = time.time()
    await tile.hover()
    await tile.locator("button:has(mat-icon:text-is('more_vert'))").first.click()
    entry = page.locator(".cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('add_2'))")
    await entry.first.wait_for(state="visible", timeout=5_000)
    await entry.first.click()
    attached = await wait_for_new(page, before)
    return {
        "attached_s": attached,
        "total_s": round(time.time() - started, 2),
        "after": await counts(page),
    }


async def mention(page: Page, asset: str) -> dict[str, Any]:
    before = await counts(page)
    await page.locator(COMPOSER).first.click()
    started = time.time()
    await page.keyboard.type("@")
    options = page.locator("[role='option']")
    await options.first.wait_for(state="visible", timeout=8_000)
    opened = round(time.time() - started, 2)
    await page.keyboard.type(asset.split(".")[0][:12], delay=20)
    target = options.filter(has_text=asset)
    await target.first.wait_for(state="visible", timeout=8_000)
    await target.first.click()
    attached = await wait_for_new(page, before)
    return {
        "options_s": opened,
        "attached_s": attached,
        "total_s": round(time.time() - started, 2),
        "after": await counts(page),
    }


async def run(project_id: str, asset: str, profile: str) -> None:
    report: dict[str, Any] = {}
    url = f"https://flow.google.com/project/{project_id}"
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(url)
        try:
            for name, technique in (("plus", plus), ("tile", tile_menu), ("mention", mention)):
                await fresh(page, url)
                report[f"{name}_baseline"] = await counts(page)
                try:
                    report[name] = await technique(page, asset)
                except Exception as exc:
                    report[name] = {"error": f"{type(exc).__name__}: {str(exc)[:240]}"}
                report[f"{name}_shot"] = await shot(page, name)
            await fresh(page, url)
        finally:
            await session.detach()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id")
    parser.add_argument("asset")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.asset, args.profile))


if __name__ == "__main__":
    main()
