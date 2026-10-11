"""Spike: how to reach old media in a large Flow project, at zero cost.

Usage:
    uv run python spikes/ui_grid_probe.py PROJECT_ID [MEDIA_ID ...] [--profile NAME]

Records the sidebar filters (text and icon ligature), the grid's scroll container, whether
the grid is virtualized, how tiles link to media (hrefs, data attributes), the composer's
"+" picker, and how long a scroll to each MEDIA_ID takes. Nothing is submitted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from playwright.async_api import async_playwright

from studiowright import paths
from studiowright.browser import attach, ensure_rendering
from studiowright.flow import selectors as s

FLOW = "https://flow.google.com/"

SIDEBAR_JS = r"""
() => [...document.querySelectorAll('nav a, nav button, aside a, aside button, [role="tab"], a[href*="/project/"]')]
  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.left < 280; })
  .map(e => ({text: (e.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
              icon: (e.querySelector('mat-icon, i, .material-icons, .google-symbols') || {}).innerText || null,
              tag: e.tagName.toLowerCase(), href: e.getAttribute('href'), aria: e.getAttribute('aria-label')}))
"""

SCROLLER_JS = r"""
() => {
  const out = [];
  for (const e of document.querySelectorAll('*')) {
    const st = getComputedStyle(e);
    if (!/(auto|scroll)/.test(st.overflowY) || e.scrollHeight <= e.clientHeight + 50) continue;
    out.push({tag: e.tagName.toLowerCase(), cls: (e.className || '').toString().slice(0, 80),
              id: e.id, scrollHeight: e.scrollHeight, clientHeight: e.clientHeight,
              tiles: e.querySelectorAll('img').length});
  }
  return out;
}
"""

TILE_JS = r"""
() => {
  const imgs = [...document.querySelectorAll('img')].filter(i => /\/(image|media)\//.test(i.src) || i.src.includes('flow-content'));
  const first = imgs[0];
  if (!first) return null;
  let tile = first; for (let i = 0; i < 6 && tile; i++) tile = tile.parentElement;
  const anchors = tile ? [...tile.querySelectorAll('a[href]')].map(a => a.getAttribute('href')) : [];
  const attrs = tile ? [...tile.querySelectorAll('*')].flatMap(e => [...e.attributes].filter(a => a.name.startsWith('data-')).map(a => a.name + '=' + a.value.slice(0, 60))) : [];
  return {count: imgs.length, src: first.src.slice(0, 160), anchors: anchors.slice(0, 5), data: [...new Set(attrs)].slice(0, 12),
          tileTag: tile ? tile.tagName.toLowerCase() : null};
}
"""


async def run(project: str, media: list[str], profile: str) -> None:
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(f"{FLOW}project/{project}")
        await ensure_rendering(session, page)
        try:
            await page.goto(f"{FLOW}project/{project}", wait_until="domcontentloaded")
            await page.locator(s.READY).first.wait_for(state="visible", timeout=30_000)
            await page.wait_for_timeout(2500)
            print("sidebar:", await page.evaluate(SIDEBAR_JS))
            print("scrollers:", await page.evaluate(SCROLLER_JS))
            print("tile:", await page.evaluate(TILE_JS))
            for media_id in media:
                started = time.monotonic()
                found = await page.evaluate(
                    r"""async (id) => {
                      const sc = [...document.querySelectorAll('*')].filter(e => /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 50)
                        .sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
                      if (!sc) return 'no scroller';
                      sc.scrollTop = 0;
                      for (let i = 0; i < 400; i++) {
                        if ([...document.querySelectorAll('img')].some(im => im.src.includes(id))) return {step: i, top: sc.scrollTop, height: sc.scrollHeight};
                        const before = sc.scrollTop;
                        sc.scrollTop += sc.clientHeight * 0.8;
                        await new Promise(r => setTimeout(r, 250));
                        if (sc.scrollTop === before) return {end: true, top: sc.scrollTop, height: sc.scrollHeight};
                      }
                      return 'gave up';
                    }""",
                    media_id,
                )
                print(f"scroll to {media_id[:8]}: {found} in {time.monotonic() - started:.1f} s")
            plus = page.locator("button").filter(has=page.locator("mat-icon, i", has_text="add")).last
            await page.evaluate("() => { const sc = [...document.querySelectorAll('*')].filter(e => /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 50)[0]; if (sc) sc.scrollTop = 0; }")
            try:
                await plus.click(timeout=5000)
                await page.wait_for_timeout(1500)
                text = await page.evaluate(
                    "() => [...document.querySelectorAll('.cdk-overlay-pane, [role=dialog], [role=menu]')].map(e => e.innerText.replace(/\\s+/g, ' ').slice(0, 600))"
                )
                print("composer + :", text)
                await page.screenshot(path=str(paths.captures_dir() / "grid-plus.png"))
            except Exception as exc:
                print("composer + failed:", str(exc).splitlines()[0])
            await page.keyboard.press("Escape")
        finally:
            await session.detach()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project")
    parser.add_argument("media", nargs="*")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(args.project, args.media, args.profile))


if __name__ == "__main__":
    main()
