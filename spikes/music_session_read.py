"""Spike: open a Flow Music session and record its replies and visible controls, at zero cost.

Usage:
    uv run python spikes/music_session_read.py SESSION_ID [--profile NAME]

Loads /session/<id>, records every /__api reply (path, status, a body excerpt, JSON keys)
and the visible controls, and saves the bodies and a screenshot to the captures folder.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from typing import Any

from playwright.async_api import Response, async_playwright

from studiowright import paths
from studiowright.browser import attach, ensure_rendering

ROOT = "https://www.flowmusic.app"


def keys_of(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        return {k: keys_of(v, depth + 1) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [keys_of(value[0], depth + 1)] if value else []
    return type(value).__name__


async def run(session_id: str, profile: str) -> None:
    replies: list[dict[str, Any]] = []
    tasks: list[asyncio.Future[None]] = []

    async def read(response: Response) -> None:
        try:
            text = await response.text()
        except Exception:
            text = ""
        entry: dict[str, Any] = {
            "path": response.url.split("/__api")[1][:120],
            "method": response.request.method,
            "status": response.status,
            "body": text,
        }
        with contextlib.suppress(ValueError):
            entry["keys"] = keys_of(json.loads(text))
        replies.append(entry)

    def on_response(response: Response) -> None:
        if "/__api/" in response.url:
            tasks.append(asyncio.ensure_future(read(response)))

    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(ROOT)
        await ensure_rendering(session, page)
        page.on("response", on_response)
        try:
            await page.goto(f"{ROOT}/session/{session_id}", wait_until="domcontentloaded")
            await page.wait_for_timeout(8000)
            await asyncio.gather(*tasks, return_exceptions=True)
            controls = await page.evaluate(
                r"""() => [...document.querySelectorAll('main button, main a[href], main [role="button"]')]
                  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                  .map(e => ((e.innerText || '').trim() || e.getAttribute('aria-label') || '')
                    .replace(/\s+/g, ' ').slice(0, 50) + (e.getAttribute('href') ? ' -> ' + e.getAttribute('href') : ''))"""
            )
            text = await page.evaluate("() => document.querySelector('main').innerText")
            shot = paths.captures_dir() / f"msr-{session_id[:8]}.png"
            await page.screenshot(path=str(shot), full_page=True)
        finally:
            page.remove_listener("response", on_response)
            await session.detach()
    out = paths.captures_dir() / f"music-session-{session_id[:8]}.json"
    out.write_text(json.dumps(replies, indent=1, ensure_ascii=False), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print("text:", " ".join(text.split())[:900])
    print("controls:", [c for c in controls if c][:60])
    for reply in replies:
        if any(s in reply["path"] for s in ("/billing", "/personalize", "/auth/", "/users/")):
            continue
        print(f"-- {reply['method']} {reply['status']} {reply['path']}")
        print("   keys:", json.dumps(reply.get("keys"))[:600])
    print("bodies:", out, "| shot:", shot)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session_id")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    asyncio.run(run(args.session_id, args.profile))


if __name__ == "__main__":
    main()
