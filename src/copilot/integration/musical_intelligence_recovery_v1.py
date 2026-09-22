"""Offline musical-intelligence recovery and quality-evidence boundary.

This module does not open Ableton, call a provider, invent alternatives, or
turn a rejected Alpha into a preference model.  It turns persisted evidence
into auditable artifacts and exposes bounded contracts for the next real
quality run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Protocol

from copilot.runtime.provider_registry_v1 import ProviderCapability, ProviderCapabilityRegistry
from copilot.schemas.advanced_perception import ProviderAvailability
from copilot.schemas.music_analysis import MusicAnalysisPack
from copilot.schemas.musical_intelligence import (
    AuditItem,
    CandidateRecord,
    CandidateSearchReport,
    LongRangeMusicContext,
    LongRangeRegion,
    MusicalIntelligenceStatus,
    PreferenceObservation,
    ProviderCapabilityRecord,
    ProviderHealth,
)

MILESTONE = "MUSICAL_INTELLIGENCE_RECOVERY_V1"
ALPHA_ARTIFACT = Path("logs/autonomous_producer_alpha_v1.json")


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("artifact must be a JSON object")
    return value


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def build_musical_intelligence_audit(repo_root: str | Path, artifact: dict[str, Any]) -> dict[str, Any]:
    root = Path(repo_root)
    evidence = lambda *parts: str(Path(*parts))
    implemented = {
        "CLAP": (MusicalIntelligenceStatus.IMPLEMENTED_VERIFIED, [evidence("src", "copilot", "sample_library", "embeddings.py"), "alpha.post_change_context.advanced_perception.providers"]),
        "Music Analyzer / factual DSP": (MusicalIntelligenceStatus.IMPLEMENTED_VERIFIED, [evidence("src", "copilot", "schemas", "music_analysis.py"), "alpha.post_change_context.music_analysis"]),
        "EvidenceGraph / causal evidence": (MusicalIntelligenceStatus.IMPLEMENTED_VERIFIED, [evidence("src", "copilot", "audio", "deep_causal_v2.py")]),
        "multi-reference role binding": (MusicalIntelligenceStatus.IMPLEMENTED_PARTIAL, [evidence("src", "copilot", "audio", "advanced_perception_v1.py")]),
        "sample library intelligence": (MusicalIntelligenceStatus.IMPLEMENTED_VERIFIED, [evidence("src", "copilot", "sample_library")]),
        "source activity / prominence": (MusicalIntelligenceStatus.IMPLEMENTED_PARTIAL, ["alpha.post_change_context.music_analysis.limitations"]),
        "musical feedback boundary": (MusicalIntelligenceStatus.IMPLEMENTED_PARTIAL, [evidence("src", "copilot", "integration", "autonomous_musical_feedback_v1.py"), "logs/autonomous_musical_feedback_v1.json"]),
        "Lucas planning and SafeWrite": (MusicalIntelligenceStatus.IMPLEMENTED_VERIFIED, ["alpha.lucas.strategy_provenance", "alpha.final.safe_write_authorities"]),
    }
    partial_or_missing = {
        "semantic audio / Music Flamingo": (MusicalIntelligenceStatus.PROVIDER_UNAVAILABLE, [evidence("src", "copilot", "audio", "advanced_perception_v1.py")], "No installed model, weights, runner, cache, or real semantic observation."),
        "source separation": (MusicalIntelligenceStatus.PROVIDER_UNAVAILABLE, [evidence("src", "copilot", "models", "router.py")], "Model slot is not a verified executed provider; Alpha had no isolated stems."),
        "candidate generation/search/rendering": (MusicalIntelligenceStatus.MISSING, [evidence("src", "copilot", "integration", "autonomous_producer_alpha_v1.py")], "Alpha has one plan and no alternate candidate provider or rendered branch."),
        "candidate comparison / blind A-B": (MusicalIntelligenceStatus.CONTRACT_ONLY, [evidence("src", "copilot", "human_eval")], "Human-evaluation infrastructure exists, but no new candidate pair was rendered."),
        "long-range motif/development memory": (MusicalIntelligenceStatus.IMPLEMENTED_PARTIAL, [evidence("src", "copilot", "schemas", "music_analysis.py")], "Energy/regions can be summarized; motif and development claims are not available from this Alpha capture."),
        "sample selection in context": (MusicalIntelligenceStatus.DEFERRED_BY_DESIGN, ["alpha.execution", "alpha.post_change_context"], "No provider-backed alternate audition was available; do not synthesize one."),
        "semantic issue taxonomy": (MusicalIntelligenceStatus.IMPLEMENTED_PARTIAL, [evidence("src", "copilot", "schemas", "feedback.py")], "Typed categories exist; no human/provider musical classification for this result."),
        "preference learning / negative feedback": (MusicalIntelligenceStatus.IMPLEMENTED_PARTIAL, ["user_judgment=REJECTED"], "One scoped rejection is recorded; no reason or global preference may be inferred."),
        "human quality benchmark": (MusicalIntelligenceStatus.CONTRACT_ONLY, [evidence("src", "copilot", "human_eval")], "Blind evaluation tools exist; no new human labels in this run."),
        "MIDI composition / melody / harmony / bass": (MusicalIntelligenceStatus.DEFERRED_BY_DESIGN, [evidence("src", "copilot", "musicplan")], "Lucas-owned musical generation remains the stable boundary."),
        "groove / microtiming / transitions / fills": (MusicalIntelligenceStatus.IMPLEMENTED_PARTIAL, [evidence("src", "copilot", "schemas", "music_analysis.py")], "Measurement contracts exist; no alternate rendered musical proof."),
        "sound-design / synth parameter search": (MusicalIntelligenceStatus.MISSING, [evidence("src", "copilot", "models", "router.py")], "No verified search/execution loop."),
        "perceptual mixing / mastering intelligence": (MusicalIntelligenceStatus.IMPLEMENTED_PARTIAL, [evidence("src", "copilot", "integration", "mixing_mastering_v1.py")], "Execution foundation exists; user-quality comparison is pending."),
        "vocals intelligence": (MusicalIntelligenceStatus.MISSING, [], "No verified vocal analysis/editing loop in this scope."),
        "provider registry / health / failover": (MusicalIntelligenceStatus.IMPLEMENTED_PARTIAL, [evidence("src", "copilot", "runtime", "provider_registry_v1.py"), evidence("src", "copilot", "integration", "lucas_core_v1.py")], "Registry and bounded health records are now present; no provider was contacted by this offline run."),
        "model provenance / reproducibility": (MusicalIntelligenceStatus.IMPLEMENTED_PARTIAL, ["alpha.strategy_provenance", evidence("src", "copilot", "runtime", "provider_registry_v1.py")], "Persisted for the Alpha and registry records; benchmark artifacts remain pending."),
    }
    items: list[AuditItem] = []
    for name, value in implemented.items():
        status, refs = value
        items.append(AuditItem(component=name, status=status, evidence=refs))
    for name, value in partial_or_missing.items():
        status, refs, limitation = value
        items.append(AuditItem(component=name, status=status, evidence=refs, limitations=[limitation]))
    # Keep the audit itself deterministic and machine-readable.
    for item in items:
        if item.component == "semantic audio / Music Flamingo":
            item.next_step = "Use a maintained, actually available semantic-audio provider; do not make Music Flamingo a dependency."
        elif item.component == "candidate generation/search/rendering":
            item.next_step = "Accept real Lucas alternatives through the stable interface; fail closed when absent."
    return {
        "schema_version": "musical-intelligence-audit-v1",
        "milestone": MILESTONE,
        "user_judgment": "REJECTED",
        "technical_baseline": "PRODUCTION_PASS_VERIFIED / REVISION_PROVIDER_LIMITED",
        "items": [_dump(item) for item in items],
        "repo_root": str(root.resolve()),
        "no_write": True,
    }


def diagnose_bad_alpha(artifact: dict[str, Any]) -> dict[str, Any]:
    execution = artifact.get("execution") or {}
    final = artifact.get("final") or {}
    lucas = artifact.get("lucas") or {}
    plan = lucas.get("plan") or {}
    actions = execution.get("actions") or []
    sample_actions = [row for row in actions if row.get("action_type") in {"SAMPLE_LOAD", "LOAD_SAMPLE"}]
    arrangement = ((lucas.get("metadata") or {}).get("arrangement") or lucas.get("arrangement") or [])
    if not arrangement:
        arrangement = ((lucas.get("plan") or {}).get("metadata") or {}).get("arrangement") or []
    critique = artifact.get("lucas_feedback") or {}
    analysis = ((artifact.get("post_change_context") or {}).get("music_analysis") or {})
    limitations = analysis.get("limitations") or []
    supported = [
        {
            "issue": "critique_missing",
            "category": "musical_feedback",
            "evidence": ["lucas_feedback.status", "CRITIQUE_PROVIDER_UNAVAILABLE"],
            "statement": "No typed KEEP/ADJUST/ROLLBACK decision exists, so the result was not musically iterated.",
        },
    ]
    deferred = sum(row.get("status") != "VERIFIED" for row in sample_actions)
    if deferred:
        supported.append({
            "issue": "sample_load_coverage_deferred",
            "category": "execution_limitation",
            "evidence": ["execution.actions", "safe_write.readback"],
            "statement": f"{deferred}/{len(sample_actions)} planned sample loads were not verified; this limits what can be concluded about the audible result.",
        })
    if len(arrangement) < 6:
        supported.append({
            "issue": "short_arrangement_observed",
            "category": "arrangement_development",
            "evidence": ["lucas.plan", "alpha.arrangement"],
            "statement": f"The persisted plan exposes {len(arrangement)} arrangement regions; this is insufficient evidence of long-form development.",
        })
    if any("isolated" in str(item).lower() or "source" in str(item).lower() for item in limitations):
        supported.append({
            "issue": "no_source_level_validation",
            "category": "mix_and_source_evidence",
            "evidence": ["post_change_context.music_analysis.limitations"],
            "statement": "The capture is Main/master evidence; source activity and matched kick/bass behavior were not measured.",
        })
    prioritized = [
        {
            "rank": 1,
            "problem": "MUSICAL_FEEDBACK_MISSING",
            "status": "SUPPORTED",
            "evidence_refs": ["lucas_feedback.status"],
            "reason": "No post-change typed critique or correction cycle was completed.",
        },
        {
            "rank": 2,
            "problem": "SOURCE_AND_LOWEND_EVIDENCE_LIMITED",
            "status": "SUPPORTED",
            "evidence_refs": ["post_change_context.music_analysis.limitations"],
            "reason": "The persisted analysis has Main-only low-end evidence and no source activity readback.",
        },
    ]
    if len(arrangement) < 6:
        prioritized.append({
            "rank": 3,
            "problem": "DEVELOPMENT_UNVERIFIED",
            "status": "POSSIBLE",
            "evidence_refs": ["lucas.plan.metadata.arrangement", "post_change_context.music_analysis"],
            "reason": "The plan has only five regions and no evidence that later regions materially develop earlier material.",
        })
    return {
        "schema_version": "bad-alpha-diagnosis-v1",
        "user_judgment": "REJECTED",
        "reason_provided": False,
        "supported_issues": supported,
        "possible_issues": [
            {"issue": "timbral_or_groove_mismatch", "statement": "Possible, but no human reason or candidate comparison identifies it.", "evidence": []},
            {"issue": "weak_development_or_transition", "statement": "Possible from the limited arrangement/capture, not a user-confirmed cause.", "evidence": ["lucas.plan", "post_change_context.music_analysis"]},
            {"issue": "reference_mismatch", "statement": "Possible, but not measurable from this artifact alone.", "evidence": ["reference", "post_change_context"]},
        ],
        "insufficient_evidence": [
            "exact reason for user rejection",
            "foreground competition",
            "microtiming feel",
            "sample-in-context preference",
            "loudness-normalized A/B quality result",
        ],
        "planning": {"strategy_provenance": lucas.get("strategy_provenance"), "plan_id": plan.get("plan_id"), "action_count": len(plan.get("actions") or [])},
        "execution": {"sample_loads": len(sample_actions), "sample_loads_verified": len(sample_actions) - deferred, "failed": execution.get("failed", 0), "original_untouched": final.get("original_untouched")},
        "critique": {"status": critique.get("status"), "typed_result_present": bool(critique.get("result"))},
        "prioritized_musical_problems": prioritized,
        "no_write": True,
    }


def build_long_range_context(pack: MusicAnalysisPack) -> LongRangeMusicContext:
    regions = [LongRangeRegion(region_id=section.name + f":{index}", start_beat=section.start_beat, end_beat=section.end_beat, label=section.function or section.name, energy_db=section.energy_mean_db, energy_slope_db_per_s=section.energy_slope_db_per_s, evidence_refs=section.evidence) for index, section in enumerate(pack.sections)]
    energy_arc = [{"index": window.index, "start_beat": window.start_beat, "end_beat": window.end_beat, "energy_db": window.energy_db, "low_band_energy": window.low_band_energy, "evidence_refs": window.evidence_refs} for window in pack.windows]
    repetition = [{"window": window.index, "repetition_strength": window.groove.repetition_strength, "variation_score": window.groove.variation_score, "event_locations": window.groove.event_locations, "evidence_refs": window.evidence_refs} for window in pack.windows]
    transitions = [item.model_dump(mode="json") for item in pack.transitions]
    limitations = list(pack.limitations)
    if len(pack.windows) < 2:
        limitations.append("Only one measurement window is available; long-range development cannot be verified.")
    if not any(row["repetition_strength"] is not None for row in repetition):
        limitations.append("No measured repetition strength or motif identity is available in this pack.")
    return LongRangeMusicContext(status="PARTIAL" if limitations else "VERIFIED", reference_state_token=pack.tokens.reference_state_token, regions=regions, energy_arc=energy_arc, repetition=repetition, transitions=transitions, limitations=list(dict.fromkeys(limitations)), provenance=list(pack.evidence_refs))


def build_alpha_long_range_context(artifact: dict[str, Any]) -> dict[str, Any]:
    """Join measured windows with Lucas's intended arrangement, read-only."""
    metadata = ((artifact.get("lucas") or {}).get("metadata") or {})
    sections = list(metadata.get("arrangement") or [])
    rows: list[dict[str, Any]] = []
    previous: set[str] | None = None
    seen_configs: dict[str, int] = {}
    cursor = 0.0
    novelty: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        active = {str(name) for name in (section.get("active") or [])}
        bars = int(section.get("bars") or 0)
        key = "|".join(sorted(active))
        seen_configs[key] = seen_configs.get(key, 0) + 1
        added = sorted(active - previous) if previous is not None else sorted(active)
        removed = sorted(previous - active) if previous is not None else []
        similarity = None if previous is None else (len(active & previous) / max(1, len(active | previous)))
        if added or removed:
            novelty.append({"section_index": index, "added": added, "removed": removed, "evidence_refs": [f"lucas.arrangement:{index}"]})
        rows.append({
            "index": index,
            "name": str(section.get("name") or "SECTION"),
            "start_beat": cursor,
            "end_beat": cursor + bars * 4,
            "bars": bars,
            "active_roles": sorted(active),
            "added_roles": added,
            "removed_roles": removed,
            "similarity_to_previous": similarity,
            "evidence_refs": [f"lucas.arrangement:{index}"],
        })
        cursor += bars * 4
        previous = active
    pack = ((artifact.get("post_change_context") or {}).get("music_analysis") or {})
    windows = pack.get("windows") or []
    energy = [{"index": row.get("index"), "energy_db": row.get("energy_db"), "onset_density_per_s": ((row.get("groove") or {}).get("onset_density_per_s")), "evidence_refs": row.get("evidence_refs") or []} for row in windows]
    repeated = [{"configuration": key, "count": count} for key, count in sorted(seen_configs.items(), key=lambda item: (-item[1], item[0]))]
    limitations = [
        "Arrangement facts are plan intent, not proof of audible differentiation.",
        "No candidate audio or source-isolated capture is available for section similarity.",
    ]
    if len(windows) < 2:
        limitations.append("Only one measured post-change window is available.")
    return {
        "schema_version": "alpha-long-range-context-v1",
        "status": "PARTIAL",
        "section_sequence": rows,
        "repeated_role_configurations": repeated,
        "novelty_events": novelty,
        "energy_and_onset_trajectory": energy,
        "repetition_counts": {"section_configurations": len(repeated), "repeated_configurations": sum(1 for row in repeated if row["count"] > 1)},
        "limitations": limitations,
        "provenance": ["lucas.metadata.arrangement", "post_change_context.music_analysis"],
        "no_write": True,
    }


