"""Run a list of image and video jobs in order, resumably.

A batch file is JSON Lines (one job per line) or a JSON list. Each job is an object:

    {"id": "shot-01", "kind": "image", "prompt": "...", "model": "nano-banana-2.1",
     "aspect": "16:9", "count": 1, "refs": ["a.png"], "size": "2K"}
    {"id": "shot-01-clip", "kind": "video", "prompt": "...", "start": "shot-01",
     "resolution": "720p", "duration": 8, "size": "1080p"}

``start``, ``end`` and ``refs`` accept file paths, media ids, or the ``id`` of an earlier
job in the same batch (its first result is used). Results are appended to
``<batch>.results.jsonl``; running the batch again skips jobs that already finished.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from studiowright.flow.options import ImageOptions, OptionError, VideoOptions
from studiowright.studio import ImageRequest, VideoRequest

IMAGE_KEYS = {"id", "kind", "prompt", "model", "aspect", "count", "refs", "project", "size", "out"}
VIDEO_KEYS = IMAGE_KEYS | {"mode", "start", "end", "resolution", "duration"}


class BatchError(ValueError):
    """The batch file is not valid."""


def load_jobs(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        jobs = json.loads(text)
    else:
        jobs = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
        raise BatchError("a batch is a JSON list of objects or one JSON object per line")
    for index, job in enumerate(jobs):
        job.setdefault("id", f"job-{index + 1:03d}")
        kind = job.get("kind")
        if kind not in ("image", "video"):
            raise BatchError(f"{job['id']}: kind must be 'image' or 'video'")
        if not job.get("prompt"):
            raise BatchError(f"{job['id']}: prompt is required")
        unknown = set(job) - (IMAGE_KEYS if kind == "image" else VIDEO_KEYS)
        if unknown:
            raise BatchError(f"{job['id']}: unknown fields {sorted(unknown)}")
    ids = [job["id"] for job in jobs]
    if len(ids) != len(set(ids)):
        raise BatchError("job ids must be unique")
    return jobs


def results_path(batch: Path) -> Path:
    return batch.with_name(batch.name + ".results.jsonl")


def finished(results: Path) -> dict[str, dict[str, Any]]:
    """Jobs already done in earlier runs, by id."""
    done: dict[str, dict[str, Any]] = {}
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get("status") == "done":
                    done[record["id"]] = record
    return done


def _resolve(value: str | None, done: dict[str, dict[str, Any]]) -> str | None:
    """A reference to an earlier job's id becomes that job's first media id."""
    if value and value in done:
        media = done[value].get("result", {}).get("media_ids") or []
        if media:
            return media[0]
    return value


def build_request(
    job: dict[str, Any], done: dict[str, dict[str, Any]], base: Path
) -> ImageRequest | VideoRequest:
    refs = tuple(_resolve(r, done) or r for r in job.get("refs", []))
    out = Path(job["out"]) if job.get("out") else None
    if out is not None and not out.is_absolute():
        out = base / out
    refs = tuple(
        str(base / r) if not Path(r).is_absolute() and (base / r).is_file() else r for r in refs
    )
    if job["kind"] == "image":
        options = ImageOptions.build(
            job.get("model", ImageOptions().model.value),
            job.get("aspect", "16:9"),
            int(job.get("count", 1)),
        )
        return ImageRequest(job["prompt"], options, refs, job.get("project"), out, job.get("size"))
    options = VideoOptions.build(
        job.get("model", VideoOptions().model.value),
        job.get("mode", "frames"),
        job.get("aspect", "16:9"),
        job.get("resolution", "720p"),
        int(job.get("duration", 8)),
        int(job.get("count", 1)),
    )

    def frame(value: str | None) -> str | None:
        value = _resolve(value, done)
        if value and not Path(value).is_absolute() and (base / value).is_file():
            return str(base / value)
        return value

    return VideoRequest(
        job["prompt"],
        options,
        frame(job.get("start")),
        frame(job.get("end")),
        refs,
        job.get("project"),
        out,
        job.get("size"),
    )


@dataclass
class BatchSummary:
    total: int
    skipped: int = 0
    done: int = 0
    failed: int = 0


async def run_batch(
    batch: Path,
    run_image: Callable[[ImageRequest], Awaitable[dict[str, Any]]],
    run_video: Callable[[VideoRequest], Awaitable[dict[str, Any]]],
    *,
    keep_going: bool = False,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> BatchSummary:
    jobs = load_jobs(batch)
    results = results_path(batch)
    done = finished(results)
    summary = BatchSummary(total=len(jobs))
    for job in jobs:
        if job["id"] in done:
            summary.skipped += 1
            continue
        started = time.time()
        record: dict[str, Any] = {"id": job["id"], "kind": job["kind"], "started": started}
        try:
            request = build_request(job, done, batch.parent)
            result = await (
                run_image(request) if isinstance(request, ImageRequest) else run_video(request)
            )
            record.update(status="done", result=result)
            done[job["id"]] = record
            summary.done += 1
        except (OptionError, BatchError) as exc:
            record.update(status="invalid", error=str(exc))
            summary.failed += 1
        except Exception as exc:
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            summary.failed += 1
        record["seconds"] = round(time.time() - started, 1)
        with results.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if on_event:
            on_event(record)
        if record["status"] != "done" and not keep_going:
            break
    return summary
