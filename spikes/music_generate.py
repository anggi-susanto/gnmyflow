"""Spike: one real Flow Music generation through the compose panel, observed passively.

Usage:
    uv run python spikes/music_generate.py [--profile NAME] [--length 0:30] [--bpm 90]

Fills the compose panel (instrumental, sound description, BPM, length, title) and clicks
Generate. Every /__api request and JSON reply is recorded through passive listeners until
an audio URL appears. Costs Flow Music credits for one generation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from typing import Any

from playwright.async_api import Request, Response, async_playwright

from studiowright import paths
from studiowright.browser import attach

ROOT = "https://www.flowmusic.app"
AUDIO = re.compile(r"https://[^\"'\s]+\.(?:mp3|wav|m4a)[^\"'\s]*")


def field_after(label: str) -> str:
    return f"xpath=//*[normalize-space(text())='{label}']/following::input[1]"


async def run(args: argparse.Namespace) -> None:
    events: list[dict[str, Any]] = []
    audio_urls: dict[str, float] = {}
    t0 = time.time()
    tasks: list[asyncio.Future[None]] = []

    def on_request(request: Request) -> None:
        if "/__api" in request.url:
            body = request.post_data or ""
            events.append(
                {
                    "t": round(time.time() - t0, 2),
                    "dir": "req",
                    "method": request.method,
                    "path": request.url.split("/__api")[1][:100],
                    "body": body[:1500],
                }
            )

    async def read(response: Response) -> None:
        try:
            text = await response.text()
        except Exception:
            text = ""
        for url in AUDIO.findall(text):
            audio_urls.setdefault(url, round(time.time() - t0, 2))
        events.append(
            {
                "t": round(time.time() - t0, 2),
                "dir": "res",
                "status": response.status,
                "path": response.url.split("/__api")[1][:100],
                "body": text[:3000],
            }
        )

    def on_response(response: Response) -> None:
        if "/__api" in response.url:
            tasks.append(asyncio.ensure_future(read(response)))

    async with async_playwright() as playwright:
        session = await attach(playwright, args.profile)
        page = await session.page_for(ROOT)
        try:
            await page.goto(ROOT + "/session", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            sound = page.locator("textarea[aria-label='Sound description']")
            if not await sound.is_visible():
                await page.get_by_role("button", name="Toggle compose panel").click()
            await sound.wait_for(state="visible", timeout=10_000)
            instrumental = page.locator("button[aria-label='Toggle instrumental mode']")
            if await instrumental.get_attribute("aria-checked") != "true":
                await instrumental.click()
            advanced = page.locator("button[aria-label='Toggle advanced sound mode']")
            if await advanced.get_attribute("aria-checked") != "true":
                await advanced.click()
            await sound.fill(args.sound)
            await page.locator(field_after("BPM")).fill(str(args.bpm))
            length = page.locator(field_after("Length"))
            await length.fill(args.length)
            await length.press("Tab")
            await page.wait_for_timeout(300)
            details = page.locator("button[aria-label='Expand Details section']")
            if await details.count() and await details.get_attribute("aria-expanded") == "false":
                await details.click()
            title = page.locator(
                "textarea[placeholder*='title' i], input[placeholder*='title' i]"
            ).first
            if await title.count():
                await title.fill("StudioWright test")
            readback = {
                "instrumental": await instrumental.get_attribute("aria-checked"),
                "bpm": await page.locator(field_after("BPM")).input_value(),
                "length": await length.input_value(),
            }
            print("form:", readback, flush=True)
            page.on("request", on_request)
            page.on("response", on_response)
            t0 = time.time()
            await page.get_by_role("button", name="Generate").click()
            deadline = time.time() + args.timeout
            while time.time() < deadline and len(audio_urls) < 1:
                await asyncio.sleep(1)
            await asyncio.sleep(8)
            await asyncio.gather(*tasks, return_exceptions=True)
            shot = paths.captures_dir() / "music-after-generate.png"
            await page.screenshot(path=str(shot))
        finally:
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)
            await session.detach()
    out = paths.captures_dir() / f"music-generate-{time.strftime('%H%M%S')}.json"
    out.write_text(json.dumps({"events": events, "audio": audio_urls}, indent=1), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for e in events:
        if e["dir"] == "req":
            print(f"{e['t']:>7} REQ {e['method']} {e['path']}  {e['body'][:160]}")
        else:
            print(f"{e['t']:>7} RES {e['status']} {e['path']}  {e['body'][:160]}")
    print("audio urls:", {u.split("?")[0][-60:]: t for u, t in audio_urls.items()})
    print("raw:", out, "| shot:", shot)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    parser.add_argument(
        "--sound",
        default="calm documentary underscore, soft piano and warm strings, gentle, no drums",
    )
    parser.add_argument("--bpm", type=int, default=80)
    parser.add_argument("--length", default="0:30")
    parser.add_argument("--timeout", type=float, default=300)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