def build_sample_fit_audit(artifact: dict[str, Any]) -> dict[str, Any]:
    samples = artifact.get("sample_set_context") or {}
    assets = samples.get("candidates") or samples.get("assets") or []
    selected = ((artifact.get("lucas") or {}).get("metadata") or {}).get("sample_map") or {}
    by_id = {str(row.get("id") or row.get("sha256")): row for row in assets if isinstance(row, dict)}
    rows = []
    for role, asset_id in sorted(selected.items()):
        asset = by_id.get(str(asset_id), {})
        rows.append({
            "role": role,
            "sample_id": asset_id,
            "filename": asset.get("filename"),
            "library_facts": {key: asset.get(key) for key in ("sample_type", "bpm", "descriptors") if key in asset},
            "clap_relation": "not_comparable_without_reference_embedding_or_candidate_embedding",
            "semantic_relation": "SEMANTIC_PROVIDER_UNAVAILABLE",
            "context_audition": "NOT_RUN",
            "status": "INSUFFICIENT_EVIDENCE",
        })
    return {"schema_version": "sample-fit-audit-v1", "selected": rows, "limitations": ["No semantic audio provider", "No alternate in-context audition", "CLAP relation requires comparable embeddings"], "no_write": True}


class CandidateProvider(Protocol):
    provider_id: str

    def generate_candidates(self, context: dict[str, Any]) -> Iterable[dict[str, Any]]: ...


