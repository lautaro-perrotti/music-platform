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
from copilot.audio.session_diagnose import preflight_session
from copilot.audio.source_capture_pool_v1 import capture_source_post_mixer_ref
from copilot.audio.working_copy_policy_v1 import evaluate_working_copy
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.object_ref import ref_from_track
from copilot.daw.session_ready_v1 import SESSION_READY, probe_session_ready
from copilot.daw.state_tokens import attach_tokens
from copilot.musicplan import build_create_track_action
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.runtime.safe_write import build_safe_write_executor
from copilot.schemas.musicplan import (
    ActionTarget,
    DiagnosisBinding,
    ExpectedEffect,
    ExecutionVerificationSpec,
    MusicPlan,
    MusicalVerificationSpec,
    PatternActionParams,
    PlanAction,
    PlanIntentClass,
    PlanStatus,
    ProductionActionKind,
    RollbackSpec,
    VerificationSpec,
)
from copilot.schemas.session import MidiNote
from copilot.studio.contracts import (
    ArtifactRecord,
    CandidateRecord,
    CapabilityState,
    JobRecord,
    JobStatus,
    ProduceCapability,
    ProduceRequest,
    ProjectRecord,
    VariationRecord,
    VersionRecord,
)
from copilot.studio.store import StudioStore, utc_now
from copilot.studio.simulation import SimulatedMusicProvider


class GenerationBackendNotAvailable(RuntimeError):
    """Typed refusal: the real Ableton generation path is not certified yet."""

    code = "GENERATION_BACKEND_NOT_AVAILABLE"

    def __init__(self, operation: str, missing: list[str]) -> None:
        super().__init__(self.code)
        self.operation = operation
        self.missing = missing

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "operation": self.operation, "missing": self.missing}


