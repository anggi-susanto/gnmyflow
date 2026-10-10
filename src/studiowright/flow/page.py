"""Drive one Flow project tab through its own UI and read the replies it receives.

Everything here is something a person could do in the page: click, type, choose a file.
Requests are never intercepted or changed; replies are only read, through passive
listeners, to learn ids, signed URLs and status.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import FileChooser, Locator, Page, Request, Response

from studiowright.browser import ChromeSession, capture_download, ensure_rendering
from studiowright.flow import batchexecute as be
from studiowright.flow import replies
from studiowright.flow import selectors as s
from studiowright.flow.options import ImageOptions, VideoOptions, count_pattern, duration_pattern

FLOW_ROOT = "https://flow.google.com/"
IMAGE_RPC = "ogiZ0b"
UPLOAD_RPC = "maseQ"
VIDEO_SUBMIT_RPCS = ("YhhmEf", "eb1hJf", "MZZa6b", "nprQif")
VIDEO_STATUS_RPCS = ("jwpduf", "as29s")
UNUSUAL = "UNUSUAL_ACTIVITY"


class FlowError(RuntimeError):
    """Flow did not do what was asked."""


class UnusualActivity(FlowError):
    """Flow refused with an unusual-activity error; the profile must cool down."""


class GenerationRefused(FlowError):
    """Flow refused the generation, e.g. for its content policy."""


class AssetNotFound(FlowError):
    """The media id is not on this project's grid."""


@dataclass(frozen=True)
class VideoClip:
    media_id: str
    url: str
    poster_url: str | None
    workflow_id: str | None = None


def _rpc(url: str) -> str:
    match = re.search(r"[?&]rpcids=([A-Za-z0-9]+)", url)
    return match.group(1) if match else ""


def _asb_token(src: str) -> str | None:
    match = re.search(r"/asb/([^=?]+)", src)
    return match.group(1) if match else None


class ReplyWatch:
    """Collect the bodies of selected RPC replies while it is attached to a page."""

    def __init__(
        self, page: Page, rpcs: Sequence[str], on_request: Callable[[str], None] | None = None
    ) -> None:
        self.page = page
        self.rpcs = set(rpcs)
        self.on_request_cb = on_request
        self.bodies: list[tuple[str, int, str]] = []
        self._tasks: list[asyncio.Future[None]] = []
        self.changed = asyncio.Event()

    def _on_request(self, request: Request) -> None:
        rpc = _rpc(request.url)
        if rpc in self.rpcs and self.on_request_cb:
            self.on_request_cb(rpc)

    async def _read(self, response: Response, rpc: str) -> None:
        try:
            text = await response.text()
        except Exception:  # the body is gone if the page navigated; nothing to learn
            return
        self.bodies.append((rpc, response.status, text))
        self.changed.set()

    def _on_response(self, response: Response) -> None:
        rpc = _rpc(response.url)
        if rpc in self.rpcs:
            self._tasks.append(asyncio.ensure_future(self._read(response, rpc)))

    def __enter__(self) -> ReplyWatch:
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        return self

    def __exit__(self, *_: object) -> None:
        self.page.remove_listener("request", self._on_request)
        self.page.remove_listener("response", self._on_response)

    async def wait(self, timeout_s: float) -> bool:
        """Wait for the next reply; False on timeout."""
        self.changed.clear()
        try:
            await asyncio.wait_for(self.changed.wait(), timeout=timeout_s)
        except TimeoutError:
            return False
        return True


def _raise_for_refusal(exc: be.RpcError) -> None:
    if any(UNUSUAL in reason for reason in exc.reasons):
        raise UnusualActivity(str(exc)) from exc
    raise GenerationRefused(str(exc)) from exc


