from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from studiowright import batch as b
from studiowright.flow.options import OptionError
from studiowright.music.options import (
    AudioFormat,
    MusicModel,
    MusicRequest,
    format_length,
    parse_length,
)
from studiowright.music.page import _clips


@pytest.mark.parametrize(
    ("value", "seconds"), [("1:30", 90), ("90", 90), (120, 120), ("3:00", 180), (None, None)]
)
def test_parse_length(value: Any, seconds: int | None) -> None:
    assert parse_length(value) == seconds


@pytest.mark.parametrize("value", ["0:30", "3:01", "abc", 45])
def test_parse_length_rejects_out_of_range(value: Any) -> None:
    with pytest.raises(OptionError):
        parse_length(value)


def test_format_length() -> None:
    assert format_length(90) == "1:30"
    assert format_length(60) == "1:00"


def test_request_defaults_to_instrumental_wav_lyria_3_5() -> None:
    request = MusicRequest.build("calm piano")
    assert request.instrumental
    assert (request.model, request.audio_format) == (MusicModel.LYRIA_3_5, AudioFormat.WAV)
    assert not MusicRequest.build("pop", lyrics="la la").instrumental


def test_request_validation() -> None:
    with pytest.raises(OptionError, match="describe"):
        MusicRequest.build("  ")
    with pytest.raises(OptionError, match="bpm"):
        MusicRequest.build("rock", bpm=0)
    with pytest.raises(OptionError, match="AudioFormat"):
        MusicRequest.build("rock", audio_format="flac")


def test_clips_reads_flow_music_reply() -> None:
    reply = {
        "clips": {
            "5d8ce1b8": {
                "title": "Untitled",
                "duration": {"status": "completed", "value": "60.78933333333333"},
                "wav_url": "https://storage.googleapis.com/producer-app-public/clips/5d8ce1b8.wav",
                "audio_url": "https://storage.googleapis.com/producer-app-public/clips/5d8ce1b8.m4a",
                "image_url": None,
            }
        }
    }
    (clip,) = _clips(reply)
    assert clip.clip_id == "5d8ce1b8"
    assert round(clip.duration_s or 0) == 61
    assert clip.wav_url and clip.wav_url.endswith(".wav")


async def test_batch_runs_music_jobs(tmp_path: Path) -> None:
    path = tmp_path / "jobs.jsonl"
    path.write_text(
        json.dumps({"id": "bed", "kind": "music", "sound": "calm piano", "length": "1:30"}),
        encoding="utf-8",
    )
    seen: list[MusicRequest] = []

    async def run_music(request: MusicRequest) -> dict[str, Any]:
        seen.append(request)
        return {"files": ["bed.wav"]}

    async def never(request: Any) -> dict[str, Any]:
        raise AssertionError("not an image/video job")

    summary = await b.run_batch(path, never, never, run_music=run_music)
    assert summary.done == 1 and seen[0].length_s == 90
