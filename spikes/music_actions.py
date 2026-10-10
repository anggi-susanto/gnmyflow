"""Spike: open each Flow Music song action and record its dialog, at zero cost.

Usage:
    uv run python spikes/music_actions.py [--profile NAME] [--only extend,cover]

For one of the account's songs it opens More options → Remix → Cover / Replace / Extend /
Variation / Trim, More options → Split stems, Download → Get stems / Download video, and the
"New music video" dialog. It records the visible controls of whatever opens (dialog, panel
or route), any price text, and a screenshot, then closes it with Escape. Nothing is
submitted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from typing import Any

from playwright.async_api import Page, async_playwright

from studiowright import paths
from studiowright.browser import attach, ensure_rendering

ROOT = "https://www.flowmusic.app"

CONTROLS_JS = r"""
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const pick = (el) => ({
    tag: el.tagName.toLowerCase(), role: el.getAttribute('role'),
    text: (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 80),
    aria: (el.getAttribute('aria-label') || '').slice(0, 60),
    placeholder: (el.getAttribute('placeholder') || '').slice(0, 60),
    checked: el.getAttribute('aria-checked'),
    disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
  });
  const scope = document.querySelector('[role="dialog"]') || document.querySelector('main') || document;
  const q = 'button, [role="button"], [role="menuitem"], [role="option"], [role="tab"], '
    + '[role="switch"], [role="slider"], [role="radio"], input, textarea, select, label, h1, h2, h3';
  return {
    dialog: !!document.querySelector('[role="dialog"]'),
    url: location.href,
    controls: [...scope.querySelectorAll(q)].filter(visible).map(pick),
    text: (scope.innerText || '').replace(/\s+/g, ' ').slice(0, 1500),
  };
}
"""

PRICE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:credits?|kredi)", re.I)


class Probe:
    def __init__(self, page: Page, song_url: str) -> None:
        self.page = page
        self.song_url = song_url
        self.report: dict[str, Any] = {}

    async def reset(self) -> None:
        for _ in range(3):
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(200)
        await self.page.goto(self.song_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(3500)

    async def more(self) -> None:
        await self.page.locator("button[aria-label^='More options']").first.click(timeout=8000)
        await self.page.wait_for_timeout(500)

    async def record(self, name: str, wait_ms: int = 2500) -> None:
        await self.page.wait_for_timeout(wait_ms)
        state = await self.page.evaluate(CONTROLS_JS)
        shot = paths.captures_dir() / f"ma-{name}.png"
        await self.page.screenshot(path=str(shot))
        state["shot"] = str(shot)
        state["prices"] = PRICE.findall(state["text"])
        self.report[name] = state

    async def remix(self, action: str) -> None:
        await self.reset()
        await self.more()
        await self.page.get_by_role("menuitem", name="Remix").hover()
        await self.page.wait_for_timeout(500)
        await self.page.get_by_role("menuitem", name=action, exact=True).click()
        await self.record(f"remix-{action.lower()}")

    async def top(self, item: str, sub: str | None = None) -> None:
        await self.reset()
        await self.more()
        entry = self.page.get_by_role("menuitem", name=item, exact=True)
        if sub:
            await entry.hover()
            await self.page.wait_for_timeout(500)
            await self.page.get_by_role("menuitem", name=sub, exact=True).click()
        else:
            await entry.click()
        await self.record(f"{item}-{sub or ''}".lower().replace(" ", "_").strip("-_"))


async def run(profile: str, only: set[str]) -> None:
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(ROOT)
        await ensure_rendering(session, page)
        await page.goto(ROOT + "/library/my-songs", wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)
        hrefs = await page.locator("a[href^='/song/']").evaluate_all(
            "els => els.map(e => e.getAttribute('href'))"
        )
        probe = Probe(page, ROOT + hrefs[0])
        steps = [
            ("cover", lambda: probe.remix("Cover")),
            ("replace", lambda: probe.remix("Replace")),
            ("extend", lambda: probe.remix("Extend")),
            ("variation", lambda: probe.remix("Variation")),
            ("trim", lambda: probe.remix("Trim")),
            ("useprompt", lambda: probe.remix("Use prompt")),
            ("startsession", lambda: probe.remix("Start session")),
            ("stems", lambda: probe.top("Split stems")),
            ("getstems", lambda: probe.top("Download", "Get stems")),
            ("downloadvideo", lambda: probe.top("Download", "Download video")),
            ("editdetails", lambda: probe.top("Details", "Edit details")),
        ]
        try:
            for key, step in steps:
                if only and key not in only:
                    continue
                try:
                    await step()
                except Exception as exc:
                    probe.report[key] = {"error": str(exc).splitlines()[0]}
            if not only or "video" in only:
                try:
                    await page.goto(ROOT + "/library/videos", wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)
                    await page.get_by_role("button", name="New music video").click()
                    await probe.record("new-music-video")
                except Exception as exc:
                    probe.report["new-music-video"] = {"error": str(exc).splitlines()[0]}
            for _ in range(3):
                await page.keyboard.press("Escape")
        finally:
            await session.detach()
    out = paths.captures_dir() / f"music-actions-{time.strftime('%H%M%S')}.json"
    out.write_text(json.dumps(probe.report, indent=1, ensure_ascii=False), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for name, state in probe.report.items():
        if "error" in state:
            print(f"== {name}: ERROR {state['error']}")
            continue
        print(f"== {name}: dialog={state['dialog']} url={state['url']} prices={state['prices']}")
        print("   text:", state["text"][:700])
    print("report:", out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    asyncio.run(run(args.profile, {s for s in args.only.split(",") if s}))


if __name__ == "__main__":
    main()
