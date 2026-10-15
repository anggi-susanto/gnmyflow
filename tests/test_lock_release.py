import asyncio
from pathlib import Path

import pytest

from studiowright import paths
from studiowright.studio import Studio


def test_failed_project_lookup_releases_the_profile_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    studio = Studio("test")

    async def broken_project(_: object) -> str:
        raise RuntimeError("project did not become ready")

    monkeypatch.setattr(studio, "_project", broken_project)
    request = type("Request", (), {"project": None})()
    with pytest.raises(RuntimeError):
        asyncio.run(studio.image(request))
    assert not studio._lock.is_locked
    with pytest.raises(RuntimeError):
        asyncio.run(studio.video(request))
    assert not studio._lock.is_locked
