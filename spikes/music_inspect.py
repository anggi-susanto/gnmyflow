"""Spike: map flowmusic.app's signed-in UI, at zero cost.

Usage:
    uv run python spikes/music_inspect.py [--profile NAME] [--path /create]

Opens flowmusic.app in StudioWright's Chrome and records, without submitting anything:

- the visible buttons, inputs and links (text, aria-label, placeholder, role);
- the page's own /__api calls (method, path, status), read passively;
- a screenshot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from playwright.async_api import Request, Response, async_playwright

from studiowright import paths
from studiowright.browser import attach

ROOT = "https://www.flowmusic.app"

DOM_JS = r"""
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const pick = (el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role'),
    text: (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 50),
    aria: (el.getAttribute('aria-label') || '').slice(0, 50),
    placeholder: (el.getAttribute('placeholder') || '').slice(0, 50),
    name: el.getAttribute('name'), type: el.getAttribute('type'),
    testid: el.getAttribute('data-testid'),
    href: el.tagName === 'A' ? el.getAttribute('href') : null,
  });
  return {
    title: document.title, url: location.href,
    controls: [...document.querySelectorAll('button, [role="button"], input, textarea, select, [contenteditable="true"], [role="slider"], [role="switch"], [role="tab"], [role="combobox"]')].filter(visible).map(pick),
    links: [...document.querySelectorAll('a[href]')].filter(visible).map(pick).slice(0, 60),
  };
}
"""


async def run(profile: str, path: str) -> None:
    calls: list[dict[str, Any]] = []
    t0 = time.time()

    def on_request(request: Request) -> None:
        if "/__api" in request.url:
            calls.append(
                {
                    "t": round(time.time() - t0, 2),
                    "method": request.method,
                    "path": request.url.split("/__api")[1][:80],
                }
            )

    def on_response(response: Response) -> None:
        if "/__api" in response.url:
            calls.append(
                {
                    "t": round(time.time() - t0, 2),
                    "status": response.status,
                    "path": response.url.split("/__api")[1][:80],
                }
            )

    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(ROOT)
        page.on("request", on_request)
        page.on("response", on_response)
        try:
            await page.goto(ROOT + path, wait_until="domcontentloaded")
            await page.wait_for_timeout(6000)
            report = await page.evaluate(DOM_JS)
            shot = paths.captures_dir() / f"music-{path.strip('/').replace('/', '-') or 'home'}.png"
            await page.screenshot(path=str(shot))
            report["shot"] = str(shot)
            report["api_calls"] = calls[:60]
        finally:
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)
            await session.detach()
    sys.stdout.reconfigure(encoding="utf-8")
    out = paths.captures_dir() / f"music-inspect-{time.strftime('%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print("url:", report["url"], "| title:", report["title"])
    print("controls:")
    for c in report["controls"]:
        label = c["text"] or c["aria"] or c["placeholder"]
        print(f"  {c['tag']}/{c['role'] or c['type'] or ''} {label!r} testid={c['testid']}")
    print("links:", [(link["text"][:25], link["href"]) for link in report["links"]][:40])
    print(
        "api:", [(c.get("method") or c.get("status"), c["path"]) for c in report["api_calls"]][:40]
    )
    print("shot:", report["shot"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    parser.add_argument("--path", default="/")
    args = parser.parse_args()
    asyncio.run(run(args.profile, args.path))


if __name__ == "__main__":
    main()
