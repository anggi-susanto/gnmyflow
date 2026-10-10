"""Spike: download the newest clip of a project without regenerating it (zero cost).

Usage:
    uv run python spikes/clip_fetch.py PROJECT_ID [--profile NAME]

Finds the newest video tile's media id, opens its edit route (the page then asks
``as29s`` for fresh signed URLs), parses that reply properly and downloads the mp4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from typing import Any

from playwright.async_api import Response, async_playwright

from studiowright import paths
from studiowright.browser import attach
from studiowright.flow import batchexecute as be

READY = ".settings-trigger-button"


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in strings(item)]
    return []


async def run(project_id: str, profile: str) -> None:
    report: dict[str, Any] = {}
    url = f"https://flow.google.com/project/{project_id}"
    replies: list[str] = []
    tasks: list[asyncio.Future[None]] = []

    async def read(response: Response) -> None:
        replies.append(await response.text())

    def on_response(response: Response) -> None:
        if "rpcids=as29s" in response.url:
            tasks.append(asyncio.ensure_future(read(response)))

    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(url)
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await page.locator(READY).first.wait_for(state="visible", timeout=30_000)
            tile = page.locator("flow-video-tile").first
            await tile.wait_for(state="visible", timeout=10_000)
            page.on("response", on_response)
            started = time.time()
            await tile.click()
            await page.wait_for_url(re.compile(r".*/edit/[0-9a-f-]{36}.*"), timeout=10_000)
            media_id = page.url.split("/edit/")[1].split("?")[0].split("/")[0]
            report["media_id"] = media_id
            deadline = time.time() + 20
            while not replies and time.time() < deadline:
                await asyncio.sleep(0.1)
            await asyncio.sleep(2.0)
            await asyncio.gather(*tasks, return_exceptions=True)
            report["replies"] = len(replies)
            report["https_strings"] = sorted(
                {
                    (u.split("?")[0][:90] + ("?..." if "?" in u else ""))
                    for text in replies
                    for u in strings(be.parse_reply(text, "as29s"))
                    if u.startswith("https://")
                }
            )[:20]
            report["as29s_after_s"] = round(time.time() - started, 2)
            video_urls: list[str] = []
            for text in replies:
                payload = be.parse_reply(text, "as29s")
                video_urls += [
                    s
                    for s in strings(payload)
                    if s.startswith("https://flow-content.google/video/")
                ]
            report["video_urls"] = len(video_urls)
            if video_urls:
                response = await page.context.request.get(video_urls[0])
                body = await response.body()
                path = paths.captures_dir() / f"clip-{media_id[:8]}.mp4"
                path.write_bytes(body)
                report["download"] = {
                    "http": response.status,
                    "bytes": len(body),
                    "mp4": body[4:8] == b"ftyp",
                    "file": str(path),
                }
            await page.goto(url, wait_until="domcontentloaded")
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
