"""Spike: one real i2v clip (start + end frame) through the UI, observed passively.

Usage:
    uv run python spikes/ui_video_generate.py PROJECT_ID START END [--profile NAME]
        [--seconds 4] [--resolution 360p] [--prompt TEXT]

Binds START with the tile menu's Animate entry and END through the end slot's picker.
Sets Omni 1.1 Flash, the duration, the resolution and x1, and reads the price shown in
the settings pane. Then it submits and follows the clip passively: submit reply,
``jwpduf`` polls, ``as29s``, signed mp4 URL. Finally it downloads the clip.

Costs credits: one short clip.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from typing import Any

from playwright.async_api import Request, Response, async_playwright
from ui_attach2 import BOX, fresh
from ui_video_explore import OVERLAY, animate, open_pane

from studiowright import paths
from studiowright.browser import attach

SUBMIT_RPCS = ("YhhmEf", "eb1hJf", "MZZa6b", "nprQif")
VIDEO_URL = re.compile(r"https://flow-content\.google/video/[^\"\\\s]+")
PICKER_ADD = f"{OVERLAY} button.detail-add-to-prompt-btn"


def rpc_of(url: str) -> str:
    match = re.search(r"rpcids=([A-Za-z0-9]+)", url)
    return match.group(1) if match else ""


async def run(args: argparse.Namespace) -> None:
    report: dict[str, Any] = {"events": []}
    url = f"https://flow.google.com/project/{args.project_id}"
    t0 = time.time()
    found: dict[str, Any] = {}
    tasks: list[asyncio.Future[None]] = []

    def log(kind: str, **fields: Any) -> None:
        report["events"].append({"t": round(time.time() - t0, 2), "kind": kind, **fields})

    def on_request(request: Request) -> None:
        rpc = rpc_of(request.url)
        if rpc in SUBMIT_RPCS:
            found.setdefault("submit_t", time.time())
            log("submit_request", rpc=rpc)

    async def read(response: Response, rpc: str) -> None:
        try:
            text = await response.text()
        except Exception:
            return
        urls = VIDEO_URL.findall(text)
        if rpc in SUBMIT_RPCS:
            found.setdefault("accepted_t", time.time())
            log("submit_reply", rpc=rpc, http=response.status, refused="UNUSUAL_ACTIVITY" in text)
        elif rpc in ("jwpduf", "as29s"):
            log(rpc, http=response.status, video_urls=len(urls))
        if urls and "video_url" not in found:
            found["video_url"] = urls[0]
            found["video_t"] = time.time()

    def on_response(response: Response) -> None:
        rpc = rpc_of(response.url)
        if rpc in (*SUBMIT_RPCS, "jwpduf", "as29s"):
            tasks.append(asyncio.ensure_future(read(response, rpc)))

    async with async_playwright() as playwright:
        session = await attach(playwright, args.profile)
        page = await session.page_for(url)
        page.on("request", on_request)
        page.on("response", on_response)
        try:
            await fresh(page, url)
            started = time.time()
            await animate(page, args.start)
            await page.locator(f"{BOX} button.empty-chip").first.click()
            option = page.locator(f"{OVERLAY} .asset-item").filter(has_text=args.end)
            await option.first.wait_for(state="visible", timeout=5_000)
            await option.first.click()
            await page.locator(PICKER_ADD).first.click()
            report["frames_bound_s"] = round(time.time() - started, 2)

            pane = await open_pane(page)
            radios = pane.locator("[role='radio']")
            await radios.filter(has_text=args.resolution).first.click()
            await radios.filter(has_text=re.compile(rf"^\s*{args.seconds}\b")).first.click()
            await radios.filter(has_text=re.compile(r"^\s*x1\s*$")).first.click()
            await page.wait_for_timeout(300)
            report["pane_text"] = " ".join((await pane.inner_text()).split())[:400]
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            report["chip"] = " | ".join(
                (await page.locator(".settings-trigger-button").first.inner_text()).split("\n")
            )

            composer = page.locator(f"{BOX} [contenteditable='true']").first
            await composer.click()
            await page.keyboard.insert_text(args.prompt)
            await page.wait_for_timeout(300)
            click_t = time.time()
            await page.locator(f"{BOX} button:has(mat-icon:text-is('arrow_forward'))").first.click()
            deadline = time.time() + args.timeout
            while "video_url" not in found and time.time() < deadline:
                await asyncio.sleep(0.25)
            if "submit_t" in found:
                report["click_to_request_s"] = round(found["submit_t"] - click_t, 2)
            if "accepted_t" in found:
                report["request_to_accepted_s"] = round(found["accepted_t"] - found["submit_t"], 2)
            if "video_url" in found:
                report["click_to_video_url_s"] = round(found["video_t"] - click_t, 1)
                dl_started = time.time()
                response = await page.context.request.get(found["video_url"])
                body = await response.body()
                path = paths.captures_dir() / f"clip-{time.strftime('%H%M%S')}.mp4"
                path.write_bytes(body)
                report["download"] = {
                    "http": response.status,
                    "bytes": len(body),
                    "mp4": body[4:8] == b"ftyp",
                    "s": round(time.time() - dl_started, 2),
                    "file": str(path),
                }
        finally:
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)
            await session.detach()
    sys.stdout.reconfigure(encoding="utf-8")
    report["events"] = report["events"][:40]
    print(json.dumps(report, indent=1, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id")
    parser.add_argument("start")
    parser.add_argument("end")
    parser.add_argument("--profile", default=paths.DEFAULT_PROFILE)
    parser.add_argument("--seconds", type=int, default=4)
    parser.add_argument("--resolution", default="360p")
    parser.add_argument("--prompt", default="the teapot smoothly morphs into the red bowl, flat 2D")
    parser.add_argument("--timeout", type=float, default=480)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