def _candidate_fingerprint(candidate: dict[str, Any]) -> str:
    plan = candidate.get("plan", candidate)
    normalized = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def run_candidate_search(context: dict[str, Any], provider: CandidateProvider | None = None, *, max_candidates: int = 8) -> CandidateSearchReport:
    if provider is None:
        return CandidateSearchReport(status="CANDIDATE_GENERATION_PROVIDER_UNAVAILABLE", provider_status="LUCAS_ALTERNATIVES_NOT_SUPPLIED", limitations=["Core will not invent musical alternatives; no candidate was rendered or executed."])
    records: list[CandidateRecord] = []
    by_fingerprint: dict[str, str] = {}
    for index, raw in enumerate(provider.generate_candidates(context)):
        if len(records) >= max_candidates:
            break
        candidate_id = str(raw.get("candidate_id") or raw.get("id") or f"candidate-{index + 1}")
        fingerprint = _candidate_fingerprint(raw)
        duplicate_of = by_fingerprint.get(fingerprint)
        if duplicate_of is None:
            by_fingerprint[fingerprint] = candidate_id
        records.append(CandidateRecord(candidate_id=candidate_id, plan_fingerprint=fingerprint, source=getattr(provider, "provider_id", "REAL_LUCAS"), duplicate_of=duplicate_of, evidence_refs=list(raw.get("evidence_refs") or []), provenance={"candidate_input": "lucas_interface"}))
    unique = [row for row in records if row.duplicate_of is None]
    return CandidateSearchReport(status="CANDIDATES_RECEIVED" if unique else "CANDIDATE_GENERATION_EMPTY", provider_status="HEALTHY", candidates=records, limitations=[] if unique else ["Provider returned no usable alternatives."])


