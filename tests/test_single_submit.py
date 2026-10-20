"""Offline contract tests: no Chrome, account, network, or paid submission."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner
from playwright.async_api import Error as PlaywrightError

from studiowright import cli
from studiowright.flow.options import OptionError
from studiowright.flow.page import FlowError
from studiowright.music import page as music_page
from studiowright.music.options import (
    MAX_LENGTH_S,
    MIN_LENGTH_S,
    AudioFormat,
    Cover,
    MusicModel,
    MusicRequest,
    parse_length,
)
from studiowright.music.page import MusicPage, NotStarted
from studiowright.studio import Studio


@pytest.mark.parametrize("single_submit", [True, False])
@pytest.mark.parametrize("explicit_create", [True, False])
def test_cli_propagates_opt_in(monkeypatch, single_submit, explicit_create):
    studio = SimpleNamespace(music=AsyncMock(return_value={"files": []}))
    monkeypatch.setattr(cli, "_run", lambda make, profile: asyncio.run(make(studio)))
    args = ["music"] + (["create"] if explicit_create else []) + ["calm piano"]
    if single_submit:
        args += ["--single-submit"]
    result = CliRunner().invoke(cli.main, [*args, "--json"])
    assert result.exit_code == 0, result.output
    request = studio.music.call_args.args[0]
    assert request.single_submit is single_submit
    assert request.press_again is not single_submit
    assert request.heuristic_recovery is not single_submit


def test_cover_prompt_rejected_before_browser(monkeypatch):
    run = MagicMock(side_effect=AssertionError("must not open Studio"))
    monkeypatch.setattr(cli, "_run", run)
    result = CliRunner().invoke(
        cli.main, ["music", "create", "piano", "--single-submit", "--cover-prompt", "sky"]
    )
    assert result.exit_code != 0
    assert "extra paid generation" in result.output
    run.assert_not_called()
    with pytest.raises(OptionError, match="cover prompt"):
        MusicRequest.build("piano", single_submit=True, cover_prompt="sky")
    with pytest.raises(OptionError, match="cover prompt"):
        MusicRequest("piano", single_submit=True, cover=Cover(prompt="sky"))
    # Legacy requests and local cover images remain supported.
    assert MusicRequest.build("piano", cover_prompt="sky").cover.prompt == "sky"


def test_safe_local_cover_image(tmp_path):
    image = tmp_path / "cover.png"
    image.write_bytes(b"local test fixture")
    assert MusicRequest.build("piano", single_submit=True, cover_image=image).cover.image == image


@pytest.mark.parametrize("single_submit, clicks", [(True, 1), (False, 2)])
async def test_not_started_clicks_once_in_safe_mode_twice_in_legacy(
    monkeypatch, single_submit, clicks
):
    # Exercise the real generate -> _run -> trigger path, not a mocked generate.
    watch = MagicMock()
    watch.wait = AsyncMock(side_effect=NotStarted("unknown submission status"))
    context = MagicMock()
    context.__enter__.return_value = watch
    monkeypatch.setattr(music_page, "Watch", MagicMock(return_value=context))
    button = SimpleNamespace(click=AsyncMock())
    page = SimpleNamespace(get_by_role=MagicMock(return_value=SimpleNamespace(last=button)))
    music = MusicPage.__new__(MusicPage)
    music.page = page
    request = MusicRequest.build("piano", single_submit=single_submit)
    with pytest.raises(NotStarted):
        await music.generate(press_again=request.press_again)
    assert button.click.await_count == clicks
    assert watch.wait.await_count == clicks


async def test_generate_default_preserves_legacy_retry(monkeypatch):
    music = MusicPage.__new__(MusicPage)
    music._run = AsyncMock()
    await music.generate()
    assert music._run.call_args.kwargs["press_again"] is True


@pytest.mark.parametrize("single_submit", [True, False])
@pytest.mark.parametrize("error_type", [FlowError, NotStarted])
async def test_studio_ambiguous_failure_and_library_policy(monkeypatch, single_submit, error_type):
    failure = error_type("ambiguous timeout")
    music = SimpleNamespace(
        compose=AsyncMock(), generate=AsyncMock(side_effect=failure),
        library=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(MusicPage, "open", AsyncMock(return_value=music))
    studio = Studio.__new__(Studio)
    studio._session = object()
    studio.music_governor = SimpleNamespace(wait_turn=AsyncMock())
    studio._save_songs = AsyncMock()

    async def job(kind, params, work):
        assert params["single_submit"] is single_submit
        return await work("offline-job")

    studio._music_job = job
    with pytest.raises(error_type) as caught:
        await studio.music(MusicRequest.build("piano", single_submit=single_submit))
    assert caught.value is failure
    music.generate.assert_awaited_once()
    assert music.generate.call_args.kwargs["press_again"] is not single_submit
    assert music.library.await_count == (0 if single_submit or error_type is NotStarted else 1)
    studio._save_songs.assert_not_awaited()


async def test_legacy_title_time_library_recovery_remains(monkeypatch):
    clip = SimpleNamespace(
        kind="audio__create_song", complete=True,
        created=datetime.now(UTC), title="my song",
    )
    music = SimpleNamespace(
        compose=AsyncMock(), generate=AsyncMock(side_effect=FlowError("timeout")),
        library=AsyncMock(return_value=[clip]), credits_remaining=10, plan="test",
    )
    monkeypatch.setattr(MusicPage, "open", AsyncMock(return_value=music))
    studio = Studio.__new__(Studio)
    studio._session = object()
    studio.music_governor = SimpleNamespace(wait_turn=AsyncMock())
    studio._save_songs = AsyncMock(return_value=[{"file": "offline.wav"}])

    async def job(kind, params, work):
        return await work("offline-job")

    studio._music_job = job
    result = await studio.music(MusicRequest.build("piano", title="my song"))
    assert result["files"] == ["offline.wav"]
    assert studio._save_songs.call_args.args[1] == [clip]
    music.generate.assert_awaited_once()
    music.library.assert_awaited_once()


async def test_safe_mode_allows_refill_only_before_click(monkeypatch):
    before = SimpleNamespace(compose=AsyncMock(side_effect=PlaywrightError("fill failed")))
    after = SimpleNamespace(
        compose=AsyncMock(),
        generate=AsyncMock(return_value=SimpleNamespace(ready_clips=lambda: [])),
        credits_remaining=10, plan="test",
    )
    opened = AsyncMock(side_effect=[before, after])
    monkeypatch.setattr(MusicPage, "open", opened)
    studio = Studio.__new__(Studio)
    studio._session = object()
    studio.music_governor = SimpleNamespace(wait_turn=AsyncMock())
    studio._save_songs = AsyncMock(return_value=[])

    async def job(kind, params, work):
        return await work("offline-job")

    studio._music_job = job
    await studio.music(MusicRequest.build("piano", single_submit=True))
    assert opened.await_count == 2
    after.generate.assert_awaited_once()
    assert after.generate.call_args.kwargs["press_again"] is False


def test_capabilities_json_is_read_only_and_backed_by_options(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("capabilities must not access Studio, Store or browser")

    monkeypatch.setattr(cli, "Studio", forbidden)
    monkeypatch.setattr(cli, "Store", forbidden)
    monkeypatch.setattr(cli, "_run", forbidden)
    monkeypatch.setenv("STUDIOWRIGHT_HOME", str(tmp_path / "unused"))
    result = CliRunner().invoke(cli.main, ["capabilities", "--json"])
    assert result.exit_code == 0, result.output
    contract = json.loads(result.output)
    safe = MusicRequest.build("piano", single_submit=True)
    assert contract == {
        "production_create_contract": 1,
        "single_submit": safe.single_submit and not safe.press_again,
        "heuristic_recovery": safe.heuristic_recovery,
        "create_flag": "--single-submit",
        "models": [m.value for m in MusicModel],
        "formats": [f.value for f in AudioFormat],
        "min_length": MIN_LENGTH_S, "max_length": MAX_LENGTH_S,
    }
    for model in contract["models"]:
        for fmt in contract["formats"]:
            assert MusicRequest.build("piano", model=model).model.value == model
            assert MusicRequest.build("piano", audio_format=fmt).audio_format.value == fmt
    assert parse_length(contract["min_length"]) == 60
    assert parse_length(contract["max_length"]) == 180
    assert not (tmp_path / "unused").exists()
