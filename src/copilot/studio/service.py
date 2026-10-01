from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np
import soundfile as sf

from copilot.music_generation.ace_step import AceStepProvider
from copilot.music_generation.elevenlabs import ElevenLabsMusicProvider
from copilot.music_generation.schemas import (
    GeneratedAsset,
    GenerationBrief,
    GeneratorRequest,
    ModelManifest,
    PerformanceManifest,
    RightsManifest,
)
from copilot.music_generation.benchmark import validate_generated_audio
from copilot.music_generation.registry import MusicGeneratorRegistry
from copilot.audio.session_diagnose import preflight_session
from copilot.audio.capture_preflight import generic_capture_preflight
from copilot.audio.live_capture import capture_master_segment
from copilot.audio.source_capture_pool_v1 import capture_source_post_mixer_ref
from copilot.audio.working_copy_policy_v1 import evaluate_working_copy
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.object_ref import ref_from_track
from copilot.daw.session_ready_v1 import SESSION_READY, probe_session_ready
from copilot.daw.state_tokens import attach_tokens
from copilot.daw.transport_events import AbletonTransportEventClient, TRANSPORT_EVENTS_CAPABILITY
from copilot.integration.reference_variation_v1 import (
    ReferenceVariationError,
    build_reference_bound_bass_notes,
    load_astra_interpretation,
    load_musical_understanding,
    load_reference_pack,
    validate_midi_reference,
)
from copilot.integration.multi_element_variation_v1 import (
    SUPPORTED_ELEMENTS,
    MultiElementVariationBundle,
    build_multi_element_variation_bundle,
    load_harmonic_understanding,
)
from copilot.musicplan import build_create_track_action
from copilot.music_model import build_bass_variation_intent, build_canonical_music_model_view_v2
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.runtime.safe_write import build_safe_write_executor
from copilot.schemas.musicplan import (
    ActionTarget,
    ArrangementDuplicateActionParams,
    DiagnosisBinding,
    DeviceLoadActionParams,
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
from copilot.studio.contracts import (
    ArtifactRecord,
    CandidateRecord,
    CapabilityState,
    JobRecord,
    JobStatus,
    ProduceCapability,
    ProduceRequest,
    ProjectRecord,
    PersistenceStatus,
    MusicalDecision,
    VariationRecord,
    VersionRecord,
)
from copilot.schemas.session import SessionState
from copilot.studio.drum_workbench import (
    load_drum_workbench,
    read_drum_event_preview,
    read_drum_sample_preview,
)
from copilot.studio.persistence import (
    MUSICAL_DECISION_ACCEPTED,
    MUSICAL_DECISION_KEPT,
    MUSICAL_DECISION_PENDING,
    PERSISTENCE_CANDIDATE_PENDING,
    PERSISTENCE_DISK_SAVE_REQUIRED,
    candidate_persistence_state,
    persist_working_copy,
    reconciled_after_rollback,
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


class StudioTransportUnavailable(RuntimeError):
    """Typed read-only transport subscription refusal for Studio SSE."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _inspect_variation_wav(path: Path) -> dict[str, Any]:
    """Measure the isolated preview independently of capture metadata."""
    try:
        info = sf.info(str(path))
        if info.frames <= 0 or info.samplerate <= 0:
            raise ValueError("empty WAV")
        square_sum = 0.0
        sample_count = 0
        peak = 0.0
        for block in sf.blocks(str(path), blocksize=65536, dtype="float32", always_2d=True):
            if not np.isfinite(block).all():
                raise ValueError("non-finite WAV sample")
            peak = max(peak, float(np.max(np.abs(block))))
            square_sum += float(np.sum(np.square(block, dtype=np.float64)))
            sample_count += block.size
        rms = math.sqrt(square_sum / sample_count) if sample_count else 0.0
        return {
            "sample_rate": info.samplerate,
            "duration_s": info.frames / info.samplerate,
            "peak": peak,
            "rms": rms,
            "rms_dbfs": 20.0 * math.log10(rms) if rms > 0 else None,
            "signal_status": "HAS_SIGNAL" if peak > 1e-4 and rms > 1e-5 else "SILENCE",
            "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    except Exception as exc:  # noqa: BLE001
        raise ProduceExecutionBlocked("PREVIEW_WAV_INVALID", detail=str(exc)) from exc


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
        configured_default = os.environ.get("MUSIC_GENERATOR_PROVIDER")
        default_id = "simulation" if self.mode == "SIMULATION" and not configured_default else (configured_default or "eleven_music")
        provider_id = (requested or default_id).strip().lower()
        aliases = {
            "eleven_music": "elevenlabs-music",
            "elevenlabs": "elevenlabs-music",
            "elevenlabs-music": "elevenlabs-music",
            "ace": "ace-step",
            "ace-step": "ace-step",
            "acestep": "ace-step",
            "simulation": "simulated-music",
            "simulated": "simulated-music",
            "demo": "simulated-music",
        }
        canonical = aliases.get(provider_id)
        if canonical is None:
            return None
        registry = MusicGeneratorRegistry()
        if canonical == "elevenlabs-music":
            registry.register(ElevenLabsMusicProvider())
        elif canonical == "ace-step":
            registry.register(AceStepProvider())
        elif canonical == "simulated-music" and (requested or self.mode == "SIMULATION"):
            registry.register(SimulatedMusicProvider())
        elif canonical == "simulated-music":
            return None
        return registry.get(canonical)

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
        self.store.update_job(job_id, status=status, current_stage=stage)
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
                missing_credential = descriptor.provider_id == "elevenlabs-music" and not getattr(provider, "api_key", None)
                error_code = "CREDENTIAL_REQUIRED" if missing_credential else "PROVIDER_UNAVAILABLE"
                stage = "credentials_required" if missing_credential else "provider_unavailable"
                self.store.update_job(job_id, status=JobStatus.BLOCKED, current_stage=stage,
                                      error={"code": error_code, "provider": descriptor.provider_id, "health": health,
                                             "credential": "ELEVENLABS_API_KEY" if missing_credential else None})
                self.store.append_event(job_id, "job.blocked", {"code": error_code, "stage": stage, "health": health})
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
            sample_rate=asset.sample_rate,
            provider_metadata={
                **(asset.provider_metadata or {}),
                "provider_request": asset.provider_request,
                "internal_candidate_seed": asset.seed,
                "generation_request_id": asset.asset_id.rsplit(":", 1)[-1],
                "generation_ordinal": ordinal,
                "brief_id": brief.brief_id,
                "asset_sha256": digest,
                "generated_asset_id": asset.asset_id,
            },
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
        if candidate.status != "READY":
            raise ValueError("CANDIDATE_NOT_TECHNICALLY_VALID")
        existing = self.store.list_versions(job.project_id)
        version_id = f"version_{uuid.uuid4().hex[:16]}"
        version = VersionRecord(
            version_id=version_id, project_id=job.project_id,
            name=f"Version {len(existing) + 1:03d}", source_candidate_id=candidate_id,
            manifest={
                "source": "music-studio",
                "job_id": job.job_id,
                "candidate_id": candidate_id,
                "selected_candidate_id": candidate_id,
                "musical_winner": candidate_id,
                "selected_by": "HUMAN",
                "artifact_id": artifact.artifact_id,
                "artifact_sha256": artifact.sha256,
                "ableton_writes": 0,
                "non_destructive": True,
            }, created_at=utc_now(),
        )
        self.store.create_version(version)
        self.store.append_event(job.job_id, "version.created", version.model_dump(mode="json"))
        self.store.set_state(job.project_id, "active_version_id", version.version_id)
        self.store.set_state(job.project_id, "human_selection", {
            "selected_candidate_id": candidate_id,
            "selected_by": "HUMAN",
            "selected_at": version.created_at,
            "version_id": version.version_id,
        })
        self.store.append_event(job.job_id, "candidate.human_selected", {
            "candidate_id": candidate_id,
            "selected_by": "HUMAN",
            "musical_winner": candidate_id,
            "technical_score_is_not_winner_authority": True,
        })
        self.store.add_activity(job.project_id, "version.created", f"{version.name} kept from {candidate.label}", {"version_id": version.version_id, "candidate_id": candidate_id})
        return version

    def separate_selected_candidate_stems(self, candidate_id: str, *, variation_id: str = "six_stems_v1") -> list[ArtifactRecord]:
        """Paid stem separation is available only after explicit human selection."""
        candidate = self.store.get_candidate(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        job = self.store.get_job(candidate.job_id)
        artifact = self.store.get_artifact(candidate.artifact_id)
        if job is None or artifact is None:
            raise KeyError(candidate_id)
        selection = self.store.get_state(job.project_id, "human_selection", {})
        if selection.get("selected_candidate_id") != candidate_id or selection.get("selected_by") != "HUMAN":
            raise ValueError("HUMAN_SELECTION_REQUIRED_BEFORE_STEM_SEPARATION")
        if job.provider_id != "elevenlabs-music":
            raise ValueError("STEM_SEPARATION_REQUIRES_ELEVENLABS_ASSET")
        persisted_ids = self.store.get_state(job.project_id, f"stem_separation:{candidate_id}", [])
        if persisted_ids:
            existing_records = [self.store.get_artifact(item) for item in persisted_ids]
            if all(item is not None for item in existing_records):
                return [item for item in existing_records if item is not None]
        path = (self.data_dir / artifact.relative_path).resolve()
        if not path.is_file():
            raise FileNotFoundError("SELECTED_AUDIO_ARTIFACT_MISSING")
        if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
            raise ValueError("SELECTED_AUDIO_ARTIFACT_HASH_MISMATCH")
        provider = self.provider_factory("elevenlabs-music")
        separate = getattr(provider, "separate_stems", None)
        if not callable(separate):
            raise GenerationBackendNotAvailable("stem-separation", ["ElevenLabs stem provider unavailable"])
        model_data = job.provider_model or {}
        model = ModelManifest.model_validate(model_data)
        source_asset = GeneratedAsset(
            asset_id=str(artifact.provider_metadata.get("generated_asset_id") or f"studio:{artifact.artifact_id}"),
            path=path,
            sha256=artifact.sha256,
            bytes=artifact.bytes,
            duration_s=artifact.duration_s,
            sample_rate=artifact.sample_rate,
            non_silent=bool(candidate.technical.get("non_silent")),
            model=model,
            seed=int(artifact.provider_metadata.get("internal_candidate_seed", 0)),
            prompt=str(job.brief.get("user_intent", "")),
            performance=PerformanceManifest(device=model.provider, actual_duration_s=artifact.duration_s, sample_rate=artifact.sample_rate),
            rights_manifest=RightsManifest.model_validate(artifact.rights),
            provider_metadata=artifact.provider_metadata,
        )
        raw_root = self.data_dir / "provider_runs" / "derived_stems" / candidate_id
        derived = separate(source_asset, output_dir=raw_root, variation_id=variation_id)
        persisted: list[ArtifactRecord] = []
        for ordinal, item in enumerate(derived, 1):
            source = Path(item["path"])
            relative = Path("artifacts") / "derived_stems" / candidate_id / f"stem-{ordinal:02d}.wav"
            destination = self.data_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            record = ArtifactRecord(
                artifact_id=f"artifact_{uuid.uuid4().hex[:16]}",
                job_id=job.job_id,
                candidate_id=candidate_id,
                filename=destination.name,
                relative_path=relative.as_posix(),
                sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                bytes=destination.stat().st_size,
                duration_s=float(item["duration_s"]),
                sample_rate=int(item["sample_rate"]),
                mime_type="audio/wav",
                provider_metadata={
                    **item,
                    "path": None,
                    "kind": "DERIVED_STEM",
                    "lineage": {
                        "source_generated_asset_id": source_asset.asset_id,
                        "source_artifact_id": artifact.artifact_id,
                        "source_sha256": artifact.sha256,
                        "human_selected_candidate_id": candidate_id,
                    },
                },
                rights={**artifact.rights, "source_provider": model.provider, "source_model": model.model_id, "source_asset_id": source_asset.asset_id, "source_hash": artifact.sha256},
                created_at=utc_now(),
            )
            self.store.create_artifact(record)
            persisted.append(record)
        self.store.append_event(job.job_id, "candidate.stems_separated", {
            "candidate_id": candidate_id,
            "selected_by": "HUMAN",
            "derived_artifact_ids": [item.artifact_id for item in persisted],
            "stem_variation_id": variation_id,
        })
        self.store.set_state(job.project_id, f"stem_separation:{candidate_id}", [item.artifact_id for item in persisted])
        return persisted

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

    # --- Produce: generate bounded real Ableton variations, preview here ----
    PRODUCE_CAPABILITIES: tuple[tuple[str, CapabilityState, str], ...] = (
        ("ABLETON_SESSION_READINESS", CapabilityState.REAL, "copilot.daw.session_ready_v1.probe_session_ready"),
        ("REFERENCE_ANALYSIS", CapabilityState.REAL, "copilot.audio.music_analyzer.analyze_reference_file (tempo is an input)"),
        ("TRACK_REGION_CAPTURE", CapabilityState.REAL, "copilot.audio.source_capture_pool_v1.capture_source_post_mixer_ref"),
        ("SAFE_WRITE_KEEP_ROLLBACK", CapabilityState.REAL, "SAFE_WRITE_FOUNDATION_V2, certified producer actions only"),
        ("READ_SELECTION", CapabilityState.PARTIAL, "V1 accepts an explicit region; Live selection is not required for this slice"),
        ("SINGLE_VARIATION_PLAN", CapabilityState.REAL, "one 8-bar bass variation from explicit, project-bound MIDI evidence paths"),
        ("VARIATION_PLANNER_N", CapabilityState.REAL, "deterministic 1/3/5 reference-bound strategies with distinct symbolic signatures"),
        ("MULTI_VARIATION_PLAN", CapabilityState.REAL, "one request yields 1, 3, or 5 independently reviewable variation records"),
        ("WRITE_MIDI_CLIP", CapabilityState.REAL, "ProductionCompiler -> SafeWrite -> authoritative MIDI and Arrangement readback"),
        ("MULTI_ACTION_PLAN_COMPILE", CapabilityState.REAL, "bounded grouped BASS/DRUMS/HARMONIC create -> instrument -> pattern -> Arrangement compound"),
        ("MULTI_ELEMENT_COMBINED_PREVIEW", CapabilityState.REAL, "one real candidate captured from Ableton Main for human review; harmonic support remains provisional"),
        ("COPILOT_OWNED_TRACK_POLICY", CapabilityState.REAL, "new deterministic Copilot Variation track plus persisted ownership registry"),
        ("VARIATION_PREVIEW_CAPTURE", CapabilityState.REAL, "created Arrangement clip -> isolated capture; READY requires measured signal"),
        ("STUDIO_PRODUCER_BRIDGE", CapabilityState.REAL, "POST /api/projects/{id}/produce calls the real Core compiler/executor"),
        ("WORKING_COPY_PERSISTENCE", CapabilityState.PARTIAL, "candidate state is persisted and disk evidence is verified; current Ableton bridge does not advertise session.save"),
        ("KEEP_VARIATION", CapabilityState.PARTIAL, "musical KEEP is separate from SafeWrite; durable working-copy save is unavailable until the bridge advertises session.save"),
        ("FOCUS_CLIP", CapabilityState.PARTIAL, "select_clip / set_detail_clip only via untyped bridge_command"),
    )

    PRODUCE_REQUIRED_FOR_ONE = frozenset({
        "ABLETON_SESSION_READINESS", "TRACK_REGION_CAPTURE", "SAFE_WRITE_KEEP_ROLLBACK",
        "SINGLE_VARIATION_PLAN", "WRITE_MIDI_CLIP", "MULTI_ACTION_PLAN_COMPILE",
        "COPILOT_OWNED_TRACK_POLICY", "VARIATION_PREVIEW_CAPTURE",
        "STUDIO_PRODUCER_BRIDGE",
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
            "scope": "ONE_TO_FIVE_BASS_VARIATIONS_PLUS_ONE_MULTI_ELEMENT_CANDIDATE",
            "reference_evidence_required": ["reference_analysis_path", "musical_understanding_path"],
            "reference_evidence_sources": ["explicit request", "project configuration"],
            "discard_after_studio_restart": "UNAVAILABLE",
        }

    def _require_generation_backend(self, operation: str) -> None:
        report = self.produce_capabilities()
        if not report["generation_available"]:
            raise GenerationBackendNotAvailable(operation, report["required_for_one_variation_missing"])

    def produce_generate(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.get_project(project_id)
        configured = self.store.get_state(project_id, "reference_variation_config", {}) or {}
        if not isinstance(configured, dict):
            configured = {}
        start_qn = float(payload.get("start_qn", configured.get("start_qn", 0.0)) or 0.0)
        end_qn = payload.get("end_qn", configured.get("end_qn"))
        reference_path = payload.get("reference_analysis_path") or configured.get("reference_analysis_path")
        understanding_path = payload.get("musical_understanding_path") or configured.get("musical_understanding_path")
        requested_elements = payload.get("elements", configured.get("elements", [])) or []
        if isinstance(requested_elements, str):
            requested_elements = [requested_elements]
        elements = [str(item).strip().upper() for item in requested_elements if str(item).strip()]
        harmonic_path = payload.get("harmonic_understanding_path") or configured.get("harmonic_understanding_path")
        request = ProduceRequest(
            scope=str(payload.get("scope") or "REGION").upper(),
            instruction=str(payload.get("instruction") or "").strip(),
            variations=int(payload.get("variations") or 0),
            length_bars=int(payload["length_bars"]) if payload.get("length_bars") not in (None, "", "auto", "AUTO") else None,
            start_qn=start_qn,
            end_qn=float(end_qn) if end_qn not in (None, "") else None,
            reference_analysis_path=str(reference_path) if reference_path else None,
            musical_understanding_path=str(understanding_path) if understanding_path else None,
            harmonic_understanding_path=str(harmonic_path) if harmonic_path else None,
            elements=elements,
            astra_interpretation_path=str(payload["astra_interpretation_path"]) if payload.get("astra_interpretation_path") else None,
        )
        if request.scope not in {"TRACK", "REGION"}:
            raise ValueError("PRODUCE_SCOPE_INVALID")
        if not request.instruction:
            raise ValueError("PRODUCE_INSTRUCTION_REQUIRED")
        if request.variations not in {1, 3, 5}:
            raise ValueError("PRODUCE_VARIATION_COUNT_INVALID")
        if request.length_bars not in {None, 8}:
            raise ValueError("PRODUCE_LENGTH_INVALID")
        if request.elements and set(request.elements) != set(SUPPORTED_ELEMENTS):
            raise ValueError("MULTI_ELEMENT_REQUIRED_ROLES")
        if request.elements and request.variations != 1:
            raise ValueError("MULTI_ELEMENT_ONE_CANDIDATE_ONLY")
        if not request.reference_analysis_path or not request.musical_understanding_path:
            raise ReferenceVariationError("AUTHORITATIVE_REFERENCE_MIDI_REQUIRED")
        if request.elements and not request.harmonic_understanding_path:
            raise ReferenceVariationError("HARMONIC_UNDERSTANDING_REQUIRED")
        if payload.get("start_qn") is None and configured.get("start_qn") is None:
            request.start_qn = float(load_reference_pack(request.reference_analysis_path).timeline.get("start_qn") or 0.0)
        self._require_generation_backend("produce.generate")
        if request.elements:
            return self._produce_one_multi_element_candidate(project_id, request)
        if request.variations == 1:
            return self._produce_one_real_variation(project_id, request)
        shared_daw, _ = self._open_variation_live()
        try:
            results = [
                self._produce_one_real_variation(
                    project_id, request, variation_index=index,
                    variation_count=request.variations, live_daw=shared_daw,
                )
                for index in range(1, request.variations + 1)
            ]
        except Exception:
            try:
                shared_daw.disconnect()
            except Exception:
                pass
            raise
        if request.variations == 1:
            return results[0]
        return {
            "variations": [result["variation"] for result in results],
            "status": "READY",
            "musical_writes": sum(int(result.get("musical_writes") or 0) for result in results),
            "write_authority": "ProductionCompiler->SafeWriteExecutor",
        }

    def _build_variation_plan(
        self, *, request: ProduceRequest, session: Any, variation_id: str,
        variation_index: int = 1, variation_count: int = 1,
    ) -> MusicPlan:
        bars = request.length_bars or 8
        length_beats = float(bars * 4)
        track_name = f"Copilot Variation {variation_index} {variation_id[-8:]}"
        pack_path = Path(request.reference_analysis_path or "").resolve()
        understanding_path = Path(request.musical_understanding_path or "").resolve()
        working_root = Path(session.project_path).resolve().parent
        if not pack_path.is_relative_to(working_root) or not understanding_path.is_relative_to(working_root):
            raise ReferenceVariationError("REFERENCE_OUTSIDE_WORKING_COPY")
        reference_pack = load_reference_pack(pack_path)
        understanding = load_musical_understanding(understanding_path)
        validate_midi_reference(
            reference_pack, understanding, pack_path=pack_path,
            understanding_path=understanding_path,
            project_identity=session.project_identity or "",
        )
        if request.end_qn is not None and abs(request.end_qn - request.start_qn - length_beats) > 1e-6:
            raise ReferenceVariationError("REFERENCE_REGION_LENGTH_MISMATCH")
        notes, reference_features = build_reference_bound_bass_notes(
            reference_pack, understanding, start_qn=request.start_qn,
            length_beats=length_beats, variation_index=variation_index,
        )
        reference_features.update({
            "source_reference_id": reference_pack.reference_id,
            "source_project_id": reference_pack.project_id,
            "tempo_bpm": reference_pack.tempo_bpm,
        })
        canonical_view = build_canonical_music_model_view_v2(
            reference_pack,
            musical_understanding=understanding,
        )
        variation_intent = build_bass_variation_intent(
            canonical_view,
            intent_id=f"intent_{variation_id}",
            start_qn=request.start_qn,
            length_bars=bars,
            instruction=request.instruction,
            variation_index=variation_index,
            variation_count=variation_count,
        )
        reference_features["canonical_music_model"] = canonical_view.model_dump(mode="json")
        reference_features["musical_variation_intent"] = variation_intent.model_dump(mode="json")
        astra_evidence = load_astra_interpretation(request.astra_interpretation_path) if request.astra_interpretation_path else None
        reference_features["pack_sha256"] = hashlib.sha256(pack_path.read_bytes()).hexdigest()
        reference_features["understanding_sha256"] = hashlib.sha256(understanding_path.read_bytes()).hexdigest()
        create = build_create_track_action(
            project_identity=session.project_identity,
            track_name=track_name,
            track_kind="midi",
            reason=request.instruction,
            evidence_refs=[f"region:{request.start_qn}:{request.end_qn or request.start_qn + length_beats}"],
        )
        device = PlanAction(
            action_id=f"device_{variation_id}",
            action_type=ProductionActionKind.LOAD_DEVICE,
            target=ActionTarget(ref=dict(create.target.ref)),
            params=DeviceLoadActionParams(device_name="Operator", device_uri="Operator"),
            reason="give the Copilot MIDI variation an audible native instrument",
            evidence_refs=list(create.evidence_refs),
            expected_effect=ExpectedEffect(
                affected_target=f"{track_name}.devices",
                direction="add",
                description="load one native Ableton instrument for real preview capture",
                measurement_to_compare_after="authoritative device readback",
            ),
            verification=VerificationSpec(
                execution=ExecutionVerificationSpec(parameter="track.device", expected_after=1.0, unit="present"),
                musical=MusicalVerificationSpec(comparison="preview_capture_for_human_review", deferred=True),
            ),
            rollback=RollbackSpec(parameter="device", unit="device", restore_value=-1.0, prepared=True),
        )
        pattern = PlanAction(
            action_id=f"pattern_{variation_id}",
            action_type=ProductionActionKind.CREATE_PATTERN,
            target=ActionTarget(ref=create.target.ref),
            params=PatternActionParams(
                clip_index=0,
                length_beats=length_beats,
                notes=notes,
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
        arrangement = PlanAction(
            action_id=f"arrangement_{variation_id}",
            action_type=ProductionActionKind.DUPLICATE_CLIP_TO_ARRANGEMENT,
            target=ActionTarget(ref=dict(create.target.ref)),
            params=ArrangementDuplicateActionParams(
                clip_index=0, destination_time=request.start_qn, length=None,
            ),
            reason="place the generated bass clip in the exact region to be captured",
            evidence_refs=list(create.evidence_refs),
            expected_effect=ExpectedEffect(
                affected_target=f"{track_name}.arrangement", direction="add",
                description="place the editable bass clip at the reference region",
                measurement_to_compare_after="authoritative arrangement clip readback",
            ),
            verification=VerificationSpec(
                execution=ExecutionVerificationSpec(parameter="arrangement.clip", expected_after=1.0, unit="present"),
                musical=MusicalVerificationSpec(comparison="isolated_preview_capture", deferred=True),
            ),
            rollback=RollbackSpec(parameter="arrangement.clip", unit="clip", restore_value=0.0, prepared=True),
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
            actions=[create, device, pattern, arrangement],
            notes=[
                "V1 bounded planner: one bass variation only; no N-variation claims.",
                "Authoritative source MIDI pitches preserved; secondary onsets varied within their bars.",
                "Variation intent is derived from the canonical read-only musical model view.",
            ],
            gate={
                "reference_region": {"start_qn": request.start_qn, "end_qn": request.end_qn or request.start_qn + length_beats},
                "reference_features": reference_features,
                "astra_interpretation_advisory": astra_evidence,
            },
        )

    def _build_multi_element_plan(
        self,
        *,
        request: ProduceRequest,
        session: Any,
        variation_id: str,
    ) -> tuple[MusicPlan, MultiElementVariationBundle]:
        """Build one bounded BASS/DRUMS/HARMONIC compound plan.

        The bundle is the immutable, evidence-bound planning artifact.  This
        method only translates it into the existing canonical MusicPlan
        actions; it does not analyze audio or write to Live.
        """
        bars = request.length_bars or 8
        length_beats = float(bars * 4)
        pack_path = Path(request.reference_analysis_path or "").resolve()
        understanding_path = Path(request.musical_understanding_path or "").resolve()
        harmonic_path = Path(request.harmonic_understanding_path or "").resolve()
        working_root = Path(session.project_path).resolve().parent
        for source in (pack_path, understanding_path, harmonic_path):
            if not source.is_relative_to(working_root):
                raise ReferenceVariationError("REFERENCE_OUTSIDE_WORKING_COPY")
        reference_pack = load_reference_pack(pack_path)
        understanding = load_musical_understanding(understanding_path)
        validate_midi_reference(
            reference_pack,
            understanding,
            pack_path=pack_path,
            understanding_path=understanding_path,
            project_identity=session.project_identity or "",
        )
        if request.end_qn is not None and abs(request.end_qn - request.start_qn - length_beats) > 1e-6:
            raise ReferenceVariationError("REFERENCE_REGION_LENGTH_MISMATCH")
        harmonic = load_harmonic_understanding(harmonic_path)
        bundle = build_multi_element_variation_bundle(
            reference_pack,
            understanding,
            start_qn=request.start_qn,
            length_beats=length_beats,
            variation_index=1,
            variation_count=1,
            harmonic_understanding=harmonic,
        )
        if bundle.status != "READY":
            raise ReferenceVariationError(
                "MULTI_ELEMENT_EVIDENCE_INSUFFICIENT: " + "; ".join(bundle.limitations)
            )

        actions: list[PlanAction] = []
        role_track_names: dict[str, str] = {}
        for role in SUPPORTED_ELEMENTS:
            element = bundle.elements[role]
            track_name = f"Copilot Multi {role.title()} {variation_id[-8:]}"
            role_track_names[role] = track_name
            evidence_refs = list(element.evidence_refs) or [
                f"reference:{bundle.reference_id}:{role}:{bundle.start_qn}:{bundle.end_qn}"
            ]
            create = build_create_track_action(
                project_identity=session.project_identity,
                track_name=track_name,
                track_kind="midi",
                reason=f"create evidence-bound {role.lower()} element",
                evidence_refs=evidence_refs,
            )
            actions.append(create)
            actions.append(
                PlanAction(
                    action_id=f"device_{role.lower()}_{variation_id}",
                    action_type=ProductionActionKind.LOAD_DEVICE,
                    target=ActionTarget(ref=dict(create.target.ref)),
                    params=DeviceLoadActionParams(device_name="Operator", device_uri="Operator"),
                    reason=f"load one native instrument for the {role.lower()} preview",
                    evidence_refs=evidence_refs,
                    expected_effect=ExpectedEffect(
                        affected_target=f"{track_name}.devices",
                        direction="add",
                        description="load one native Ableton instrument",
                        measurement_to_compare_after="authoritative device readback",
                    ),
                    verification=VerificationSpec(
                        execution=ExecutionVerificationSpec(parameter="track.device", expected_after=1.0, unit="present"),
                        musical=MusicalVerificationSpec(comparison="combined_preview_for_human_review", deferred=True),
                    ),
                    rollback=RollbackSpec(parameter="device", unit="device", restore_value=-1.0, prepared=True),
                )
            )
            actions.append(
                PlanAction(
                    action_id=f"pattern_{role.lower()}_{variation_id}",
                    action_type=ProductionActionKind.CREATE_PATTERN,
                    target=ActionTarget(ref=dict(create.target.ref)),
                    params=PatternActionParams(
                        clip_index=0,
                        length_beats=length_beats,
                        notes=element.notes,
                    ),
                    reason=element.transformation,
                    evidence_refs=evidence_refs,
                    expected_effect=ExpectedEffect(
                        affected_target=f"{track_name}.clip[0]",
                        direction="add",
                        description=f"create the evidence-bound {role.lower()} pattern",
                        measurement_to_compare_after="authoritative MIDI note readback",
                    ),
                    verification=VerificationSpec(
                        execution=ExecutionVerificationSpec(parameter="clip.notes", expected_after=float(len(element.notes)), unit="patterned"),
                        musical=MusicalVerificationSpec(comparison="combined_preview_for_human_review", deferred=True),
                    ),
                    rollback=RollbackSpec(parameter="pattern", unit="clip", restore_value=-1.0, prepared=True),
                )
            )
            actions.append(
                PlanAction(
                    action_id=f"arrangement_{role.lower()}_{variation_id}",
                    action_type=ProductionActionKind.DUPLICATE_CLIP_TO_ARRANGEMENT,
                    target=ActionTarget(ref=dict(create.target.ref)),
                    params=ArrangementDuplicateActionParams(
                        clip_index=0,
                        destination_time=request.start_qn,
                        length=None,
                    ),
                    reason=f"place the {role.lower()} element at the exact reference region",
                    evidence_refs=evidence_refs,
                    expected_effect=ExpectedEffect(
                        affected_target=f"{track_name}.arrangement",
                        direction="add",
                        description="place the editable clip at the reference region",
                        measurement_to_compare_after="authoritative arrangement readback",
                    ),
                    verification=VerificationSpec(
                        execution=ExecutionVerificationSpec(parameter="arrangement.clip", expected_after=1.0, unit="present"),
                        musical=MusicalVerificationSpec(comparison="combined_preview_for_human_review", deferred=True),
                    ),
                    rollback=RollbackSpec(parameter="arrangement.clip", unit="clip", restore_value=0.0, prepared=True),
                )
            )

        canonical_view = build_canonical_music_model_view_v2(
            reference_pack,
            musical_understanding=understanding,
        )
        reference_features = {
            "source_reference_id": reference_pack.reference_id,
            "source_project_id": reference_pack.project_id,
            "tempo_bpm": reference_pack.tempo_bpm,
            "pack_sha256": hashlib.sha256(pack_path.read_bytes()).hexdigest(),
            "understanding_sha256": hashlib.sha256(understanding_path.read_bytes()).hexdigest(),
            "harmonic_understanding_sha256": hashlib.sha256(harmonic_path.read_bytes()).hexdigest(),
            "canonical_music_model": canonical_view.model_dump(mode="json"),
            "multi_element_bundle": bundle.model_dump(mode="json"),
            "role_track_names": role_track_names,
        }
        plan = MusicPlan(
            plan_id=f"multi_element_plan_{variation_id}",
            status=PlanStatus.DRAFT,
            intent_class=PlanIntentClass.AUTONOMOUS_MUSICAL_IMPROVEMENT,
            diagnosis=DiagnosisBinding(
                diagnosis_id=f"multi_element_reference_{variation_id}",
                diagnosis_status="REFERENCE_REGION_BOUND",
                diagnosis_accepted=True,
                region_id=f"region_{variation_id}",
            ),
            project_state_token=session.project_token or session.project_identity or "",
            audible_state_token=session.audible_token or "",
            created_at=utc_now(),
            actions=actions,
            notes=[
                "One bounded multi-element candidate: BASS + DRUMS + HARMONIC_SUPPORT.",
                "All material is newly generated from persisted evidence-bound patterns.",
                "Harmonic support is provisional pending human listening; no auto-keep.",
            ],
            gate={
                "reference_region": {"start_qn": bundle.start_qn, "end_qn": bundle.end_qn},
                "reference_features": reference_features,
                "multi_element_bundle": bundle.model_dump(mode="json"),
                "harmonic_status": "PROVISIONAL_EVIDENCE",
                "role_track_names": role_track_names,
            },
        )
        return plan, bundle

    def _produce_one_multi_element_candidate(
        self, project_id: str, request: ProduceRequest
    ) -> dict[str, Any]:
        """Execute exactly one real multi-element candidate and capture Main."""
        variation_id = f"multi_element_{uuid.uuid4().hex[:16]}"
        bars = request.length_bars or 8
        length_beats = float(bars * 4)
        daw, session = self._open_variation_live()
        try:
            plan, bundle = self._build_multi_element_plan(
                request=request,
                session=session,
                variation_id=variation_id,
            )
            bundle_dir = self.data_dir / "multi_element_bundles"
            bundle_dir.mkdir(parents=True, exist_ok=True)
            bundle_path = bundle_dir / f"{variation_id}.json"
            bundle_path.write_text(
                bundle.model_dump_json(indent=2), encoding="utf-8"
            )
            bundle_sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
            compiled = ProductionCompiler().compile(plan, session=session)
            if compiled.status != "COMPILED" or compiled.intent is None:
                raise ProduceExecutionBlocked(
                    "PLAN_NOT_COMPILED",
                    detail="; ".join(compiled.reasons),
                    evidence={"plan_id": plan.plan_id},
                )
            persist_dir = self.data_dir / "safe_write" / "multi_element"
            executor = build_safe_write_executor(
                daw,
                journal_path=persist_dir / f"{variation_id}.jsonl",
                persist_dir=persist_dir,
            )
            result = executor.run(compiled.intent)
            if not result.ok:
                raise ProduceExecutionBlocked(
                    "SAFE_WRITE_FAILED",
                    detail=result.error or "SafeWrite rejected the multi-element candidate",
                    evidence=result.to_dict(),
                )
            live_after = daw.snapshot()
            role_tracks: dict[str, Any] = {}
            for role, track_name in (plan.gate.get("role_track_names") or {}).items():
                track = live_after.track_by_name(track_name)
                if track is None:
                    raise ProduceExecutionBlocked("ROLE_TRACK_READBACK_MISSING", evidence={"role": role, "track_name": track_name})
                role_tracks[role] = track
            arrangement_steps = [
                step for step in compiled.intent.executions
                if step.action_type == "DUPLICATE_CLIP_TO_ARRANGEMENT"
            ]
            if len(arrangement_steps) != len(SUPPORTED_ELEMENTS) or any(
                not step.expected_after.get("arrangement_clip_ids") for step in arrangement_steps
            ):
                rollback_error = executor._rollback_applied(result, compiled.intent)
                raise ProduceExecutionBlocked(
                    "ARRANGEMENT_READBACK_MISSING",
                    detail=rollback_error or "arrangement readback missing",
                    evidence=result.to_dict(),
                )
            preflight = generic_capture_preflight(
                preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))
            )
            if not preflight.get("pass"):
                rollback_error = executor._rollback_applied(result, compiled.intent)
                raise ProduceExecutionBlocked(
                    "CAPTURE_PREFLIGHT_NOT_READY",
                    detail="; ".join(preflight.get("missing") or []) + (f"; rollback={rollback_error}" if rollback_error else ""),
                    evidence={"preflight": preflight, "safe_write": result.to_dict()},
                )
            capture_root = self.data_dir / "multi_element_captures" / variation_id
            capture_root.mkdir(parents=True, exist_ok=True)
            try:
                combined_asset = capture_master_segment(
                    daw,
                    request.start_qn,
                    request.end_qn or request.start_qn + length_beats,
                    require_signal=True,
                )
            except Exception as exc:  # noqa: BLE001
                rollback_error = executor._rollback_applied(result, compiled.intent)
                raise ProduceExecutionBlocked(
                    "COMBINED_PREVIEW_CAPTURE_FAILED",
                    detail=f"{exc}; rollback={rollback_error or 'verified'}",
                ) from exc
            source_wav = Path(str(combined_asset.analysis_file_path or combined_asset.file_path))
            if not source_wav.is_file():
                rollback_error = executor._rollback_applied(result, compiled.intent)
                raise ProduceExecutionBlocked(
                    "COMBINED_PREVIEW_ARTIFACT_MISSING",
                    detail=f"{source_wav}; rollback={rollback_error or 'verified'}",
                )
            measured = _inspect_variation_wav(source_wav)
            expected_duration = length_beats * 60.0 / float(live_after.transport.tempo)
            if measured["signal_status"] != "HAS_SIGNAL" or abs(measured["duration_s"] - expected_duration) > 0.25:
                rollback_error = executor._rollback_applied(result, compiled.intent)
                raise ProduceExecutionBlocked(
                    "COMBINED_PREVIEW_INVALID",
                    detail=f"measured={measured}; rollback={rollback_error or 'verified'}",
                )
            artifact_id = f"artifact_{uuid.uuid4().hex[:16]}"
            preview_job_id = self._create_preview_job(project_id, variation_id)
            relative = Path("artifacts") / "multi_element_captures" / variation_id / source_wav.name
            destination = self.data_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source_wav.resolve() != destination.resolve():
                shutil.copy2(source_wav, destination)
            artifact = ArtifactRecord(
                artifact_id=artifact_id,
                job_id=preview_job_id,
                filename=destination.name,
                relative_path=relative.as_posix(),
                sha256=measured["audio_sha256"],
                bytes=destination.stat().st_size,
                duration_s=measured["duration_s"],
                sample_rate=measured["sample_rate"],
                provider_metadata={
                    "kind": "MULTI_ELEMENT_COMBINED_PREVIEW",
                    "candidate_id": variation_id,
                    "bundle_path": str(bundle_path),
                    "bundle_sha256": bundle_sha256,
                    "capture": combined_asset.model_dump(mode="json"),
                    "measured_signal": measured,
                    "plan_id": plan.plan_id,
                },
                rights={"source": "GENERATED_IN_ABLETON", "copilot_owned": True},
                created_at=utc_now(),
            )
            self.store.create_artifact(artifact)
            first_role = role_tracks[SUPPORTED_ELEMENTS[0]]
            first_pattern = next(
                step for step in compiled.intent.executions if step.action_type == "CREATE_PATTERN"
            )
            record = VariationRecord(
                variation_id=variation_id,
                project_id=project_id,
                request_id=variation_id,
                index=1,
                status="READY",
                musical_decision=MUSICAL_DECISION_PENDING,
                persistence_status=PERSISTENCE_CANDIDATE_PENDING,
                persistence=candidate_persistence_state(session, live_after),
                ableton_track_ref=first_role.stable_id,
                ableton_clip_ref=f"{first_role.stable_id}:clip:{int(first_pattern.arguments['clip_index'])}",
                preview={
                    "artifact_id": artifact_id,
                    "bars": bars,
                    "duration_s": artifact.duration_s,
                    "capture_region_id": f"region_{variation_id}",
                    "signal_status": measured["signal_status"],
                    "rms_dbfs": measured["rms_dbfs"],
                },
                plan_id=plan.plan_id,
                ownership={
                    "owner": "COPILOT",
                    "candidate_kind": "MULTI_ELEMENT",
                    "roles": {
                        role: {
                            "track_stable_id": track.stable_id,
                            "track_name": track.name,
                        }
                        for role, track in role_tracks.items()
                    },
                    "arrangement_clip_ids": [
                        clip_id
                        for step in arrangement_steps
                        for clip_id in step.expected_after["arrangement_clip_ids"]
                    ],
                },
                safe_write={
                    "result": result.to_dict(),
                    "journal_path": result.journal_path,
                    "prestate_path": result.prestate_path,
                    "single_transaction": True,
                    "rollback_scope": "BASS+DRUMS+HARMONIC_ALL_OR_NONE",
                },
                region={
                    "start_qn": request.start_qn,
                    "end_qn": request.end_qn or request.start_qn + length_beats,
                    "bars": bars,
                    "scope": request.scope,
                },
                reference={
                    "source_reference_id": bundle.reference_id,
                    "source_project_id": bundle.project_id,
                    "tempo_bpm": bundle.structure.get("tempo_bpm"),
                },
                musical_summary={
                    "elements": list(SUPPORTED_ELEMENTS),
                    "harmonic_status": "PROVISIONAL_EVIDENCE",
                    "human_review": "PENDING",
                    "combined_preview": True,
                    "limitations": bundle.limitations,
                },
                provenance={
                    "bundle_path": str(bundle_path),
                    "bundle_sha256": bundle_sha256,
                    "bundle_schema_version": bundle.schema_version,
                    "evidence_refs": bundle.evidence_refs,
                    "all_material_is_new": bundle.all_material_is_new,
                    "harmonic_status": "PROVISIONAL_EVIDENCE",
                    "source_not_copied": True,
                },
                created_at=utc_now(),
            )
            self.store.set_state(
                project_id,
                "variations",
                [*self.store.get_state(project_id, "variations", []), record.model_dump(mode="json")],
            )
            self.store.set_state(project_id, "project_persistence", record.persistence)
            self.store.add_activity(
                project_id,
                "variation.ready",
                "Real Ableton multi-element candidate captured for human review",
                {
                    "variation_id": variation_id,
                    "candidate_kind": "MULTI_ELEMENT",
                    "roles": list(SUPPORTED_ELEMENTS),
                    "musical_writes": result.musical_writes,
                    "write_authority": "ProductionCompiler->SafeWriteExecutor",
                },
            )
            self._variation_runtime[variation_id] = (daw, executor, (result, compiled.intent))
            return {
                "variation": dict(record.model_dump(mode="json"), preview_url=record.preview.audio_url if record.preview else None),
                "status": "READY",
                "candidate_kind": "MULTI_ELEMENT",
                "musical_writes": result.musical_writes,
                "write_authority": "ProductionCompiler->SafeWriteExecutor",
                "human_review": "PENDING",
            }
        except Exception:
            try:
                daw.disconnect()
            except Exception:
                pass
            raise

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

    def _produce_one_real_variation(
        self, project_id: str, request: ProduceRequest, *,
        variation_index: int = 1, variation_count: int = 1,
        live_daw: AbletonTcpAdapter | None = None,
    ) -> dict[str, Any]:
        variation_id = f"variation_{uuid.uuid4().hex[:16]}"
        bars = request.length_bars or 8
        length_beats = float(bars * 4)
        owns_daw = live_daw is None
        daw, session = self._open_variation_live() if owns_daw else (live_daw, live_daw.snapshot())
        if not session.project_identity:
            raise ProduceExecutionBlocked("PROJECT_IDENTITY_MISSING")
        try:
            plan = self._build_variation_plan(
                request=request, session=session, variation_id=variation_id,
                variation_index=variation_index, variation_count=variation_count,
            )
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
            pattern_step = next(
                item for item in compiled.intent.executions
                if item.action_type == "CREATE_PATTERN"
            )
            arrangement_step = next(
                item for item in compiled.intent.executions
                if item.action_type == "DUPLICATE_CLIP_TO_ARRANGEMENT"
            )
            track = live_after.track_by_id(create_target.stable_id)
            clip_ref = f"{track.stable_id}:clip:{int(pattern_step.arguments['clip_index'])}"

            def fail_preview(reason: str, *, detail: str = "", evidence: dict[str, Any] | None = None) -> None:
                rollback_error = executor._rollback_applied(result, compiled.intent)
                if rollback_error:
                    raise ProduceExecutionBlocked(
                        "VARIATION_ROLLBACK_FAILED", detail=rollback_error,
                        evidence={"reason": reason, "safe_write": result.to_dict()},
                    )
                failed = VariationRecord(
                    variation_id=variation_id, project_id=project_id, request_id=variation_id,
                    index=variation_index, status="FAILED", ableton_track_ref=track.stable_id,
                    ableton_clip_ref=clip_ref, plan_id=plan.plan_id, failure_reason=reason,
                    safe_write={"result": result.to_dict(), "rollback_verified": True},
                    region={"start_qn": request.start_qn, "end_qn": request.start_qn + length_beats, "bars": bars},
                    created_at=utc_now(),
                )
                self.store.set_state(project_id, "variations", [
                    *self.store.get_state(project_id, "variations", []), failed.model_dump(mode="json"),
                ])
                self.store.add_activity(project_id, "variation.failed", reason, {"variation_id": variation_id, "rollback_verified": True})
                raise ProduceExecutionBlocked(reason, detail=detail, evidence=evidence)

            if not arrangement_step.expected_after.get("arrangement_clip_ids"):
                fail_preview("ARRANGEMENT_READBACK_MISSING")

            try:
                preflight = generic_capture_preflight(
                    preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))
                )
            except Exception as exc:  # noqa: BLE001
                fail_preview("CAPTURE_PREFLIGHT_FAILED", detail=str(exc))
            if not preflight.get("pass"):
                fail_preview("CAPTURE_PREFLIGHT_NOT_READY", detail="; ".join(preflight.get("missing") or []), evidence={"preflight": preflight})
            capture_root = self.data_dir / "variation_captures" / variation_id
            try:
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
            except Exception as exc:  # noqa: BLE001
                fail_preview("PREVIEW_CAPTURE_FAILED", detail=str(exc))
            if not capture.get("ok") or not capture.get("wav_path"):
                fail_preview("PREVIEW_CAPTURE_FAILED", detail=str(capture.get("error") or "capture returned no WAV"), evidence={"capture": capture})
            wav_path = Path(str(capture["wav_path"]))
            if not wav_path.is_file():
                fail_preview("PREVIEW_ARTIFACT_MISSING", detail=str(wav_path), evidence={"capture": capture})
            try:
                measured = _inspect_variation_wav(wav_path)
            except ProduceExecutionBlocked as exc:
                fail_preview(exc.reason, detail=exc.detail)
            captured_ref = capture.get("ref") or {}
            if (
                captured_ref.get("name") != track.name
                or captured_ref.get("project_identity") != live_after.project_identity
                or not any(int(count) > 0 for count in captured_ref.get("note_counts") or [])
            ):
                fail_preview("CAPTURE_SOURCE_MISMATCH", evidence={"capture_ref": captured_ref})
            if capture.get("audio_sha256") != measured["audio_sha256"]:
                fail_preview("PREVIEW_HASH_MISMATCH")
            if capture.get("signal_status") != "HAS_SIGNAL" or measured["signal_status"] != "HAS_SIGNAL":
                fail_preview("SILENT_PREVIEW", evidence={"capture_signal_status": capture.get("signal_status"), "measured": measured})
            expected_duration = length_beats * 60.0 / float(live_after.transport.tempo)
            if abs(measured["duration_s"] - expected_duration) > 0.25:
                fail_preview("PREVIEW_REGION_MISMATCH", evidence={"duration_s": measured["duration_s"], "expected_duration_s": expected_duration})
            preview_job_id = self._create_preview_job(project_id, variation_id)
            artifact_id = f"artifact_{uuid.uuid4().hex[:16]}"
            # Browser-served artifacts must live below StudioStore.artifacts_root.
            # The capture itself remains in the run directory, while the
            # persisted/served copy is kept inside the store's safe artifact
            # boundary.
            relative = Path("artifacts") / "variation_captures" / variation_id / wav_path.name
            destination = self.data_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if wav_path.resolve() != destination.resolve():
                shutil.copy2(wav_path, destination)
            artifact = ArtifactRecord(
                artifact_id=artifact_id,
                job_id=preview_job_id,
                filename=destination.name,
                relative_path=relative.as_posix(),
                sha256=measured["audio_sha256"],
                bytes=destination.stat().st_size,
                duration_s=measured["duration_s"],
                sample_rate=measured["sample_rate"],
                provider_metadata={"kind": "VARIATION_PREVIEW", "capture": capture, "measured_signal": measured, "plan_id": plan.plan_id},
                rights={"source": "GENERATED_IN_ABLETON", "copilot_owned": True},
                created_at=utc_now(),
            )
            self.store.create_artifact(artifact)
            record = VariationRecord(
                variation_id=variation_id,
                project_id=project_id,
                request_id=variation_id,
                index=variation_index,
                status="READY",
                musical_decision=MUSICAL_DECISION_PENDING,
                persistence_status=PERSISTENCE_CANDIDATE_PENDING,
                persistence=candidate_persistence_state(session, live_after),
                ableton_track_ref=track.stable_id,
                ableton_clip_ref=clip_ref,
                preview={"artifact_id": artifact_id, "bars": bars, "duration_s": artifact.duration_s, "capture_region_id": f"region_{variation_id}", "signal_status": measured["signal_status"], "rms_dbfs": measured["rms_dbfs"]},
                plan_id=plan.plan_id,
                ownership={"owner": "COPILOT", "track_stable_id": track.stable_id, "track_name": track.name, "clip_index": int(pattern_step.arguments["clip_index"]), "arrangement_clip_ids": arrangement_step.expected_after["arrangement_clip_ids"]},
                safe_write={"result": result.to_dict(), "journal_path": result.journal_path, "prestate_path": result.prestate_path},
                region={"start_qn": request.start_qn, "end_qn": request.end_qn or request.start_qn + length_beats, "bars": bars, "scope": request.scope},
                reference={
                    "source_reference_id": plan.gate["reference_features"].get("source_reference_id"),
                    "region": plan.gate["reference_features"].get("source_region_qn"),
                    "tempo_bpm": plan.gate["reference_features"].get("tempo_bpm"),
                },
                musical_summary={
                    "candidate_label": f"Candidate {chr(64 + variation_index)}",
                    "preserved": [
                        "evidenced pitch material",
                        "reference tempo and QN grid",
                        "phrase length and source register",
                    ],
                    "changed": [
                        plan.gate["reference_features"].get("variation_strategy", "deterministic reference-bound transformation"),
                    ],
                    "limitations": [
                        "harmony remains evidence-bound and unresolved where the source is ambiguous",
                        "musical quality still requires human listening",
                    ],
                },
                provenance={
                    "source_kind": plan.gate["reference_features"].get("source_kind"),
                    "source_event_count": plan.gate["reference_features"].get("source_event_count"),
                    "generated_event_count": plan.gate["reference_features"].get("generated_event_count"),
                    "variation_index": variation_index,
                    "variation_count": variation_count,
                    "candidate_label": f"Candidate {chr(64 + variation_index)}",
                    "variation_strategy": plan.gate["reference_features"].get("variation_strategy"),
                    "source_not_copied": plan.gate["reference_features"].get("source_not_copied"),
                    "event_traceability": plan.gate["reference_features"].get("event_traceability", []),
                    "symbolic_validation": plan.gate["reference_features"].get("symbolic_validation", {}),
                    "canonical_music_model": plan.gate["reference_features"].get("canonical_music_model", {}),
                    "musical_variation_intent": plan.gate["reference_features"].get("musical_variation_intent", {}),
                    "pack_sha256": plan.gate["reference_features"].get("pack_sha256"),
                    "understanding_sha256": plan.gate["reference_features"].get("understanding_sha256"),
                },
                created_at=utc_now(),
            )
            self.store.set_state(project_id, "variations", [*self.store.get_state(project_id, "variations", []), record.model_dump(mode="json")])
            self.store.set_state(project_id, "project_persistence", record.persistence)
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
        result: dict[str, Any] = {"variations": [dict(v.model_dump(mode="json"), preview_url=v.preview.audio_url if v.preview else None) for v in variations]}
        persistence = self.store.get_state(project_id, "project_persistence", None)
        if persistence is not None:
            result["project_persistence"] = persistence
        return result

    def variation_action(self, variation_id: str, action: str) -> dict[str, Any]:
        if action not in {"keep", "open", "discard", "select"}:
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
        if action == "select":
            if record.status not in {"READY", "KEPT"}:
                raise ValueError("VARIATION_NOT_REVIEWABLE")

            # Selection is a musical decision only.  It deliberately does not
            # call SafeWrite, delete candidates, or attempt Live/disk save.
            # The selected candidate remains in the working copy and is
            # explicitly marked for the human persistence boundary.
            rows: list[VariationRecord] = []
            for item in self.store.get_state(project_id, "variations", []):
                candidate = VariationRecord.model_validate(item)
                if candidate.variation_id != variation_id and candidate.musical_decision == MusicalDecision.ACCEPTED:
                    pending_persistence = dict(candidate.persistence or {})
                    pending_persistence.update({
                        "status": PERSISTENCE_CANDIDATE_PENDING,
                        "musical_decision": MUSICAL_DECISION_PENDING,
                        "save_required": False,
                        "reason": "candidate remains available after selection changed",
                    })
                    candidate = candidate.model_copy(update={
                        "musical_decision": MusicalDecision.PENDING,
                        "persistence_status": PersistenceStatus.CANDIDATE_PENDING,
                        "persistence": pending_persistence,
                    })
                rows.append(candidate)

            persistence = dict(record.persistence or {})
            persistence.update({
                "status": PERSISTENCE_DISK_SAVE_REQUIRED,
                "musical_decision": MUSICAL_DECISION_ACCEPTED,
                "save_required": True,
                "save_attempted": False,
                "save_verified": False,
                "reason": "musical selection accepted; manual Ableton disk save is still required",
                "selected_variation_id": variation_id,
            })
            record = record.model_copy(update={
                "musical_decision": MusicalDecision.ACCEPTED,
                "persistence_status": PersistenceStatus.DISK_SAVE_REQUIRED,
                "persistence": persistence,
            })
            rows = [record if item.variation_id == variation_id else item for item in rows]
            self.store.set_state(project_id, "variations", [item.model_dump(mode="json") for item in rows])
            self.store.set_state(project_id, "project_persistence", persistence)
            self.store.add_activity(
                project_id,
                "variation.musical_accepted",
                "Selected a Copilot variation for musical review; manual disk save remains required",
                {"variation_id": variation_id, "persistence_status": PERSISTENCE_DISK_SAVE_REQUIRED},
            )
            return {
                "variation": dict(record.model_dump(mode="json"), preview_url=record.preview.audio_url if record.preview else None),
                "status": MUSICAL_DECISION_ACCEPTED,
                "persistence_status": PERSISTENCE_DISK_SAVE_REQUIRED,
                "disk_save_required": True,
                "musical_writes": 0,
            }
        if action == "keep":
            if record.status not in {"READY", "KEPT"}:
                raise ValueError("VARIATION_NOT_REVIEWABLE")
            if record.status == "KEPT" and record.musical_decision == MUSICAL_DECISION_KEPT and record.persistence_status == "IN_SYNC":
                return {"variation": dict(record.model_dump(mode="json"), preview_url=record.preview.audio_url if record.preview else None), "status": "KEPT"}
            runtime = self._variation_runtime.get(variation_id)
            if runtime is None:
                raise ProduceExecutionBlocked(
                    "VARIATION_RUNTIME_UNAVAILABLE",
                    detail="KEEP requires the originating Studio process so Core can verify the live identity before saving; no save was attempted.",
                )
            daw, _executor, pair = runtime
            result, _intent = pair
            if not result.ok or result.journal_terminal_state != "VERIFIED":
                raise ProduceExecutionBlocked(
                    "KEEP_SAFE_WRITE_NOT_VERIFIED",
                    detail="Musical KEEP requires a verified SafeWrite transaction before disk persistence.",
                    evidence=result.to_dict(),
                )
            live = daw.snapshot()
            if not live.project_identity:
                attach_tokens(live, path=live.project_path, name=live.project_name)
            expected_identity = str((record.persistence or {}).get("project_identity") or live.project_identity)
            if not expected_identity or live.project_identity != expected_identity:
                raise ProduceExecutionBlocked(
                    "KEEP_PROJECT_MISMATCH",
                    detail="The live project identity no longer matches the candidate identity; no save was attempted.",
                    evidence={
                        "expected_project_identity": expected_identity,
                        "actual_project_identity": live.project_identity,
                    },
                )
            persistence = persist_working_copy(
                daw,
                live,
                expected_project_identity=expected_identity,
            )
            if not persistence.get("save_verified"):
                record = record.model_copy(
                    update={
                        "persistence_status": PersistenceStatus.SAVE_FAILED,
                        "persistence": persistence,
                    }
                )
                rows = [record.model_dump(mode="json") if item.get("variation_id") == variation_id else item for item in self.store.get_state(project_id, "variations", [])]
                self.store.set_state(project_id, "variations", rows)
                self.store.set_state(project_id, "project_persistence", persistence)
                self.store.add_activity(project_id, "variation.keep_blocked", "Musical KEEP was not persisted because the working-copy save boundary is unavailable or failed", {"variation_id": variation_id, "reason": persistence.get("reason")})
                raise ProduceExecutionBlocked(
                    "KEEP_PERSISTENCE_FAILED",
                    detail=str(persistence.get("reason") or "working-copy save was not verified"),
                    evidence=persistence,
                )
            record = record.model_copy(update={
                "status": "KEPT",
                "musical_decision": MusicalDecision.KEPT,
                "persistence_status": PersistenceStatus.IN_SYNC,
                "persistence": persistence,
            })
            rows = [record.model_dump(mode="json") if item.get("variation_id") == variation_id else item for item in self.store.get_state(project_id, "variations", [])]
            self.store.set_state(project_id, "variations", rows)
            self.store.set_state(project_id, "project_persistence", persistence)
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
        try:
            rollback_session = _daw.snapshot()
        except Exception as exc:  # noqa: BLE001
            raise ProduceExecutionBlocked("VARIATION_ROLLBACK_READBACK_FAILED", detail=str(exc)) from exc
        persistence = reconciled_after_rollback(
            rollback_session,
            (record.persistence or {}).get("disk_before"),
        )
        record = record.model_copy(update={
            "status": "DISCARDED",
            "musical_decision": MusicalDecision.DISCARDED,
            "persistence_status": PersistenceStatus(str(persistence.get("status", "UNKNOWN"))),
            "persistence": persistence,
        })
        rows = [record.model_dump(mode="json") if item.get("variation_id") == variation_id else item for item in self.store.get_state(project_id, "variations", [])]
        self.store.set_state(project_id, "variations", rows)
        self.store.set_state(project_id, "project_persistence", persistence)
        self.store.add_activity(project_id, "variation.discarded", "Rolled back Copilot-owned Ableton variation", {"variation_id": variation_id})
        return {"variation": record.model_dump(mode="json"), "status": "DISCARDED", "rollback_verified": True}

    def reject_all_variations(self, project_id: str) -> dict[str, Any]:
        """Rollback every reviewable Copilot candidate in this project.

        This is a thin product action over the existing per-candidate rollback
        authority; it never deletes arbitrary Live material and never bypasses
        SafeWrite journals.
        """
        self.get_project(project_id)
        candidates = [
            VariationRecord.model_validate(item)
            for item in self.store.get_state(project_id, "variations", [])
            if item.get("status") in {"READY", "KEPT"}
        ]
        results = [self.variation_action(item.variation_id, "discard") for item in candidates]
        return {
            "status": "DISCARDED",
            "variations": results,
            "musical_writes": 0,
        }

    def ableton_status(self) -> dict[str, Any]:
        from copilot.daw.detect import detect_ableton
        from copilot.daw.session_ready_v1 import probe_session_ready
        try:
            detection = detect_ableton().to_dict()
            probe = probe_session_ready()
            return {"detection": detection, "session": probe.to_dict(), "musical_writes": 0}
        except Exception as exc:
            return {"status": "ENVIRONMENT_STATUS_UNAVAILABLE", "reason": str(exc), "musical_writes": 0}

    def drum_workbench(self) -> dict[str, Any]:
        """Expose hash-verified drum evidence plus the separate local human review."""
        return load_drum_workbench(review_overlay_path=self._drum_review_path())

    def _drum_review_path(self) -> Path:
        return self.data_dir / "drum_reviews" / "current.json"

    def correct_drum_event_role(self, event_id: str, role: str, note: str | None = None) -> dict[str, Any]:
        """Persist a user-entered label correction, not an analyzer or DAW write."""
        allowed_roles = {"KICK", "SNARE", "CLAP", "CLOSED_HAT", "OPEN_HAT", "PERCUSSION", "OTHER", "UNKNOWN"}
        normalized_role = str(role).strip().upper()
        if normalized_role not in allowed_roles:
            raise ValueError("DRUM_REVIEW_ROLE_UNSUPPORTED")
        clean_note = str(note or "").strip()
        if len(clean_note) > 500:
            raise ValueError("DRUM_REVIEW_NOTE_TOO_LONG")
        view = self.drum_workbench()
        if view.get("status") != "READY":
            raise ValueError(f"DRUM_REVIEW_SOURCE_NOT_READY:{view.get('status')}")
        event = next((item for item in view["events"] if item["event_id"] == event_id), None)
        if event is None:
            raise KeyError(event_id)

        review_path = self._drum_review_path()
        if review_path.is_file():
            payload = json.loads(review_path.read_text(encoding="utf-8"))
            if (
                payload.get("source_asset_id") != view["source"]["asset_id"]
                or payload.get("source_sha256", "").lower() != view["source"]["sha256"].lower()
                or payload.get("schema_version") != "drum-human-review-v1"
            ):
                raise ValueError("DRUM_REVIEW_SOURCE_MISMATCH")
        else:
            payload = {
                "schema_version": "drum-human-review-v1",
                "source_asset_id": view["source"]["asset_id"],
                "source_sha256": view["source"]["sha256"],
                "corrections": [],
            }
        corrections = payload["corrections"]
        previous = next((row for row in reversed(corrections) if row.get("event_id") == event_id), None)
        if previous and previous.get("role") == normalized_role and previous.get("note") == (clean_note or None):
            return view
        corrections.append({
            "event_id": event_id,
            "role": normalized_role,
            "reviewer_id": "STUDIO_LOCAL_HUMAN",
            "note": clean_note or None,
            "recorded_at_utc": utc_now(),
        })
        review_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".drum-review-", suffix=".tmp", dir=review_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, review_path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return self.drum_workbench()

    def drum_event_preview(self, event_id: str) -> tuple[bytes, int]:
        """Read a bounded audition window from the manifest-verified source."""
        return read_drum_event_preview(event_id)

    def drum_sample_preview(self, asset_id: str) -> tuple[bytes, int]:
        """Read a bounded sample audition resolved from the trusted index."""
        return read_drum_sample_preview(asset_id)

    def open_transport_event_client(self) -> tuple[AbletonTcpAdapter, AbletonTransportEventClient, float]:
        """Open a push-only transport subscription after readiness and identity checks."""
        started = time.perf_counter()
        probe = probe_session_ready()
        if probe.status != SESSION_READY:
            raise StudioTransportUnavailable(probe.status, probe.reason)
        daw = AbletonTcpAdapter()
        try:
            daw.connect()
            if TRANSPORT_EVENTS_CAPABILITY not in daw.capabilities:
                raise StudioTransportUnavailable("UNSUPPORTED", TRANSPORT_EVENTS_CAPABILITY)
            info = daw.get_session_info()
            path_info = daw.get_session_path()
            path = str((path_info or {}).get("path") or "").strip()
            name = str((path_info or {}).get("name") or (info or {}).get("name") or "").strip()
            session = SessionState(daw="ableton", connected=True)
            attach_tokens(session, path=path or None, name=name or None)
            identity = session.project_identity or session.project_token or ""
            if not identity or identity != probe.project_identity:
                raise StudioTransportUnavailable("PROJECT_MISMATCH", "Live project identity changed during subscription setup.")
            client = AbletonTransportEventClient(daw)
            client.connect()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return daw, client, elapsed_ms
        except Exception:
            daw.disconnect()
            raise
