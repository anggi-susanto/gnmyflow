"""Read the replies flowmusic.app's own page receives (measured 2026-10-10/11).

Nothing here sends anything; these functions only parse reply bodies:

- ``/clips``, ``/clips/auth-user``: song records with their audio URLs;
- ``/messages/<job>/stream``: the Producer agent's server-sent events for one turn
  (``begin``, ``conversation_id``, ``part``, ``message``, ``complete``, ``suggestion``,
  ``final``), whose parts are user prompts, text, tool calls and tool returns;
- ``/audio-create-song-status/<op>`` and ``/producer/upload-audio/.../upload-check-status``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Clip:
    clip_id: str
    title: str | None
    duration_s: float | None
    wav_url: str | None
    audio_url: str | None
    image_url: str | None = None
    video_url: str | None = None
    kind: str | None = None
    created_at: str | None = None
    lyrics: str | None = None
    complete: bool = False

    @property
    def created(self) -> datetime | None:
        if not self.created_at:
            return None
        try:
            return datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError:
            return None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.clip_id,
            "title": self.title,
            "seconds": round(self.duration_s, 1) if self.duration_s else None,
            "kind": (self.kind or "").replace("audio__", "") or None,
            "created": self.created_at,
            "cover_url": self.image_url,
        }


def _clip(clip_id: str, record: dict[str, Any]) -> Clip:
    duration = record.get("duration") or {}
    lyrics = record.get("lyrics") or {}
    lyrics_value = lyrics.get("value") if isinstance(lyrics, dict) else None
    value = duration.get("value") if isinstance(duration, dict) else duration
    status = duration.get("status") if isinstance(duration, dict) else None
    return Clip(
        clip_id=record.get("id") or clip_id,
        title=record.get("title"),
        duration_s=float(value) if value else None,
        wav_url=record.get("wav_url"),
        audio_url=record.get("audio_url"),
        image_url=record.get("image_url"),
        video_url=record.get("video_url"),
        kind=record.get("op_type"),
        created_at=record.get("created_at"),
        lyrics=lyrics_value.get("text") if isinstance(lyrics_value, dict) else None,
        complete=status == "completed" and bool(record.get("wav_url")),
    )


def clips_in(payload: Any) -> list[Clip]:
    """Song records from a ``/clips`` reply (a map by id) or a listing (a list)."""
    clips = payload.get("clips") if isinstance(payload, dict) else None
    if isinstance(clips, dict):
        return [_clip(k, v) for k, v in clips.items() if isinstance(v, dict)]
    if isinstance(clips, list):
        return [_clip(v.get("id", ""), v) for v in clips if isinstance(v, dict)]
    return []


def sse_events(text: str) -> list[tuple[str, Any]]:
    """(event, data) pairs from a server-sent-events body; data is JSON when it parses."""
    events = []
    for block in re.split(r"\r?\n\r?\n", text):
        name, data = "message", []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if not data:
            continue
        raw = "\n".join(data)
        try:
            events.append((name, json.loads(raw)))
        except ValueError:
            events.append((name, raw))
    return events


@dataclass
class Turn:
    """What the Producer did in one turn, read from its event stream."""

    conversation_id: str | None = None
    texts: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    finished: bool = False

    def merge(self, events: list[tuple[str, Any]]) -> None:
        for name, data in events:
            if name == "conversation_id" and isinstance(data, dict):
                self.conversation_id = data.get("id") or self.conversation_id
            elif name == "final":
                self.finished = True
            elif name == "part" and isinstance(data, dict) and data.get("status") == "final":
                self._part(data.get("part") or {})
            elif name in ("message", "suggestion") and isinstance(data, dict):
                for part in data.get("parts") or []:
                    self._part(part, suggestion=name == "suggestion")

    def _part(self, part: dict[str, Any], suggestion: bool = False) -> None:
        kind = part.get("part_kind")
        if kind == "text" and not suggestion:
            content = (part.get("content") or "").strip()
            if content and content not in self.texts:
                self.texts.append(content)
        elif kind == "tool-call":
            name = part.get("tool_name") or ""
            args = part.get("args") or {}
            if name == "synthetic__suggest_actions" and isinstance(args, dict):
                for value in args.values():
                    if isinstance(value, str) and value not in self.suggestions:
                        self.suggestions.append(value)
            elif name and not any(c.get("id") == part.get("tool_call_id") for c in self.tool_calls):
                self.tool_calls.append({"id": part.get("tool_call_id"), "tool": name, "args": args})

    @property
    def lyrics(self) -> str | None:
        """Lyrics the Producer wrote this turn, if it registered any."""
        for call in reversed(self.tool_calls):
            if call["tool"] == "lyrics__register" and isinstance(call["args"], dict):
                return call["args"].get("lyrics")
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "session": self.conversation_id,
            "reply": "\n\n".join(self.texts),
            "lyrics": self.lyrics,
            "tools": [c["tool"] for c in self.tool_calls],
            "suggestions": self.suggestions,
        }


#: Upload-check states that mean "still checking".
UPLOAD_PENDING = {"pending", "processing", "in_progress", "queued", "running"}


def upload_state(payload: Any) -> tuple[str, str | None]:
    """(status, reason) from an upload-check-status reply."""
    if not isinstance(payload, dict):
        return "pending", None
    status = str(payload.get("status") or "pending").lower()
    decision = payload.get("decision")
    reason = payload.get("reason")
    if decision and str(decision).lower() in ("reject", "rejected", "block", "blocked", "deny"):
        return "rejected", reason or str(decision)
    return status, reason
