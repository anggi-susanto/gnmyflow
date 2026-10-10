"""StudioWright as an MCP server (stdio): the same operations as the CLI, for AI agents."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from studiowright import paths
from studiowright.flow.options import (
    COUNTS,
    DURATIONS,
    RESOLUTIONS,
    VIDEO_ASPECTS,
    Aspect,
    ImageModel,
    ImageOptions,
    VideoMode,
    VideoModel,
    VideoOptions,
)
from studiowright.pacing import Governor
from studiowright.store import Store
from studiowright.studio import ImageRequest, Studio, VideoRequest

INSTRUCTIONS = """\
StudioWright drives Google Flow (images and video) in the user's own signed-in Chrome,
through Flow's web UI, using the user's own Flow subscription and credits.

- Images (Nano Banana) cost no credits; videos cost credits (the tool reports what Flow
  charges in Flow's own settings pane, not here). Ask before generating many videos.
- Each call takes real time: about 20-30 s for an image, 30-90 s for a clip.
- Calls are queued and paced per profile so Flow is not flooded. If Flow reports unusual
  activity the profile cools down (~30 min); do not retry during a cooldown.
- References and frames accept a local file path (uploaded once, then reused by content)
  or a Flow media id returned by an earlier call.
- Every result lists the saved file paths and the media ids, which later calls can reuse.
"""

server = MCPServer("studiowright", instructions=INSTRUCTIONS)


def _profile(profile: str | None) -> str:
    return profile or os.environ.get("STUDIOWRIGHT_PROFILE") or paths.DEFAULT_PROFILE


def _out(out_dir: str | None) -> Path | None:
    return Path(out_dir).expanduser() if out_dir else None


@server.tool()
async def flow_options() -> dict[str, Any]:
    """List every model and option Flow offers, with the defaults."""
    return {
        "image": {
            "models": [m.value for m in ImageModel],
            "default_model": ImageModel.NANO_BANANA_2_1.value,
            "aspects": [a.value for a in Aspect],
            "counts": list(COUNTS),
        },
        "video": {
            "models": [m.value for m in VideoModel],
            "default_model": VideoModel.OMNI_FLASH.value,
            "modes": {
                "frames": "optional start/end frames; text-only when none",
                "ingredients": "up to several reference images",
            },
            "aspects": [a.value for a in VIDEO_ASPECTS],
            "resolutions (omni only)": list(RESOLUTIONS),
            "durations_s (omni only)": list(DURATIONS),
            "counts": list(COUNTS),
        },
    }


@server.tool()
async def flow_generate_image(
    prompt: str,
    model: str = ImageModel.NANO_BANANA_2_1.value,
    aspect: str = "16:9",
    count: int = 1,
    refs: list[str] | None = None,
    project: str | None = None,
    out_dir: str | None = None,
    size: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Generate 1-4 images in Google Flow and save them locally.

    refs: reference images, each a local file path or a Flow media id.
    size: download size as Flow labels it ("1K" original, "2K" upscaled); default original.
    Returns file paths, media ids, image sizes and the job id.
    """
    request = ImageRequest(
        prompt,
        ImageOptions.build(model, aspect, count),
        tuple(refs or ()),
        project,
        _out(out_dir),
        size,
    )
    async with Studio(_profile(profile)) as studio:
        return await studio.image(request)


@server.tool()
async def flow_generate_video(
    prompt: str,
    model: str = VideoModel.OMNI_FLASH.value,
    mode: str = VideoMode.FRAMES.value,
    start: str | None = None,
    end: str | None = None,
    refs: list[str] | None = None,
    aspect: str = "16:9",
    resolution: str = "720p",
    duration: int = 8,
    count: int = 1,
    project: str | None = None,
    out_dir: str | None = None,
    size: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Generate a video clip in Google Flow (costs credits) and save it locally.

    mode "frames": optional start and end frame (file path or media id); text-to-video
    when neither is given. mode "ingredients": refs are reference images.
    resolution and duration apply to Omni only. size: download size as Flow labels it
    (e.g. "1080p" upscaled from a 720p clip, "270p" GIF); default original.
    Returns file paths and media ids.
    """
    if end and not start:
        raise ValueError("an end frame needs a start frame")
    if mode == VideoMode.FRAMES.value and refs:
        raise ValueError("refs need mode 'ingredients'")
    if mode == VideoMode.INGREDIENTS.value and (start or end):
        raise ValueError("start/end frames need mode 'frames'")
    options = VideoOptions.build(model, mode, aspect, resolution, duration, count)
    request = VideoRequest(
        prompt, options, start, end, tuple(refs or ()), project, _out(out_dir), size
    )
    async with Studio(_profile(profile)) as studio:
        return await studio.video(request)


@server.tool()
async def flow_upload(
    files: list[str], project: str | None = None, profile: str | None = None
) -> dict[str, Any]:
    """Upload local images to a Flow project once; returns their media ids."""
    async with Studio(_profile(profile)) as studio:
        return await studio.upload(files, project)


@server.tool()
async def flow_download(
    media_ids: list[str],
    project: str | None = None,
    out_dir: str | None = None,
    size: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Download existing images or clips by media id (or unique id prefix), e.g. a clip
    that finished after a timeout, optionally at another size ("2K", "1080p"). Generates
    nothing new."""
    async with Studio(_profile(profile)) as studio:
        return await studio.download(media_ids, project, _out(out_dir), size)


@server.tool()
async def flow_create_project(
    title: str | None = None, make_default: bool = True, profile: str | None = None
) -> dict[str, Any]:
    """Create a Flow project; by default later calls use it."""
    async with Studio(_profile(profile)) as studio:
        project_id = await studio.create_project(title)
        if make_default:
            studio.use_project(project_id)
    return {"project_id": project_id, "default": make_default}


@server.tool()
async def flow_use_project(project_id: str, profile: str | None = None) -> dict[str, Any]:
    """Make an existing Flow project (id from its URL) the default."""
    Studio(_profile(profile)).use_project(project_id)
    return {"default_project": project_id}


@server.tool()
async def flow_status(profile: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Default project, known projects, recent jobs and pacing/cooldown state."""
    name = _profile(profile)
    store = Store()
    jobs = [
        {
            "id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "error": row["error"],
            "result": row["result"],
        }
        for row in store.recent_jobs(name, limit)
    ]
    return {
        "profile": name,
        "default_project": store.get_setting(f"project.default.{name}"),
        "projects": [{"id": r["id"], "title": r["title"]} for r in store.projects(name)],
        "jobs": jobs,
        "pacing": Governor(store, name).status(),
    }


def run() -> None:
    server.run("stdio")
