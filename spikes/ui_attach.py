"""Spike: compare ways to attach a reference image to the prompt box, at zero cost.

Usage:
    uv run python spikes/ui_attach.py PROJECT_ID [--profile NAME]

The project must already hold at least one image. Each technique is timed from the first
action to a new attachment appearing in the prompt box, and the box is cleared between
techniques:

- ``plus``: the prompt box's own add button, then pick the first asset;
- ``tile``: the tile's more_vert menu, entries dumped, then the add-to-prompt entry if one
  is recognisable by its icon;
- ``mention``: type ``@``, wait for options, press Enter;
- ``drop``: a trusted CDP file drop of a local PNG onto the prompt box. Flow may upload it,
  which is free.

Nothing is submitted. Only passive listeners are used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from flow_probe import tiny_png
from playwright.async_api import Page, Response, async_playwright

from studiowright import paths
from studiowright.browser import attach

READY = ".settings-trigger-button"
PROMPT_BOX = "flow-prompt-box"
COMPOSER = "flow-prompt-box [contenteditable='true']"

#: Attachments in the prompt box: any image or chip-like element inside it.
ATTACHMENTS_JS = r"""
() => {
  const box = document.querySelector('flow-prompt-box');
  if (!box) return { images: -1, chips: -1 };
  const chips = [...box.querySelectorAll('[class*="chip"]')].filter((el) => !el.classList.contains('agent-mode-chip'));
  return {
    images: box.querySelectorAll('img').length,
    chips: chips.length,
    chipClasses: chips.slice(0, 4).map((c) => c.className.toString().slice(0, 60)),
  };
}
"""

MENU_JS = r"""
() => [...document.querySelectorAll('.cdk-overlay-pane [role="menuitem"], .cdk-overlay-pane button')]
  .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0; })
  .map((el) => ({ icons: [...el.querySelectorAll('mat-icon')].map((i) => i.textContent.trim()),
                  text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
                  cls: el.className.toString().slice(0, 60) }))
"""


async def attachments(page: Page) -> dict[str, Any]:
    return await page.evaluate(ATTACHMENTS_JS)


async def wait_for_more(page: Page, before: dict[str, Any], timeout_s: float) -> float | None:
    started = time.time()
    while time.time() - started < timeout_s:
        now = await attachments(page)
        if now["images"] > before["images"] or now["chips"] > before["chips"]:
            return round(time.time() - started, 2)
        await asyncio.sleep(0.05)
    return None


async def clear_box(page: Page) -> None:
    await page.keyboard.press("Escape")
    await page.locator(COMPOSER).first.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await page.keyboard.press("Backspace")
    await page.wait_for_timeout(300)


async def try_plus(page: Page) -> dict[str, Any]:
    before = await attachments(page)
    started = time.time()
    await page.locator(f"{PROMPT_BOX} button:has(mat-icon:text-is('add'))").first.click()
    option = page.locator("button.asset-item[role='option'], [role='option']").first
    await option.wait_for(state="visible", timeout=8_000)
    opened = round(time.time() - started, 2)
    menu = await page.evaluate(MENU_JS)
    await option.click()
    confirm = page.locator("button.detail-add-to-prompt-btn").first
    try:
        await confirm.wait_for(state="visible", timeout=1_500)
        await confirm.click()
        needed_confirm = True
    except Exception:
        needed_confirm = False
    attached = await wait_for_more(page, before, 6)
    return {
        "opened_s": opened,
        "needed_confirm": needed_confirm,
        "attached_after_s": attached,
        "total_s": round(time.time() - started, 2),
        "popover": menu[:12],
    }


async def try_tile(page: Page) -> dict[str, Any]:
    tile = page.locator("flow-image-tile").first
    await tile.hover()
    await tile.locator("button:has(mat-icon:text-is('more_vert'))").first.click()
    await page.wait_for_timeout(500)
    entries = await page.evaluate(MENU_JS)
    await page.keyboard.press("Escape")
    return {"menu_entries": entries}


async def try_mention(page: Page) -> dict[str, Any]:
    before = await attachments(page)
    await page.locator(COMPOSER).first.click()
    started = time.time()
    await page.keyboard.type("@")
    option = page.locator("[role='option']").first
    await option.wait_for(state="visible", timeout=8_000)
    opened = round(time.time() - started, 2)
    await page.keyboard.press("Enter")
    attached = await wait_for_more(page, before, 6)
    return {
        "options_after_s": opened,
        "attached_after_s": attached,
        "total_s": round(time.time() - started, 2),
    }


async def try_drop(page: Page, image: Path, uploads: list[float]) -> dict[str, Any]:
    before = await attachments(page)
    box = await page.locator(PROMPT_BOX).first.bounding_box()
    if box is None:
        return {"error": "prompt box has no bounding box"}
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    cdp = await page.context.new_cdp_session(page)
    data = {"items": [], "files": [str(image)], "dragOperationsMask": 1}
    started = time.time()
    uploads.clear()
    for kind in ("dragEnter", "dragOver", "drop"):
        await cdp.send("Input.dispatchDragEvent", {"type": kind, "x": x, "y": y, "data": data})
    attached = await wait_for_more(page, before, 10)
    await cdp.detach()
    return {
        "attached_after_s": attached,
        "upload_seen_after_s": uploads[0] if uploads else None,
        "total_s": round(time.time() - started, 2),
        "dialog": await page.locator("[role='dialog']").count(),
    }


async def run(project_id: str, profile: str) -> None:
    report: dict[str, Any] = {}
    image = tiny_png(paths.captures_dir() / "probe-green.png", rgb=(40, 160, 60))
    uploads: list[float] = []
    t0 = time.time()

    def on_response(response: Response) -> None:
        if "rpcids=maseQ" in response.url:
            uploads.append(round(time.time() - t0, 2))

    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        url = f"https://flow.google.com/project/{project_id}"
        page = await session.page_for(url)
        page.on("response", on_response)
        try:
            if not page.url.startswith(url):
                await page.goto(url, wait_until="domcontentloaded")
            await page.locator(READY).first.wait_for(state="visible", timeout=30_000)
            report["tiles"] = await page.locator("flow-image-tile").count()
            for name, action in (("plus", try_plus), ("mention", try_mention), ("tile", try_tile)):
                await clear_box(page)
                try:
                    report[name] = await action(page)
                except Exception as exc:
                    report[name] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            await clear_box(page)
            t0 = time.time()
            try:
                report["drop"] = await try_drop(page, image, uploads)
            except Exception as exc:
                report["drop"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            await clear_box(page)
            report["box_after_clear"] = await attachments(page)
        finally:
            page.remove_listener("response", on_response)
            await session.detach()
    out = paths.captures_dir() / f"ui-attach-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False)[:6000])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.profile))


if __name__ == "__main__":
    main()
