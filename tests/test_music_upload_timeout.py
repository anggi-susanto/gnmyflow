"""Offline upload timeout plumbing and upload acceptance checks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from studiowright import cli
from studiowright.flow.options import OptionError
from studiowright.flow.page import FlowError
from studiowright.music.options import ChatRequest
from studiowright.music.page import MusicPage, Watch
from studiowright.music.wire import Turn, upload_state
from studiowright.studio import Studio


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("-inf"), float("nan")])
def test_chat_timeout_validation(value):
    with pytest.raises(OptionError, match="finite, positive"):
        ChatRequest.build("hello", upload_timeout_s=value)
    with pytest.raises(OptionError, match="finite, positive"):
        ChatRequest("hello", upload_timeout_s=value)


@pytest.mark.parametrize("timeout", [None, 2400])
@pytest.mark.parametrize("command", ["upload", "chat"])
def test_cli_propagates_upload_timeout(monkeypatch, tmp_path, timeout, command):
    audio = tmp_path / "mine.wav"
    audio.write_bytes(b"RIFF")
    studio = SimpleNamespace(music_upload=AsyncMock(return_value={}),
                             music_chat=AsyncMock(return_value={}))
    monkeypatch.setattr(cli, "_run", lambda make, profile: asyncio.run(make(studio)))
    args = ["music", command]
    if command == "upload":
        args += [str(audio), "--confirm-rights"]
    else:
        args += ["hello", "--audio", str(audio), "--confirm-rights", "--timeout", "123"]
    if timeout is not None:
        args += ["--timeout" if command == "upload" else "--upload-timeout", str(timeout)]
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == 0, result.output
    expected = timeout or 1800
    if command == "upload":
        studio.music_upload.assert_awaited_once_with(audio, confirm_rights=True, timeout_s=expected)
    else:
        request = studio.music_chat.call_args.args[0]
        assert request.upload_timeout_s == expected
        assert request.timeout_s == 123


@pytest.mark.parametrize("command,flag", [("upload", "--timeout"), ("chat", "--upload-timeout")])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_cli_rejects_bad_upload_timeout(monkeypatch, tmp_path, command, flag, value):
    audio = tmp_path / "mine.wav"
    audio.touch()
    def never(*args):
        pytest.fail("invalid timeout must not open Chrome")
    monkeypatch.setattr(cli, "_run", never)
    args = ["music", command, str(audio) if command == "upload" else "hello", flag, value]
    if command == "upload":
        args += ["--confirm-rights"]
    assert CliRunner().invoke(cli.main, args).exit_code == 2


@pytest.mark.parametrize("timeout", [1800, 2400])
async def test_studio_upload_and_chat_propagation(monkeypatch, tmp_path, timeout):
    audio = tmp_path / "mine.wav"
    audio.touch()
    music = SimpleNamespace(upload_audio=AsyncMock(return_value="song"),
                            chat=AsyncMock(return_value=SimpleNamespace(
                                ready_clips=lambda: [], turn=Turn())),
                            credits_remaining=None, plan=None)
    monkeypatch.setattr(MusicPage, "open", AsyncMock(return_value=music))
    studio = object.__new__(Studio)
    studio._session = object()
    studio.music_governor = SimpleNamespace(wait_turn=AsyncMock())
    studio._save_songs = AsyncMock(return_value=[])
    async def job(kind, params, work):
        return await work("job")
    studio._music_job = job
    kwargs = {} if timeout == 1800 else {"timeout_s": timeout}
    await studio.music_upload(audio, confirm_rights=True, **kwargs)
    music.upload_audio.assert_awaited_once_with(audio, confirm_rights=True, timeout_s=timeout)
    music.upload_audio.reset_mock()
    await studio.music_chat(ChatRequest.build("hello", audio=audio, confirm_rights=True,
                                             timeout_s=123, upload_timeout_s=timeout))
    music.upload_audio.assert_awaited_once_with(audio, confirm_rights=True, timeout_s=timeout)
    music.chat.assert_awaited_once_with("hello", timeout_s=123)


async def test_studio_upload_rights_and_validation_before_browser(tmp_path):
    audio = tmp_path / "mine.wav"
    audio.touch()
    studio = object.__new__(Studio)
    with pytest.raises(OptionError, match="rights"):
        await studio.music_upload(audio, confirm_rights=False)
    with pytest.raises(OptionError, match="positive"):
        await studio.music_upload(audio, confirm_rights=True, timeout_s=0)


@pytest.mark.parametrize("payload,error", [
    ({"status": "complete"}, None),
    ({"status": "complete", "decision": "reject", "reason": "copyright match"}, "copyright match"),
    ({"status": "failed", "reason": "bad audio"}, "bad audio"),
    ({"status": "error"}, "error"),
    ({"status": "mystery", "reason": "new status"}, "unknown upload status.*new status"),
])
async def test_page_upload_checks_status_and_preserves_rejection(monkeypatch, payload, error):
    page = SimpleNamespace(on=lambda *args: None, remove_listener=lambda *args: None,
                           locator=lambda selector: SimpleNamespace(set_input_files=AsyncMock()))
    music = MusicPage(page, None)
    music._attach_menu = AsyncMock()
    async def wait(watch, *, timeout_s, until, settle_s):
        assert timeout_s == 2400
        watch.uploads = {"song": ("pending", None)}
        assert not until(watch)
        watch.uploads = {"song": upload_state(payload)}
        assert until(watch)
    monkeypatch.setattr(Watch, "wait", wait)
    if error:
        with pytest.raises(FlowError, match=error):
            await music.upload_audio(Path("mine.wav"), confirm_rights=True, timeout_s=2400)
    else:
        uploaded = await music.upload_audio(
            Path("mine.wav"), confirm_rights=True, timeout_s=2400
        )
        assert uploaded == "song"
    music._attach_menu.assert_awaited_once_with("Audio", True)


async def test_mcp_propagates_upload_timeouts(monkeypatch, tmp_path):
    from studiowright import mcp_server as mcp
    audio = tmp_path / "mine.wav"
    audio.touch()
    studio = SimpleNamespace(
        music_upload=AsyncMock(return_value={}), music_chat=AsyncMock(return_value={})
    )
    class Context:
        async def __aenter__(self):
            return studio
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(mcp, "Studio", lambda profile: Context())
    await mcp.music_upload(str(audio), confirm_rights=True, timeout_s=2400)
    studio.music_upload.assert_awaited_once_with(str(audio), confirm_rights=True, timeout_s=2400)
    await mcp.music_chat("hello", audio=str(audio), confirm_rights=True, timeout_s=123,
                         upload_timeout_s=2400)
    request = studio.music_chat.call_args.args[0]
    assert (request.timeout_s, request.upload_timeout_s) == (123, 2400)
    with pytest.raises(OptionError, match="positive"):
        await mcp.music_upload(str(audio), confirm_rights=True, timeout_s=0)


async def test_page_upload_pending_times_out(monkeypatch):
    page = SimpleNamespace(on=lambda *args: None, remove_listener=lambda *args: None,
                           locator=lambda selector: SimpleNamespace(set_input_files=AsyncMock()))
    music = MusicPage(page, None)
    music._attach_menu = AsyncMock()
    # A real Watch with no terminal reply must not return an upload id.
    with pytest.raises(FlowError, match="did not finish"):
        await music.upload_audio(Path("mine.wav"), confirm_rights=True, timeout_s=0.01)


async def test_studio_rejected_audio_does_not_send_chat(monkeypatch, tmp_path):
    audio = tmp_path / "mine.wav"
    audio.touch()
    music = SimpleNamespace(upload_audio=AsyncMock(side_effect=FlowError("copyright match")),
                            chat=AsyncMock())
    monkeypatch.setattr(MusicPage, "open", AsyncMock(return_value=music))
    studio = object.__new__(Studio)
    studio._session = object()
    async def job(kind, params, work):
        return await work("job")
    studio._music_job = job
    with pytest.raises(FlowError, match="copyright match"):
        await studio.music_chat(ChatRequest.build("remix", audio=audio, confirm_rights=True))
    music.chat.assert_not_awaited()
