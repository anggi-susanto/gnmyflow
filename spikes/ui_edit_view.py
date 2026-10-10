"""Spike: map a clip's edit view and its download options, at zero cost.

Usage:
    uv run python spikes/ui_edit_view.py PROJECT_ID WORKFLOW_ID [--profile NAME]

Opens /project/<id>/edit/<workflow id> and dumps the visible buttons. If a download button
exists, it opens that menu and dumps the entries without choosing any.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from playwright.async_api import async_playwright

from studiowright import paths
from studiowright.browser import attach

BUTTONS_JS = r"""
() => [...document.querySelectorAll('button, [role="menuitem"]')]
  .filter((el) => el.getBoundingClientRect().width > 0)
  .map((el) => ({
    icons: [...el.querySelectorAll('mat-icon')].map((i) => i.textContent.trim()),
    text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 50),
    disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
  }))
"""


async def run(project_id: str, workflow_id: str, profile: str) -> None:
    report: dict[str, Any] = {}
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        page = await session.page_for(f"https://flow.google.com/project/{project_id}")
        try:
            await page.goto(
                f"https://flow.google.com/project/{project_id}/edit/{workflow_id}",
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(4000)
            report["url"] = page.url
            report["buttons"] = await page.evaluate(BUTTONS_JS)
            download = page.locator("button:has(mat-icon:text-is('download'))").first
            if await download.count():
                await download.click()
                await page.wait_for_timeout(800)
                report["download_menu"] = await page.evaluate(
                    r"""() => [...document.querySelectorAll('.cdk-overlay-pane [role="menuitem"], .cdk-overlay-pane button')]
                      .filter((el) => el.getBoundingClientRect().width > 0)
                      .map((el) => ({text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 50),
                                     disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true'}))"""
                )
                await page.keyboard.press("Escape")
            shot = paths.captures_dir() / "edit-view.png"
            await page.screenshot(path=str(shot))
            report["shot"] = str(shot)
            await page.goto(
                f"https://flow.google.com/project/{project_id}", wait_until="domcontentloaded"
            )
        finally:
            await session.detach()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False)[:6000])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id")
    parser.add_argument("workflow_id")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.workflow_id, args.profile))


if __name__ == "__main__":
    main()
