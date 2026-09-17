"""Enrich a persisted external pack with Main-local event descriptors. No Live."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import soundfile as sf

from copilot.audio.astra_external_reasoning_v1 import (
    CANONICAL_PACK_PATH,
    RecordingProvider,
    classify_rejection,
    load_persisted_pack,
    pack_payload_hash,
)
from copilot.audio.file_hash import sha256_file
from copilot.audio.fullmix import FORBIDDEN_DIAGNOSIS_RE, compute_fullmix_observation
from copilot.audio.producer_analyze_v1 import apply_reasoning_result
from copilot.human_eval.store import now_iso
from copilot.reasoning.from_fullmix import ANALYZER_ID, fullmix_items
from copilot.reasoning.pipeline import _parse_output, reason
from copilot.reasoning.provider import ReasoningProvider, configured_http_provider
from copilot.reasoning.session_astra import ASTRA_TIMEOUT_S
from copilot.schemas.diagnosis import DiagnosisStatus
from copilot.schemas.evidence import EvidencePack, ObservationLimitation
from copilot.schemas.fullmix import FullMixObservation

MILESTONE = "EXTERNAL_EVIDENCE_ENRICHMENT_V1"
ARTIFACT = "external_evidence_enrichment_v1.json"
STATUS = "IMPLEMENTED"
EXPECTED_PARENT_PACK_ID = "pack_afe4d6403ee4"
PARENT_PACK_PATH = CANONICAL_PACK_PATH

# Astra asked for event class, position, duration, energy depth, similarity/period.
# Do not re-emit event_count (already on ev.fullmix) or unrelated stereo/spectral families.
SELECTED_EVENT_NAMES = frozenset(
    {
        "fullmix_window_ms",
        "fullmix_hop_ms",
        "fullmix_strong_dip_threshold_db",
        "fullmix_energy_event_kind",
        "fullmix_energy_event_start_s",
        "fullmix_energy_event_end_s",
        "fullmix_energy_event_duration_ms",
        "fullmix_energy_event_min_rms",
        "fullmix_energy_event_reference_rms",
        "fullmix_energy_event_relative_drop_db",
        "fullmix_energy_event_similarity_count",
        "fullmix_energy_event_repetition_strength",
        "fullmix_energy_event_approx_period_s",
        "fullmix_energy_event_approx_period_ms",
    }
)
REDUNDANT_NAMES = frozenset(
    {
        "fullmix_energy_event_count",
        "fullmix_energy_frame_count",
    }
)
AVAILABLE_IN_DSP_NOT_SELECTED = frozenset(
    {
        "fullmix_transient_count",
        "fullmix_transient_density_per_s",
        "fullmix_transient_precision_ms",
        "fullmix_transient_median_ioi_ms",
        "fullmix_spectral_event_count",
        "fullmix_spectral_strongest_change_db",
        "fullmix_stereo_median_correlation",
        "fullmix_stereo_median_side_mid_ratio",
        "fullmix_median_crest_factor",
        "fullmix_median_short_term_dynamic_range_db",
    }
)


def locate_main_wav(pack: EvidencePack, *, repo: Path | None = None) -> dict[str, Any]:
    main = next((item for item in pack.items if item.evidence_id == "ev.main.capture"), None)
    if main is None:
        return {"ok": False, "error": "MAIN_EVIDENCE_MISSING"}
    value = main.value if isinstance(main.value, dict) else {}
    raw_path = Path(str(value.get("path") or ""))
    repo = repo or Path.cwd()
    candidates = [raw_path]
    if not raw_path.is_absolute():
        candidates.append(repo / raw_path)
    wav_path = next((path for path in candidates if path.is_file()), None)
    if wav_path is None:
        return {
            "ok": False,
            "error": "MAIN_WAV_MISSING",
            "declared_path": str(raw_path),
        }
    digest = sha256_file(wav_path) or ""
    declared = str(value.get("audio_sha256") or "")
    info = sf.info(str(wav_path))
    return {
        "ok": digest == declared and bool(digest),
        "path": str(wav_path.resolve()),
        "declared_path": str(raw_path),
        "audio_sha256": digest,
        "declared_audio_sha256": declared,
        "hash_match": digest == declared,
        "duration_s": float(info.duration),
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
        "frames": int(info.frames),
        "signal_class": value.get("signal_class"),
        "capture_ok": value.get("ok"),
        "quality": main.quality.value if hasattr(main.quality, "value") else str(main.quality),
        "evidence_id": main.evidence_id,
        "source_ref": main.source_ref,
        "region": main.region,
        "view": main.view,
    }


def descriptor_audit(pack: EvidencePack) -> dict[str, Any]:
    present_names = {item.name for item in pack.items}
    present_ids = {item.evidence_id for item in pack.items}
    available = []
    if "fullmix_observation" in present_names or "ev.fullmix" in present_ids:
        available.append("ev.fullmix.event_count (truncated aggregate)")
    missing = sorted(SELECTED_EVENT_NAMES)
    redundant = sorted(name for name in REDUNDANT_NAMES)
    return {
        "AVAILABLE": available,
        "MISSING": missing,
        "REDUNDANT": redundant,
        "AVAILABLE_IN_DSP_NOT_SELECTED": sorted(AVAILABLE_IN_DSP_NOT_SELECTED),
        "gap": (
            "Astra needs per-event kind, times, duration, energy depth, and "
            "similarity/period. Those exist in frozen fullmix-obs-1 but were "
            "not copied into the producer pack."
        ),
    }


def select_event_items(obs: FullMixObservation, pack: EvidencePack) -> list[Any]:
    items = fullmix_items(
        obs,
        project_token=pack.project_token,
        audible_token=pack.audible_token,
        target_token=pack.target_token,
    )
    return [item for item in items if item.name in SELECTED_EVENT_NAMES]


def _assert_factual(obs: FullMixObservation) -> None:
    blob = json.dumps(obs.model_dump(mode="json"), ensure_ascii=False)
    if FORBIDDEN_DIAGNOSIS_RE.search(blob):
        raise ValueError("fullmix observation contains diagnosis language")


def enrich_pack(
    *,
    parent_path: Path | None = None,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Derive a new pack. Never rewrite the parent artifact."""
    parent_path = Path(parent_path or PARENT_PACK_PATH)
    parent_bytes = parent_path.read_bytes()
    parent_file_sha = hashlib.sha256(parent_bytes).hexdigest()
    parent = load_persisted_pack(parent_path)
    parent_payload = pack_payload_hash(parent)
    wav = locate_main_wav(parent, repo=repo)
    audit = descriptor_audit(parent)
    report: dict[str, Any] = {
        "milestone": MILESTONE,
        "ts": now_iso(),
        "parent_pack_id": parent.pack_id,
        "parent_payload_sha256": parent_payload,
        "parent_file_sha256": parent_file_sha,
        "parent_path": str(parent_path),
        "wav": wav,
        "descriptor_audit": audit,
        "NO LIVE": True,
        "NO RECAPTURE": True,
        "MUSICAL WRITES": 0,
    }
    if not wav.get("ok"):
        report["status"] = "BLOCKED"
        report[MILESTONE] = "BLOCKED"
        report["BLOCKER"] = wav.get("error") or "MAIN_WAV_PROVENANCE"
        return report
    obs = compute_fullmix_observation(
        wav["path"],
        region_id=str(parent.region).split(":")[0],
        region_label=parent.region,
        audio_sha256=str(wav["audio_sha256"]),
    )
    _assert_factual(obs)
    added = select_event_items(obs, parent)
    existing_ids = {item.evidence_id for item in parent.items}
    added = [item for item in added if item.evidence_id not in existing_ids]
    limitations = list(parent.limitations)
    limitations.append(
        ObservationLimitation(
            code="MAIN_LOCAL_TIMING_NOT_CROSS_SOURCE",
            detail=(
                "Main-local event times are not cross-source alignment. "
                "Cross-source remains LIMITED ±52 ms."
            ),
            precision_ms=float(parent.alignment_envelope_ms),
            capability_ms=[float(parent.alignment_envelope_ms)],
        )
    )
    limitations.append(
        ObservationLimitation(
            code="FULLMIX_ANALYZER",
            detail=(
                f"{ANALYZER_ID} sha={obs.analyzer_sha256[:12]} "
                f"config={obs.configuration_hash[:12]}"
            ),
            capability_ms=[float(obs.window_s * 1000.0), float(obs.hop_s * 1000.0)],
        )
    )
    enriched = parent.model_copy(
        update={
            "pack_id": f"pack_{uuid4().hex[:12]}",
            "items": list(parent.items) + added,
            "limitations": limitations,
        }
    )
    after_parent = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    report.update(
        {
            "status": "VERIFIED",
            MILESTONE: "PACK_READY",
            "new_pack_id": enriched.pack_id,
            "new_payload_sha256": pack_payload_hash(enriched),
            "added_observations": [
                {
                    "evidence_id": item.evidence_id,
                    "name": item.name,
                    "kind": item.kind.value,
                    "unit": item.unit,
                    "source_ref": item.source_ref,
                }
                for item in added
            ],
            "added_count": len(added),
            "dsp": {
                "analyzer_id": obs.analyzer_id,
                "analyzer_sha256": obs.analyzer_sha256,
                "configuration_hash": obs.configuration_hash,
                "energy_event_count": len(obs.energy_events),
                "cache_hit": obs.cache_hit,
            },
            "source_wav_identity": {
                "path": wav["path"],
                "audio_sha256": wav["audio_sha256"],
            },
            "alignment_claim": enriched.alignment_claim,
            "alignment_envelope_ms": enriched.alignment_envelope_ms,
            "parent_file_unchanged": after_parent == parent_file_sha,
            "pack": enriched.model_dump(mode="json"),
        }
    )
    return report


