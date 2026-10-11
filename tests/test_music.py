from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from studiowright import batch as b
from studiowright.cli import main
from studiowright.flow.options import OptionError
from studiowright.music.options import (
    AudioFormat,
    ChatRequest,
    EditMode,
    EditRequest,
    MusicJob,
    MusicModel,
    MusicRequest,
    format_length,
    format_time,
    parse_length,
    parse_time,
    song_id,
)
from studiowright.music.page import stem_name
from studiowright.music.wire import Turn, clips_in, sse_events, upload_state

SONG = "1e4f82e9-fe67-4e71-b538-5637f04adf1b"


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


@pytest.mark.parametrize(
    ("value", "seconds"), [("0:45", 45.0), ("1:05.5", 65.5), ("65", 65.0), (12, 12.0)]
)
def test_parse_time(value: Any, seconds: float) -> None:
    assert parse_time(value) == seconds


def test_format_time_matches_the_panel() -> None:
    assert format_time(45) == "0:45"
    assert format_time(65.5) == "1:05.5"
    assert format_time(150) == "2:30"


def test_song_id_accepts_ids_urls_prefixes_and_last() -> None:
    assert song_id(f"https://www.flowmusic.app/song/{SONG}") == SONG
    assert song_id(SONG.upper()) == SONG
    assert song_id("1e4f82e9") == "1e4f82e9"
    assert song_id("last") == "last"
    with pytest.raises(OptionError):
        song_id("my song")


def test_request_defaults_to_instrumental_wav_lyria_3_5() -> None:
    request = MusicRequest.build("calm piano")
    assert request.instrumental
    assert (request.model, request.audio_format) == (MusicModel.LYRIA_3_5, AudioFormat.WAV)
    assert not MusicRequest.build("pop", lyrics="la la").instrumental
    assert MusicRequest.build("pop", audio_format="mp3").audio_format is AudioFormat.MP3


def test_request_validation() -> None:
    with pytest.raises(OptionError, match="describe"):
        MusicRequest.build("  ")
    with pytest.raises(OptionError, match="bpm"):
        MusicRequest.build("rock", bpm=0)
    with pytest.raises(OptionError, match="AudioFormat"):
        MusicRequest.build("rock", audio_format="flac")
    with pytest.raises(OptionError, match="either"):
        MusicRequest.build("rock", cover_image=__file__, cover_prompt="a fox")


def test_edit_request_rules() -> None:
    extend = EditRequest.build(SONG, "extend", end="2:30")
    assert (extend.mode, extend.end_s, extend.prompt) == (EditMode.EXTEND, 150.0, None)
    with pytest.raises(OptionError, match="prompt"):
        EditRequest.build(SONG, "cover")
    with pytest.raises(OptionError, match="whole song"):
        EditRequest.build(SONG, "cover", prompt="jazz", start="0:10")
    with pytest.raises(OptionError, match="after"):
        EditRequest.build(SONG, "replace", prompt="x", start="0:40", end="0:20")


def test_chat_request_needs_rights_for_files(tmp_path: Path) -> None:
    audio = tmp_path / "mine.wav"
    audio.write_bytes(b"RIFF")
    with pytest.raises(OptionError, match="rights"):
        ChatRequest.build("remix this", audio=audio)
    request = ChatRequest.build("remix this", audio=audio, confirm_rights=True, session=SONG)
    assert request.audio == audio and request.session == SONG


def test_clips_reads_both_reply_shapes() -> None:
    record = {
        "id": SONG,
        "title": "Untitled - drums",
        "op_type": "audio__split_stems",
        "duration": {"status": "completed", "value": "61.46"},
        "wav_url": f"https://storage.googleapis.com/producer-app-public/clips/{SONG}.wav",
        "audio_url": f"https://storage.googleapis.com/producer-app-public/clips/{SONG}.m4a",
        "created_at": "2026-10-10T21:01:35.707561Z",
        "lyrics": {"status": "completed", "value": {"text": "[Instrumental]"}},
    }
    (by_map,) = clips_in({"clips": {SONG: record}})
    (by_list,) = clips_in({"clips": [record]})
    assert by_map == by_list
    assert by_map.complete and round(by_map.duration_s or 0) == 61
    assert by_map.lyrics == "[Instrumental]"
    assert by_map.summary()["kind"] == "split_stems"
    assert stem_name(by_map) == "drums"
    pending = clips_in({"clips": {SONG: {**record, "duration": {"status": "pending"}}}})
    assert not pending[0].complete


