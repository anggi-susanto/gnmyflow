"""The `studiowright` / `sw` command line."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import click
from playwright.async_api import Error as PlaywrightError

from studiowright import __version__, chrome, paths
from studiowright.flow.options import (
    COUNTS,
    DURATIONS,
    RESOLUTIONS,
    Aspect,
    ImageModel,
    ImageOptions,
    OptionError,
    VideoMode,
    VideoModel,
    VideoOptions,
)
from studiowright.flow.page import FlowError
from studiowright.pacing import CoolingDown
from studiowright.store import Store
from studiowright.studio import ImageRequest, Studio, VideoRequest

IMAGE_MODELS = [m.value for m in ImageModel]
VIDEO_MODELS = [m.value for m in VideoModel]
ASPECTS = [a.value for a in Aspect]


def _run(make: Callable[[Studio], Awaitable[Any]], profile: str) -> Any:
    async def main() -> Any:
        async with Studio(profile) as studio:
            return await make(studio)

    try:
        return asyncio.run(main())
    except (FlowError, CoolingDown, OptionError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    except chrome.ChromeError as exc:
        raise click.ClickException(str(exc)) from exc
    except PlaywrightError as exc:
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise click.ClickException(f"browser step failed: {first_line}") from exc


def _emit(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=1))
        return
    for path in result.get("files", []):
        click.echo(path)
    seconds = result.get("seconds")
    if seconds is not None:
        click.echo(f"done in {seconds} s (job {result.get('job_id')})", err=True)


@click.group()
@click.version_option(__version__, "-V", "--version", prog_name="StudioWright")
@click.option(
    "--profile",
    envvar="STUDIOWRIGHT_PROFILE",
    default=paths.DEFAULT_PROFILE,
    show_default=True,
    help="Signed-in Chrome profile to use.",
)
@click.pass_context
def main(ctx: click.Context, profile: str) -> None:
    """StudioWright: Google Flow from the terminal. Unofficial; not affiliated with Google."""
    ctx.obj = {"profile": profile}


# --- auth ---------------------------------------------------------------------------------
@main.group()
def auth() -> None:
    """Sign in and manage profiles."""


@auth.command("login")
@click.option("--url", default=chrome.FLOW_URL, show_default=True, help="Page to open.")
@click.pass_obj
def auth_login(obj: dict[str, str], url: str) -> None:
    """Open Chrome on the profile; sign in by hand, then close the window."""
    profile = obj["profile"]
    if chrome.endpoint_version(chrome.debug_port(profile)):
        raise click.ClickException(
            "StudioWright's Chrome for this profile is running; close its window first."
        )
    exe = chrome.find_chrome()
    if exe is None:
        raise click.ClickException("Google Chrome was not found; install it first.")
    target = paths.profile_dir(profile)
    click.echo(f"Opening Chrome on profile '{profile}' ({target}).")
    click.echo("Sign in to your Google account, open Flow once, then close the window.")
    chrome.run_sign_in(exe, target, url)
    click.echo("Chrome closed. The profile is saved.")


@auth.command("list")
def auth_list() -> None:
    """List profiles."""
    root = paths.home() / "profiles"
    names = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    if not names:
        click.echo("No profiles yet. Run `sw auth login`.")
    for name in names:
        click.echo(name)


# --- window -------------------------------------------------------------------------------
@main.group()
def window() -> None:
    """Show or hide StudioWright's Chrome window (it keeps working either way)."""


@window.command("show")
@click.pass_obj
def window_show(obj: dict[str, str]) -> None:
    """Bring the window on screen."""
    _run(lambda studio: studio.window(visible=True), obj["profile"])


@window.command("hide")
@click.pass_obj
def window_hide(obj: dict[str, str]) -> None:
    """Move the window off-screen; do not minimize it, which would pause the page."""
    _run(lambda studio: studio.window(visible=False), obj["profile"])


# --- projects -----------------------------------------------------------------------------
@main.group()
def project() -> None:
    """Flow projects."""


@project.command("create")
@click.argument("title", required=False)
@click.option(
    "--use/--no-use", default=True, show_default=True, help="Make it the default project."
)
@click.pass_obj
def project_create(obj: dict[str, str], title: str | None, use: bool) -> None:
    """Create a Flow project."""

    async def make(studio: Studio) -> str:
        project_id = await studio.create_project(title)
        if use:
            studio.use_project(project_id)
        return project_id

    click.echo(_run(make, obj["profile"]))


