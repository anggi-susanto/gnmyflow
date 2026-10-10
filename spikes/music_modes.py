"""Spike: map the compose panel's edit modes (Extend, Cover, Replace, ...), at zero cost.

Usage:
    uv run python spikes/music_modes.py [--profile NAME] [--action Extend]

Opens More options → Remix → <action> for the newest song, then records the mode dropdown,
the SETTINGS popover, every collapsible section of the panel, and the region controls.
Generate is never pressed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from playwright.async_api import Page, async_playwright

from studiowright import paths
from studiowright.browser import attach, ensure_rendering

ROOT = "https://www.flowmusic.app"

PANEL_JS = r"""
() => {
  const heading = [...document.querySelectorAll('h1,h2,h3,span,div')].find(e => e.innerText === 'Compose');
  let panel = heading; for (let i = 0; i < 6 && panel; i++) { panel = panel.parentElement; if (panel && panel.getBoundingClientRect().height > 600) break; }
  const visible = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const q = 'button, input, textarea, [role="switch"], [role="slider"], [role="menuitem"], [role="option"], [contenteditable="true"]';
  return [...(panel || document).querySelectorAll(q)].filter(visible).map(e => ({
    tag: e.tagName.toLowerCase(), role: e.getAttribute('role'),
    text: (e.innerText || e.value || '').replace(/\s+/g, ' ').trim().slice(0, 60),
    aria: e.getAttribute('aria-label'), ph: e.getAttribute('placeholder'),
    expanded: e.getAttribute('aria-expanded'), checked: e.getAttribute('aria-checked'),
    type: e.getAttribute('type'),
  }));
}
"""

POPUP_JS = r"""
() => {
  const visible = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const roots = document.querySelectorAll('[role="menu"], [role="listbox"], [role="dialog"], [data-radix-popper-content-wrapper]');
  const out = [];
  for (const root of roots) for (const e of root.querySelectorAll('*')) {
    if (!visible(e) || e.children.length > 3) continue;
    const t = (e.innerText || e.value || e.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (t && !out.includes(t)) out.push(t.slice(0, 70));
  }
  return out;
}
"""


def show(label: str, rows: list[dict]) -> None:
    parts = []
    for r in rows:
        text = r.get("text") or r.get("aria") or r.get("ph") or ""
        extra = "".join(
            f" [{k}={r[k]}]" for k in ("expanded", "checked", "type") if r.get(k) not in (None, "")
        )
        parts.append(f"{r['tag']}:{text}{extra}")
    print(f"== {label}:", " | ".join(parts)[:2000])


async def open_action(page: Page, action: str) -> None:
    await page.goto(ROOT + "/library/my-songs", wait_until="domcontentloaded")
    await page.wait_for_timeout(3500)
    await page.locator("button[aria-label^='More options for']").first.click()
    await page.wait_for_timeout(400)
    remix = page.get_by_role("menuitem", name="Remix")
    await remix.focus()
    await page.keyboard.press("ArrowRight")
    item = page.get_by_role("menuitem", name=action, exact=True)
    await item.wait_for(state="visible", timeout=5000)
    await item.focus()
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(3500)


async def run(profile: str, action: str) -> None:
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(ROOT)
        await ensure_rendering(session, page)
        try:
            await open_action(page, action)
            show("panel", await page.evaluate(PANEL_JS))
            mode = page.locator("button").filter(has_text=action.upper()).first
            await mode.click()
            await page.wait_for_timeout(600)
            print("== mode menu:", await page.evaluate(POPUP_JS))
            await page.screenshot(path=str(paths.captures_dir() / f"mm-{action}-modes.png"))
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            settings = page.get_by_role("button", name="SETTINGS")
            if await settings.count():
                await settings.first.click()
                await page.wait_for_timeout(600)
                print("== settings:", await page.evaluate(POPUP_JS))
                await page.screenshot(path=str(paths.captures_dir() / f"mm-{action}-settings.png"))
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)
            for section in ("Instruction", "Source Lyrics", "Advanced", "Details"):
                button = page.get_by_role("button", name=section, exact=True)
                if await button.count() and await button.first.get_attribute("aria-expanded") != "true":
                    try:
                        await button.first.click(timeout=3000)
                    except Exception as exc:
                        print("   cannot open", section, str(exc).splitlines()[0])
            await page.wait_for_timeout(600)
            show("panel expanded", await page.evaluate(PANEL_JS))
            await page.screenshot(
                path=str(paths.captures_dir() / f"mm-{action}-expanded.png"), full_page=True
            )
            region = page.get_by_text(f"{action} 0:", exact=False)
            if await region.count():
                await region.first.click()
                await page.wait_for_timeout(600)
                print("== region click:", await page.evaluate(POPUP_JS))
                show("panel after region click", await page.evaluate(PANEL_JS))
                await page.screenshot(path=str(paths.captures_dir() / f"mm-{action}-region.png"))
            for _ in range(2):
                await page.keyboard.press("Escape")
        finally:
            await session.detach()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    parser.add_argument("--action", default="Extend")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(args.profile, args.action))


if __name__ == "__main__":
    main()
