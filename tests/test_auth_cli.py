from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from studiowright import paths
from studiowright.cli import main
from studiowright.store import Store


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    prefs = tmp_path / "profiles" / "work" / "Default"
    prefs.mkdir(parents=True)
    (prefs / "Preferences").write_text(
        json.dumps({"account_info": [{"email": "someone@example.com"}]}), encoding="utf-8"
    )
    (tmp_path / "profiles" / "spare").mkdir()
    Store().set_setting("project.default.work", "p1")
    return tmp_path


def run(*args: str) -> str:
    result = CliRunner().invoke(main, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_list_shows_the_signed_in_account(home: Path) -> None:
    lines = run("auth", "list").splitlines()
    assert lines == ["spare  (not signed in)", "work   someone@example.com"]


def test_rename_keeps_sign_in_and_settings(home: Path) -> None:
    run("auth", "rename", "work", "account-1")
    assert "account-1  someone@example.com" in run("auth", "list")
    assert Store().get_setting("project.default.account-1") == "p1"
    result = CliRunner().invoke(main, ["auth", "rename", "spare", "account-1"])
    assert result.exit_code != 0 and "already exists" in result.output


def test_logout_removes_the_profile_only(home: Path) -> None:
    run("auth", "logout", "work", "--yes")
    assert not (home / "profiles" / "work").exists()
    assert (home / "profiles" / "spare").is_dir()
    assert Store().get_setting("project.default.work") is None
    result = CliRunner().invoke(main, ["auth", "logout", "nobody", "--yes"])
    assert result.exit_code != 0 and "no profile named" in result.output