class ProduceExecutionBlocked(RuntimeError):
    """A real variation could not be executed safely; never simulate it."""

    code = "PRODUCE_EXECUTION_BLOCKED"

    def __init__(self, reason: str, *, detail: str = "", evidence: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.evidence = evidence or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "reason": self.reason, "detail": self.detail, "evidence": self.evidence}


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
        # Runtime handles are intentionally scoped to the one vertical slice.
        # They let KEEP/ROLLBACK reuse the same Core SafeWrite authority; the
        # durable record never becomes a second mutation system.
        self._variation_runtime: dict[str, tuple[Any, Any, Any]] = {}

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

    # --- Produce: generate one real Ableton variation, preview here ----------
    # This is deliberately narrower than the UI's future N-variation surface.
    # The report describes the smallest certified bass slice, not a promise
    # that every Produce control is executable.
    PRODUCE_CAPABILITIES: tuple[tuple[str, CapabilityState, str], ...] = (
        ("ABLETON_SESSION_READINESS", CapabilityState.REAL, "copilot.daw.session_ready_v1.probe_session_ready"),
        ("REFERENCE_ANALYSIS", CapabilityState.REAL, "copilot.audio.music_analyzer.analyze_reference_file (tempo is an input)"),
        ("TRACK_REGION_CAPTURE", CapabilityState.REAL, "copilot.audio.source_capture_pool_v1.capture_source_post_mixer_ref"),
        ("SAFE_WRITE_KEEP_ROLLBACK", CapabilityState.REAL, "SAFE_WRITE_FOUNDATION_V2, certified producer actions only"),
        ("READ_SELECTION", CapabilityState.PARTIAL, "V1 accepts an explicit region; Live selection is not required for this slice"),
        ("SINGLE_VARIATION_PLAN", CapabilityState.REAL, "typed bass plan: explicit region + instruction -> one CREATE_TRACK/CREATE_PATTERN MusicPlan"),
        ("VARIATION_PLANNER_N", CapabilityState.MISSING, "no planner returns N distinct plans from reference + instruction"),
        ("WRITE_MIDI_CLIP", CapabilityState.REAL, "ProductionCompiler -> SafeWrite CREATE_TRACK + CREATE_PATTERN -> authoritative MIDI readback"),
        ("MULTI_ACTION_PLAN_COMPILE", CapabilityState.REAL, "bounded two-action compound only: CREATE_TRACK -> CREATE_PATTERN"),
        ("COPILOT_OWNED_TRACK_POLICY", CapabilityState.REAL, "new deterministic Copilot Variation track plus persisted ownership registry"),
        ("VARIATION_PREVIEW_CAPTURE", CapabilityState.REAL, "created Copilot track -> source_capture_post_mixer_ref -> artifact"),
        ("STUDIO_PRODUCER_BRIDGE", CapabilityState.REAL, "POST /api/projects/{id}/produce calls the real Core compiler/executor"),
        ("KEEP_VARIATION", CapabilityState.REAL, "same SafeWrite transaction handle supports KEEP and owned rollback"),
        ("FOCUS_CLIP", CapabilityState.PARTIAL, "select_clip / set_detail_clip only via untyped bridge_command"),
    )

    PRODUCE_REQUIRED_FOR_ONE = frozenset({
        "ABLETON_SESSION_READINESS", "TRACK_REGION_CAPTURE", "SAFE_WRITE_KEEP_ROLLBACK",
        "SINGLE_VARIATION_PLAN", "WRITE_MIDI_CLIP", "MULTI_ACTION_PLAN_COMPILE",
        "COPILOT_OWNED_TRACK_POLICY", "VARIATION_PREVIEW_CAPTURE",
        "STUDIO_PRODUCER_BRIDGE", "KEEP_VARIATION",
    })

    def produce_capabilities(self) -> dict[str, Any]:
        rows = [ProduceCapability(name=n, state=s, detail=d).model_dump(mode="json") for n, s, d in self.PRODUCE_CAPABILITIES]
        missing = [r["name"] for r in rows if r["state"] != CapabilityState.REAL.value]
        required_missing = [r["name"] for r in rows if r["name"] in self.PRODUCE_REQUIRED_FOR_ONE and r["state"] != CapabilityState.REAL.value]
        return {
            "generation_available": not required_missing,
            "capabilities": rows,
            "missing": missing,
            "required_for_one_variation_missing": required_missing,
            "scope": "ONE_BASS_VARIATION_ONLY",
        }

    def _require_generation_backend(self, operation: str) -> None:
        report = self.produce_capabilities()
        if not report["generation_available"]:
            raise GenerationBackendNotAvailable(operation, report["required_for_one_variation_missing"])

    def produce_generate(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.get_project(project_id)
        start_qn = float(payload.get("start_qn", 0.0) or 0.0)
        end_qn = payload.get("end_qn")
        request = ProduceRequest(
            scope=str(payload.get("scope") or "REGION").upper(),
            instruction=str(payload.get("instruction") or "").strip(),
            variations=int(payload.get("variations") or 0),
            length_bars=int(payload["length_bars"]) if payload.get("length_bars") not in (None, "", "auto", "AUTO") else None,
            start_qn=start_qn,
            end_qn=float(end_qn) if end_qn not in (None, "") else None,
        )
        if request.scope not in {"TRACK", "REGION"}:
            raise ValueError("PRODUCE_SCOPE_INVALID")
        if not request.instruction:
            raise ValueError("PRODUCE_INSTRUCTION_REQUIRED")
        if request.variations not in {1, 3, 5}:
            raise ValueError("PRODUCE_VARIATION_COUNT_INVALID")
        if request.variations != 1:
            raise ValueError("PRODUCE_VARIATION_COUNT_UNSUPPORTED_FOR_V1")
        if request.length_bars not in {None, 8, 16, 32}:
            raise ValueError("PRODUCE_LENGTH_INVALID")
        self._require_generation_backend("produce.generate")
        return self._produce_one_real_variation(project_id, request)

    @staticmethod
    def _bass_notes(length_beats: float) -> list[MidiNote]:
        """Small deterministic bass vocabulary used only for this slice."""
        motif = (36, 36, 39, 41, 36, 43, 41, 39)
        notes: list[MidiNote] = []
        cursor = 0.0
        index = 0
        while cursor + 1.5 <= length_beats:
            notes.append(MidiNote(
                pitch=motif[index % len(motif)],
                start_time=cursor,
                duration=1.5,
                velocity=92 if index % 4 else 104,
            ))
            cursor += 2.0
            index += 1
        return notes

    def _build_variation_plan(self, *, request: ProduceRequest, session: Any, variation_id: str) -> MusicPlan:
        bars = request.length_bars or 8
        length_beats = float(bars * 4)
        track_name = f"Copilot Variation {variation_id[-8:]}"
        create = build_create_track_action(
            project_identity=session.project_identity,
            track_name=track_name,
            track_kind="midi",
            reason=request.instruction,
            evidence_refs=[f"region:{request.start_qn}:{request.end_qn or request.start_qn + length_beats}"],
        )
        pattern = PlanAction(
            action_id=f"pattern_{variation_id}",
            action_type=ProductionActionKind.CREATE_PATTERN,
            target=ActionTarget(ref=create.target.ref),
            params=PatternActionParams(
                clip_index=0,
                length_beats=length_beats,
                notes=self._bass_notes(length_beats),
            ),
            reason=request.instruction,
            evidence_refs=list(create.evidence_refs),
            expected_effect=ExpectedEffect(
                affected_target=f"{track_name}.clip[0]",
                direction="add",
                description="create one editable Copilot bass variation",
                measurement_to_compare_after="authoritative MIDI note readback",
            ),
            verification=VerificationSpec(
                execution=ExecutionVerificationSpec(parameter="clip.notes", expected_after=1.0, unit="patterned"),
                musical=MusicalVerificationSpec(comparison="preview_capture_for_human_review", deferred=True),
            ),
            rollback=RollbackSpec(parameter="pattern", unit="clip", restore_value=-1.0, prepared=True),
        )
        return MusicPlan(
            plan_id=f"variation_plan_{variation_id}",
            status=PlanStatus.DRAFT,
            intent_class=PlanIntentClass.AUTONOMOUS_MUSICAL_IMPROVEMENT,
            diagnosis=DiagnosisBinding(
                diagnosis_id=f"variation_reference_{variation_id}",
                diagnosis_status="REFERENCE_REGION_BOUND",
                diagnosis_accepted=True,
                region_id=f"region_{variation_id}",
            ),
            project_state_token=session.project_token or session.project_identity or "",
            audible_state_token=session.audible_token or "",
            created_at=utc_now(),
            actions=[create, pattern],
            notes=["V1 bounded planner: one bass variation only; no N-variation claims."],
            gate={"reference_region": {"start_qn": request.start_qn, "end_qn": request.end_qn or request.start_qn + length_beats}},
        )

    def _open_variation_live(self) -> tuple[AbletonTcpAdapter, Any]:
        probe = probe_session_ready()
        if probe.status != SESSION_READY or not probe.writes_permitted:
            raise ProduceExecutionBlocked(
                "ABLETON_SESSION_NOT_READY",
                detail=probe.reason,
                evidence=probe.to_dict(),
            )
        daw = AbletonTcpAdapter()
        try:
            daw.connect()
            session = daw.snapshot()
            attach_tokens(session, path=session.project_path, name=session.project_name)
            policy = evaluate_working_copy(session.project_path, session.project_name)
            if not policy.get("operate") or not policy.get("autonomous_writes_ok"):
                raise ProduceExecutionBlocked(
                    "WORKING_COPY_REQUIRED",
                    detail=str(policy.get("reason") or "Ableton project is not a controlled working copy"),
                    evidence={"policy": policy, "project_path": session.project_path, "project_identity": session.project_identity},
                )
            if not session.project_identity:
                raise ProduceExecutionBlocked("PROJECT_IDENTITY_MISSING")
            return daw, session
        except Exception:
            try:
                daw.disconnect()
            except Exception:
                pass
            raise

    def _create_preview_job(self, project_id: str, variation_id: str) -> str:
        job_id = f"variation_capture_{variation_id}"
        if self.store.get_job(job_id) is None:
            job = JobRecord(
                job_id=job_id,
                project_id=project_id,
                status=JobStatus.SUCCEEDED,
                brief={"kind": "VARIATION_PREVIEW_CAPTURE", "variation_id": variation_id},
                provider_id="ableton",
                requested_candidates=1,
                completed_candidates=1,
                current_stage="captured",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            self.store.create_job(job)
        return job_id

    def _produce_one_real_variation(self, project_id: str, request: ProduceRequest) -> dict[str, Any]:
        variation_id = f"variation_{uuid.uuid4().hex[:16]}"
        bars = request.length_bars or 8
        length_beats = float(bars * 4)
        daw, session = self._open_variation_live()
        try:
            plan = self._build_variation_plan(request=request, session=session, variation_id=variation_id)
            compiled = ProductionCompiler().compile(plan, session=session)
            if compiled.status != "COMPILED" or compiled.intent is None:
                raise ProduceExecutionBlocked("PLAN_NOT_COMPILED", detail="; ".join(compiled.reasons), evidence={"plan_id": plan.plan_id})
            persist_dir = self.data_dir / "safe_write" / "variations"
            executor = build_safe_write_executor(
                daw,
                journal_path=persist_dir / f"{variation_id}.jsonl",
                persist_dir=persist_dir,
            )
            result = executor.run(compiled.intent)
            if not result.ok:
                raise ProduceExecutionBlocked(
                    "SAFE_WRITE_FAILED",
                    detail=result.error or "SafeWrite rejected the variation",
                    evidence=result.to_dict(),
                )
            live_after = daw.snapshot()
            create_target = next(item for item in compiled.intent.targets if item.action_id == compiled.intent.executions[0].action_id)
            pattern_step = compiled.intent.executions[1]
            track = live_after.track_by_id(create_target.stable_id)
            clip_ref = f"{track.stable_id}:clip:{int(pattern_step.arguments['clip_index'])}"

            preflight = preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))
            if not preflight.get("pass"):
                executor._rollback_applied(result, compiled.intent)
                raise ProduceExecutionBlocked("CAPTURE_PREFLIGHT_NOT_READY", detail="; ".join(preflight.get("missing") or []), evidence={"preflight": preflight})
            capture_root = self.data_dir / "variation_captures" / variation_id
            capture = capture_source_post_mixer_ref(
                daw,
                session=live_after,
                preflight={**preflight, "project_token": live_after.project_token, "audible_token": live_after.audible_token},
                target_ref=ref_from_track(track, project_identity=live_after.project_identity),
                start_qn=request.start_qn,
                end_qn=request.end_qn or request.start_qn + length_beats,
                region_id=f"region_{variation_id}",
                tempo=float(live_after.transport.tempo),
                dest_root=capture_root,
            )
            if not capture.get("ok") or not capture.get("wav_path"):
                executor._rollback_applied(result, compiled.intent)
                raise ProduceExecutionBlocked("PREVIEW_CAPTURE_FAILED", detail=str(capture.get("error") or "capture returned no WAV"), evidence={"capture": capture})
            wav_path = Path(str(capture["wav_path"]))
            if not wav_path.is_file():
                executor._rollback_applied(result, compiled.intent)
                raise ProduceExecutionBlocked("PREVIEW_ARTIFACT_MISSING", detail=str(wav_path), evidence={"capture": capture})
            preview_job_id = self._create_preview_job(project_id, variation_id)
            artifact_id = f"artifact_{uuid.uuid4().hex[:16]}"
            relative = Path("variation_captures") / variation_id / wav_path.name
            destination = self.data_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if wav_path.resolve() != destination.resolve():
                shutil.copy2(wav_path, destination)
            artifact = ArtifactRecord(
                artifact_id=artifact_id,
                job_id=preview_job_id,
                filename=destination.name,
                relative_path=relative.as_posix(),
                sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                bytes=destination.stat().st_size,
                duration_s=float(capture.get("duration_s") or (length_beats * 60.0 / live_after.transport.tempo)),
                sample_rate=int(capture.get("sample_rate") or 44100),
                provider_metadata={"kind": "VARIATION_PREVIEW", "capture": capture, "plan_id": plan.plan_id},
                rights={"source": "GENERATED_IN_ABLETON", "copilot_owned": True},
                created_at=utc_now(),
            )
            self.store.create_artifact(artifact)
            record = VariationRecord(
                variation_id=variation_id,
                project_id=project_id,
                request_id=variation_id,
                index=1,
                status="READY",
                ableton_track_ref=track.stable_id,
                ableton_clip_ref=clip_ref,
                preview={"artifact_id": artifact_id, "bars": bars, "duration_s": artifact.duration_s, "capture_region_id": f"region_{variation_id}"},
                plan_id=plan.plan_id,
                ownership={"owner": "COPILOT", "track_stable_id": track.stable_id, "track_name": track.name, "clip_index": int(pattern_step.arguments["clip_index"])},
                safe_write={"result": result.to_dict(), "journal_path": result.journal_path, "prestate_path": result.prestate_path},
                region={"start_qn": request.start_qn, "end_qn": request.end_qn or request.start_qn + length_beats, "bars": bars, "scope": request.scope},
                created_at=utc_now(),
            )
            self.store.set_state(project_id, "variations", [*self.store.get_state(project_id, "variations", []), record.model_dump(mode="json")])
            self.store.add_activity(project_id, "variation.ready", "Real Ableton bass variation captured for review", {"variation_id": variation_id, "musical_writes": result.musical_writes, "write_authority": "ProductionCompiler->SafeWriteExecutor"})
            self._variation_runtime[variation_id] = (daw, executor, (result, compiled.intent))
            return {"variation": dict(record.model_dump(mode="json"), preview_url=record.preview.audio_url if record.preview else None), "status": "READY", "musical_writes": result.musical_writes, "write_authority": "ProductionCompiler->SafeWriteExecutor"}
        except Exception:
            try:
                if 'daw' in locals() and daw is not None:
                    daw.disconnect()
            except Exception:
                pass
            raise

    def list_variations(self, project_id: str) -> dict[str, Any]:
        self.get_project(project_id)
        variations = [VariationRecord.model_validate(v) for v in self.store.get_state(project_id, "variations", [])]
        return {"variations": [dict(v.model_dump(mode="json"), preview_url=v.preview.audio_url if v.preview else None) for v in variations]}

    def variation_action(self, variation_id: str, action: str) -> dict[str, Any]:
        if action not in {"keep", "open", "discard"}:
            raise ValueError("VARIATION_ACTION_INVALID")
        found: tuple[str, VariationRecord] | None = None
        for project in self.list_projects():
            rows = [VariationRecord.model_validate(v) for v in self.store.get_state(project.project_id, "variations", [])]
            for row in rows:
                if row.variation_id == variation_id:
                    found = (project.project_id, row)
                    break
            if found:
                break
        if found is None:
            raise KeyError(variation_id)
        project_id, record = found
        if action == "open":
            return {"variation": dict(record.model_dump(mode="json"), preview_url=record.preview.audio_url if record.preview else None)}
        if action == "keep":
            if record.status not in {"READY", "KEPT"}:
                raise ValueError("VARIATION_NOT_REVIEWABLE")
            record = record.model_copy(update={"status": "KEPT"})
            rows = [record.model_dump(mode="json") if item.get("variation_id") == variation_id else item for item in self.store.get_state(project_id, "variations", [])]
            self.store.set_state(project_id, "variations", rows)
            self.store.add_activity(project_id, "variation.kept", "Kept Copilot-owned Ableton variation", {"variation_id": variation_id})
            return {"variation": dict(record.model_dump(mode="json"), preview_url=record.preview.audio_url if record.preview else None), "status": "KEPT"}
        if record.status == "DISCARDED":
            return {"variation": record.model_dump(mode="json"), "status": "DISCARDED"}
        runtime = self._variation_runtime.get(variation_id)
        if runtime is None:
            raise ProduceExecutionBlocked("VARIATION_RUNTIME_UNAVAILABLE", detail="Rollback authority is not attached to this Studio process; no direct delete was attempted.")
        _daw, executor, pair = runtime
        result, intent = pair
        rollback_error = executor._rollback_applied(result, intent)
        if rollback_error:
            raise ProduceExecutionBlocked("VARIATION_ROLLBACK_FAILED", detail=rollback_error, evidence=result.to_dict())
        record = record.model_copy(update={"status": "DISCARDED"})
        rows = [record.model_dump(mode="json") if item.get("variation_id") == variation_id else item for item in self.store.get_state(project_id, "variations", [])]
        self.store.set_state(project_id, "variations", rows)
        self.store.add_activity(project_id, "variation.discarded", "Rolled back Copilot-owned Ableton variation", {"variation_id": variation_id})
        return {"variation": record.model_dump(mode="json"), "status": "DISCARDED", "rollback_verified": True}

    def ableton_status(self) -> dict[str, Any]:
        from copilot.daw.detect import detect_ableton
        from copilot.daw.session_ready_v1 import probe_session_ready
        try:
            detection = detect_ableton().to_dict()
            probe = probe_session_ready()
            return {"detection": detection, "session": probe.to_dict(), "musical_writes": 0}
        except Exception as exc:
            return {"status": "ENVIRONMENT_STATUS_UNAVAILABLE", "reason": str(exc), "musical_writes": 0}