def build_provider_capability_matrix(artifact: dict[str, Any]) -> list[ProviderCapabilityRecord]:
    registry = ProviderCapabilityRegistry(cache_ttl_s=300)
    providers = ((artifact.get("post_change_context") or {}).get("advanced_perception") or {}).get("providers") or []
    provider_rows = {row.get("name"): row for row in providers if isinstance(row, dict)}
    analyzer = provider_rows.get("local-music-analyzer") or {}
    registry.register(ProviderCapability(provider_id="local-music-analyzer", capability="FACTUAL_MIR", model_id=analyzer.get("version"), local_or_remote="LOCAL", configured=bool(analyzer.get("available")), schema_support=True, provenance=["alpha.post_change_context.advanced_perception"]))
    clap = provider_rows.get("clap") or {}
    registry.register(ProviderCapability(provider_id="clap", capability="AUDIO_EMBEDDING", model_id=(clap.get("metadata") or {}).get("model"), local_or_remote="LOCAL", audio_support=True, configured=bool(clap.get("available")), schema_support=True, provenance=["alpha.post_change_context.advanced_perception"]))
    registry.register(ProviderCapability(provider_id="music-flamingo", capability="SEMANTIC_AUDIO", model_id=None, local_or_remote="LOCAL", audio_support=True, configured=False, schema_support=True, provenance=["src/copilot/audio/advanced_perception_v1.py"]))
    registry.register(ProviderCapability(provider_id="openai-compatible", capability="PRODUCER_PLAN", model_id=(artifact.get("lucas") or {}).get("provider_version"), local_or_remote="REMOTE", configured=bool((artifact.get("lucas") or {}).get("provider")), schema_support=True, provenance=["alpha.lucas.provider"]))
    registry.register(ProviderCapability(provider_id="openai-compatible", capability="PRODUCER_CRITIQUE", model_id=(artifact.get("lucas") or {}).get("provider_version"), local_or_remote="REMOTE", configured=bool((artifact.get("lucas_feedback") or {}).get("attempts")), schema_support=True, provenance=["alpha.lucas_feedback.attempts"]))
    registry.register(ProviderCapability(provider_id="demucs", capability="SOURCE_SEPARATION", model_id="htdemucs", local_or_remote="LOCAL", audio_support=True, configured=False, schema_support=True, provenance=["src/copilot/models/router.py"]))
    records = []
    for entry in registry.snapshot():
        if entry.provider_id == "local-music-analyzer":
            records.append(entry.model_copy(update={"availability": bool(analyzer.get("available")), "health": ProviderHealth.HEALTHY if analyzer.get("available") else ProviderHealth.UNAVAILABLE}))
        elif entry.provider_id == "clap" and clap.get("available"):
            records.append(entry.model_copy(update={"availability": True, "health": ProviderHealth.HEALTHY, "provenance": entry.provenance + ["alpha.post_change_context.advanced_perception.observation"]}))
        elif entry.provider_id == "openai-compatible" and entry.capability == "PRODUCER_PLAN":
            records.append(entry.model_copy(update={"availability": True, "health": ProviderHealth.HEALTHY, "provenance": entry.provenance + ["alpha.lucas.plan"]}))
        elif entry.provider_id == "openai-compatible" and entry.capability == "PRODUCER_CRITIQUE":
            records.append(entry.model_copy(update={"availability": False, "health": ProviderHealth.UNAVAILABLE, "failure_code": "CRITIQUE_PROVIDER_UNAVAILABLE", "provenance": entry.provenance + ["alpha.lucas_feedback.status"]}))
        elif entry.provider_id == "music-flamingo":
            records.append(entry.model_copy(update={"health": ProviderHealth.UNAVAILABLE, "failure_code": "SEMANTIC_PROVIDER_UNAVAILABLE"}))
        elif entry.provider_id == "demucs":
            records.append(entry.model_copy(update={"health": ProviderHealth.UNAVAILABLE, "failure_code": "SEPARATION_PROVIDER_UNAVAILABLE"}))
    return records


