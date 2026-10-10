"""Spike: composer clearing, picker identity and multi-file upload (free, no generation).

Usage:
    uv run python spikes/ui_basics.py PROJECT_ID [--profile NAME]

1. Attach two references through the tile menu, then press the prompt box's own close
   button. Does it remove every chip and the text?
2. Open the Frames end-slot picker and dump each asset item's attributes. Does an item
   carry a media id or an image source we can match against the grid?
3. Upload two new PNGs in one Ctrl+U file chooser, then record every maseQ reply (media
   id and display name) and the time. Uploads are free.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from flow_probe import tiny_png
from playwright.async_api import Response, async_playwright
from ui_attach2 import BOX, counts, fresh
from ui_video_explore import OVERLAY, choose

from studiowright import paths
from studiowright.browser import attach
from studiowright.flow import batchexecute as be

ITEMS_JS = r"""
() => [...document.querySelectorAll('.cdk-overlay-pane .asset-item')].map((el) => ({
  attrs: Object.fromEntries([...el.attributes].map((a) => [a.name, a.value.slice(0, 80)])),
  img: (el.querySelector('img') || {}).src ? el.querySelector('img').src.slice(0, 90) : null,
  imgAttrs: el.querySelector('img') ? Object.fromEntries([...el.querySelector('img').attributes].map((a) => [a.name, a.value.slice(0, 60)])) : null,
  text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
}))
"""


async def run(project_id: str, profile: str) -> None:
    report: dict[str, Any] = {}
    url = f"https://flow.google.com/project/{project_id}"
    uploads: list[dict[str, Any]] = []
    tasks: list[asyncio.Future[None]] = []
    t0 = time.time()

    async def read(response: Response) -> None:
        try:
            payload = be.parse_reply(await response.text(), "maseQ")
            uploads.append(
                {
                    "t": round(time.time() - t0, 2),
                    "media_id": payload[0][0],
                    "name": payload[1][3][0] if len(payload) > 1 else None,
                }
            )
        except Exception as exc:
            uploads.append({"t": round(time.time() - t0, 2), "error": str(exc)[:120]})

    def on_response(response: Response) -> None:
        if "rpcids=maseQ" in response.url:
            tasks.append(asyncio.ensure_future(read(response)))

    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(url)
        try:
            # 1. clearing
            await fresh(page, url)
            await choose(page, "image")
            for nth in (0, 1):
                img = page.locator("flow-image-tile img[data-media-id]").nth(nth)
                tile = img.locator("xpath=ancestor::flow-image-tile[1]")
                await img.hover()
                await tile.locator("button:has(mat-icon:text-is('more_vert'))").first.click()
                entry = page.locator(f"{OVERLAY} [role='menuitem']:has(mat-icon:text-is('add_2'))")
                await entry.first.click()
                await page.wait_for_timeout(400)
            await page.locator(f"{BOX} [contenteditable='true']").first.click()
            await page.keyboard.insert_text("some text")
            report["before_clear"] = await counts(page)
            started = time.time()
            await page.locator(f"{BOX} button:has(mat-icon:text-is('close'))").first.click()
            await page.wait_for_timeout(500)
            report["after_close_button"] = await counts(page)
            report["text_after"] = (
                await page.locator(f"{BOX} [contenteditable='true']").first.inner_text()
            ).strip()
            report["clear_s"] = round(time.time() - started, 2)

            # 2. picker identity
            await choose(page, "videocam", "crop_free")
            await page.locator(f"{BOX} button.empty-chip").first.click()
            await page.locator(f"{OVERLAY} .asset-item").first.wait_for(
                state="visible", timeout=5_000
            )
            report["picker_items"] = (await page.evaluate(ITEMS_JS))[:3]
            report["grid_ids"] = await page.evaluate(
                "() => [...document.querySelectorAll('flow-image-tile img[data-media-id]')].slice(0,3)"
                ".map(i => [i.dataset.mediaId, i.src.slice(0, 90)])"
            )
            await page.keyboard.press("Escape")
            await choose(page, "image")

            # 3. multi-file upload
            files = [
                str(tiny_png(paths.captures_dir() / f"sw-probe-{name}.png", rgb=rgb))
                for name, rgb in (("yellow", (230, 200, 40)), ("purple", (120, 50, 160)))
            ]
            page.on("response", on_response)
            t0 = time.time()
            async with page.expect_file_chooser(timeout=5_000) as chooser_info:
                await page.keyboard.press("Control+U")
            await (await chooser_info.value).set_files(files)
            deadline = time.time() + 30
            while len(uploads) < len(files) and time.time() < deadline:
                await asyncio.sleep(0.1)
            await asyncio.gather(*tasks, return_exceptions=True)
            report["uploads"] = uploads
            report["dialog_open"] = await page.locator("[role='dialog']").count()
        finally:
            page.remove_listener("response", on_response)
            await session.detach()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.profile))


if __name__ == "__main__":
    main()
