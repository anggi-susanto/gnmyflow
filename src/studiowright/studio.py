"""StudioWright's operations: one entry point shared by the CLI and the MCP server.

A :class:`Studio` attaches to the profile's Chrome, runs one operation at a time per profile
(a cross-process lock), paces submits through the :class:`Governor`, and records projects,
assets and jobs in the local store.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filelock import FileLock
from playwright.async_api import Playwright, async_playwright

from studiowright import paths
from studiowright.browser import ChromeSession, attach, place_window
from studiowright.flow import batchexecute as be
from studiowright.flow.options import ImageOptions, VideoMode, VideoOptions
from studiowright.flow.page import FLOW_ROOT, FlowError, FlowPage, UnusualActivity, suffix_for
from studiowright.flow.replies import is_uuid
from studiowright.pacing import Governor
from studiowright.store import Store

SESSION_JS = r"""
() => {
  const wiz = window.WIZ_global_data || {};
  return { sid: wiz.FdrFJe || null, build: wiz.cfb2h || null, csrf: wiz.SNlM0e || null,
           locale: document.documentElement.lang || 'en' };
}
"""

FETCH_JS = r"""
async ([url, body]) => {
  const response = await fetch(url, {
    method: 'POST', credentials: 'include',
    headers: {
      'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
      'x-same-domain': '1',
    },
    body,
  });
  return { status: response.status, text: await response.text() };
}
"""


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    options: ImageOptions = field(default_factory=ImageOptions)
    refs: Sequence[str] = ()
    project: str | None = None
    out_dir: Path | None = None
    #: Flow's download size token (e.g. ``2K`` or ``1080p``); None keeps the original.
    size: str | None = None


@dataclass(frozen=True)
class VideoRequest:
    prompt: str
    options: VideoOptions = field(default_factory=VideoOptions)
    start: str | None = None
    end: str | None = None
    refs: Sequence[str] = ()
    project: str | None = None
    out_dir: Path | None = None
    #: Flow's download size token (e.g. ``2K`` or ``1080p``); None keeps the original.
    size: str | None = None
    #: Refuse to submit when Flow's stated price is higher than this.
    max_credits: int | None = None


def _unique(path: Path) -> Path:
    candidate, number = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        number += 1
    return candidate


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Studio:
    def __init__(self, profile: str = paths.DEFAULT_PROFILE, store: Store | None = None) -> None:
        self.profile = profile
        self.store = store or Store()
        self.governor = Governor(self.store, profile)
        self._playwright: Playwright | None = None
        self._session: ChromeSession | None = None
        paths.locks_dir().mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(paths.locks_dir() / f"{profile}.lock"))

    async def __aenter__(self) -> Studio:
        self._playwright = await async_playwright().start()
        self._session = await attach(self._playwright, self.profile)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._session:
            await self._session.detach()
        if self._playwright:
            await self._playwright.stop()

    @property
    def session(self) -> ChromeSession:
        if self._session is None:
            raise RuntimeError("use `async with Studio(...)`")
        return self._session

    async def _locked(self) -> None:
        await asyncio.to_thread(self._lock.acquire)
        # Only one job runs per profile, so any job still marked running died mid-way.
        self.store.mark_interrupted(self.profile)

    # --- projects ---------------------------------------------------------------------
    def default_project(self) -> str | None:
        return self.store.get_setting(f"project.default.{self.profile}")

    def use_project(self, project_id: str) -> None:
        if not is_uuid(project_id):
            raise ValueError(f"not a Flow project id: {project_id!r}")
        self.store.add_project(project_id, self.profile, None)
        self.store.set_setting(f"project.default.{self.profile}", project_id)

    async def create_project(self, title: str | None = None) -> str:
        """Create a Flow project with the page's own project RPC (it carries no token)."""
        title = title or f"StudioWright {time.strftime('%Y-%m-%d %H:%M')}"
        page = await self.session.page_for(FLOW_ROOT)
        if not page.url.startswith(FLOW_ROOT):
            await page.goto(FLOW_ROOT, wait_until="domcontentloaded")
        info = await page.evaluate(SESSION_JS)
        if not info["csrf"]:
            raise FlowError("Flow did not load a signed-in session; run `sw auth login`")
        session = be.Session(info["sid"], info["build"], info["csrf"], info["locale"])
        payload = ["projects/*", [None, [title]], [None, 22]]
        url = be.request_url(
            "jHPbke", session, source_path="/", request_id=random.randint(10_000, 99_999)
        )
        result = await page.evaluate(
            FETCH_JS, [url, be.request_body("jHPbke", payload, session.csrf)]
        )
        reply = be.parse_reply(result["text"], "jHPbke")
        project_id = reply[0]
        if not is_uuid(project_id):
            raise FlowError("Flow did not return a project id")
        self.store.add_project(project_id, self.profile, title)
        if not self.default_project():
            self.store.set_setting(f"project.default.{self.profile}", project_id)
        return project_id

    async def _project(self, requested: str | None) -> str:
        project = requested or self.default_project()
        if project:
            return project
        return await self.create_project()

    # --- window -----------------------------------------------------------------------
    async def window(self, *, visible: bool) -> None:
        page = await self.session.page_for(FLOW_ROOT)
        await place_window(self.session, page, visible=visible)

    # --- assets -----------------------------------------------------------------------
    async def _media_ids(self, flow: FlowPage, refs: Sequence[str]) -> list[str]:
        """Media ids for refs that are ids already or local files (uploaded when new)."""
        resolved: list[str | None] = []
        pending: list[tuple[int, Path, str]] = []
        for index, ref in enumerate(refs):
            if is_uuid(ref):
                resolved.append(ref)
                continue
            path = Path(ref).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"reference not found: {ref}")
            digest = file_sha256(path)
            known = self.store.asset_by_hash(flow.project_id, digest)
            resolved.append(known.media_id if known else None)
            if not known:
                pending.append((index, path, digest))
        if pending:
            paths.staging_dir().mkdir(parents=True, exist_ok=True)
            staged = []
            for _index, path, digest in pending:
                copy = paths.staging_dir() / f"sw-{digest[:10]}{path.suffix.lower()}"
                if not copy.exists():
                    shutil.copyfile(path, copy)
                staged.append(copy)
            results = await flow.upload(staged)
            for (index, path, digest), result in zip(pending, results, strict=True):
                self.store.add_asset(
                    flow.project_id, result.media_id, "image", result.name, digest, str(path)
                )
                resolved[index] = result.media_id
            # No reload: new uploads appear on the grid at once with their final id, while a
            # freshly loaded grid can lag one upload behind.
        return [media_id for media_id in resolved if media_id]

    async def upload(self, files: Sequence[str], project: str | None = None) -> dict[str, Any]:
        await self._locked()
        try:
            project_id = await self._project(project)
            flow = await FlowPage.open(self.session, project_id)
            media_ids = await self._media_ids(flow, files)
            return {"project_id": project_id, "media_ids": media_ids}
        finally:
            self._lock.release()

    async def download(
        self,
        media_ids: Sequence[str],
        project: str | None = None,
        out_dir: Path | None = None,
        size: str | None = None,
    ) -> dict[str, Any]:
        """Download existing media by id (e.g. a clip that finished after a timeout)."""
        await self._locked()
        try:
            project_id = await self._project(project)
            flow = await FlowPage.open(self.session, project_id)
            urls = await flow.fresh_media_urls()
            if any(not any(m.startswith(r.lower()) for m in urls) for r in media_ids):
                urls.update(await flow.fresh_media_urls())
            out = self._out_dir(out_dir)
            files = []
            for requested in media_ids:
                matches = [m for m in urls if m.startswith(requested.lower())]
                if len(matches) > 1:
                    raise FlowError(
                        f"{requested!r} matches {len(matches)} media; give more characters"
                    )
                media_id = matches[0] if matches else requested
                found = urls.get(media_id, {})
                url = found.get("video") or found.get("image")
                if not url:
                    raise FlowError(f"no downloadable file for {media_id} in this project (yet)")
                default = ".mp4" if "video" in found else ".jpg"
                if size:
                    workflow_id = found.get("workflow")
                    if not workflow_id:
                        raise FlowError(f"cannot open {media_id} to download size {size}")
                    saved = await flow.download_size(workflow_id, size, out)
                    target = _unique(out / f"{media_id}-{size}{saved.suffix or default}")
                    saved.rename(target)
                else:
                    target = await flow.download(url, out / f"{media_id}{default}")
                files.append(str(target))
            return {"project_id": project_id, "files": files}
        finally:
            self._lock.release()

    # --- generation -------------------------------------------------------------------
    def _out_dir(self, requested: Path | None) -> Path:
        out = requested or paths.output_dir()
        out.mkdir(parents=True, exist_ok=True)
        return out

    async def _download_all(
        self,
        flow: FlowPage,
        items: list[tuple[str, str, str | None]],
        out: Path,
        stem: str,
        default_suffix: str,
        size: str | None = None,
    ) -> list[str]:
        files = []
        for number, (media_id, url, workflow_id) in enumerate(items, start=1):
            if size:
                if not workflow_id:
                    raise FlowError(f"cannot open {media_id} to download size {size}")
                saved = await flow.download_size(workflow_id, size, out)
                target = _unique(out / f"{stem}-{number}-{size}{saved.suffix or default_suffix}")
                saved.rename(target)
            else:
                target = out / f"{stem}-{number}{default_suffix}"
                await flow.download(url, target)
                real_suffix = suffix_for(target.read_bytes()[:12], default_suffix)
                if real_suffix != default_suffix:
                    target = target.rename(target.with_suffix(real_suffix))
            files.append(str(target))
            self.store.add_asset(
                flow.project_id,
                media_id,
                "video" if default_suffix == ".mp4" else "image",
                target.name,
                None,
                "generated",
            )
        return files

    async def _submit_guarded(self, call: Any) -> Any:
        await self.governor.wait_turn()
        try:
            return await call()
        except UnusualActivity:
            self.governor.report_unusual_activity()
            raise

    async def image(self, request: ImageRequest) -> dict[str, Any]:
        await self._locked()
        project_id = await self._project(request.project)
        job_id = self.store.create_job(
            "image",
            self.profile,
            project_id,
            {
                "prompt": request.prompt,
                "model": request.options.model.value,
                "aspect": request.options.aspect.value,
                "count": request.options.count,
                "refs": list(request.refs),
            },
        )
        started = time.time()
        self.store.update_job(job_id, status="running", started=started)
        try:
            flow = await FlowPage.open(self.session, project_id)
            media_ids = await self._media_ids(flow, request.refs)
            await flow.reset()
            await flow.apply_image_options(request.options)
            for media_id in media_ids:
                await flow.attach_reference(media_id)
            results = await self._submit_guarded(lambda: flow.generate_images(request.prompt))
            stem = time.strftime("%Y%m%d-%H%M%S") + f"-image-{job_id[:6]}"
            files = await self._download_all(
                flow,
                [(r.media_id, r.url, r.workflow_id) for r in results],
                self._out_dir(request.out_dir),
                stem,
                ".jpg",
                request.size,
            )
            result = {
                "job_id": job_id,
                "project_id": project_id,
                "files": files,
                "media_ids": [r.media_id for r in results],
                "sizes": [[r.width, r.height] for r in results],
                "seconds": round(time.time() - started, 1),
            }
            self.store.update_job(job_id, status="done", result=result, finished=time.time())
            return result
        except BaseException as exc:
            self.store.update_job(
                job_id, status="failed", error=f"{type(exc).__name__}: {exc}", finished=time.time()
            )
            raise
        finally:
            self._lock.release()

    async def video(self, request: VideoRequest) -> dict[str, Any]:
        await self._locked()
        project_id = await self._project(request.project)
        options = request.options
        job_id = self.store.create_job(
            "video",
            self.profile,
            project_id,
            {
                "prompt": request.prompt,
                "model": options.model.value,
                "mode": options.mode.value,
                "aspect": options.aspect.value,
                "resolution": options.resolution,
                "duration": options.duration,
                "count": options.count,
                "start": request.start,
                "end": request.end,
                "refs": list(request.refs),
            },
        )
        started = time.time()
        self.store.update_job(job_id, status="running", started=started)
        try:
            flow = await FlowPage.open(self.session, project_id)
            frames = [ref for ref in (request.start, request.end) if ref]
            frame_ids = await self._media_ids(flow, frames)
            ref_ids = await self._media_ids(flow, request.refs)
            await flow.reset()
            if options.mode is VideoMode.FRAMES and request.start:
                await flow.set_start_frame(frame_ids[0])
            credits = await flow.apply_video_options(options)
            if (
                request.max_credits is not None
                and credits is not None
                and credits > request.max_credits
            ):
                raise FlowError(
                    f"Flow prices this clip at {credits} credits, above the limit of "
                    f"{request.max_credits}; nothing was submitted"
                )
            if options.mode is VideoMode.FRAMES and request.end:
                await flow.set_end_frame(frame_ids[-1])
            if options.mode is VideoMode.INGREDIENTS:
                for media_id in ref_ids:
                    await flow.attach_reference(media_id)
            clips = await self._submit_guarded(lambda: flow.generate_video(request.prompt))
            stem = time.strftime("%Y%m%d-%H%M%S") + f"-video-{job_id[:6]}"
            files = await self._download_all(
                flow,
                [(c.media_id, c.url, c.workflow_id) for c in clips],
                self._out_dir(request.out_dir),
                stem,
                ".mp4",
                request.size,
            )
            result = {
                "job_id": job_id,
                "project_id": project_id,
                "files": files,
                "media_ids": [c.media_id for c in clips],
                "credits": credits,
                "seconds": round(time.time() - started, 1),
            }
            self.store.update_job(job_id, status="done", result=result, finished=time.time())
            return result
        except BaseException as exc:
            self.store.update_job(
                job_id, status="failed", error=f"{type(exc).__name__}: {exc}", finished=time.time()
            )
            raise
        finally:
            self._lock.release()
