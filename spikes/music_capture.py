"""Spike: record what Flow Music does on the wire for one UI action.

Usage:
    uv run python spikes/music_capture.py mp3 [--profile NAME]
    uv run python spikes/music_capture.py video-simple
    uv run python spikes/music_capture.py chat "Brainstorm four short lines about a lighthouse"
    uv run python spikes/music_capture.py upload path/to/audio.wav
    uv run python spikes/music_capture.py trim

Each action is what a person would do in the UI. Every /__api request (method, path, body
excerpt) and reply (status, body excerpt) is saved to the captures folder, and downloads
are saved to the captures folder too. ``mp3``, ``video-simple`` and ``trim`` act on the
newest song in the library.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from playwright.async_api import Page, Request, Response, async_playwright

from studiowright import paths
from studiowright.browser import ChromeSession, attach, capture_download, ensure_rendering

ROOT = "https://www.flowmusic.app"


async def song_menu(page: Page, *path: str) -> None:
    await page.locator("button[aria-label^='More options for']").first.click()
    await page.wait_for_timeout(400)
    for index, name in enumerate(path):
        item = page.get_by_role("menuitem", name=name, exact=True)
        await item.wait_for(state="visible", timeout=5000)
        await item.focus()
        await page.keyboard.press("ArrowRight" if index < len(path) - 1 else "Enter")
        await page.wait_for_timeout(400)


async def act(session: ChromeSession, page: Page, action: str, arg: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    folder = paths.captures_dir() / "music-downloads"
    if action in ("mp3", "video-simple", "trim"):
        await page.goto(ROOT + "/library/my-songs", wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)
    if action == "mp3":

        async def trigger() -> None:
            await song_menu(page, "Download", "MP3")

        out["file"] = str(await capture_download(session, trigger, folder, timeout_s=120))
    elif action == "video-simple":

        async def trigger() -> None:
            await song_menu(page, "Download", "Download video")
            await page.wait_for_timeout(1000)
            await page.get_by_text("Simple", exact=True).click()

        out["file"] = str(await capture_download(session, trigger, folder, timeout_s=300))
    elif action == "trim":
        if arg:
            await page.goto(f"{ROOT}/song/{arg}", wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)
        await song_menu(page, "Remix", "Trim")
        await page.wait_for_timeout(1200)
        dialog = page.locator("[role='dialog']")
        inputs = dialog.locator("input:not([type='checkbox'])")
        await inputs.nth(0).fill("0:05")
        await inputs.nth(0).press("Enter")
        await inputs.nth(1).fill("0:35")
        await inputs.nth(1).press("Enter")
        await page.wait_for_timeout(500)
        await dialog.get_by_role("button", name="Trim", exact=True).click()
        await page.wait_for_timeout(15000)
    elif action == "chat":
        await page.goto(ROOT + "/session", wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)
        box = page.locator("textarea[aria-label='Chat message']")
        await box.fill(arg or "Hello")
        await page.get_by_role("button", name="Send message").click()
        await page.wait_for_timeout(45000)
        out["text"] = " ".join(
            (await page.evaluate("() => document.querySelector('main').innerText")).split()
        )[:2000]
        out["url"] = page.url
    elif action == "upload":
        await page.goto(ROOT + "/session", wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)
        await page.get_by_role("button", name="Add audio or image").click()
        await page.wait_for_timeout(500)
        await page.get_by_role("menuitem").filter(has_text="Audio").first.click()
        await page.wait_for_timeout(800)
        agree = page.get_by_role("button", name="I agree")
        if await agree.count():
            # The file is one this account generated, so the rights notice holds.
            await agree.click()
            await page.wait_for_timeout(800)
        await page.keyboard.press("Escape")
        await page.locator("input[type='file'][accept^='audio']").set_input_files(arg or "")
        await page.wait_for_timeout(20000)
        out["text"] = " ".join(
            (await page.evaluate("() => document.querySelector('main').innerText")).split()
        )[:2000]
        out["url"] = page.url
    await page.screenshot(path=str(paths.captures_dir() / f"mc-{action}.png"))
    return out


async def run(action: str, arg: str | None, profile: str) -> None:
    calls: list[dict[str, Any]] = []
    tasks: list[asyncio.Future[None]] = []
    t0 = time.time()

    def on_request(request: Request) -> None:
        if "/__api/" in request.url or "storage" in request.url:
            calls.append(
                {
                    "t": round(time.time() - t0, 1),
                    "dir": ">",
                    "method": request.method,
                    "url": request.url.split("/__api")[-1][:200],
                    "body": (request.post_data or "")[:600],
                }
            )

    async def read(response: Response) -> None:
        try:
            text = await response.text()
            body = text if "/messages/" in response.url else text[:3000]
        except Exception:
            body = "<stream or binary>"
        calls.append(
            {
                "t": round(time.time() - t0, 1),
                "dir": "<",
                "status": response.status,
                "url": response.url.split("/__api")[-1][:200],
                "body": body,
            }
        )

    def on_response(response: Response) -> None:
        if "/__api/" in response.url:
            tasks.append(asyncio.ensure_future(read(response)))

    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(ROOT)
        await ensure_rendering(session, page)
        page.on("request", on_request)
        page.on("response", on_response)
        try:
            out = await act(session, page, action, arg)
            await asyncio.sleep(1)
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)
            await session.detach()
    noise = ("/billing", "/personalize", "/auth/", "/users/", "/partner", "/producer/invite")
    calls = [c for c in calls if not c["url"].startswith(noise)]
    target = paths.captures_dir() / f"music-capture-{action}-{time.strftime('%H%M%S')}.json"
    target.write_text(json.dumps({"out": out, "calls": calls}, indent=1, ensure_ascii=False))
    sys.stdout.reconfigure(encoding="utf-8")
    print("out:", json.dumps(out, ensure_ascii=False)[:2500])
    for c in calls:
        head = f"{c['t']:>6} {c['dir']} {c.get('method') or c.get('status')} {c['url'][:110]}"
        print(head)
        if c["body"] and c["dir"] == ">":
            print("         body:", c["body"][:300])
    print("saved:", target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["mp3", "video-simple", "chat", "upload", "trim"])
    parser.add_argument("arg", nargs="?")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    asyncio.run(run(args.action, args.arg, args.profile))


if __name__ == "__main__":
    main()
