"""Spike: see what a submit would send, without it reaching Google (zero cost).

Usage:
    uv run python spikes/ui_offline_submit.py PROJECT_ID ASSET_NAME [--profile NAME]

For each attach technique (tile menu, @ mention):

1. reload the project;
2. attach ASSET_NAME;
3. type a prompt;
4. switch the browser context OFFLINE;
5. click generate.

The request fails inside Chrome before it leaves the machine. Its body is read through a
passive ``request`` listener, and the context goes back online. Nothing is intercepted or
modified.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from playwright.async_api import Page, Request, async_playwright
from ui_attach2 import BOX, COMPOSER, fresh, mention, shot, tile_menu

from studiowright import paths
from studiowright.browser import attach
from studiowright.flow import batchexecute as be


def summarize(value: Any, limit: int = 70) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else f"<str len={len(value)}>"
    if isinstance(value, list):
        return [summarize(v, limit) for v in value]
    return value


async def offline_submit(page: Page, prompt: str) -> dict[str, Any]:
    seen: list[Request] = []

    def on_request(request: Request) -> None:
        if "batchexecute" in request.url and "ogiZ0b" in request.url:
            seen.append(request)

    page.on("request", on_request)
    try:
        await page.locator(COMPOSER).first.click()
        await page.keyboard.press("End")
        await page.keyboard.insert_text(" " + prompt)
        await page.wait_for_timeout(300)
        await page.context.set_offline(True)
        await page.locator(f"{BOX} button:has(mat-icon:text-is('arrow_forward'))").first.click()
        deadline = time.time() + 8
        while not seen and time.time() < deadline:
            await asyncio.sleep(0.05)
        await page.wait_for_timeout(500)
    finally:
        await page.context.set_offline(False)
        page.remove_listener("request", on_request)
    if not seen:
        return {"request": None}
    (call,) = be.decode_envelope(seen[0].post_data or "")
    return {"payload": summarize(json.loads(call[1]))}


async def run(project_id: str, asset: str, profile: str) -> None:
    report: dict[str, Any] = {}
    url = f"https://flow.google.com/project/{project_id}"
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(url)
        try:
            for name, technique in (("tile", tile_menu), ("mention", mention)):
                await fresh(page, url)
                report[name] = await technique(page, asset)
                await page.wait_for_timeout(1500)
                report[f"{name}_shot"] = await shot(page, f"{name}-settled")
                report[f"{name}_submit"] = await offline_submit(page, "in the same style")
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