def replay_enriched(
    *,
    evidence: Path | None = None,
    parent_path: Path | None = None,
    provider: ReasoningProvider | None = None,
    timeout_s: float = ASTRA_TIMEOUT_S,
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    built = enrich_pack(parent_path=parent_path)
    if built.get("status") == "BLOCKED":
        _persist(built, evidence)
        return built
    pack = EvidencePack.model_validate(built["pack"])
    http = provider if provider is not None else configured_http_provider()
    if http is None:
        built["status"] = "BLOCKED"
        built[MILESTONE] = "BLOCKED"
        built["BLOCKER"] = "ASTRA_NOT_CONFIGURED"
        _persist(built, evidence)
        return built
    recorder = RecordingProvider(http)
    result = reason(pack, recorder, timeout_s=timeout_s)
    raw = recorder.raws[-1] if recorder.raws else ""
    parsed, parse_error = _parse_output(raw) if raw else (result.output, None)
    applied = apply_reasoning_result(result)
    classification = classify_rejection(
        accepted=result.accepted,
        failure=None if result.failure is None else result.failure.value,
        parse_error=parse_error,
        issues=list(result.audit.issues),
        output=result.output,
    )
    requested = (
        [item.model_dump(mode="json") for item in result.output.requested_evidence]
        if result.output is not None
        else []
    )
    astra_status = (
        result.output.status.value
        if result.accepted and result.output is not None
        else applied["status"]
    )
    next_request = _next_evidence_request(result.accepted, astra_status, requested)
    built.update(
        {
            "accepted": result.accepted,
            "ASTRA RESULT": astra_status,
            "parsed_result": None if parsed is None else parsed.model_dump(mode="json"),
            "parse_error": parse_error,
            "validator_result": {
                "accepted": result.accepted,
                "failure": None if result.failure is None else result.failure.value,
                "issues": result.audit.issues,
            },
            "rejection": classification,
            "gate": applied["gate"],
            "diagnosis": applied["diagnosis"],
            "reasoning_audit": applied["reasoning_audit"],
            "requested_evidence": requested,
            "NEXT_EVIDENCE_REQUEST": next_request,
            "comparison": _compare_to_original_replay(evidence, astra_status, result.accepted, requested),
        }
    )
    if result.accepted:
        built["status"] = "VERIFIED"
        built[MILESTONE] = "VERIFIED"
    else:
        built["status"] = "BLOCKED"
        built[MILESTONE] = "BLOCKED"
        built["BLOCKER"] = classification.get("detail") or classification.get("label")
    _persist(built, evidence)
    return built


def _next_evidence_request(
    accepted: bool,
    status: str,
    requested: list[dict[str, Any]],
) -> str | None:
    if not accepted or status != DiagnosisStatus.INSUFFICIENT_EVIDENCE.value:
        return None
    kinds = [str(item.get("request_kind") or "") for item in requested]
    if "READ_MIDI" in kinds:
        return "MIDI_READ_ONLY"
    if requested:
        return json.dumps(requested, ensure_ascii=False)
    return None


def _compare_to_original_replay(
    evidence: Path,
    new_status: str,
    new_accepted: bool,
    new_requested: list[dict[str, Any]],
) -> dict[str, Any]:
    previous_path = evidence / "astra_external_reasoning_v1.json"
    if not previous_path.is_file():
        return {"previous": None}
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    prev_req = previous.get("requested_evidence") or []
    return {
        "original_status": previous.get("ASTRA RESULT") or previous.get("status"),
        "original_accepted": previous.get("accepted"),
        "original_requested_kinds": [
            item.get("request_kind") for item in prev_req if isinstance(item, dict)
        ],
        "enriched_status": new_status,
        "enriched_accepted": new_accepted,
        "enriched_requested_kinds": [
            item.get("request_kind") for item in new_requested if isinstance(item, dict)
        ],
        "status_changed": (previous.get("ASTRA RESULT") or previous.get("status")) != new_status,
    }


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    report["artifact"] = str(path)
    return path


def main() -> int:
    report = replay_enriched()
    summary = {
        "milestone": MILESTONE,
        "parent_pack_id": report.get("parent_pack_id"),
        "new_pack_id": report.get("new_pack_id"),
        "added_count": report.get("added_count"),
        "accepted": report.get("accepted"),
        "ASTRA RESULT": report.get("ASTRA RESULT"),
        "NEXT_EVIDENCE_REQUEST": report.get("NEXT_EVIDENCE_REQUEST"),
        MILESTONE: report.get(MILESTONE),
        "parent_file_unchanged": report.get("parent_file_unchanged"),
        "MUSICAL WRITES": 0,
        "NO LIVE": True,
        "NO RECAPTURE": True,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get(MILESTONE) == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