@project.command("use")
@click.argument("project_id")
@click.pass_obj
def project_use(obj: dict[str, str], project_id: str) -> None:
    """Make PROJECT_ID (from its URL) the default project."""
    store = Store()
    studio = Studio(obj["profile"], store)
    try:
        studio.use_project(project_id)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"default project: {project_id}")


@project.command("list")
@click.pass_obj
def project_list(obj: dict[str, str]) -> None:
    """Projects StudioWright knows for this profile."""
    store = Store()
    default = store.get_setting(f"project.default.{obj['profile']}")
    for row in store.projects(obj["profile"]):
        mark = "*" if row["id"] == default else " "
        click.echo(f"{mark} {row['id']}  {row['title'] or ''}")


# --- media --------------------------------------------------------------------------------
@main.command()
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--project", help="Project id; defaults to the default project.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def upload(obj: dict[str, str], files: tuple[str, ...], project: str | None, as_json: bool) -> None:
    """Upload images to a project once; later jobs reuse them by content."""
    result = _run(lambda studio: studio.upload(list(files), project), obj["profile"])
    if as_json:
        click.echo(json.dumps(result, indent=1))
    else:
        for path, media_id in zip(files, result["media_ids"], strict=False):
            click.echo(f"{media_id}  {path}")


@main.command()
@click.argument("prompt")
@click.option(
    "--model",
    type=click.Choice(IMAGE_MODELS),
    default=ImageModel.NANO_BANANA_2_1.value,
    show_default=True,
)
@click.option("--aspect", type=click.Choice(ASPECTS), default="16:9", show_default=True)
@click.option(
    "--count", type=click.Choice([str(c) for c in COUNTS]), default="1", show_default=True
)
@click.option(
    "--ref",
    "refs",
    multiple=True,
    help="Reference image: a file path or a Flow media id. Repeatable.",
)
@click.option("--project", help="Project id; defaults to the default project.")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), help="Output folder.")
@click.option(
    "--size",
    help="Download size as Flow labels it, e.g. 2K (image) or 1080p (video); default original.",
)
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def image(
    obj: dict[str, str],
    prompt: str,
    model: str,
    aspect: str,
    count: str,
    refs: tuple[str, ...],
    project: str | None,
    out: Path | None,
    size: str | None,
    as_json: bool,
) -> None:
    """Generate images from PROMPT."""
    try:
        options = ImageOptions.build(model, aspect, int(count))
    except OptionError as exc:
        raise click.BadParameter(str(exc)) from exc
    request = ImageRequest(prompt, options, refs, project, out, size)
    _emit(_run(lambda studio: studio.image(request), obj["profile"]), as_json)


@main.command()
@click.argument("prompt")
@click.option(
    "--model",
    type=click.Choice(VIDEO_MODELS),
    default=VideoModel.OMNI_FLASH.value,
    show_default=True,
)
@click.option(
    "--mode",
    type=click.Choice([m.value for m in VideoMode]),
    default="frames",
    show_default=True,
    help="frames: start/end frames or text only; ingredients: reference images.",
)
@click.option("--start", help="Start frame: a file path or a Flow media id.")
@click.option("--end", help="End frame: a file path or a Flow media id.")
@click.option(
    "--ref", "refs", multiple=True, help="Ingredient image (mode ingredients). Repeatable."
)
@click.option("--aspect", type=click.Choice(["16:9", "9:16"]), default="16:9", show_default=True)
@click.option(
    "--resolution",
    type=click.Choice(list(RESOLUTIONS)),
    default="720p",
    show_default=True,
    help="Omni only.",
)
@click.option(
    "--duration",
    type=click.Choice([str(d) for d in DURATIONS]),
    default="8",
    show_default=True,
    help="Seconds; Omni only.",
)
@click.option(
    "--count", type=click.Choice([str(c) for c in COUNTS]), default="1", show_default=True
)
@click.option("--project", help="Project id; defaults to the default project.")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), help="Output folder.")
@click.option(
    "--size",
    help="Download size as Flow labels it, e.g. 2K (image) or 1080p (video); default original.",
)
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def video(
    obj: dict[str, str],
    prompt: str,
    model: str,
    mode: str,
    start: str | None,
    end: str | None,
    refs: tuple[str, ...],
    aspect: str,
    resolution: str,
    duration: str,
    count: str,
    project: str | None,
    out: Path | None,
    size: str | None,
    as_json: bool,
) -> None:
    """Generate a video clip from PROMPT."""
    if end and not start:
        raise click.BadParameter("--end needs --start", param_hint="--end")
    if mode == "frames" and refs:
        raise click.BadParameter("--ref needs --mode ingredients", param_hint="--ref")
    if mode == "ingredients" and (start or end):
        raise click.BadParameter("--start/--end need --mode frames", param_hint="--mode")
    try:
        options = VideoOptions.build(model, mode, aspect, resolution, int(duration), int(count))
    except OptionError as exc:
        raise click.BadParameter(str(exc)) from exc
    request = VideoRequest(prompt, options, start, end, refs, project, out, size)
    _emit(_run(lambda studio: studio.video(request), obj["profile"]), as_json)


