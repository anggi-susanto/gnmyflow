"""Spike: map Flow's composer UI with locale-proof handles, at zero cost.

Usage:
    uv run python spikes/ui_inspect.py PROJECT_ID [--profile NAME]

Opens the project and dumps, without submitting anything:

- the settings chip text;
- every visible button with its icon ligature and classes (aria-labels are translated, so
  they are listed for orientation only);
- the prompt box structure;
- the hover menu of the first grid tile;
- whether Ctrl+U opens a file chooser (it is cancelled, nothing is uploaded).

Only passive listeners are used; no request is intercepted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from playwright.async_api import async_playwright

from studiowright import paths
from studiowright.browser import attach

READY = ".settings-trigger-button"

BUTTONS_JS = r"""
(root) => {
  const scope = root ? document.querySelector(root) : document;
  if (!scope) return [];
  return [...scope.querySelectorAll('button, [role="button"], [role="menuitem"], [role="option"], [role="radio"]')]
    .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
    .map((el) => {
      const icons = [...el.querySelectorAll('mat-icon, .material-icons, .material-symbols-outlined, i')]
        .map((i) => i.textContent.trim()).filter(Boolean);
      const host = el.closest('[class*="flow-"], flow-prompt-box, flow-image-tile, flow-video-tile');
      return {
        tag: el.tagName.toLowerCase(), role: el.getAttribute('role'), icons,
        cls: (el.className && el.className.baseVal === undefined ? el.className : '').split(' ').filter(Boolean).slice(0, 6),
        aria: (el.getAttribute('aria-label') || '').slice(0, 40),
        text: (el.innerText || '').trim().slice(0, 40),
        host: host ? host.tagName.toLowerCase() : null,
        disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      };
    });
}
"""

PROMPT_BOX_JS = r"""
() => {
  const box = document.querySelector('flow-prompt-box');
  if (!box) return null;
  const walk = (el, depth) => depth > 4 ? null : ({
    tag: el.tagName.toLowerCase(),
    cls: (typeof el.className === 'string' ? el.className : '').split(' ').filter(Boolean).slice(0, 4),
    editable: el.getAttribute('contenteditable'),
    kids: [...el.children].slice(0, 12).map((c) => walk(c, depth + 1)).filter(Boolean),
  });
  return walk(box, 0);
}
"""


async def run(project_id: str, profile: str) -> None:
    report: dict[str, Any] = {}
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        context = session.context
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            started = time.time()
            await page.goto(
                f"https://flow.google.com/project/{project_id}", wait_until="domcontentloaded"
            )
            await page.locator(READY).first.wait_for(state="visible", timeout=30_000)
            report["ready_s"] = round(time.time() - started, 1)
            report["lang"] = await page.evaluate("document.documentElement.lang")
            report["settings_chip"] = (await page.locator(READY).first.inner_text()).strip()
            report["buttons"] = await page.evaluate(BUTTONS_JS, None)
            report["prompt_box"] = await page.evaluate(PROMPT_BOX_JS)
            report["tiles"] = await page.locator("img[data-media-id]").count()
            if report["tiles"]:
                tile = page.locator("img[data-media-id]").first
                await tile.hover()
                await page.wait_for_timeout(600)
                host = await tile.evaluate(
                    "el => (el.closest('flow-image-tile, flow-video-tile') || el.parentElement)"
                    ".tagName.toLowerCase()"
                )
                report["tile_host"] = host
                report["tile_hover_buttons"] = await page.evaluate(BUTTONS_JS, host)
            await page.mouse.move(5, 5)
            try:
                async with page.expect_file_chooser(timeout=3_000) as chooser_info:
                    await page.keyboard.press("Control+U")
                chooser = await chooser_info.value
                report["ctrl_u"] = {"chooser": True, "multiple": chooser.is_multiple()}
                await chooser.set_files([])
            except Exception as exc:
                report["ctrl_u"] = {"chooser": False, "note": type(exc).__name__}
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            out = paths.captures_dir() / f"ui-inspect-{time.strftime('%Y%m%d-%H%M%S')}.json"
            out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"report: {out}", flush=True)
            await session.detach()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.profile))


if __name__ == "__main__":
    main()
