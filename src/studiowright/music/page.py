"""Drive flowmusic.app the way a person does, and read the replies its page receives.

Only a person's actions are used: typing into panels, flipping switches, choosing menu
entries and pressing buttons. The page's own /__api replies are read passively (see
``music.wire``) to learn what was made and where its audio lives.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from playwright.async_api import Page, Request, Response
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from studiowright.browser import ChromeSession, capture_download, ensure_rendering
from studiowright.flow.page import FlowError
from studiowright.music.options import (
    DEFAULT_UPLOAD_TIMEOUT_S,
    AudioFormat,
    Cover,
    EditMode,
    EditRequest,
    MusicRequest,
    VideoTemplate,
    format_length,
    format_time,
    validate_upload_timeout,
)
from studiowright.music.wire import UPLOAD_PENDING, Clip, Turn, clips_in, sse_events, upload_state

log = logging.getLogger("studiowright.music")

ROOT = "https://www.flowmusic.app"
SESSION_URL = f"{ROOT}/session"
LIBRARY_URL = f"{ROOT}/library/my-songs"

SIGNED_IN = "a[href='/library/my-songs']"
COMPOSE_TOGGLE = "button[aria-label='Toggle compose panel']"
LYRICS = "textarea[aria-label='Lyrics']"
SOUND = "textarea[aria-label='Sound description']"
INSTRUCTION = "textarea[aria-label='Instruction']"
INSTRUMENTAL = "button[aria-label='Toggle instrumental mode']"
ADVANCED = "button[aria-label='Toggle advanced sound mode']"
SECTION = "button[aria-label='Expand {name} section']"
TITLE = "xpath=//*[normalize-space(text())='Details']/following::textarea[1]"
#: A song page's own "..." menu; the player bar's menu sits outside <main>.
SONG_MENU = "main button[aria-haspopup='menu']"
CHAT_BOX = "textarea[aria-label='Chat message']"
ATTACH = "button[aria-label='Add audio or image']"
AUDIO_INPUT = "input[type='file'][accept^='audio']"
IMAGE_INPUT = "input[type='file'][accept='image/*']"
POPOVER = "[data-radix-popper-content-wrapper]"
DIALOG = "[role='dialog']"

#: The Details section's cover preview (next to "Prompt image") once loaded, else null.
COVER_LOADED_JS = r"""
() => {
  const button = [...document.querySelectorAll('button')]
    .find((b) => (b.innerText || '').trim() === 'Prompt image');
  let box = button;
  for (let i = 0; i < 5 && box; i++) box = box.parentElement;
  const image = box && box.querySelector('img');
  return image && image.complete && image.naturalWidth > 0 ? image.src : null;
}
"""

#: Song kinds (``op_type``) each action makes.
SONG_KINDS = {"audio__create_song"}
EDIT_KINDS = {"audio__render_edit", "audio__create_song"}
TRIM_KINDS = {"audio__apply_effect"}
STEM_KINDS = {"audio__split_stems"}
#: Allowed difference between this computer's clock and Flow Music's.
CLOCK_SKEW_S = 30
#: How long results may lag behind Flow Music's own "done".
RESULT_GRACE_S = 180

#: Requests that start work on Flow Music's side (and may spend credits).
_SUBMIT_PATHS = ("/producer/tool-call", "/conversation", "/producer/upload-audio")
#: How long a pressed button may go without starting anything before it counts as not
#: pressed (seen once: a session opened, nothing was sent, nothing was spent).
START_GRACE_S = 45

#: Replies whose failure means the action failed.
_FAIL_PATHS = ("/producer", "/audio-create", "/conversation", "/video", "/download")


def _input_after(label: str) -> str:
    return f"xpath=//*[normalize-space(text())='{label}']/following::input[1]"


class NotStarted(FlowError):
    """The action sent nothing to Flow Music, so nothing was spent; it is safe to retry."""


class Watch:
    """Collects the page's own /__api replies while one action runs."""

    def __init__(
        self, page: Page, known: set[str] | None = None, kinds: set[str] | None = None
    ) -> None:
        self.page = page
        self.known = set(known or ())
        #: Only songs of these kinds (``op_type``) count as results; None takes any kind.
        self.kinds = kinds
        self.started = time.time()
        self.bodies: list[tuple[str, int, str]] = []
        self.tasks: list[asyncio.Future[None]] = []
        self.changed = asyncio.Event()
        self.ops: dict[str, dict[str, Any]] = {}
        self.clips: dict[str, Clip] = {}
        self.jobs: set[str] = set()
        self.finished_jobs: set[str] = set()
        self.turn = Turn()
        self.uploads: dict[str, tuple[str, str | None]] = {}
        self.library: list[Clip] | None = None
        #: New songs of other kinds, kept to explain a result that never came.
        self.others: dict[str, str | None] = {}
        #: Work-starting requests the page sent (see ``_SUBMIT_PATHS``).
        self.submitted = 0
        self._seen = 0

    def __enter__(self) -> Watch:
        self.page.on("response", self._on_response)
        self.page.on("request", self._on_request)
        return self

    def __exit__(self, *_: object) -> None:
        self.page.remove_listener("response", self._on_response)
        self.page.remove_listener("request", self._on_request)

    def _on_request(self, request: Request) -> None:
        if request.method == "POST" and "/__api/" in request.url:
            path = request.url.split("/__api", 1)[1].split("?")[0]
            if path in _SUBMIT_PATHS:
                self.submitted += 1

    def _on_response(self, response: Response) -> None:
        if "/__api/" in response.url:
            self.tasks.append(asyncio.ensure_future(self._read(response)))

    async def _read(self, response: Response) -> None:
        try:
            text = await response.text()
        except Exception:
            return
        path = response.url.split("/__api", 1)[1]
        self.bodies.append((path, response.status, text))
        self.changed.set()

    def _wanted(self, clip: Clip) -> bool:
        """A song this action made: not seen before, of the right kind, created after the
        action began (pages also fetch recent songs for their lists and player)."""
        if clip.clip_id in self.known:
            return False
        if self.kinds is not None and clip.kind not in self.kinds:
            return False
        created = clip.created
        return created is None or created.timestamp() >= self.started - CLOCK_SKEW_S

    def digest(self) -> bool:
        """Read new replies; True when something relevant arrived."""
        relevant = False
        for full_path, status, text in self.bodies[self._seen :]:
            self._seen += 1
            path = full_path.split("?")[0]
            if status >= 400 and path.startswith(_FAIL_PATHS):
                raise FlowError(f"Flow Music answered HTTP {status} on {path}: {text[:200]}")
            data: Any = None
            with contextlib.suppress(ValueError):
                data = json.loads(text)
            if path in ("/producer/tool-call", "/conversation") and isinstance(data, dict):
                if data.get("job_id"):
                    self.jobs.add(data["job_id"])
                    relevant = True
            elif path.startswith("/messages/") and path.endswith("/stream"):
                job = path.split("/")[2]
                events = sse_events(text)
                self.turn.merge(events)
                if any(name == "final" for name, _ in events):
                    self.finished_jobs.add(job)
                relevant = True
            elif path.startswith("/audio-create-song-status/") and isinstance(data, dict):
                key = data.get("operation_id") or path
                self.ops[key] = data
                state = data.get("status")
                log.info("song %s: %s", data.get("clip_id"), state)
                if state in ("failed", "error") or data.get("error_type"):
                    reason = data.get("error_type") or state
                    raise FlowError(f"Flow Music could not make the song: {reason}")
                relevant = True
            elif path.startswith("/clips/auth-user") and isinstance(data, dict):
                self.library = clips_in(data)
            elif path == "/clips" and isinstance(data, dict):
                for clip in clips_in(data):
                    if self._wanted(clip):
                        self.clips[clip.clip_id] = clip
                        relevant = True
                    elif clip.clip_id not in self.known and self.kinds is not None:
                        self.others[clip.clip_id] = clip.kind
            elif path.startswith("/producer/upload-audio") and isinstance(data, dict):
                if path == "/producer/upload-audio" and data.get("id"):
                    self.uploads.setdefault(data["id"], ("pending", None))
                elif "upload-check-status" in path:
                    upload = path.split("/")[3]
                    self.uploads[upload] = upload_state(data)
                    log.info("upload %s: %s", upload, self.uploads[upload][0])
                relevant = True
        return relevant

    def ready_clips(self) -> list[Clip]:
        return [c for c in self.clips.values() if c.complete]

    async def wait(
        self,
        *,
        timeout_s: float,
        min_clips: int = 0,
        need_turn: bool = False,
        settle_s: float = 4.0,
        until: Callable[[Watch], bool] | None = None,
        start_s: float | None = None,
    ) -> None:
        """Wait until the action's turn has ended, its songs are complete and nothing new
        arrived for ``settle_s``. With ``start_s``, raise :class:`NotStarted` when nothing
        was sent to Flow Music within that time."""
        begun = time.monotonic()
        deadline = begun + timeout_s
        settled_at: float | None = None
        finished_at: float | None = None
        while time.monotonic() < deadline:
            self.changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), timeout=1.0)
            if self.digest():
                settled_at = None
            idle = not (self.submitted or self.jobs or self.ops or self.clips)
            if start_s is not None and idle and time.monotonic() - begun > start_s:
                raise NotStarted("Flow Music did not start the job; nothing was spent")
            turn_done = self.jobs <= self.finished_jobs and (
                not need_turn or bool(self.finished_jobs)
            )
            ops_done = all(o.get("status") == "complete" for o in self.ops.values())
            clips_done = len(self.ready_clips()) >= min_clips and all(
                c.complete for c in self.clips.values()
            )
            extra = until(self) if until else True
            if turn_done and ops_done and self.jobs and not clips_done:
                # Flow Music says it is done, yet the expected songs have not shown up.
                finished_at = finished_at or time.monotonic()
                if time.monotonic() - finished_at > RESULT_GRACE_S:
                    seen = sorted(set(self.others.values()) - {None}) or ["none"]
                    raise FlowError(
                        f"Flow Music finished, but no new song of kind {sorted(self.kinds or [])}"
                        f" arrived (new kinds seen: {', '.join(map(str, seen))})"
                    )
            else:
                finished_at = None
            if turn_done and ops_done and clips_done and extra:
                settled_at = settled_at or time.monotonic()
                if time.monotonic() - settled_at >= settle_s:
                    return
            else:
                settled_at = None
        raise FlowError(f"Flow Music did not finish within {timeout_s:.0f} s")

    async def drain(self) -> None:
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.digest()


