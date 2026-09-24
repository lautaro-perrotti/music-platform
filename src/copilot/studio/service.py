from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from copilot.music_generation.ace_step import AceStepProvider
from copilot.music_generation.elevenlabs import ElevenLabsMusicProvider
from copilot.music_generation.schemas import GenerationBrief, GeneratorRequest
from copilot.music_generation.benchmark import validate_generated_audio
from copilot.studio.contracts import (
    ArtifactRecord,
    CandidateRecord,
    JobRecord,
    JobStatus,
    ProjectRecord,
    VersionRecord,
)
from copilot.studio.store import StudioStore, utc_now
from copilot.studio.simulation import SimulatedMusicProvider


class StudioService:
    """Application service for the first Music Studio vertical slice."""

    def __init__(self, data_dir: Path, *, provider_factory: Callable[[str | None], Any] | None = None) -> None:
        self.store = StudioStore(data_dir)
        self.data_dir = Path(data_dir).resolve()
        self.provider_factory = provider_factory or self._default_provider
        self.mode = os.environ.get("COPILOT_STUDIO_MODE", "HYBRID").upper()
        self._threads: dict[str, threading.Thread] = {}
        self._threads_lock = threading.Lock()
        self._cancelled_jobs: set[str] = set()

    def _default_provider(self, requested: str | None) -> Any | None:
        provider_id = (requested or "").strip().lower()
        if provider_id in {"simulation", "simulated", "demo"}:
            return SimulatedMusicProvider()
        if provider_id in {"ace", "ace-step", "acestep"} or (not provider_id and os.environ.get("ACESTEP_API_URL")):
            return AceStepProvider()
        if provider_id in {"elevenlabs", "elevenlabs-music"} or (not provider_id and os.environ.get("ELEVENLABS_API_KEY")):
            return ElevenLabsMusicProvider()
        if self.mode in {"HYBRID", "SIMULATION"}:
            return SimulatedMusicProvider()
        return None

    def create_project(self, name: str) -> ProjectRecord:
        project_id = f"project_{uuid.uuid4().hex[:16]}"
        project = self.store.create_project(project_id, name, {"mode": self.mode, "current_version_id": None})
        self.store.set_state(project_id, "workspace", {"mode": self.mode, "references": [], "chat": [], "reviews": [], "stems": [], "voice": [], "mixes": [], "studio": {"selected_region": None, "operations": []}, "notifications": []})
        self.store.add_activity(project_id, "project.created", f"Project created: {project.name}")
        return project

    def list_projects(self) -> list[ProjectRecord]:
        return self.store.list_projects()

    def get_project(self, project_id: str) -> ProjectRecord:
        project = self.store.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        return project

    def submit_generation(self, project_id: str, payload: dict[str, Any], *, idempotency_key: str | None = None) -> tuple[JobRecord, bool]:
        self.get_project(project_id)
        existing = self.store.find_idempotent_job(project_id, idempotency_key)
        if existing:
            return existing, False
        prompt = str(payload.get("prompt") or payload.get("user_intent") or "").strip()
        if not prompt:
            raise ValueError("GENERATION_PROMPT_REQUIRED")
        brief = GenerationBrief(
            brief_id=f"brief_{uuid.uuid4().hex[:16]}",
            user_intent=prompt,
            target_duration_s=float(payload.get("target_duration_s", 30.0)),
            tempo_bpm=float(payload["tempo_bpm"]) if payload.get("tempo_bpm") else None,
            meter=payload.get("meter"),
            key_context=payload.get("key_context"),
            instrumental=bool(payload.get("instrumental", True)),
            structural_intent=list(payload.get("structural_intent") or []),
            energy_intent=payload.get("energy_intent"),
            groove_intent=payload.get("groove_intent"),
            candidate_count=min(16, max(1, int(payload.get("candidate_count", 2)))),
            no_write=True,
        )
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        provider_id = str(payload.get("provider") or "auto")
        job = JobRecord(
            job_id=job_id, project_id=project_id, status=JobStatus.CREATED,
            brief=brief.model_dump(mode="json"), provider_id=provider_id,
            requested_candidates=brief.candidate_count, created_at=utc_now(), updated_at=utc_now(),
            idempotency_key=idempotency_key,
        )
        self.store.create_job(job)
        self.store.append_event(job_id, "job.created", {"status": JobStatus.CREATED.value, "brief_id": brief.brief_id})
        thread = threading.Thread(target=self._run_job, args=(job_id,), daemon=True, name=f"studio-{job_id}")
        with self._threads_lock:
            self._threads[job_id] = thread
        thread.start()
        return job, True

    def _emit_status(self, job_id: str, status: JobStatus, stage: str, payload: dict[str, Any] | None = None) -> None:
        if status not in {JobStatus.CANCEL_REQUESTED, JobStatus.CANCELLED} and self._is_cancelled(job_id):
            return
        job = self.store.update_job(job_id, status=status, current_stage=stage)
        body = {"status": status.value, "stage": stage}
        if payload:
            body.update(payload)
        self.store.append_event(job_id, "job.status", body)

    def _is_cancelled(self, job_id: str) -> bool:
        with self._threads_lock:
            return job_id in self._cancelled_jobs

    def _run_job(self, job_id: str) -> None:
        try:
            job = self.store.get_job(job_id)
            if job is None:
                return
            brief = GenerationBrief.model_validate(job.brief)
            if self._is_cancelled(job_id):
                return
            self._emit_status(job_id, JobStatus.QUEUED, "queued")
            if self.mode == "SIMULATION" and os.environ.get("STUDIO_SIMULATION_FAST", "1") != "1":
                time.sleep(0.35)
            self._emit_status(job_id, JobStatus.PROVISIONING, "provider_selection")
            requested = None if job.provider_id in {None, "", "auto"} else job.provider_id
            provider = self.provider_factory(requested)
            if provider is None:
                self.store.update_job(job_id, status=JobStatus.BLOCKED, current_stage="provider_required",
                                      error={"code": "REAL_PROVIDER_REQUIRED", "detail": "No authorized ACE-Step or ElevenLabs provider is configured."})
                self.store.append_event(job_id, "job.blocked", {"code": "REAL_PROVIDER_REQUIRED"})
                return
            descriptor = provider.describe()
            self.store.update_job(job_id, provider_id=descriptor.provider_id,
                                  provider_model=descriptor.model.model_dump(mode="json"))
            health = provider.health().value
            self.store.append_event(job_id, "provider.ready", {"provider": descriptor.provider_id, "model": descriptor.model.model_dump(mode="json"), "health": health})
            if health not in {"HEALTHY", "CONFIGURED"}:
                self.store.update_job(job_id, status=JobStatus.BLOCKED, current_stage="provider_unavailable",
                                      error={"code": "PROVIDER_UNAVAILABLE", "provider": descriptor.provider_id, "health": health})
                self.store.append_event(job_id, "job.blocked", {"code": "PROVIDER_UNAVAILABLE", "health": health})
                return
            self._emit_status(job_id, JobStatus.RUNNING, "generation", {"provider": descriptor.provider_id})
            output_dir = self.data_dir / "provider_runs" / job_id
            output_dir.mkdir(parents=True, exist_ok=True)
            for ordinal in range(1, brief.candidate_count + 1):
                if self._is_cancelled(job_id):
                    return
                request_id = f"{job_id}-candidate-{ordinal:02d}"
                request = GeneratorRequest(
                    request_id=request_id, brief=brief, seed=1721 + ordinal - 1,
                    output_dir=output_dir, no_ableton_access=True,
                )
                self.store.append_event(job_id, "candidate.started", {"ordinal": ordinal, "request_id": request_id})
                batch = provider.generate(request)
                self.store.append_event(job_id, "candidate.provider_result", {
                    "ordinal": ordinal, "status": batch.status, "failures": batch.failures,
                    "provider": batch.provider, "model": batch.model.model_dump(mode="json"),
                    "request": request.model_dump(mode="json"),
                })
                for asset in batch.assets:
                    self._register_asset(job_id, ordinal, asset, brief)
                completed = len(self.store.list_candidates(job_id))
                self.store.update_job(job_id, completed_candidates=completed, current_stage="generation")
                self.store.append_event(job_id, "candidate.progress", {"ordinal": ordinal, "completed_candidates": completed, "requested_candidates": brief.candidate_count})
                if descriptor.provider_id == "simulated-music" and os.environ.get("STUDIO_SIMULATION_FAST", "1") != "1":
                    time.sleep(0.45)
            completed = len(self.store.list_candidates(job_id))
            if self._is_cancelled(job_id):
                return
            if completed:
                self._emit_status(job_id, JobStatus.REVIEW_REQUIRED, "review", {"completed_candidates": completed})
                self._emit_status(job_id, JobStatus.SUCCEEDED, "ready", {"completed_candidates": completed})
            else:
                self.store.update_job(job_id, status=JobStatus.FAILED, current_stage="failed", error={"code": "NO_VALID_CANDIDATES"})
                self.store.append_event(job_id, "job.failed", {"code": "NO_VALID_CANDIDATES"})
        except Exception as exc:
            self.store.update_job(job_id, status=JobStatus.FAILED, current_stage="failed",
                                  error={"code": type(exc).__name__, "detail": str(exc)})
            self.store.append_event(job_id, "job.failed", {"code": type(exc).__name__, "detail": str(exc)})
        finally:
            with self._threads_lock:
                self._threads.pop(job_id, None)

    def _register_asset(self, job_id: str, ordinal: int, asset: Any, brief: GenerationBrief) -> None:
        source = Path(asset.path)
        if not source.is_file():
            return
        validation = validate_generated_audio(asset, expected_duration_s=brief.target_duration_s)
        candidate_id = f"candidate_{job_id}_{ordinal:02d}"
        artifact_id = f"artifact_{uuid.uuid4().hex[:16]}"
        relative = Path("artifacts") / job_id / f"candidate_{ordinal:02d}.wav"
        destination = self.data_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        artifact = ArtifactRecord(
            artifact_id=artifact_id, job_id=job_id, candidate_id=candidate_id,
            filename=destination.name, relative_path=relative.as_posix(), sha256=digest,
            bytes=destination.stat().st_size, duration_s=asset.duration_s,
            sample_rate=asset.sample_rate, provider_metadata=asset.provider_metadata or {},
            rights=asset.rights_manifest.model_dump(mode="json"), created_at=utc_now(),
        )
        self.store.create_artifact(artifact)
        candidate = CandidateRecord(
            candidate_id=candidate_id, job_id=job_id, ordinal=ordinal,
            status="READY" if validation.status == "VALID" else "INVALID",
            label=f"Candidate {chr(64 + min(ordinal, 26))}", artifact_id=artifact_id,
            technical=validation.model_dump(mode="json"), created_at=utc_now(),
        )
        self.store.create_candidate(candidate)
        self.store.append_event(job_id, "candidate.ready", {"candidate_id": candidate_id, "label": candidate.label, "artifact_id": artifact_id, "technical": candidate.technical})

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return {"job": job.model_dump(mode="json"), "candidates": [c.model_dump(mode="json") for c in self.store.list_candidates(job_id)]}

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.BLOCKED, JobStatus.CANCELLED}:
            return self.get_job(job_id)
        with self._threads_lock:
            self._cancelled_jobs.add(job_id)
        self._emit_status(job_id, JobStatus.CANCEL_REQUESTED, "cancel_requested")
        self._emit_status(job_id, JobStatus.CANCELLED, "cancelled")
        self.store.add_activity(job.project_id, "job.cancelled", f"Job cancelled: {job_id}")
        return self.get_job(job_id)

    def retry_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        retry_key = f"retry:{job_id}:{uuid.uuid4().hex[:8]}"
        brief = dict(job.brief)
        retry_job, _ = self.submit_generation(job.project_id, brief, idempotency_key=retry_key)
        self.store.add_activity(job.project_id, "job.retry", f"Retry started for {job_id}", {"attempt_of": job_id, "retry_job_id": retry_job.job_id})
        return {"job": retry_job.model_dump(mode="json"), "attempt_of": job_id}

    def keep_candidate(self, candidate_id: str) -> VersionRecord:
        candidate = self.store.get_candidate(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        job = self.store.get_job(candidate.job_id)
        if job is None:
            raise KeyError(candidate.job_id)
        artifact = self.store.get_artifact(candidate.artifact_id)
        if artifact is None:
            raise KeyError(candidate.artifact_id)
        existing = self.store.list_versions(job.project_id)
        version_id = f"version_{uuid.uuid4().hex[:16]}"
        version = VersionRecord(
            version_id=version_id, project_id=job.project_id,
            name=f"Version {len(existing) + 1:03d}", source_candidate_id=candidate_id,
            manifest={
                "source": "music-studio",
                "job_id": job.job_id,
                "candidate_id": candidate_id,
                "artifact_id": artifact.artifact_id,
                "artifact_sha256": artifact.sha256,
                "ableton_writes": 0,
                "non_destructive": True,
            }, created_at=utc_now(),
        )
        self.store.create_version(version)
        self.store.append_event(job.job_id, "version.created", version.model_dump(mode="json"))
        self.store.set_state(job.project_id, "active_version_id", version.version_id)
        self.store.add_activity(job.project_id, "version.created", f"{version.name} kept from {candidate.label}", {"version_id": version.version_id, "candidate_id": candidate_id})
        return version

    def project_snapshot(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        return {
            "project": project.model_dump(mode="json"),
            "jobs": [job.model_dump(mode="json") for job in self.store.list_jobs(project_id)],
            "versions": [version.model_dump(mode="json") for version in self.store.list_versions(project_id)],
            "workspace": self.store.get_state(project_id, "workspace", {}),
            "activity": self.store.list_activity(project_id),
            "mode": self.mode,
        }

    def workspace_snapshot(self, project_id: str) -> dict[str, Any]:
        self.get_project(project_id)
        return self.project_snapshot(project_id)

    def workspace_action(self, project_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.get_project(project_id)
        workspace = self.store.get_state(project_id, "workspace", {})
        if action == "project.rename":
            name = str(payload.get("name") or "").strip()
            if not name:
                raise ValueError("PROJECT_NAME_REQUIRED")
            with self.store._lock, self.store._connect() as conn:
                conn.execute("UPDATE projects SET name=?, updated_at=? WHERE project_id=?", (name, utc_now(), project_id))
            self.store.add_activity(project_id, "project.renamed", f"Project renamed to {name}")
        elif action == "reference.add":
            reference = {"reference_id": f"reference_{uuid.uuid4().hex[:12]}", "name": str(payload.get("name") or "Demo reference"), "rights": str(payload.get("rights") or "UNKNOWN"), "purposes": list(payload.get("purposes") or ["groove"]), "status": "REGISTERED", "analysis_source": None}
            workspace.setdefault("references", []).append(reference)
            self.store.add_activity(project_id, "reference.added", f"Added reference: {reference['name']}", reference)
        elif action == "reference.analyze":
            reference_id = payload.get("reference_id")
            for reference in workspace.setdefault("references", []):
                if reference.get("reference_id") == reference_id:
                    reference.update({"status": "ANALYZED", "analysis_source": "SIMULATED", "tempo": 128.0, "key": "F minor", "sections": ["Intro", "Groove", "Drop", "Break", "Outro"], "energy": [0.2, 0.55, 0.9, 0.35, 0.18]})
                    self.store.add_activity(project_id, "reference.analyzed", f"Reference analyzed: {reference['name']}", {"analysis_source": "SIMULATED"})
                    break
        elif action == "chat.send":
            message = str(payload.get("message") or "").strip()
            if not message:
                raise ValueError("CHAT_MESSAGE_REQUIRED")
            workspace.setdefault("chat", []).append({"role": "user", "message": message, "origin": "USER"})
            response = {"role": "assistant", "message": "I can make that change as a simulated producer proposal. I will preserve the current version and create a reviewable derived version.", "origin": "SIMULATED_PRODUCER", "proposal": {"kind": "GENERATE_VARIATION", "safe": True}}
            workspace["chat"].append(response)
            self.store.add_activity(project_id, "chat.message", "Producer conversation updated", {"origin": "SIMULATED_PRODUCER"})
        elif action in {"studio.operation", "voice.generate", "stems.generate", "mix.run", "ableton.apply"}:
            record = {"operation_id": f"op_{uuid.uuid4().hex[:12]}", "kind": action, "status": "SIMULATED", "payload": payload, "provenance": "SIMULATED", "created_at": utc_now()}
            key = "studio" if action == "studio.operation" else "voice" if action == "voice.generate" else "stems" if action == "stems.generate" else "mixes" if action == "mix.run" else "ableton"
            workspace.setdefault(key, []).append(record) if isinstance(workspace.get(key), list) else workspace.__setitem__(key, [record])
            if action == "ableton.apply":
                record.update({"status": "VERIFIED_SIMULATED", "real_ableton_writes": 0, "stages": ["preparing", "applying", "verifying", "verified"]})
            self.store.add_activity(project_id, action, f"{action.replace('.', ' ').title()} simulated", {"provenance": "SIMULATED"})
        elif action == "compare.review":
            review = {"review_id": f"review_{uuid.uuid4().hex[:12]}", "kind": payload.get("kind", "candidate"), "choice": payload.get("choice", "TOO_CLOSE"), "notes": payload.get("notes", ""), "created_at": utc_now()}
            workspace.setdefault("reviews", []).append(review)
            self.store.add_activity(project_id, "review.created", "Comparison review saved", review)
        else:
            raise ValueError("UNSUPPORTED_WORKSPACE_ACTION")
        self.store.set_state(project_id, "workspace", workspace)
        return self.workspace_snapshot(project_id)

    def ableton_status(self) -> dict[str, Any]:
        from copilot.daw.detect import detect_ableton
        from copilot.daw.session_ready_v1 import probe_session_ready
        try:
            detection = detect_ableton().to_dict()
            probe = probe_session_ready()
            return {"detection": detection, "session": probe.to_dict(), "musical_writes": 0}
        except Exception as exc:
            return {"status": "ENVIRONMENT_STATUS_UNAVAILABLE", "reason": str(exc), "musical_writes": 0}
