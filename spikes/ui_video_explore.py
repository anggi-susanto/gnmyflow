"""Spike: map the video composer's frame and ingredient slots, at zero cost.

Usage:
    uv run python spikes/ui_video_explore.py PROJECT_ID ASSET_NAME [--profile NAME]

Steps, each from a freshly loaded project:

1. ``animate``: the tile menu's ``motion_blur`` entry ("Animate"). Record the settings
   chip, the prompt box slots, and a screenshot.
2. ``frames``: switch to video → Frames. Dump the empty slots, open the first one, dump
   the picker, choose ASSET_NAME, and time the bind.
3. ``ingredients``: switch to video → Ingredients and attach ASSET_NAME via the tile menu.

At the end the composer goes back to image mode. Nothing is submitted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from playwright.async_api import Locator, Page, async_playwright
from ui_attach2 import BOX, fresh, shot, tile_menu

from studiowright import paths
from studiowright.browser import attach

READY = ".settings-trigger-button"
OVERLAY = ".cdk-overlay-pane"

BOX_JS = r"""
() => {
  const box = document.querySelector('flow-prompt-box');
  if (!box) return null;
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(),
    cls: el.className.toString().split(' ').filter(Boolean).slice(0, 4).join('.'),
    icons: [...el.querySelectorAll('mat-icon')].map((i) => i.textContent.trim()),
    text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
    img: !!el.querySelector('img'),
  });
  return {
    buttons: [...box.querySelectorAll('button')].map(describe),
    chips: [...box.querySelectorAll('[class*="chip"]')].filter((c) => !c.classList.contains('agent-mode-chip')).map(describe).slice(0, 10),
  };
}
"""

POPOVER_JS = r"""
() => [...document.querySelectorAll('.cdk-overlay-pane button, .cdk-overlay-pane [role="option"]')]
  .filter((el) => el.getBoundingClientRect().width > 0)
  .map((el) => ({ cls: el.className.toString().split(' ').slice(0, 3).join('.'),
                  icons: [...el.querySelectorAll('mat-icon')].map((i) => i.textContent.trim()),
                  text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40) }))
"""


async def open_pane(page: Page) -> Locator:
    pane = page.locator(f"{OVERLAY}:has([role='radiogroup'])")
    for _ in range(4):
        if await pane.count():
            return pane.first
        await page.mouse.move(5, 5)
        await page.locator(READY).first.click()
        try:
            await pane.first.wait_for(state="visible", timeout=1_500)
        except Exception:
            await page.wait_for_timeout(300)
    return pane.first


async def choose(page: Page, *icons: str) -> None:
    pane = await open_pane(page)
    for icon in icons:
        radio = pane.locator("[role='radio']").filter(
            has=page.locator(f"mat-icon:text-is('{icon}')")
        )
        await radio.first.click()
        await page.wait_for_timeout(200)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)


async def chip_text(page: Page) -> str:
    return " | ".join((await page.locator(READY).first.inner_text()).split("\n"))


async def animate(page: Page, asset: str) -> dict[str, Any]:
    tile = page.locator("flow-image-tile").filter(has_text=asset).first
    started = time.time()
    await tile.hover()
    await tile.locator("button:has(mat-icon:text-is('more_vert'))").first.click()
    entry = page.locator(f"{OVERLAY} [role='menuitem']:has(mat-icon:text-is('motion_blur'))")
    await entry.first.wait_for(state="visible", timeout=5_000)
    await entry.first.click()
    await page.wait_for_timeout(1500)
    return {
        "s": round(time.time() - started, 2),
        "chip": await chip_text(page),
        "box": await page.evaluate(BOX_JS),
        "submenu": await page.evaluate(POPOVER_JS),
    }


async def frames(page: Page, asset: str) -> dict[str, Any]:
    await choose(page, "videocam", "crop_free")
    result: dict[str, Any] = {
        "chip": await chip_text(page),
        "box_before": await page.evaluate(BOX_JS),
    }
    slot = page.locator(f"{BOX} button.empty-chip").first
    if not await slot.count():
        result["error"] = "no empty slot"
        return result
    started = time.time()
    await slot.click()
    await page.wait_for_timeout(600)
    result["picker"] = await page.evaluate(POPOVER_JS)
    option = page.locator(f"{OVERLAY} [role='option'], {OVERLAY} button").filter(has_text=asset)
    if await option.count():
        await option.first.click()
        await page.wait_for_timeout(400)
        result["after_pick"] = await page.evaluate(POPOVER_JS)
        confirm = page.locator(f"{OVERLAY} button").filter(
            has=page.locator("mat-icon:text-is('add_2')")
        )
        if await confirm.count():
            await confirm.first.click()
            result["confirmed"] = True
    bound = page.locator(f"{BOX} button.chip-container:has(img)")
    try:
        await bound.first.wait_for(state="visible", timeout=6_000)
        result["bound_s"] = round(time.time() - started, 2)
    except Exception:
        result["bound_s"] = None
    result["box_after"] = await page.evaluate(BOX_JS)
    return result


async def ingredients(page: Page, asset: str) -> dict[str, Any]:
    await choose(page, "videocam", "chrome_extension")
    result: dict[str, Any] = {"chip": await chip_text(page), "attach": await tile_menu(page, asset)}
    result["box_after"] = await page.evaluate(BOX_JS)
    return result


async def run(project_id: str, asset: str, profile: str) -> None:
    report: dict[str, Any] = {}
    url = f"https://flow.google.com/project/{project_id}"
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(url)
        try:
            for name, step in (
                ("animate", animate),
                ("frames", frames),
                ("ingredients", ingredients),
            ):
                await fresh(page, url)
                try:
                    report[name] = await step(page, asset)
                except Exception as exc:
                    report[name] = {"error": f"{type(exc).__name__}: {str(exc)[:240]}"}
                report[f"{name}_shot"] = await shot(page, f"video-{name}")
            await fresh(page, url)
            await choose(page, "image")
            report["chip_at_end"] = await chip_text(page)
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