STREAM = "\r\n\r\n".join(
    [
        'id: 1-0\r\nevent: begin\r\ndata: {"data": {}}',
        'id: 2-0\r\nevent: conversation_id\r\ndata: {"id": "c4aa26fb"}',
        'id: 3-0\r\nevent: part\r\ndata: {"index": 1, "status": "final", "part": '
        '{"content": "Write lyrics", "part_kind": "user-prompt"}, "delta": ""}',
        'id: 4-0\r\nevent: part\r\ndata: {"index": 2, "status": "start", "part": '
        '{"content": "I", "part_kind": "text"}, "delta": ""}',
        'id: 5-0\r\nevent: part\r\ndata: {"index": 2, "status": "final", "part": '
        '{"content": "Here are your lines.", "part_kind": "text"}, "delta": ""}',
        'id: 6-0\r\nevent: part\r\ndata: {"index": 3, "status": "final", "part": '
        '{"tool_name": "lyrics__register", "args": {"lyrics": "Waves on the shore"}, '
        '"tool_call_id": "t1", "part_kind": "tool-call"}, "delta": ""}',
        "id: 7-0\r\nevent: complete\r\ndata: {}",
        'id: 8-0\r\nevent: suggestion\r\ndata: {"parts": [{"tool_name": '
        '"synthetic__suggest_actions", "args": {"action1": "Make it folk", "action2": '
        '"Add a chorus"}, "tool_call_id": "t2", "part_kind": "tool-call"}]}',
        "id: 9-0\r\nevent: final\r\ndata: {}",
    ]
)


def test_turn_reads_the_producer_stream() -> None:
    events = sse_events(STREAM)
    assert events[0] == ("begin", {"data": {}})
    turn = Turn()
    turn.merge(events)
    assert turn.finished and turn.conversation_id == "c4aa26fb"
    assert turn.texts == ["Here are your lines."]
    assert turn.lyrics == "Waves on the shore"
    assert turn.suggestions == ["Make it folk", "Add a chorus"]
    assert turn.summary()["tools"] == ["lyrics__register"]


def test_upload_state() -> None:
    assert upload_state({"status": "pending", "decision": None}) == ("pending", None)
    assert upload_state({"status": "complete", "decision": "reject", "reason": "match"}) == (
        "rejected",
        "match",
    )


async def test_batch_chains_music_jobs(tmp_path: Path) -> None:
    path = tmp_path / "jobs.jsonl"
    lines = [
        {"id": "bed", "kind": "music", "sound": "calm piano", "length": "1:30"},
        {"id": "longer", "kind": "music", "action": "extend", "song": "bed", "end": "2:30"},
        {"id": "parts", "kind": "music", "action": "stems", "song": "longer"},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    seen: list[MusicJob] = []

    async def run_music(job: MusicJob) -> dict[str, Any]:
        seen.append(job)
        return {"files": [f"{job.action}.wav"], "songs": [{"id": f"{len(seen):08d}{SONG[8:]}"}]}

    async def never(request: Any) -> dict[str, Any]:
        raise AssertionError("not an image/video job")

    summary = await b.run_batch(path, never, never, run_music=run_music)
    assert summary.done == 3
    create, extend, stems = seen
    assert isinstance(create.request, MusicRequest) and create.request.length_s == 90
    assert isinstance(extend.request, EditRequest) and extend.request.song == f"00000001{SONG[8:]}"
    assert (stems.action, stems.song) == ("stems", f"00000002{SONG[8:]}")


def test_batch_rejects_unknown_music_action(tmp_path: Path) -> None:
    path = tmp_path / "jobs.jsonl"
    path.write_text(json.dumps({"kind": "music", "action": "remaster", "song": "x"}))
    with pytest.raises(b.BatchError, match="action"):
        b.load_jobs(path)


def test_music_group_defaults_to_create() -> None:
    runner = CliRunner()
    short = runner.invoke(main, ["music", "--bpm", "0", "calm piano"])
    assert "bpm must be between" in short.output
    listed = runner.invoke(main, ["music", "--help"])
    assert "stems" in listed.output and "chat" in listed.output


def test_result_kinds_use_flow_music_op_types() -> None:
    from studiowright.music import page

    kinds = page.SONG_KINDS | page.EDIT_KINDS | page.TRIM_KINDS | page.STEM_KINDS
    assert all(kind.startswith("audio__") for kind in kinds)
    assert "audio__render_edit" in page.EDIT_KINDS


class _QuietPage:
    """A page that never sends or receives anything."""

    def on(self, *_: Any) -> None:
        pass

    def remove_listener(self, *_: Any) -> None:
        pass


async def test_watch_reports_a_job_that_never_started() -> None:
    from studiowright.music.page import NotStarted, Watch

    with Watch(_QuietPage()) as watch, pytest.raises(NotStarted):  # type: ignore[arg-type]
        await watch.wait(timeout_s=10, min_clips=1, need_turn=True, start_s=0.5)
