"""Spike: open flowmusic.app's compose panel and map its fields, at zero cost.

Usage:
    uv run python spikes/music_compose.py [--profile NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from music_inspect import DOM_JS, ROOT
from playwright.async_api import async_playwright

from studiowright import paths
from studiowright.browser import attach

PANEL_JS = r"""
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const label = (el) => {
    const id = el.getAttribute('id');
    const l = id ? document.querySelector(`label[for="${id}"]`) : null;
    return l ? l.innerText.trim().slice(0, 40) : null;
  };
  return [...document.querySelectorAll('input, textarea, select, [role="slider"], [role="switch"], [role="combobox"], [role="tab"], [role="radio"], [contenteditable="true"]')]
    .filter(visible).map((el) => ({
      tag: el.tagName.toLowerCase(), role: el.getAttribute('role'), type: el.getAttribute('type'),
      label: label(el), aria: (el.getAttribute('aria-label') || '').slice(0, 50),
      placeholder: (el.getAttribute('placeholder') || '').slice(0, 60),
      value: (el.value || el.getAttribute('aria-valuenow') || el.getAttribute('aria-checked') || el.innerText || '').toString().slice(0, 40),
      min: el.getAttribute('min') || el.getAttribute('aria-valuemin'), max: el.getAttribute('max') || el.getAttribute('aria-valuemax'),
      x: Math.round(el.getBoundingClientRect().x), y: Math.round(el.getBoundingClientRect().y),
    }));
}
"""

TEXTS_JS = r"""
() => [...document.querySelectorAll('label, h1, h2, h3, h4, span, p, button')]
  .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.x > 700; })
  .map((el) => (el.innerText || '').replace(/\s+/g, ' ').trim()).filter((t) => t && t.length < 40)
"""


async def run(profile: str) -> None:
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(ROOT)
        try:
            await page.goto(ROOT + "/session", wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
            toggle = page.get_by_role("button", name="Toggle compose panel")
            sound = page.locator("textarea[aria-label='Sound description']")
            if not await sound.is_visible():
                await toggle.click()
            await sound.wait_for(state="visible", timeout=10_000)
            advanced = page.locator("button[aria-label='Toggle advanced sound mode']")
            if await advanced.get_attribute("aria-checked") != "true":
                await advanced.click()
                await page.wait_for_timeout(1200)
            details = page.locator("button[aria-label='Expand Details section']")
            if await details.count():
                await details.first.click()
                await page.wait_for_timeout(1200)
            fields = await page.evaluate(PANEL_JS)
            texts = await page.evaluate(TEXTS_JS)
            dom = await page.evaluate(DOM_JS)
            shot = paths.captures_dir() / "music-compose.png"
            await page.screenshot(path=str(shot))
        finally:
            await session.detach()
    sys.stdout.reconfigure(encoding="utf-8")
    print("fields:")
    for f in fields:
        print(" ", json.dumps(f, ensure_ascii=False))
    print("panel texts:", list(dict.fromkeys(texts))[:80])
    print(
        "buttons:", [c["text"] or c["aria"] for c in dom["controls"] if c["tag"] == "button"][-30:]
    )
    print("shot:", shot)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    asyncio.run(run(parser.parse_args().profile))


if __name__ == "__main__":
    main()
