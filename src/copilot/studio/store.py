from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from copilot.studio.contracts import (
    ArtifactRecord,
    CandidateRecord,
    JobRecord,
    JobStatus,
    ProjectRecord,
    StudioEvent,
    VersionRecord,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _decode(value: str | None) -> Any:
    return json.loads(value) if value else {}


class StudioStore:
    """Small durable store for the Studio application boundary.

    SQLite is deliberately scoped to the Studio. It is not a second Ableton
    transaction system: no Live mutation is stored or executed here.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts_root = self.root / "artifacts"
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db_path = self.root / "studio.sqlite3"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    ableton_identity TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    status TEXT NOT NULL,
                    brief_json TEXT NOT NULL,
                    provider_id TEXT,
                    provider_model_json TEXT,
                    requested_candidates INTEGER NOT NULL,
                    completed_candidates INTEGER NOT NULL DEFAULT 0,
                    current_stage TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    idempotency_key TEXT,
                    UNIQUE(project_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS events (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    candidate_id TEXT,
                    filename TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    duration_s REAL,
                    sample_rate INTEGER,
                    mime_type TEXT NOT NULL,
                    provider_metadata_json TEXT NOT NULL,
                    rights_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    ordinal INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    label TEXT NOT NULL,
                    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                    technical_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS versions (
                    version_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    name TEXT NOT NULL,
                    source_candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_state (
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    state_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, state_key)
                );
                CREATE TABLE IF NOT EXISTS activity (
                    activity_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _project(row: sqlite3.Row) -> ProjectRecord:
        return ProjectRecord(
            project_id=row["project_id"], name=row["name"], created_at=row["created_at"],
            updated_at=row["updated_at"], ableton_identity=row["ableton_identity"],
            metadata=_decode(row["metadata_json"]),
        )

    @staticmethod
    def _job(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"], project_id=row["project_id"], status=row["status"],
            brief=_decode(row["brief_json"]), provider_id=row["provider_id"],
            provider_model=_decode(row["provider_model_json"]) if row["provider_model_json"] else None,
            requested_candidates=row["requested_candidates"],
            completed_candidates=row["completed_candidates"], current_stage=row["current_stage"],
            error=_decode(row["error_json"]) if row["error_json"] else None,
            created_at=row["created_at"], updated_at=row["updated_at"],
            idempotency_key=row["idempotency_key"],
        )

    def create_project(self, project_id: str, name: str, metadata: dict[str, Any] | None = None) -> ProjectRecord:
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, NULL, ?)",
                (project_id, name.strip() or "Untitled project", now, now, _json(metadata or {})),
            )
        return self.get_project(project_id)  # type: ignore[return-value]

    def get_project(self, project_id: str) -> ProjectRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
        return self._project(row) if row else None

    def list_projects(self) -> list[ProjectRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [self._project(row) for row in rows]

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def find_idempotent_job(self, project_id: str, key: str | None) -> JobRecord | None:
        if not key:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE project_id=? AND idempotency_key=?", (project_id, key)
            ).fetchone()
        return self._job(row) if row else None

    def create_job(self, job: JobRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO jobs
                (job_id, project_id, status, brief_json, provider_id, provider_model_json,
                 requested_candidates, completed_candidates, current_stage, error_json,
                 created_at, updated_at, idempotency_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.job_id, job.project_id, job.status.value, _json(job.brief), job.provider_id,
                    _json(job.provider_model) if job.provider_model else None,
                    job.requested_candidates, job.completed_candidates, job.current_stage,
                    _json(job.error) if job.error else None, job.created_at, job.updated_at,
                    job.idempotency_key,
                ),
            )

    def update_job(self, job_id: str, *, status: JobStatus | None = None,
                   provider_id: str | None = None, provider_model: dict[str, Any] | None = None,
                   completed_candidates: int | None = None, current_stage: str | None = None,
                   error: dict[str, Any] | None = None) -> JobRecord:
        current = self.get_job(job_id)
        if current is None:
            raise KeyError(job_id)
        now = utc_now()
        values = (
            status.value if status is not None else current.status.value,
            provider_id if provider_id is not None else current.provider_id,
            _json(provider_model) if provider_model is not None else (_json(current.provider_model) if current.provider_model else None),
            completed_candidates if completed_candidates is not None else current.completed_candidates,
            current_stage if current_stage is not None else current.current_stage,
            _json(error) if error is not None else (_json(current.error) if current.error else None),
            now,
            job_id,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE jobs SET status=?, provider_id=?, provider_model_json=?,
                   completed_candidates=?, current_stage=?, error_json=?, updated_at=?
                   WHERE job_id=?""", values,
            )
        return self.get_job(job_id)  # type: ignore[return-value]

    def append_event(self, job_id: str, event_type: str, payload: dict[str, Any] | None = None) -> StudioEvent:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM events WHERE job_id=?", (job_id,)).fetchone()
            sequence = int(row["next"])
            now = utc_now()
            event_id = f"evt_{job_id}_{sequence:06d}"
            conn.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, sequence, event_id, event_type, _json(payload or {}), now),
            )
        return StudioEvent(event_id=event_id, job_id=job_id, sequence=sequence,
                           event_type=event_type, payload=payload or {}, created_at=now)

    def events_since(self, job_id: str, after: int = 0) -> list[StudioEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE job_id=? AND sequence>? ORDER BY sequence", (job_id, after)
            ).fetchall()
        return [StudioEvent(event_id=row["event_id"], job_id=job_id, sequence=row["sequence"],
                            event_type=row["event_type"], payload=_decode(row["payload_json"]),
                            created_at=row["created_at"]) for row in rows]

    def create_artifact(self, artifact: ArtifactRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (artifact.artifact_id, artifact.job_id, artifact.candidate_id, artifact.filename,
                 artifact.relative_path, artifact.sha256, artifact.bytes, artifact.duration_s,
                 artifact.sample_rate, artifact.mime_type, _json(artifact.provider_metadata),
                 _json(artifact.rights), artifact.created_at),
            )

    def create_candidate(self, candidate: CandidateRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (candidate.candidate_id, candidate.job_id, candidate.ordinal, candidate.status,
                 candidate.label, candidate.artifact_id, _json(candidate.technical), candidate.created_at),
            )

    def get_candidate(self, candidate_id: str) -> CandidateRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if not row:
            return None
        return CandidateRecord(candidate_id=row["candidate_id"], job_id=row["job_id"], ordinal=row["ordinal"],
                              status=row["status"], label=row["label"], artifact_id=row["artifact_id"],
                              technical=_decode(row["technical_json"]), created_at=row["created_at"])

    def list_candidates(self, job_id: str) -> list[CandidateRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM candidates WHERE job_id=? ORDER BY ordinal", (job_id,)).fetchall()
        return [CandidateRecord(candidate_id=row["candidate_id"], job_id=row["job_id"], ordinal=row["ordinal"],
                                status=row["status"], label=row["label"], artifact_id=row["artifact_id"],
                                technical=_decode(row["technical_json"]), created_at=row["created_at"]) for row in rows]

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if not row:
            return None
        return ArtifactRecord(artifact_id=row["artifact_id"], job_id=row["job_id"], candidate_id=row["candidate_id"],
                              filename=row["filename"], relative_path=row["relative_path"], sha256=row["sha256"],
                              bytes=row["bytes"], duration_s=row["duration_s"], sample_rate=row["sample_rate"],
                              mime_type=row["mime_type"], provider_metadata=_decode(row["provider_metadata_json"]),
                              rights=_decode(row["rights_json"]), created_at=row["created_at"])

    def create_version(self, version: VersionRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("INSERT INTO versions VALUES (?, ?, ?, ?, ?, ?)",
                         (version.version_id, version.project_id, version.name,
                          version.source_candidate_id, _json(version.manifest), version.created_at))

    def list_versions(self, project_id: str) -> list[VersionRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM versions WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
        return [VersionRecord(version_id=row["version_id"], project_id=row["project_id"], name=row["name"],
                              source_candidate_id=row["source_candidate_id"], manifest=_decode(row["manifest_json"]),
                              created_at=row["created_at"]) for row in rows]

    def list_jobs(self, project_id: str) -> list[JobRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
        return [self._job(row) for row in rows]

    def get_state(self, project_id: str, key: str, default: Any = None) -> Any:
        with self._connect() as conn:
            row = conn.execute("SELECT value_json FROM studio_state WHERE project_id=? AND state_key=?", (project_id, key)).fetchone()
        return _decode(row["value_json"]) if row else default

    def set_state(self, project_id: str, key: str, value: Any) -> None:
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO studio_state(project_id,state_key,value_json,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(project_id,state_key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (project_id, key, _json(value), now),
            )

    def all_state(self, project_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT state_key,value_json FROM studio_state WHERE project_id=?", (project_id,)).fetchall()
        return {row["state_key"]: _decode(row["value_json"]) for row in rows}

    def add_activity(self, project_id: str, kind: str, message: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        activity = {"activity_id": f"activity_{uuid.uuid4().hex[:16]}", "project_id": project_id,
                    "kind": kind, "message": message, "payload": payload or {}, "created_at": utc_now()}
        with self._lock, self._connect() as conn:
            conn.execute("INSERT INTO activity VALUES(?,?,?,?,?,?)", (activity["activity_id"], project_id, kind, message, _json(payload or {}), activity["created_at"]))
        return activity

    def list_activity(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM activity WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
        return [{"activity_id": r["activity_id"], "project_id": project_id, "kind": r["kind"], "message": r["message"], "payload": _decode(r["payload_json"]), "created_at": r["created_at"]} for r in rows]
