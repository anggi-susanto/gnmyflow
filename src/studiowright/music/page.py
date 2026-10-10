"""Drive flowmusic.app's compose panel and read the replies it receives.

As with Flow, only a person's actions are used: typing into the panel, flipping its
switches and pressing Generate. The page's own /__api replies are read passively to learn
the new clips and their audio URLs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page, Response

from studiowright.browser import ChromeSession, ensure_rendering
from studiowright.flow.page import FlowError
from studiowright.music.options import MusicRequest, format_length

log = logging.getLogger("studiowright.music")

ROOT = "https://www.flowmusic.app"
SESSION_URL = f"{ROOT}/session"

COMPOSE_TOGGLE = "button[aria-label='Toggle compose panel']"
LYRICS = "textarea[aria-label='Lyrics']"
SOUND = "textarea[aria-label='Sound description']"
INSTRUMENTAL = "button[aria-label='Toggle instrumental mode']"
ADVANCED = "button[aria-label='Toggle advanced sound mode']"
DETAILS = "button[aria-label='Expand Details section']"


def _input_after(label: str) -> str:
    return f"xpath=//*[normalize-space(text())='{label}']/following::input[1]"


TITLE = "xpath=//*[normalize-space(text())='Details']/following::textarea[1]"


@dataclass(frozen=True)
class Clip:
    clip_id: str
    title: str | None
    duration_s: float | None
    wav_url: str | None
    audio_url: str | None
    image_url: str | None


def _clips(payload: Any) -> list[Clip]:
    found = []
    clips = payload.get("clips") if isinstance(payload, dict) else None
    for clip_id, clip in (clips or {}).items():
        duration = (clip.get("duration") or {}).get("value")
        found.append(
            Clip(
                clip_id,
                clip.get("title"),
                float(duration) if duration else None,
                clip.get("wav_url"),
                clip.get("audio_url"),
                clip.get("image_url"),
            )
        )
    return found


class MusicPage:
    def __init__(self, page: Page, session: ChromeSession) -> None:
        self.page = page
        self.session = session
        self.credits_remaining: float | None = None
        self.plan: str | None = None

    @classmethod
    async def open(cls, session: ChromeSession) -> MusicPage:
        music = cls(await session.page_for(ROOT), session)
        await ensure_rendering(session, music.page)
        bodies: dict[str, str] = {}
        tasks: list[asyncio.Future[None]] = []

        async def grab(response: Response) -> None:
            try:
                bodies[response.url.split("/__api")[1].split("?")[0]] = await response.text()
            except Exception:
                return

        def on_response(response: Response) -> None:
            if "/__api/billing/" in response.url:
                tasks.append(asyncio.ensure_future(grab(response)))

        music.page.on("response", on_response)
        try:
            await music.page.goto(SESSION_URL, wait_until="domcontentloaded")
            try:
                await music.page.locator(COMPOSE_TOGGLE).wait_for(state="visible", timeout=20_000)
            except Exception as exc:
                raise FlowError(
                    "Flow Music is not signed in for this profile; run "
                    "`sw stop` then `sw auth login --url https://www.flowmusic.app/`"
                ) from exc
            # The page fetches its credit balance and plan on load; wait for both replies.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and len(bodies) < 2:
                await asyncio.sleep(0.25)
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            music.page.remove_listener("response", on_response)
        music._read_billing(bodies)
        return music

    def _read_billing(self, bodies: dict[str, str]) -> None:
        with contextlib.suppress(KeyError, ValueError, TypeError):
            credits = json.loads(bodies["/billing/credits"])["data"]
            self.credits_remaining = credits["credits_remaining"]
        with contextlib.suppress(KeyError, ValueError, TypeError):
            self.plan = json.loads(bodies["/billing/subscription"])["data"]["subscription_tier"]

    async def _switch(self, selector: str, on: bool) -> None:
        switch = self.page.locator(selector)
        if (await switch.get_attribute("aria-checked") == "true") != on:
            await switch.click()

    async def compose(self, request: MusicRequest) -> None:
        page = self.page
        sound = page.locator(SOUND)
        if not await sound.is_visible():
            await page.locator(COMPOSE_TOGGLE).click()
        await sound.wait_for(state="visible", timeout=10_000)
        await self._switch(INSTRUMENTAL, request.instrumental)
        if not request.instrumental:
            await page.locator(LYRICS).fill(request.lyrics or "")
        await sound.fill(request.sound)
        await self._switch(ADVANCED, True)
        await page.locator(_input_after("BPM")).fill(str(request.bpm) if request.bpm else "")
        length = page.locator(_input_after("Length"))
        await length.fill(format_length(request.length_s) if request.length_s else "")
        await length.press("Tab")
        await page.locator(_input_after("Seed")).fill(
            str(request.seed) if request.seed is not None else ""
        )
        model = page.get_by_role("button", name=request.model.label)
        if not await model.count():
            for other in ("Lyria 3.5", "Lyria 3 Pro"):
                button = page.get_by_role("button", name=other)
                if await button.count():
                    await button.first.click()
                    await (
                        page.get_by_role("menuitem")
                        .filter(has_text=request.model.label)
                        .first.click()
                    )
                    break
        details = page.locator(DETAILS)
        if await details.count() and await details.get_attribute("aria-expanded") == "false":
            await details.click()
        await page.locator(TITLE).fill(request.title or "")
        log.info("compose panel filled (instrumental=%s)", request.instrumental)

    async def generate(self, timeout_s: float = 600) -> list[Clip]:
        """Press Generate and wait for every new clip to be complete with its audio URLs."""
        bodies: list[tuple[str, int, str]] = []
        tasks: list[asyncio.Future[None]] = []
        changed = asyncio.Event()

        async def read(response: Response) -> None:
            try:
                text = await response.text()
            except Exception:
                return
            bodies.append((response.url.split("/__api")[1].split("?")[0], response.status, text))
            changed.set()

        def on_response(response: Response) -> None:
            if "/__api/" in response.url and response.request.resource_type in ("fetch", "xhr"):
                tasks.append(asyncio.ensure_future(read(response)))

        self.page.on("response", on_response)
        try:
            generate = self.page.get_by_role("button", name="Generate")
            await generate.click()
            log.info("music generation submitted")
            started = time.monotonic()
            operations: dict[str, dict[str, Any]] = {}
            clips: dict[str, Clip] = {}
            seen = 0
            settled_at: float | None = None
            while time.monotonic() - started < timeout_s:
                changed.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(changed.wait(), timeout=2.0)
                for path, status, text in bodies[seen:]:
                    seen += 1
                    if status >= 400 and path.startswith(("/producer", "/audio-create")):
                        raise FlowError(
                            f"Flow Music answered HTTP {status} on {path}: {text[:200]}"
                        )
                    if path.startswith("/audio-create-song-status/"):
                        try:
                            state = json.loads(text)
                        except ValueError:
                            continue
                        operations[state.get("operation_id", path)] = state
                        log.info("song %s: %s", state.get("clip_id"), state.get("status"))
                        if state.get("status") in ("failed", "error") or state.get("error_type"):
                            reason = state.get("error_type") or state.get("status")
                            raise FlowError(f"Flow Music could not make the song: {reason}")
                    elif path == "/clips":
                        try:
                            for clip in _clips(json.loads(text)):
                                clips[clip.clip_id] = clip
                        except ValueError:
                            continue
                done_ids = {
                    o.get("clip_id") for o in operations.values() if o.get("status") == "complete"
                }
                ready = [clips[c] for c in done_ids if c in clips and clips[c].wav_url]
                all_done = operations and all(
                    o.get("status") == "complete" for o in operations.values()
                )
                if ready and all_done:
                    settled_at = settled_at or time.monotonic()
                    # A second take may still be announced; give it a moment.
                    if time.monotonic() - settled_at > 4:
                        return ready
            raise FlowError(f"no finished song within {timeout_s:.0f} s")
        finally:
            self.page.remove_listener("response", on_response)
