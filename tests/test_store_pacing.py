from __future__ import annotations

from pathlib import Path

import pytest

from studiowright.pacing import CoolingDown, Governor, PaceSettings
from studiowright.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "sw.db")


def test_assets_are_found_by_hash_per_project(store: Store) -> None:
    store.add_asset("p1", "m1", "image", name="a.png", sha256="h1")
    assert store.asset_by_hash("p1", "h1").media_id == "m1"
    assert store.asset_by_hash("p2", "h1") is None
    store.forget_asset("p1", "m1")
    assert store.asset_by_hash("p1", "h1") is None


def test_asset_upsert_keeps_known_fields(store: Store) -> None:
    store.add_asset("p1", "m1", "image", name="a.png", sha256="h1")
    store.add_asset("p1", "m1", "image")
    asset = store.asset("p1", "m1")
    assert (asset.name, asset.sha256) == ("a.png", "h1")


def test_jobs_round_trip(store: Store) -> None:
    job_id = store.create_job("image", "default", "p1", {"prompt": "x"})
    store.update_job(job_id, status="done", result={"files": ["a.jpg"]})
    row = store.job(job_id)
    assert row["status"] == "done"
    assert '"a.jpg"' in row["result"]
    assert [r["id"] for r in store.recent_jobs("default")] == [job_id]


def test_settings_round_trip(store: Store) -> None:
    assert store.get_setting("project.default") is None
    store.set_setting("project.default", "p1")
    store.set_setting("project.default", "p2")
    assert store.get_setting("project.default") == "p2"


def test_governor_spaces_submits(store: Store) -> None:
    governor = Governor(store, "default", PaceSettings(gap_s=20, jitter_s=0, cooldown_s=60))
    assert governor.reserve(now=1000) == 0
    assert governor.reserve(now=1000) == 20
    assert governor.reserve(now=1005) == 35
    assert governor.reserve(now=2000) == 0


def test_governor_is_per_profile(store: Store) -> None:
    settings = PaceSettings(gap_s=20, jitter_s=0, cooldown_s=60)
    assert Governor(store, "a", settings).reserve(now=1000) == 0
    assert Governor(store, "b", settings).reserve(now=1000) == 0


def test_unusual_activity_starts_cooldown(store: Store) -> None:
    governor = Governor(store, "default", PaceSettings(gap_s=20, jitter_s=0, cooldown_s=600))
    governor.report_unusual_activity(now=1000)
    with pytest.raises(CoolingDown):
        governor.reserve(now=1300)
    assert governor.reserve(now=1601) == 0
    assert governor.status()["refusals"] == 1
