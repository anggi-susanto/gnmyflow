"""Spike: inventory every Flow Music feature reachable from the UI, at zero cost.

Usage:
    uv run python spikes/music_features.py [--profile NAME]

Visits each section and opens menus (song "More options", chat "+", chat settings,
compose panel), recording the visible controls and menu entries and a screenshot of each.
Nothing is submitted: menus are opened and closed with Escape.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from playwright.async_api import Page, async_playwright

from studiowright import paths
from studiowright.browser import attach, ensure_rendering

ROOT = "https://www.flowmusic.app"

DOM_JS = r"""
(scope) => {
  const root = scope ? document.querySelector(scope) || document : document;
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const pick = (el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role'),
    text: (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 60),
    aria: (el.getAttribute('aria-label') || '').slice(0, 60),
    placeholder: (el.getAttribute('placeholder') || '').slice(0, 60),
    checked: el.getAttribute('aria-checked') || el.getAttribute('aria-pressed'),
    disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
    href: el.tagName === 'A' ? el.getAttribute('href') : null,
  });
  const q = 'button, a[href], [role="button"], [role="menuitem"], [role="menuitemradio"], '
    + '[role="menuitemcheckbox"], [role="option"], [role="tab"], [role="switch"], '
    + '[role="slider"], [role="combobox"], input, textarea, select';
  return [...root.querySelectorAll(q)].filter(visible).map(pick);
}
"""

MENU_JS = r"""
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const q = '[role="menu"] *, [role="listbox"] *, [role="dialog"] *, [data-radix-popper-content-wrapper] *';
  const seen = new Set();
  const out = [];
  for (const el of document.querySelectorAll(q)) {
    if (!visible(el)) continue;
    const role = el.getAttribute('role');
    const tag = el.tagName.toLowerCase();
    if (!(role && role.startsWith('menuitem')) && role !== 'option' && tag !== 'button'
        && role !== 'switch' && tag !== 'input' && tag !== 'textarea' && role !== 'slider') continue;
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '')
      .replace(/\s+/g, ' ').trim().slice(0, 70);
    const key = role + '|' + text;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({role: role || tag, text, aria: el.getAttribute('aria-label'),
              haspopup: el.getAttribute('aria-haspopup'),
              disabled: el.getAttribute('aria-disabled') === 'true' || !!el.disabled});
  }
  return out;
}
"""


class Inventory:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.report: dict[str, Any] = {}

    async def shot(self, name: str) -> str:
        target = paths.captures_dir() / f"mf-{name}.png"
        await self.page.screenshot(path=str(target))
        return str(target)

    async def section(self, name: str, path: str, wait_ms: int = 4000) -> None:
        await self.page.goto(ROOT + path, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(wait_ms)
        controls = await self.page.evaluate(DOM_JS, "main")
        self.report[name] = {"url": self.page.url, "controls": controls, "shot": await self.shot(name)}

    async def menu(self, name: str, opener: Any, hover_submenus: bool = True) -> None:
        try:
            await opener.click(timeout=5000)
        except Exception as exc:
            self.report[name] = {"error": str(exc).splitlines()[0]}
            return
        await self.page.wait_for_timeout(800)
        items = await self.page.evaluate(MENU_JS)
        entry: dict[str, Any] = {"items": items, "shot": await self.shot(name)}
        if hover_submenus:
            subs: dict[str, Any] = {}
            for item in items:
                if item.get("haspopup") in ("menu", "true") and item["text"]:
                    loc = self.page.get_by_role("menuitem", name=item["text"]).first
                    try:
                        await loc.hover(timeout=2000)
                        await self.page.wait_for_timeout(600)
                        subs[item["text"]] = await self.page.evaluate(MENU_JS)
                    except Exception as exc:
                        subs[item["text"]] = str(exc).splitlines()[0]
            entry["submenus"] = subs
        self.report[name] = entry
        await self.page.keyboard.press("Escape")
        await self.page.wait_for_timeout(300)
        await self.page.keyboard.press("Escape")


async def run(profile: str) -> None:
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(ROOT)
        await ensure_rendering(session, page)
        inv = Inventory(page)
        try:
            await inv.section("songs", "/library/my-songs")
            song_links = await page.locator("a[href^='/song/']").evaluate_all(
                "els => els.map(e => e.getAttribute('href'))"
            )
            if song_links:
                await inv.menu(
                    "songs-row-more", page.get_by_role("button", name="More options").first
                )
                await inv.section("song", song_links[0])
                await inv.menu("song-more", page.get_by_role("button", name="More options").first)
            for name, path in (
                ("playlists", "/library/playlists"),
                ("spaces", "/library/spaces"),
                ("videos", "/library/videos"),
                ("projects", "/projects"),
                ("turntable", "/turntable"),
            ):
                await inv.section(name, path)
            await inv.section("session", "/session")
            await inv.menu("chat-plus", page.get_by_role("button", name="Add audio or image"))
            await inv.menu("chat-settings", page.get_by_role("button", name="Chat settings"))
            await inv.menu(
                "player-more", page.locator("button[aria-label^='More options for']").last
            )
            sound = page.locator("textarea[aria-label='Sound description']")
            if not await sound.is_visible():
                await page.locator("button[aria-label='Toggle compose panel']").click()
                await sound.wait_for(state="visible", timeout=10_000)
            advanced = page.locator("button[aria-label='Toggle advanced sound mode']")
            if await advanced.get_attribute("aria-checked") != "true":
                await advanced.click()
            details = page.locator("button[aria-label='Expand Details section']")
            if await details.count() and await details.get_attribute("aria-expanded") == "false":
                await details.click()
            await page.wait_for_timeout(600)
            panel = await page.evaluate(
                r"""() => {
                  const ta = document.querySelector("textarea[aria-label='Sound description']");
                  let el = ta; for (let i = 0; i < 12 && el; i++) el = el.parentElement;
                  const visible = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
                  const q = 'button, input, textarea, [role="switch"], [role="slider"], label, h2, h3, span';
                  return [...(el || document).querySelectorAll(q)].filter(visible)
                    .map(e => ({tag: e.tagName.toLowerCase(), role: e.getAttribute('role'),
                      text: (e.innerText || e.value || '').replace(/\s+/g,' ').trim().slice(0,60),
                      aria: e.getAttribute('aria-label')}))
                    .filter(x => x.text || x.aria);
                }"""
            )
            inv.report["compose-panel"] = {"items": panel, "shot": await inv.shot("compose")}
            for label in ("Lyria 3.5", "Lyria 3 Pro"):
                button = page.get_by_role("button", name=label)
                if await button.count():
                    await inv.menu("model-menu", button.first, hover_submenus=False)
                    break
        finally:
            await session.detach()
    out = paths.captures_dir() / f"music-features-{time.strftime('%H%M%S')}.json"
    out.write_text(json.dumps(inv.report, indent=1, ensure_ascii=False), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for name, entry in inv.report.items():
        print(f"== {name}: {entry.get('url', '')} {entry.get('error', '')}")
        rows = entry.get("controls") or entry.get("items") or []
        labels = []
        for row in rows:
            label = row.get("text") or row.get("aria") or row.get("placeholder") or ""
            if label and label not in labels:
                labels.append(label)
        print("   ", " | ".join(labels)[:1500])
        for sub, items in (entry.get("submenus") or {}).items():
            texts = [i.get("text") for i in items] if isinstance(items, list) else items
            print(f"    > {sub}: {texts}")
    print("report:", out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    asyncio.run(run(parser.parse_args().profile))


if __name__ == "__main__":
    main()
