"""Spike: real image generations through the UI, observed passively (costs images only).

Usage:
    uv run python spikes/ui_generate.py PROJECT_ID ASSET_NAME [--profile NAME] [--gap 45]

Runs two generations, one per attach technique (tile menu, then @ mention), each with
ASSET_NAME as the reference, ``--gap`` seconds apart. For each it records timings, whether
the reference id appears in the outgoing body (read from a passive ``request`` listener),
the reply's image URLs, and a download of each image.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from typing import Any

from playwright.async_api import Page, Request, Response, async_playwright
from ui_attach2 import BOX, COMPOSER, fresh, mention, tile_menu

from studiowright import paths
from studiowright.browser import attach
from studiowright.flow import batchexecute as be

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


async def generate(page: Page, prompt: str, ref_media_id: str | None) -> dict[str, Any]:
    marks: dict[str, float] = {}
    bodies: dict[str, str] = {}
    tasks: list[asyncio.Future[None]] = []

    def on_request(request: Request) -> None:
        if "rpcids=ogiZ0b" in request.url and "request" not in marks:
            marks["request"] = time.time()
            bodies["post"] = request.post_data or ""

    async def read(response: Response) -> None:
        bodies["reply"] = await response.text()
        marks["reply"] = time.time()

    def on_response(response: Response) -> None:
        if "rpcids=ogiZ0b" in response.url and "reply_seen" not in marks:
            marks["reply_seen"] = time.time()
            tasks.append(asyncio.ensure_future(read(response)))

    page.on("request", on_request)
    page.on("response", on_response)
    try:
        await page.locator(COMPOSER).first.click()
        await page.keyboard.press("End")
        await page.keyboard.insert_text(prompt)
        await page.wait_for_timeout(250)
        marks["click"] = time.time()
        await page.locator(f"{BOX} button:has(mat-icon:text-is('arrow_forward'))").first.click()
        deadline = time.time() + 120
        while "reply" not in marks and time.time() < deadline:
            await asyncio.sleep(0.1)
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)
    result: dict[str, Any] = {}
    if "request" in marks:
        result["click_to_request_s"] = round(marks["request"] - marks["click"], 2)
        (call,) = be.decode_envelope(bodies["post"])
        inner = call[1]
        result["ref_in_body"] = ref_media_id in inner if ref_media_id else None
        result["uuids_in_body"] = len(set(UUID.findall(inner)))
    if "reply" in marks:
        result["request_to_reply_s"] = round(marks["reply"] - marks["request"], 2)
        try:
            reply = be.parse_reply(bodies["reply"], "ogiZ0b")
            urls = sorted(
                set(re.findall(r"https://flow-content\.google/image/[^\"\\]+", json.dumps(reply)))
            )
            result["images"] = len(urls)
            result["urls"] = urls
        except be.RpcError as exc:
            result["refused"] = {"code": exc.code, "reasons": exc.reasons}
    return result


async def download(page: Page, url: str, name: str) -> dict[str, Any]:
    started = time.time()
    response = await page.context.request.get(url)
    body = await response.body()
    kind = "png" if body[:4] == b"\x89PNG" else "jpg" if body[:2] == b"\xff\xd8" else "bin"
    path = paths.captures_dir() / f"gen-{name}-{time.strftime('%H%M%S')}.{kind}"
    path.write_bytes(body)
    return {
        "http": response.status,
        "bytes": len(body),
        "s": round(time.time() - started, 2),
        "file": str(path),
    }


async def media_id_for(page: Page, asset: str) -> str | None:
    tile = page.locator("flow-image-tile").filter(has_text=asset).first
    img = tile.locator("img[data-media-id]").first
    return await img.get_attribute("data-media-id") if await img.count() else None


async def run(project_id: str, asset: str, profile: str, gap: float) -> None:
    report: dict[str, Any] = {}
    url = f"https://flow.google.com/project/{project_id}"
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(url)
        try:
            for index, (name, technique, prompt) in enumerate(
                (
                    (
                        "tile",
                        tile_menu,
                        "a red ceramic bowl matching the colour of the reference, flat 2D",
                    ),
                    (
                        "mention",
                        mention,
                        " a red kite in the sky with the colour of the reference, flat 2D",
                    ),
                )
            ):
                if index:
                    await asyncio.sleep(gap)
                await fresh(page, url)
                ref_id = await media_id_for(page, asset)
                started = time.time()
                attach_result = await technique(page, asset)
                await page.wait_for_timeout(800)
                gen = await generate(page, prompt, ref_id)
                gen["attach_s"] = attach_result.get("total_s")
                gen["end_to_end_s"] = round(time.time() - started, 2)
                gen["downloads"] = [await download(page, u, name) for u in gen.pop("urls", [])]
                gen["ref_media_id"] = ref_id
                report[name] = gen
        finally:
            await session.detach()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id")
    parser.add_argument("asset")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    parser.add_argument("--gap", type=float, default=45.0)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.asset, args.profile, args.gap))


if __name__ == "__main__":
    main()