@main.command()
@click.argument("media_ids", nargs=-1, required=True)
@click.option("--project", help="Project id; defaults to the default project.")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), help="Output folder.")
@click.option(
    "--size",
    help="Download size as Flow labels it, e.g. 2K (image) or 1080p (video); default original.",
)
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def download(
    obj: dict[str, str],
    media_ids: tuple[str, ...],
    project: str | None,
    out: Path | None,
    size: str | None,
    as_json: bool,
) -> None:
    """Download existing images or clips by media id, without generating anything."""
    result = _run(
        lambda studio: studio.download(list(media_ids), project, out, size), obj["profile"]
    )
    _emit(result, as_json)


@main.command()
@click.argument("batch_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--keep-going", is_flag=True, help="Continue after a failed job.")
@click.pass_obj
def run(obj: dict[str, str], batch_file: Path, keep_going: bool) -> None:
    """Run a batch of image/video jobs (JSON Lines or a JSON list), resumably.

    Results are appended to BATCH_FILE.results.jsonl; running again skips finished jobs.
    A job's start/end/refs may name an earlier job's id to reuse its result.
    """
    from studiowright.batch import BatchError, load_jobs, run_batch

    try:
        load_jobs(batch_file)
    except (BatchError, ValueError) as exc:
        raise click.ClickException(f"invalid batch: {exc}") from exc

    def report(record: dict[str, Any]) -> None:
        files = (record.get("result") or {}).get("files", [])
        detail = ", ".join(files) if files else record.get("error", "")
        click.echo(f"{record['id']}: {record['status']} ({record['seconds']} s) {detail}")

    async def make(studio: Studio) -> Any:
        return await run_batch(
            batch_file.resolve(), studio.image, studio.video, keep_going=keep_going, on_event=report
        )

    summary = _run(make, obj["profile"])
    counts = f"{summary.done} done, {summary.failed} failed, {summary.skipped} skipped"
    click.echo(f"{counts} of {summary.total}", err=True)


# --- state --------------------------------------------------------------------------------
@main.command()
@click.option("--limit", default=15, show_default=True)
@click.pass_obj
def jobs(obj: dict[str, str], limit: int) -> None:
    """Recent jobs."""
    for row in Store().recent_jobs(obj["profile"], limit):
        when = time.strftime("%m-%d %H:%M", time.localtime(row["created"]))
        took = (
            f"{row['finished'] - row['started']:.0f}s" if row["finished"] and row["started"] else ""
        )
        detail = row["error"] or ""
        click.echo(
            f"{row['id']}  {when}  {row['kind']:5}  {row['status']:7} {took:>5}  {detail[:80]}"
        )


@main.command()
@click.pass_obj
def pace(obj: dict[str, str]) -> None:
    """Show submit pacing and any cooldown for the profile."""
    from studiowright.pacing import Governor

    status = Governor(Store(), obj["profile"]).status()
    now = time.time()
    click.echo(f"next submit allowed in: {max(0.0, status['next_free'] - now):.0f} s")
    cooling = status["cooldown_until"] - now
    click.echo(f"cooldown: {cooling / 60:.0f} min left" if cooling > 0 else "cooldown: none")
    click.echo(f"unusual-activity refusals so far: {status['refusals']}")


@main.command()
def mcp() -> None:
    """Run the MCP server on stdio (for Claude Code and other agents)."""
    from studiowright.mcp_server import run

    run()


if __name__ == "__main__":  # pragma: no cover
    main()
