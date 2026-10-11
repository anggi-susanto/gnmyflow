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


def test_mark_interrupted_only_touches_unfinished_jobs(store: Store) -> None:
    running = store.create_job("video", "default", "p1", {})
    store.update_job(running, status="running")
    done = store.create_job("image", "default", "p1", {})
    store.update_job(done, status="done")
    other = store.create_job("image", "other", "p1", {})
    assert store.mark_interrupted("default") == 1
    assert store.job(running)["status"] == "interrupted"
    assert store.job(done)["status"] == "done"
    assert store.job(other)["status"] == "queued"


def test_assets_by_prefix_and_source(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    store.add_asset(
        "p",
        "675340df-4eb7-4a45-aaed-3b03a640d70a",
        "image",
        "sw-2a72e6e12d.jpg",
        "h1",
        "C:/ref.jpg",
    )
    store.add_asset(
        "p", "675340aa-0000-0000-0000-000000000000", "image", "x.jpg", "h2", "generated"
    )
    assert store.assets_by_prefix("p", "675340d") == ["675340df-4eb7-4a45-aaed-3b03a640d70a"]
    assert len(store.assets_by_prefix("p", "675340")) == 2
    asset = store.asset_by_hash("p", "h1")
    assert asset is not None and asset.source == "C:/ref.jpg" and asset.name == "sw-2a72e6e12d.jpg"


def test_upscales_are_paced_apart_from_generations(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    upscale = Governor(store, "p:upscale", PaceSettings.for_upscales())
    generate = Governor(store, "p", PaceSettings(gap_s=0, jitter_s=0))
    assert upscale.settings.gap_s >= 60 and upscale.settings.cooldown_s >= 3600
    upscale.report_unusual_activity()
    with pytest.raises(CoolingDown):
        upscale.reserve()
    assert generate.reserve() == 0  # generations are not paused by an upscale refusal
