"""Spike: bind start and end frames through the Frames slots' picker, at zero cost.

Usage:
    uv run python spikes/ui_endframe.py PROJECT_ID START_ASSET END_ASSET [--profile NAME]

Uses the tile menu's Animate entry for the start frame, then the empty end slot's picker
(select the asset, then the picker's add button). Nothing is submitted; the composer goes
back to image mode at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from playwright.async_api import async_playwright
from ui_attach2 import BOX, fresh, shot
from ui_video_explore import BOX_JS, OVERLAY, POPOVER_JS, animate, choose

from studiowright import paths
from studiowright.browser import attach

PICKER_ADD = f"{OVERLAY} button.detail-add-to-prompt-btn"


async def run(project_id: str, start: str, end: str, profile: str) -> None:
    report: dict[str, Any] = {}
    url = f"https://flow.google.com/project/{project_id}"
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(url)
        try:
            await fresh(page, url)
            report["animate"] = {
                k: v for k, v in (await animate(page, start)).items() if k in ("s", "chip")
            }
            slot = page.locator(f"{BOX} button.empty-chip").first
            started = time.time()
            await slot.click()
            option = page.locator(f"{OVERLAY} .asset-item").filter(has_text=end)
            await option.first.wait_for(state="visible", timeout=5_000)
            await option.first.click()
            add = page.locator(PICKER_ADD)
            report["picker_add_found"] = await add.count()
            if not report["picker_add_found"]:
                report["picker"] = await page.evaluate(POPOVER_JS)
                classes = await page.evaluate(
                    "() => [...document.querySelectorAll('.cdk-overlay-pane button')].map(b => b.className.toString())"
                )
                report["picker_button_classes"] = classes
            else:
                await add.first.click()
            bound = page.locator(f"{BOX} button.chip-container:has(img)")
            deadline = time.time() + 6
            while await bound.count() < 2 and time.time() < deadline:
                await asyncio.sleep(0.05)
            report["end_bound_s"] = (
                round(time.time() - started, 2) if await bound.count() >= 2 else None
            )
            report["box"] = await page.evaluate(BOX_JS)
            report["shot"] = await shot(page, "endframe")
            await fresh(page, url)
            await choose(page, "image")
        finally:
            await session.detach()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False)[:5000])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id")
    parser.add_argument("start")
    parser.add_argument("end")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.start, args.end, args.profile))


if __name__ == "__main__":
    main()