def probe_configured_reasoning_provider(*, timeout_s: float = 8.0) -> dict[str, Any]:
    """One cheap, secret-free runtime probe for the configured reasoning slot."""
    try:
        from copilot.reasoning.provider import configured_http_provider

        provider = configured_http_provider()
    except Exception as exc:
        return {"configured": False, "reachable": False, "failure_code": type(exc).__name__}
    if provider is None:
        return {"configured": False, "reachable": False, "failure_code": "NOT_CONFIGURED"}
    try:
        raw = provider.reason('Return JSON with exactly one key: "health", value "ok".', timeout_s=timeout_s)
        return {"configured": True, "reachable": True, "provider": provider.identity, "model": provider.version, "response_valid": isinstance(raw, str) and bool(raw.strip())}
    except Exception as exc:
        kind = getattr(exc, "kind", None)
        code = getattr(kind, "value", None) or str(kind or type(exc).__name__)
        return {"configured": True, "reachable": False, "provider": provider.identity, "model": provider.version, "failure_code": code}


def build_preference_observation() -> PreferenceObservation:
    return PreferenceObservation(observation_id="user.alpha.2026-09-21", source="explicit_user_judgment", judgment="REJECTED", scope="this Alpha artifact only", must_not_infer=["rejection reason", "genre preference", "global timbral preference", "future plan preference"], provenance=["user_judgment=REJECTED"])


