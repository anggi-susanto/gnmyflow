"""StudioWright's local state in one SQLite file: projects, assets, jobs and pacing.

Every CLI call and MCP tool opens the same file, so writes use short transactions and a
busy timeout instead of a long-lived connection.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from studiowright import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, profile TEXT NOT NULL, title TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    project_id TEXT NOT NULL, media_id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT,
    sha256 TEXT, source TEXT, created REAL NOT NULL,
    PRIMARY KEY (project_id, media_id)
);
CREATE INDEX IF NOT EXISTS assets_by_hash ON assets (project_id, sha256);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, profile TEXT NOT NULL, project_id TEXT,
    params TEXT NOT NULL, status TEXT NOT NULL, result TEXT, error TEXT,
    created REAL NOT NULL, started REAL, finished REAL
);
CREATE TABLE IF NOT EXISTS pacing (
    profile TEXT PRIMARY KEY, next_free REAL NOT NULL DEFAULT 0,
    cooldown_until REAL NOT NULL DEFAULT 0, refusals INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Asset:
    project_id: str
    media_id: str
    kind: str
    name: str | None
    sha256: str | None


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (paths.home() / "studiowright.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """An immediate (write-locked) transaction, so read-modify-write is atomic."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    # --- settings ---------------------------------------------------------------------
    def get_setting(self, key: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # --- projects ---------------------------------------------------------------------
    def add_project(self, project_id: str, profile: str, title: str | None) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO projects (id, profile, title, created) VALUES (?, ?, ?, ?)",
                (project_id, profile, title, time.time()),
            )

    def projects(self, profile: str) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM projects WHERE profile = ? ORDER BY created DESC", (profile,)
            ).fetchall()

    # --- assets -----------------------------------------------------------------------
    def add_asset(
        self,
        project_id: str,
        media_id: str,
        kind: str,
        name: str | None = None,
        sha256: str | None = None,
        source: str | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO assets (project_id, media_id, kind, name, sha256, source, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (project_id, media_id) DO UPDATE SET "
                "name = COALESCE(excluded.name, name), sha256 = COALESCE(excluded.sha256, sha256)",
                (project_id, media_id, kind, name, sha256, source, time.time()),
            )

    def asset_by_hash(self, project_id: str, sha256: str) -> Asset | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM assets WHERE project_id = ? AND sha256 = ? ORDER BY created DESC",
                (project_id, sha256),
            ).fetchone()
        return _asset(row) if row else None

    def asset(self, project_id: str, media_id: str) -> Asset | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM assets WHERE project_id = ? AND media_id = ?",
                (project_id, media_id),
            ).fetchone()
        return _asset(row) if row else None

    def forget_asset(self, project_id: str, media_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "DELETE FROM assets WHERE project_id = ? AND media_id = ?", (project_id, media_id)
            )

    # --- jobs -------------------------------------------------------------------------
    def create_job(
        self, kind: str, profile: str, project_id: str | None, params: dict[str, Any]
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs (id, kind, profile, project_id, params, status, created) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
                (job_id, kind, profile, project_id, json.dumps(params), time.time()),
            )
        return job_id

    def update_job(self, job_id: str, **fields: Any) -> None:
        for key in ("result", "params"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        columns = ", ".join(f"{name} = ?" for name in fields)
        with self.connect() as db:
            db.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))

    def job(self, job_id: str) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def recent_jobs(self, profile: str, limit: int = 20) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM jobs WHERE profile = ? ORDER BY created DESC LIMIT ?",
                (profile, limit),
            ).fetchall()


def _asset(row: sqlite3.Row) -> Asset:
    return Asset(row["project_id"], row["media_id"], row["kind"], row["name"], row["sha256"])
