"""Spike: map every option in Flow's settings pane, at zero cost.

Usage:
    uv run python spikes/ui_settings.py PROJECT_ID [--profile NAME]

For image mode and for each video submode it records:

- every radio group with its options (icon ligature, text, checked state);
- the model menu entries;
- the settings chip text.

Nothing is submitted. At the end it puts the composer back to image mode with
Nano Banana 2.1, 16:9, x1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from playwright.async_api import Locator, Page, async_playwright

from studiowright import paths
from studiowright.browser import attach

READY = ".settings-trigger-button"
OVERLAY = ".cdk-overlay-pane"

GROUPS_JS = r"""
(pane) => [...pane.querySelectorAll('[role="radiogroup"]')].map((group) => ({
  label: (group.getAttribute('aria-label') || '').slice(0, 40),
  options: [...group.querySelectorAll('[role="radio"]')].map((radio) => ({
    icons: [...radio.querySelectorAll('mat-icon')].map((i) => i.textContent.trim()),
    text: (radio.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
    checked: radio.getAttribute('aria-checked') === 'true',
    disabled: radio.getAttribute('aria-disabled') === 'true' || radio.disabled === true,
  })),
}))
"""


async def open_pane(page: Page) -> Locator:
    # Tooltips are overlay panes too, so pick the one that holds the radio groups.
    pane = page.locator(f"{OVERLAY}:has([role='radiogroup'])")
    for _ in range(4):
        if await pane.count():
            return pane.first
        # A click during the close animation is swallowed, so retry briefly.
        await page.mouse.move(5, 5)
        await page.locator(READY).first.click()
        try:
            await pane.first.wait_for(state="visible", timeout=1_500)
        except Exception:
            await page.wait_for_timeout(300)
    await pane.first.wait_for(state="visible", timeout=3_000)
    return pane.first


async def click_radio(pane: Locator, icon: str | None = None, text: str | None = None) -> bool:
    radios = pane.locator("[role='radio']")
    target = (
        radios.filter(has=pane.page.locator(f"mat-icon:text-is('{icon}')"))
        if icon
        else radios.filter(has_text=text or "")
    )
    if not await target.count():
        return False
    await target.first.click()
    await pane.page.wait_for_timeout(250)
    return True


async def model_menu(page: Page, pane: Locator) -> dict[str, Any]:
    button = pane.locator("button:has(mat-icon:text-is('arrow_drop_down'))").first
    if not await button.count():
        return {"button": False}
    current = (await button.inner_text()).replace("arrow_drop_down", "").strip()
    await button.click()
    items = page.locator("[role='menuitem']")
    await items.first.wait_for(state="visible", timeout=5_000)
    entries = [" ".join(t.split()) for t in await items.all_inner_texts()]
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(250)
    return {"current": " ".join(current.split()), "entries": entries}


async def snapshot(page: Page, name: str) -> dict[str, Any]:
    pane = await open_pane(page)
    state = {
        "name": name,
        "groups": await pane.evaluate(GROUPS_JS),
        "model": await model_menu(page, pane),
    }
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(250)
    state["chip"] = " | ".join((await page.locator(READY).first.inner_text()).split("\n"))
    return state


async def run(project_id: str, profile: str) -> None:
    report: dict[str, Any] = {"states": []}
    async with async_playwright() as playwright:
        session = await attach(playwright, profile)
        context = session.context
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(
                f"https://flow.google.com/project/{project_id}", wait_until="domcontentloaded"
            )
            await page.locator(READY).first.wait_for(state="visible", timeout=30_000)
            report["chip_at_load"] = (await page.locator(READY).first.inner_text()).split("\n")
            pane = await open_pane(page)
            await click_radio(pane, icon="image")
            report["states"].append(await snapshot(page, "image"))
            pane = await open_pane(page)
            await click_radio(pane, icon="videocam")
            report["states"].append(await snapshot(page, "video (as remembered)"))
            for submode in ("crop_free", "chrome_extension"):
                pane = await open_pane(page)
                if await click_radio(pane, icon=submode):
                    report["states"].append(await snapshot(page, f"video {submode}"))
            for submode in ("crop_free", "chrome_extension"):
                for model_name in (
                    "Veo 3.1 - Lite",
                    "Veo 3.1 - Fast",
                    "Veo 3.1 - Quality",
                    "Omni 1.1 Flash",
                ):
                    pane = await open_pane(page)
                    await click_radio(pane, icon=submode)
                    button = pane.locator("button:has(mat-icon:text-is('arrow_drop_down'))").first
                    await button.click()
                    item = page.locator("[role='menuitem']").filter(has_text=model_name)
                    await item.first.wait_for(state="visible", timeout=5_000)
                    await item.first.click()
                    await page.wait_for_timeout(400)
                    report["states"].append(await snapshot(page, f"video {submode} {model_name}"))
            # Back to the default the CLI will use.
            pane = await open_pane(page)
            await click_radio(pane, icon="image")
            await click_radio(pane, icon="crop_16_9")
            await click_radio(pane, text="x1")
            await page.keyboard.press("Escape")
            report["chip_at_end"] = (await page.locator(READY).first.inner_text()).split("\n")
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            shot = paths.captures_dir() / f"ui-settings-error-{time.strftime('%H%M%S')}.png"
            await page.screenshot(path=str(shot))
            report["error_shot"] = str(shot)
            report["overlays"] = await page.evaluate(
                "() => [...document.querySelectorAll('.cdk-overlay-container > *')]"
                ".map(e => e.className + ' :: ' + (e.innerText || '').slice(0, 80))"
            )
        finally:
            out = paths.captures_dir() / f"ui-settings-{time.strftime('%Y%m%d-%H%M%S')}.json"
            out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
            await session.detach()
    sys.stdout.reconfigure(encoding="utf-8")
    for state in report["states"]:
        print(f"== {state['name']}  chip: {state['chip']}")
        print(f"   model: {state['model']}")
        for group in state["groups"]:
            options = [
                ("*" if o["checked"] else "")
                + (o["icons"][0] if o["icons"] else "")
                + ":"
                + o["text"]
                for o in group["options"]
            ]
            print(f"   group[{group['label']}]: {options}")
    print(
        {
            k: report.get(k)
            for k in ("chip_at_load", "chip_at_end", "error", "error_shot", "overlays")
        }
    )
    print(f"report: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.profile))


if __name__ == "__main__":
    main()
