"""Spike: check the attach-over-CDP setup and its automation fingerprint, at zero cost.

Usage:
    uv run python spikes/attach_check.py [PROJECT_ID] [--profile NAME]

It starts (or reuses) Chrome on the profile and attaches to it, then reports:

- ``navigator.webdriver``;
- the user agent and viewport;
- the WebGL renderer, which should be the real GPU, not SwiftShader;
- how fast a project page becomes ready;
- whether a second attach reuses the already open tab.

It submits nothing. Chrome stays open after the script ends.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from playwright.async_api import async_playwright

from studiowright import paths
from studiowright.browser import attach

READY = ".settings-trigger-button"

FINGERPRINT_JS = r"""
() => {
  const gl = document.createElement('canvas').getContext('webgl');
  const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
  return {
    webdriver: navigator.webdriver,
    userAgent: navigator.userAgent,
    viewport: [innerWidth, innerHeight],
    renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
    visibility: document.visibilityState,
    hasFocus: document.hasFocus(),
  };
}
"""


async def run(project_id: str | None, profile: str) -> None:
    report: dict[str, object] = {}
    async with async_playwright() as playwright:
        started = time.time()
        session = await attach(playwright, profile)
        report["attach_s"] = round(time.time() - started, 1)
        report["port"] = session.port
        report["tabs_before"] = [p.url[:60] for p in session.context.pages]
        url = (
            f"https://flow.google.com/project/{project_id}"
            if project_id
            else "https://flow.google.com/"
        )
        page = await session.page_for(url)
        if not page.url.startswith(url):
            started = time.time()
            await page.goto(url, wait_until="domcontentloaded")
            if project_id:
                await page.locator(READY).first.wait_for(state="visible", timeout=30_000)
            report["load_ready_s"] = round(time.time() - started, 1)
        report["fingerprint"] = await page.evaluate(FINGERPRINT_JS)
        await session.detach()
        started = time.time()
        session = await attach(playwright, profile)
        again = session.find_page(url)
        report["reattach_s"] = round(time.time() - started, 1)
        report["reattach_found_tab"] = again is not None
        if again is not None and project_id:
            started = time.time()
            await again.locator(READY).first.wait_for(state="visible", timeout=10_000)
            report["warm_ready_s"] = round(time.time() - started, 2)
        await session.detach()
    print(json.dumps(report, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id", nargs="?")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.profile))


if __name__ == "__main__":
    main()