class FlowPage:
    def __init__(self, page: Page, project_id: str, session: ChromeSession | None = None) -> None:
        self.page = page
        self.project_id = project_id
        self._session = session

    @property
    def session(self) -> ChromeSession:
        if self._session is None:
            raise RuntimeError("this FlowPage was created without a Chrome session")
        return self._session

    @property
    def url(self) -> str:
        return f"{FLOW_ROOT}project/{self.project_id}"

    @classmethod
    async def open(cls, session: ChromeSession, project_id: str) -> FlowPage:
        flow = cls(await session.page_for(f"{FLOW_ROOT}project/{project_id}"), project_id, session)
        await ensure_rendering(session, flow.page)
        if not flow.page.url.startswith(flow.url):
            await flow.page.goto(flow.url, wait_until="domcontentloaded")
        await flow.ready()
        return flow

    # --- page state -------------------------------------------------------------------
    async def ready(self, timeout_s: float = 30) -> None:
        try:
            await self.page.locator(s.READY).first.wait_for(
                state="visible", timeout=timeout_s * 1000
            )
        except Exception as exc:
            raise FlowError(
                f"the Flow project did not become ready ({self.page.url}); "
                "is the profile signed in?"
            ) from exc
        await self.clear_blockers()

    async def clear_blockers(self) -> None:
        for selector in (s.COOKIE_REJECT, s.DIALOG_CLOSE):
            button = self.page.locator(selector).first
            if await button.count() and await button.is_visible():
                await button.click()
                await self.page.wait_for_timeout(300)

    async def reload(self) -> None:
        await self.page.goto(self.url, wait_until="domcontentloaded")
        await self.ready()

    async def attachments(self) -> int:
        return (
            await self.page.locator(s.BOUND_CHIP).count()
            + await self.page.locator(s.MENTION_CHIP).count()
        )

    async def reset(self) -> None:
        """Close popups and empty the prompt box: text and every attached reference."""
        await self.page.keyboard.press("Escape")
        clear = self.page.locator(s.BOX_CLEAR).first
        if await clear.count() and await clear.is_visible():
            await clear.click()
        deadline = time.monotonic() + 3
        while await self.attachments() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if await self.attachments():
            await self.reload()

    # --- settings ---------------------------------------------------------------------
    async def chip_lines(self) -> list[str]:
        text = await self.page.locator(s.READY).first.inner_text()
        return [line.strip() for line in text.split("\n") if line.strip()]

    async def _settings_pane(self) -> Locator:
        pane = self.page.locator(s.SETTINGS_PANE)
        for _ in range(4):
            if await pane.count():
                return pane.first
            await self.page.mouse.move(5, 5)
            await self.page.locator(s.READY).first.click()
            try:
                await pane.first.wait_for(state="visible", timeout=1500)
            except Exception:
                await self.page.wait_for_timeout(300)
        await pane.first.wait_for(state="visible", timeout=3000)
        return pane.first

    async def _radio(
        self, pane: Locator, *, icon: str | None = None, text: str | re.Pattern[str] | None = None
    ) -> None:
        radios = pane.locator(s.RADIO)
        target = (
            radios.filter(has=self.page.locator(f"mat-icon:text-is('{icon}')"))
            if icon
            else radios.filter(has_text=text)
        )
        if not await target.count():
            raise FlowError(f"Flow offers no option {icon or text!r} here")
        radio = target.first
        if await radio.get_attribute("aria-checked") != "true":
            await radio.click()
            await self.page.wait_for_timeout(150)

    async def _model(self, pane: Locator, label: str) -> None:
        button = pane.locator(s.MODEL_MENU_BUTTON).first
        current = " ".join((await button.inner_text()).replace("arrow_drop_down", "").split())
        if current.endswith(label):
            return
        await button.click()
        item = self.page.locator(s.MENU_ITEM).filter(
            has_text=re.compile(re.escape(label) + r"\s*$")
        )
        try:
            await item.first.wait_for(state="visible", timeout=5000)
        except Exception as exc:
            await self.page.keyboard.press("Escape")
            raise FlowError(f"the model {label!r} is not offered on this account") from exc
        await item.first.click()
        await self.page.wait_for_timeout(200)

    async def apply_image_options(self, options: ImageOptions) -> None:
        lines = await self.chip_lines()
        if (
            len(lines) >= 3
            and lines[0].endswith(options.model.label)
            and lines[1] == options.aspect.icon
            and lines[2] == f"x{options.count}"
        ):
            return
        pane = await self._settings_pane()
        await self._radio(pane, icon=s.MODE_IMAGE)
        await self._model(pane, options.model.label)
        await self._radio(pane, icon=options.aspect.icon)
        await self._radio(pane, text=count_pattern(options.count))
        await self.page.keyboard.press("Escape")
        await self.page.wait_for_timeout(250)

    async def apply_video_options(self, options: VideoOptions) -> None:
        pane = await self._settings_pane()
        await self._radio(pane, icon=s.MODE_VIDEO)
        await self._radio(pane, icon=options.mode.icon)
        await self._model(pane, options.model.label)
        await self._radio(pane, icon=options.aspect.icon)
        if options.resolution:
            await self._radio(pane, text=options.resolution)
        if options.duration:
            await self._radio(pane, text=duration_pattern(options.duration))
        await self._radio(pane, text=count_pattern(options.count))
        await self.page.keyboard.press("Escape")
        await self.page.wait_for_timeout(250)

    # --- media ------------------------------------------------------------------------
    async def _open_file_chooser(self) -> FileChooser:
        """Ctrl+U opens the upload chooser, but not while the prompt box has focus (there it
        means underline). Leave the box first; fall back to the toolbar's add menu."""
        await self.page.keyboard.press("Escape")
        await self.page.evaluate("() => document.activeElement && document.activeElement.blur()")
        try:
            async with self.page.expect_file_chooser(timeout=2500) as chooser_info:
                await self.page.keyboard.press("Control+U")
            return await chooser_info.value
        except Exception:
            pass
        await self.page.locator(s.TOOLBAR_ADD).first.click()
        item = self.page.locator(s.MENU_UPLOAD).first
        await item.wait_for(state="visible", timeout=5000)
        async with self.page.expect_file_chooser(timeout=8000) as chooser_info:
            await item.click()
        return await chooser_info.value

    async def upload(
        self, files: Sequence[Path], timeout_s: float = 90
    ) -> list[replies.UploadResult]:
        """Upload files through one file chooser, in the order given."""
        with ReplyWatch(self.page, [UPLOAD_RPC]) as watch:
            chooser = await self._open_file_chooser()
            await chooser.set_files([str(f) for f in files])
            deadline = time.monotonic() + timeout_s
            while len(watch.bodies) < len(files) and time.monotonic() < deadline:
                if await self.page.locator("[role='dialog']").count():
                    raise FlowError(
                        "Flow is asking for upload consent; answer it once in the Chrome window"
                    )
                await watch.wait(1.0)
        results: list[replies.UploadResult] = []
        for _rpc_id, _status, text in watch.bodies:
            try:
                results.append(replies.upload_result(be.parse_reply(text, UPLOAD_RPC)))
            except be.RpcError as exc:
                _raise_for_refusal(exc)
        if len(results) < len(files):
            raise FlowError(f"only {len(results)} of {len(files)} uploads finished")
        by_name = {r.name: r for r in results}
        ordered = [by_name.get(Path(f).name) for f in files]
        return [r for r in ordered if r] if all(ordered) else results

    async def _media_img(self, media_id: str, wait_s: float = 8) -> Locator:
        """The grid image of ``media_id``. New media sits at the top; older media further
        down, so look at the top first and only then scroll."""
        img = self.page.locator(s.media_image(media_id)).first
        if await img.count():
            return img
        await self.page.mouse.move(700, 400)
        await self.page.mouse.wheel(0, -100_000)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if await img.count():
                return img
            await asyncio.sleep(0.1)
        for _ in range(25):
            await self.page.mouse.wheel(0, 900)
            await self.page.wait_for_timeout(300)
            if await img.count():
                return img
        raise AssetNotFound(f"media {media_id} is not on the project grid")

    async def _tile_menu(self, media_id: str, item_selector: str) -> None:
        img = await self._media_img(media_id)
        tile = img.locator(f"xpath=ancestor::{s.IMAGE_TILE}[1]")
        await img.hover()
        await tile.locator(s.TILE_MENU).first.click()
        item = self.page.locator(item_selector).first
        await item.wait_for(state="visible", timeout=5000)
        await item.click()

    async def _wait_attachments(self, at_least: int, timeout_s: float = 6) -> None:
        deadline = time.monotonic() + timeout_s
        while await self.attachments() < at_least:
            if time.monotonic() > deadline:
                raise FlowError("the reference did not attach to the prompt box")
            await asyncio.sleep(0.05)

    async def attach_reference(self, media_id: str) -> None:
        before = await self.attachments()
        await self._tile_menu(media_id, s.MENU_ADD_TO_PROMPT)
        await self._wait_attachments(before + 1)

    async def set_start_frame(self, media_id: str) -> None:
        """Animate from this image: switches to video / Frames with it as the start frame."""
        await self._tile_menu(media_id, s.MENU_ANIMATE)
        await self._wait_attachments(1)

    async def set_end_frame(self, media_id: str) -> None:
        img = await self._media_img(media_id)
        token = _asb_token(await img.get_attribute("src") or "")
        if not token:
            raise AssetNotFound(f"media {media_id} has no thumbnail to match in the picker")
        before = await self.attachments()
        await self.page.locator(s.EMPTY_SLOT).last.click()
        items = self.page.locator(s.PICKER_ITEM)
        await items.first.wait_for(state="visible", timeout=5000)
        match = items.filter(has=self.page.locator(f"img[src*='{token}']"))
        if not await match.count():
            await self.page.keyboard.press("Escape")
            raise AssetNotFound(f"media {media_id} is not offered in the frame picker")
        await match.first.click()
        await self.page.locator(s.PICKER_ADD).first.click()
        await self._wait_attachments(before + 1)

    # --- generate ---------------------------------------------------------------------
    async def _type_and_submit(self, prompt: str) -> None:
        composer = self.page.locator(s.COMPOSER).first
        await composer.click()
        await self.page.keyboard.press("Control+End")
        await self.page.keyboard.insert_text(prompt)
        submit = self.page.locator(s.SUBMIT).first
        deadline = time.monotonic() + 5
        while await submit.is_disabled():
            if await self.page.locator(s.CREDITS_WARNING).count():
                raise FlowError("Flow reports there are not enough credits for this generation")
            if time.monotonic() > deadline:
                raise FlowError("the generate button stayed disabled")
            await asyncio.sleep(0.05)
        await submit.click()

    async def generate_images(
        self, prompt: str, timeout_s: float = 240
    ) -> list[replies.ImageResult]:
        with ReplyWatch(self.page, [IMAGE_RPC]) as watch:
            await self._type_and_submit(prompt)
            deadline = time.monotonic() + timeout_s
            while not watch.bodies and time.monotonic() < deadline:
                await watch.wait(1.0)
        if not watch.bodies:
            raise FlowError(f"no image reply within {timeout_s:.0f} s")
        _rpc_id, status, text = watch.bodies[0]
        try:
            payload = be.parse_reply(text, IMAGE_RPC)
        except be.RpcError as exc:
            _raise_for_refusal(exc)
            raise
        except be.MissingReply as exc:
            raise FlowError(f"unreadable image reply (HTTP {status})") from exc
        results = replies.image_results(payload)
        if not results:
            raise GenerationRefused("Flow answered without any image")
        return results

    async def generate_video(self, prompt: str, timeout_s: float = 600) -> list[VideoClip]:
        submitted: dict[str, Any] = {}
        with ReplyWatch(self.page, [*VIDEO_SUBMIT_RPCS, *VIDEO_STATUS_RPCS]) as watch:
            await self._type_and_submit(prompt)
            deadline = time.monotonic() + timeout_s
            clips: dict[str, replies.VideoRecord] = {}
            urls: dict[str, dict[str, str]] = {}
            seen = 0
            while time.monotonic() < deadline:
                await watch.wait(2.0)
                for rpc_id, status, text in watch.bodies[seen:]:
                    seen += 1
                    try:
                        payload = be.parse_reply(text, rpc_id)
                    except be.RpcError as exc:
                        if rpc_id in VIDEO_SUBMIT_RPCS:
                            _raise_for_refusal(exc)
                        continue
                    except be.MissingReply:
                        continue
                    records = replies.video_records(payload, self.project_id)
                    if rpc_id in VIDEO_SUBMIT_RPCS and "ids" not in submitted:
                        submitted["ids"] = {r.media_id for r in records}
                        if not submitted["ids"]:
                            raise FlowError(f"{rpc_id} reply named no new clip (HTTP {status})")
                    for record in records:
                        if record.media_id in submitted.get("ids", ()):
                            clips[record.media_id] = record
                    for media_id, found in replies.media_urls(payload).items():
                        urls.setdefault(media_id, {}).update(found)
                ids = submitted.get("ids")
                if ids and all("video" in urls.get(i, {}) for i in ids):
                    return [
                        VideoClip(
                            i,
                            urls[i]["video"],
                            urls[i].get("image"),
                            clips[i].workflow_id if i in clips else None,
                        )
                        for i in sorted(ids)
                    ]
                failed = [r for r in clips.values() if r.status not in (None, 2, 3, 6)]
                if failed:
                    raise GenerationRefused(
                        f"Flow reported the clip failed (status {failed[0].status})"
                    )
        pending = " ".join(sorted(submitted.get("ids", ())))
        hint = f"; it may still finish: sw download {pending}" if pending else ""
        raise FlowError(f"the clip was not ready within {timeout_s:.0f} s{hint}")

    async def fresh_media_urls(self) -> dict[str, dict[str, str]]:
        """Signed URLs for every medium in the project, keyed by media id.

        Opening any tile's edit view makes the page fetch fresh URLs for the whole project
        (``as29s``); they are read from that reply, then the page returns to the grid.
        """
        tile = self.page.locator(f"{s.VIDEO_TILE}, {s.IMAGE_TILE}").first
        if not await tile.count():
            raise AssetNotFound("the project has no media yet")
        urls: dict[str, dict[str, str]] = {}
        with ReplyWatch(self.page, ["as29s"]) as watch:
            await tile.click()
            await self.page.wait_for_url(re.compile(r".*/edit/.*"), timeout=10_000)
            # The page answers in several batches; wait until the replies go quiet.
            started = last = time.monotonic()
            while time.monotonic() - started < 20:
                if await watch.wait(0.5):
                    last = time.monotonic()
                elif watch.bodies and time.monotonic() - last > 2.5:
                    break
        for rpc_id, _status, text in watch.bodies:
            try:
                payload = be.parse_reply(text, rpc_id)
            except (be.RpcError, be.MissingReply):
                continue
            for media_id, found in replies.media_urls(payload).items():
                urls.setdefault(media_id, {}).update(found)
            for media_id, workflow_id in replies.workflow_ids(payload).items():
                urls.setdefault(media_id, {}).setdefault("workflow", workflow_id)
        await self.page.goto(self.url, wait_until="domcontentloaded")
        await self.ready()
        return urls

    async def download_size(
        self, workflow_id: str, size: str, folder: Path, timeout_s: float = 900
    ) -> Path:
        """Download through the edit view's own menu, e.g. ``2K`` or ``1080p`` (upscaled).

        The size token is the start of the menu entry, which Flow does not translate.
        Upscaling happens in Flow and can take a while before the file arrives.
        """
        await self.page.goto(f"{self.url}/edit/{workflow_id}", wait_until="domcontentloaded")
        button = self.page.locator(s.EDIT_DOWNLOAD).first
        try:
            await button.wait_for(state="visible", timeout=15_000)
            # The button stays disabled until the edit view has loaded the medium.
            deadline = time.monotonic() + 45
            while await button.is_disabled():
                if time.monotonic() > deadline:
                    raise FlowError("the download button stayed disabled in the edit view")
                await asyncio.sleep(0.25)
            await button.click()
            entries = self.page.locator(f"{s.OVERLAY} {s.MENU_ITEM}, {s.OVERLAY} button")
            await entries.first.wait_for(state="visible", timeout=5000)
            entry = entries.filter(
                has_text=re.compile(rf"^\s*{re.escape(size)}(?![0-9A-Za-z])", re.IGNORECASE)
            )
            if not await entry.count():
                offered = [" ".join(t.split()) for t in await entries.all_inner_texts()]
                raise FlowError(f"size {size!r} is not offered; Flow offers: {offered}")
            if await entry.first.is_disabled():
                raise FlowError(f"size {size!r} is not available on this account")
            return await capture_download(
                self.session, entry.first.click, folder, timeout_s=timeout_s
            )
        finally:
            await self.page.goto(self.url, wait_until="domcontentloaded")
            await self.ready()

    async def download(self, url: str, target: Path) -> Path:
        response = await self.page.context.request.get(url)
        if response.status != 200:
            raise FlowError(f"download failed with HTTP {response.status}")
        body = await response.body()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return target


def suffix_for(body_start: bytes, default: str) -> str:
    if body_start[:4] == b"\x89PNG":
        return ".png"
    if body_start[:2] == b"\xff\xd8":
        return ".jpg"
    if body_start[4:8] == b"ftyp":
        return ".mp4"
    return default