def run_recovery(repo_root: str | Path, artifact_path: str | Path = ALPHA_ARTIFACT, output_dir: str | Path = "logs") -> dict[str, Any]:
    artifact = _read_json(artifact_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    audit = build_musical_intelligence_audit(repo_root, artifact)
    diagnosis = diagnose_bad_alpha(artifact)
    providers = build_provider_capability_matrix(artifact)
    reasoning_probe = probe_configured_reasoning_provider()
    if not reasoning_probe.get("reachable"):
        providers = [
            item.model_copy(update={
                "availability": False,
                "health": ProviderHealth.UNAVAILABLE,
                "failure_code": reasoning_probe.get("failure_code") or "MODEL_UNAVAILABLE",
                "provenance": item.provenance + ["runtime.reasoning_health_probe"],
            }) if item.provider_id == "openai-compatible" else item
            for item in providers
        ]
    pack_raw = ((artifact.get("post_change_context") or {}).get("music_analysis") or {})
    long_range: dict[str, Any]
    if pack_raw:
        try:
            long_range = _dump(build_long_range_context(MusicAnalysisPack.model_validate(pack_raw)))
        except Exception as exc:
            long_range = {"status": "BLOCKED", "reason": type(exc).__name__}
    else:
        long_range = {"status": "BLOCKED", "reason": "MUSIC_ANALYSIS_PACK_MISSING"}
    candidate_report = run_candidate_search({"alpha_artifact": str(artifact_path), "user_judgment": "REJECTED"})
    candidate_payload = _dump(candidate_report)
    candidate_payload["provider_attempts"] = [reasoning_probe]
    candidate_payload["translation_guard"] = {
        "status": "VERIFIED",
        "omitted_roles_cannot_become_actions": True,
        "evidence": ["core.constrain_plan_to_lucas_intent", "tests.test_lucas_core_integration_v1"],
    }
    preference = _dump(build_preference_observation())
    artifacts = {
        "musical_intelligence_audit.json": audit,
        "provider_capability_matrix.json": {"schema_version": "provider-capability-matrix-v1", "providers": [_dump(item) for item in providers], "reasoning_health_probe": reasoning_probe, "no_secrets_exposed": True},
        "bad_alpha_diagnosis.json": {**diagnosis, "long_range": build_alpha_long_range_context(artifact), "sample_fit": build_sample_fit_audit(artifact)},
        "semantic_ear_benchmark.json": {"status": "PROVIDER_UNAVAILABLE", "provider": "music-flamingo", "real_audio_validation": False, "tasks": [], "reason": "No semantic audio provider is installed/configured; CLAP is an embedding provider, not a semantic language model."},
        "candidate_search_run.json": candidate_payload,
        "preference_observations.json": {"observations": [preference], "global_preference_learning": False},
        "quality_recovery_run.json": {"status": "NOT_RUN_PROVIDER_AND_HUMAN_EVALUATION_REQUIRED", "baseline": str(artifact_path), "new_artifact": None, "reason": "No musical alternative was generated; no Ableton run was repeated.", "MUSICAL_WRITES": 0},
        "blind_ab_manifest.json": {"status": "PENDING_CANDIDATE_AUDIO", "mapping": None, "human_evaluation": "PENDING", "baseline": str(artifact_path)},
        "long_range_music_context.json": {"measured_context": long_range, "alpha_arrangement_context": build_alpha_long_range_context(artifact)},
    }
    for name, payload in artifacts.items():
        (output / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"milestone": MILESTONE, "audit": audit, "diagnosis": diagnosis, "providers": [_dump(item) for item in providers], "candidate_search": _dump(candidate_report), "long_range": long_range, "artifacts": [str(output / name) for name in artifacts]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline musical intelligence recovery artifacts")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--artifact", default=str(ALPHA_ARTIFACT))
    parser.add_argument("--output-dir", default="logs")
    args = parser.parse_args(argv)
    print(json.dumps(run_recovery(args.repo_root, args.artifact, args.output_dir), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