class MusicPage:
    def __init__(self, page: Page, session: ChromeSession) -> None:
        self.page = page
        self.session = session
        self.credits_remaining: float | None = None
        self.plan: str | None = None

    # --- navigation -----------------------------------------------------------------------
    @classmethod
    async def open(cls, session: ChromeSession, url: str = SESSION_URL) -> MusicPage:
        music = cls(await session.page_for(ROOT), session)
        await ensure_rendering(session, music.page)
        await music.goto(url, read_billing=True)
        return music

    async def goto(self, url: str, *, read_billing: bool = False) -> None:
        bodies: dict[str, str] = {}
        tasks: list[asyncio.Future[None]] = []

        async def grab(response: Response) -> None:
            with contextlib.suppress(Exception):
                bodies[response.url.split("/__api")[1].split("?")[0]] = await response.text()

        def on_response(response: Response) -> None:
            if read_billing and "/__api/billing/" in response.url:
                tasks.append(asyncio.ensure_future(grab(response)))

        self.page.on("response", on_response)
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            try:
                await self.page.locator(SIGNED_IN).first.wait_for(state="attached", timeout=20_000)
            except Exception as exc:
                raise FlowError(
                    "Flow Music is not signed in for this profile; run "
                    "`sw stop` then `sw auth login --url https://www.flowmusic.app/`"
                ) from exc
            if read_billing:
                # The page fetches its credit balance and plan on load; wait for both.
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and len(bodies) < 2:
                    await asyncio.sleep(0.25)
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self.page.remove_listener("response", on_response)
        if read_billing:
            self._read_billing(bodies)

    def _read_billing(self, bodies: dict[str, str]) -> None:
        with contextlib.suppress(KeyError, ValueError, TypeError):
            credits = json.loads(bodies["/billing/credits"])["data"]
            self.credits_remaining = credits["credits_remaining"]
        with contextlib.suppress(KeyError, ValueError, TypeError):
            self.plan = json.loads(bodies["/billing/subscription"])["data"]["subscription_tier"]

    async def goto_song(self, clip_id: str) -> None:
        await self.goto(f"{ROOT}/song/{clip_id}")
        try:
            await self.page.locator(SONG_MENU).first.wait_for(state="visible", timeout=20_000)
        except Exception as exc:
            raise FlowError(f"song {clip_id} was not found in this Flow Music account") from exc

    async def song_menu(self, *path: str) -> None:
        """Choose an entry in the open song page's "..." menu, through submenus by keyboard
        (pointer travel closes Radix submenus)."""
        await self.page.locator(SONG_MENU).first.click()
        for index, name in enumerate(path):
            item = self.page.get_by_role("menuitem", name=name, exact=True)
            try:
                await item.wait_for(state="visible", timeout=8_000)
            except PlaywrightTimeoutError as exc:
                visible = await self.page.get_by_role("menuitem").all_inner_texts()
                available = ", ".join(text.strip() for text in visible if text.strip()) or "none"
                raise FlowError(
                    f"Flow Music action {' > '.join(path[:index + 1])!r} is not available "
                    f"in this song's open menu. Available entries: {available}. "
                    "The upload may have succeeded even when this editing action is unavailable. "
                    "Check the song in Flow Music; no automatic fallback or resubmission was made."
                ) from exc
            await item.focus()
            await self.page.keyboard.press("ArrowRight" if index < len(path) - 1 else "Enter")
            await self.page.wait_for_timeout(250)

    # --- library --------------------------------------------------------------------------
    async def library(self) -> list[Clip]:
        """The account's newest songs (the first page of Songs → Library)."""
        with Watch(self.page) as watch:
            await self.goto(LIBRARY_URL)
            deadline = time.monotonic() + 15
            while watch.library is None and time.monotonic() < deadline:
                await asyncio.sleep(0.25)
                await asyncio.gather(*watch.tasks, return_exceptions=True)
                watch.digest()
        if watch.library is None:
            raise FlowError("the song library did not load")
        return watch.library

    async def resolve(self, song: str) -> Clip | str:
        """A full clip id as-is; ``last`` or an id prefix through the library."""
        if len(song) == 36:
            return song
        songs = await self.library()
        if song == "last":
            if not songs:
                raise FlowError("the library is empty")
            return songs[0]
        matches = [c for c in songs if c.clip_id.startswith(song)]
        if len(matches) != 1:
            raise FlowError(
                f"{len(matches)} songs match {song!r} among the newest {len(songs)}; "
                "use the full id"
            )
        return matches[0]

    # --- compose panel ----------------------------------------------------------------------
    async def _switch(self, selector: str, on: bool) -> None:
        switch = self.page.locator(selector)
        if (await switch.get_attribute("aria-checked") == "true") != on:
            await switch.click()

    async def _expand(self, name: str) -> None:
        button = self.page.locator(SECTION.format(name=name))
        if await button.count() and await button.first.get_attribute("aria-expanded") == "false":
            await button.first.click()

    async def compose(self, request: MusicRequest) -> None:
        sys.stderr.write("__SW_STAGE__Opening Flow Music Studio\n")
        sys.stderr.flush()
        page = self.page
        sound = page.locator(SOUND)
        if not await sound.is_visible():
            await page.locator(COMPOSE_TOGGLE).click()
        await sound.wait_for(state="visible", timeout=10_000)
        sys.stderr.write("__SW_STAGE__Submitting lyrics & sound prompt\n")
        sys.stderr.flush()
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
        await self._details(request.title, request.cover)
        log.info("compose panel filled (instrumental=%s)", request.instrumental)

    async def _details(self, title: str | None, cover: Cover | None) -> None:
        await self._expand("Details")
        if title is not None:
            await self.page.locator(TITLE).fill(title)
        if cover is not None:
            await self._cover(cover)

    async def _cover(self, cover: Cover) -> None:
        page = self.page
        before = await page.evaluate(COVER_LOADED_JS)
        if cover.image is not None:
            try:
                async with page.expect_file_chooser(timeout=8_000) as chooser:
                    await page.get_by_role("button", name="Upload image").click()
                await (await chooser.value).set_files(str(cover.image))
            except Exception:
                await page.locator(IMAGE_INPUT).last.set_input_files(str(cover.image))
            await self._cover_preview(before)
            log.info("cover art uploaded")
            return
        await page.get_by_role("button", name="Prompt image").click()
        popover = page.locator(POPOVER).last
        await popover.locator("input, textarea").first.fill(cover.prompt or "")
        # The popover closes at once; the image is drawn in the background (~15 s).
        await popover.get_by_role("button", name="Generate").click()
        await self._cover_preview(before)
        log.info("cover art drawn from a prompt")

    async def _cover_preview(self, before: str | None, timeout_s: float = 120) -> None:
        """Wait until the Details section shows the new cover image, fully loaded, so the
        song is not submitted with the default art."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            shown = await self.page.evaluate(COVER_LOADED_JS)
            if shown and shown != before:
                return
            await asyncio.sleep(0.5)
        raise FlowError(f"the cover art did not appear within {timeout_s:.0f} s")

    async def _panel_settings(self, values: list[str]) -> None:
        settings = self.page.locator("button[aria-haspopup='dialog']").filter(
            has_text=re.compile(r"^\s*settings\s*$", re.I)
        )
        await settings.first.click()
        popover = self.page.locator(POPOVER).last
        inputs = popover.locator("input")
        await inputs.first.wait_for(state="visible", timeout=5_000)
        for index, value in enumerate(values):
            if not value:
                continue
            field = inputs.nth(index)
            await field.fill(value)
            await field.press("Enter")
        await self.page.keyboard.press("Escape")

    async def _edit_lyrics(self, lyrics: str) -> None:
        await self.page.get_by_role("button", name="Edit Lyrics").click()
        dialog = self.page.locator(DIALOG).last
        editor = dialog.locator("textarea, [contenteditable='true']").last
        await editor.wait_for(state="visible", timeout=5_000)
        await editor.fill(lyrics)
        await dialog.get_by_role("button", name="Done").click()

    async def compose_edit(self, request: EditRequest, clip_id: str) -> None:
        await self.goto_song(clip_id)
        await self.song_menu("Remix", request.mode.label)
        instruction = self.page.locator(INSTRUCTION)
        await instruction.wait_for(state="visible", timeout=15_000)
        if request.mode is not EditMode.COVER and (
            request.start_s is not None or request.end_s is not None
        ):
            await self._panel_settings(
                [
                    format_time(request.start_s) if request.start_s is not None else "",
                    format_time(request.end_s) if request.end_s is not None else "",
                ]
            )
        if request.prompt:
            await instruction.fill(request.prompt)
        if request.lyrics:
            await self._edit_lyrics(request.lyrics)
        if request.seed is not None:
            await self._expand("Advanced")
            await self.page.locator(_input_after("Seed")).fill(str(request.seed))
        await self._details(request.title, request.cover)
        log.info("%s panel filled for %s", request.mode.value, clip_id)

    async def region_label(self) -> str | None:
        label = self.page.get_by_text(re.compile(r"^(Extend|Replace) \d+:\d\d"))
        return (await label.first.inner_text()).strip() if await label.count() else None

    # --- actions ----------------------------------------------------------------------------
    async def _run(
        self,
        trigger: Callable[[], Awaitable[None]],
        *,
        known: set[str] | None = None,
        kinds: set[str] | None = None,
        timeout_s: float = 600,
        min_clips: int = 1,
        need_turn: bool = True,
        settle_s: float = 4.0,
        press_again: bool = False,
    ) -> Watch:
        """Run ``trigger`` and wait for its results. A trigger that sends nothing within
        ``START_GRACE_S`` spent nothing; with ``press_again`` it is run once more."""
        for attempt in (1, 2):
            with Watch(self.page, known, kinds) as watch:
                await trigger()
                try:
                    await watch.wait(
                        timeout_s=timeout_s,
                        min_clips=min_clips,
                        need_turn=need_turn,
                        settle_s=settle_s,
                        start_s=START_GRACE_S,
                    )
                    return watch
                except NotStarted:
                    if attempt == 2 or not press_again:
                        raise
                    log.warning("nothing was sent to Flow Music; pressing again")
        raise AssertionError("unreachable")

    async def generate(
        self,
        *,
        known: set[str] | None = None,
        kinds: set[str] | None = None,
        timeout_s: float = 600,
    ) -> Watch:
        """Press the compose panel's Generate and wait for the new songs."""

        async def press() -> None:
            sys.stderr.write("__SW_STAGE__Rendering audio on Flow server\n")
            sys.stderr.flush()
            await self.page.get_by_role("button", name="Generate", exact=True).last.click()
            log.info("music generation submitted")

        return await self._run(
            press, known=known, kinds=kinds, timeout_s=timeout_s, press_again=True
        )

    async def variation(self, clip_id: str) -> Watch:
        await self.goto_song(clip_id)
        return await self._run(
            lambda: self.song_menu("Remix", "Variation"), known={clip_id}, kinds=SONG_KINDS
        )

    async def trim(self, clip_id: str, start: str, end: str) -> Watch:
        await self.goto_song(clip_id)
        await self.song_menu("Remix", "Trim")
        dialog = self.page.locator(DIALOG).last
        fields = dialog.locator("input:not([type='checkbox'])")
        await fields.first.wait_for(state="visible", timeout=8_000)
        for index, value in enumerate((start, end)):
            await fields.nth(index).fill(value)
            await fields.nth(index).press("Enter")

        async def press() -> None:
            await dialog.get_by_role("button", name="Trim", exact=True).click()

        return await self._run(press, known={clip_id}, kinds=TRIM_KINDS, timeout_s=300)

    async def stems(self, clip_id: str) -> Watch:
        await self.goto_song(clip_id)
        return await self._run(
            lambda: self.song_menu("Split stems"),
            known={clip_id},
            kinds=STEM_KINDS,
            min_clips=4,
            settle_s=6.0,
        )

    async def download(self, clip: Clip | str, audio_format: AudioFormat, target: Path) -> Path:
        """Save one song: WAV/M4A from its storage URL when it is there, otherwise (and for
        MP3) through the song page's Download menu."""
        sys.stderr.write("__SW_STAGE__Downloading audio file\n")
        sys.stderr.flush()
        url = None
        if isinstance(clip, Clip):
            url = clip.wav_url if audio_format is AudioFormat.WAV else None
            url = clip.audio_url if audio_format is AudioFormat.M4A else url
        if url:
            response = await self.page.context.request.get(url)
            if response.status == 200:
                target.write_bytes(await response.body())
                return target
            # Edited songs list a WAV URL before the file exists; the menu converts on demand.
            log.info("storage answered HTTP %s; using the Download menu", response.status)
        clip_id = clip.clip_id if isinstance(clip, Clip) else clip
        await self.goto_song(clip_id)
        saved = await capture_download(
            self.session,
            lambda: self.song_menu("Download", audio_format.value.upper()),
            target.parent,
            timeout_s=300,
        )
        saved.replace(target)
        return target

    async def video(self, clip_id: str, template: VideoTemplate, folder: Path) -> Path:
        """Render the song with a Download → Download video template and save the MP4."""
        await self.goto_song(clip_id)

        async def choose() -> None:
            await self.song_menu("Download", "Download video")
            dialog = self.page.locator(DIALOG).last
            await dialog.get_by_text(template.label, exact=True).first.click()

        failed = asyncio.Event()
        checks: list[asyncio.Future[None]] = []

        async def check(response: Response) -> None:
            with contextlib.suppress(Exception):
                if json.loads(await response.text()).get("status") == "failed":
                    failed.set()

        def on_response(response: Response) -> None:
            if "/__api/video/clip/" in response.url:
                checks.append(asyncio.ensure_future(check(response)))

        self.page.on("response", on_response)
        download = asyncio.ensure_future(
            capture_download(self.session, choose, folder, timeout_s=600)
        )
        failure = asyncio.ensure_future(failed.wait())
        try:
            await asyncio.wait({download, failure}, return_when=asyncio.FIRST_COMPLETED)
            if download.done():
                return download.result()
            download.cancel()
            with contextlib.suppress(BaseException):
                await download
            # Renders fail now and then on Flow Music's side; another template often works.
            raise FlowError(
                f"Flow Music could not render the {template.value} video for this song; "
                "try again or use another template"
            )
        finally:
            failure.cancel()
            self.page.remove_listener("response", on_response)

    async def _attach_menu(self, item: str, confirm_rights: bool) -> None:
        """Open the chat's "+" menu entry, accepting the upload notice only when allowed."""
        await self.page.locator(ATTACH).click()
        await self.page.get_by_role("menuitem").filter(has_text=item).first.click()
        agree = self.page.get_by_role("button", name="I agree")
        with contextlib.suppress(Exception):
            await agree.wait_for(state="visible", timeout=1_500)
        if await agree.count() and await agree.is_visible():
            if not confirm_rights:
                await self.page.keyboard.press("Escape")
                raise FlowError("Flow Music asks you to confirm you have the rights to the file")
            await agree.click()
        await self.page.keyboard.press("Escape")

    async def upload_audio(
        self, path: Path, *, confirm_rights: bool, timeout_s: float = DEFAULT_UPLOAD_TIMEOUT_S
    ) -> str:
        """Attach an audio file in the chat box; returns its song id once Flow Music has
        checked it. The file then works like any song (remix, stems, ...)."""
        validate_upload_timeout(timeout_s)
        await self._attach_menu("Audio", confirm_rights)
        with Watch(self.page) as watch:
            await self.page.locator(AUDIO_INPUT).set_input_files(str(path))

            def checked(w: Watch) -> bool:
                return bool(w.uploads) and all(
                    state not in UPLOAD_PENDING for state, _ in w.uploads.values()
                )

            await watch.wait(timeout_s=timeout_s, until=checked, settle_s=0.5)
        for state, reason in watch.uploads.values():
            if state in ("failed", "error", "rejected"):
                raise FlowError(f"Flow Music did not accept {path.name}: {reason or state}")
            if state != "complete":
                raise FlowError(
                    f"Flow Music returned unknown upload status {state!r} for {path.name}"
                    + (f": {reason}" if reason else "")
                )
        upload_id, (state, _) = next(iter(watch.uploads.items()))
        log.info("uploaded %s as %s (%s)", path.name, upload_id, state)
        return upload_id

    async def attach_image(self, path: Path, *, confirm_rights: bool) -> None:
        await self._attach_menu("Image", confirm_rights)
        await self.page.locator(IMAGE_INPUT).first.set_input_files(str(path))
        await self.page.wait_for_timeout(1500)

    async def chat(self, message: str, *, timeout_s: float = 900) -> Watch:
        """Send a message to the Producer and wait for its turn (and any songs) to finish."""
        box = self.page.locator(CHAT_BOX)
        await box.fill(message)
        send = self.page.get_by_role("button", name="Send message")
        deadline = time.monotonic() + 120
        while not await send.is_enabled():
            if time.monotonic() > deadline:
                raise FlowError("the chat box did not become ready to send")
            await asyncio.sleep(0.5)

        async def press() -> None:
            await send.click()
            log.info("message sent to the Producer")

        return await self._run(
            press, timeout_s=timeout_s, min_clips=0, need_turn=True, settle_s=6.0, press_again=True
        )

    async def inspect(self, clip_id: str) -> dict[str, Any]:
        """Inspect capabilities (cover, remix, download) on the song page."""
        await self.goto_song(clip_id)
        can_cover = True
        can_remix = True
        can_download = True
        try:
            cover_btn = self.page.get_by_role("button", name="Cover", exact=False)
            remix_btn = self.page.get_by_role("button", name="Remix", exact=False)
            download_btn = self.page.get_by_role("button", name="Download", exact=False)
            if await cover_btn.count():
                can_cover = await cover_btn.first.is_enabled()
            if await remix_btn.count():
                can_remix = await remix_btn.first.is_enabled()
            if await download_btn.count():
                can_download = await download_btn.first.is_enabled()
        except Exception:
            pass

        return {
            "song": clip_id,
            "capabilities": {
                "cover": can_cover,
                "remix": can_remix,
                "download": can_download,
            },
            "reason": "",
        }


def stem_name(clip: Clip) -> str:
    """'drums' from 'Song - drums'."""
    title = clip.title or ""
    return title.rsplit(" - ", 1)[-1].strip().lower() if " - " in title else clip.clip_id[:8]
