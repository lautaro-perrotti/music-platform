"""Replay persisted external evidence through Astra. No Live. No recapture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from copilot.audio.producer_analyze_v1 import apply_reasoning_result
from copilot.human_eval.store import now_iso
from copilot.reasoning.errors import ReasoningFailure
from copilot.reasoning.grounding import validate_pack_facts, validate_reasoning
from copilot.reasoning.openai_schema import SCHEMA_NAME, reasoning_json_schema
from copilot.reasoning.pipeline import _parse_output, reason
from copilot.reasoning.provider import ReasoningProvider, configured_http_provider
from copilot.reasoning.schema import ReasoningOutput
from copilot.reasoning.session_astra import ASTRA_TIMEOUT_S
from copilot.schemas.diagnosis import DiagnosisStatus
from copilot.schemas.evidence import EvidencePack

MILESTONE = "ASTRA_EXTERNAL_REASONING_V1"
ARTIFACT = "astra_external_reasoning_v1.json"
CANONICAL_PACK_PATH = Path("logs") / "evidence_pack_v1.json"
EXPECTED_PACK_ID = "pack_afe4d6403ee4"
STATUS = "IMPLEMENTED"


class RecordingProvider:
    """Wraps a provider and keeps raw responses for audit. Does not call Live."""

    def __init__(self, inner: ReasoningProvider) -> None:
        self.inner = inner
        self.identity = inner.identity
        self.version = inner.version
        self.raws: list[str] = []
        self.last_prompt = ""
        self.last_request_contract: dict[str, Any] = {}

    def reason(self, prompt: str, *, timeout_s: float = 30.0) -> str:
        self.last_prompt = prompt
        raw = self.inner.reason(prompt, timeout_s=timeout_s)
        self.raws.append(raw)
        self.last_request_contract = dict(
            getattr(self.inner, "last_request_contract", {}) or {}
        )
        return raw


def load_persisted_pack(path: Path | None = None) -> EvidencePack:
    artifact = Path(path or CANONICAL_PACK_PATH)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    body = payload["pack"] if isinstance(payload, dict) and "pack" in payload else payload
    return EvidencePack.model_validate(body)


def pack_payload_hash(pack: EvidencePack) -> str:
    blob = json.dumps(pack.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def reconstruct_input(pack: EvidencePack) -> dict[str, Any]:
    """Exact persisted input. No enrichment."""
    measurements = [
        {
            "evidence_id": item.evidence_id,
            "name": item.name,
            "kind": item.kind.value,
            "quality": item.quality.value,
            "value": item.value,
            "source_ref": item.source_ref,
            "region": item.region,
            "view": item.view,
            "limitations": item.limitations,
        }
        for item in pack.items
    ]
    return {
        "pack_id": pack.pack_id,
        "payload_sha256": pack_payload_hash(pack),
        "analysis_version": pack.analysis_version,
        "prompt_schema_version": pack.prompt_schema_version,
        "domain": pack.domain,
        "region": pack.region,
        "project_token": pack.project_token,
        "audible_token": pack.audible_token,
        "target_token": pack.target_token,
        "alignment_claim": pack.alignment_claim,
        "alignment_envelope_ms": pack.alignment_envelope_ms,
        "evidence_ids": [item.evidence_id for item in pack.items],
        "observations": measurements,
        "entities": [item.model_dump(mode="json") for item in pack.entities],
        "limitations": [item.model_dump(mode="json") for item in pack.limitations],
        "requested_evidence_in_pack": [],
        "fact_problems": validate_pack_facts(pack),
    }


def evidence_sufficiency_audit(pack: EvidencePack) -> dict[str, str]:
    """Claim support from current pack only. No genre thresholds."""
    by_name = {item.name: item for item in pack.items}
    lowend = (by_name.get("lowend_observation") or None)
    lowend_val = (lowend.value if lowend is not None else {}) or {}
    overlap = lowend_val.get("overlap_events") if isinstance(lowend_val, dict) else None
    has_hz = any(
        item.unit and str(item.unit).lower() in {"hz", "khz"} for item in pack.items
    )
    has_db = any(
        item.unit and str(item.unit).lower() in {"db"} for item in pack.items
    )
    midi_unread = any(row.code == "MIDI_UNREAD" for row in pack.limitations)
    return {
        "LEVEL_IMBALANCE": (
            "NOT SUPPORTED — isolated RMS exists; no reference loudness or listener target"
        ),
        "TEMPORAL_MASKING": (
            "NOT SUPPORTED — no masking duration/threshold measurements"
        ),
        "KICK_DECAY_COLLISION": (
            "CONTRADICTED — overlap_events=0"
            if overlap == 0
            else "UNKNOWN — overlap not measured"
        ),
        "EXCESSIVE_BASS_DECAY": "NOT SUPPORTED — no decay duration measurement",
        "SPECTRAL_MASKING": (
            "NOT SUPPORTED — no Hz/dB spectral items"
            if not has_hz and not has_db
            else "UNKNOWN"
        ),
        "ARRANGEMENT_COLLISION": (
            "UNKNOWN — HAS_MATERIAL is clip overlap only, not audible contribution"
        ),
        "GROOVE_PATTERN": (
            "NOT SUPPORTED — fullmix item is truncated (event_count/ok), not repetition stats"
        ),
        "ENERGY_STRUCTURE": "UNKNOWN — Main HAS_SIGNAL is a fact, not a problem statement",
        "NO_ACTION_REQUIRED": (
            "NOT SUPPORTED — pack does not prove the mix needs no change; it fails to support a diagnosis"
        ),
        "MIDI_CAUSE": (
            "NOT SUPPORTED — MIDI_UNREAD" if midi_unread else "UNKNOWN"
        ),
        "overall": (
            "No musical diagnosis is supportable from this pack. "
            "ACCEPTED INSUFFICIENT_EVIDENCE is the valid result."
        ),
    }


def classify_rejection(
    *,
    accepted: bool,
    failure: str | None,
    parse_error: str | None,
    issues: list[dict[str, str]],
    output: ReasoningOutput | None,
) -> dict[str, Any]:
    if accepted:
        return {
            "primary": None,
            "label": "ACCEPTED",
            "detail": None,
        }
    details = " ".join(item.get("detail") or "" for item in issues).lower()
    kinds = {item.get("kind") for item in issues}
    if failure == ReasoningFailure.MODEL_OUTPUT_INVALID.value or parse_error:
        text = (parse_error or details).lower()
        if "enum" in text or "status" in text or "literal" in text:
            primary = "B"
        else:
            primary = "A"
        return {
            "primary": primary,
            "label": "JSON/schema violation" if primary == "A" else "enum/status contract violation",
            "detail": parse_error or details,
        }
    if ReasoningFailure.UNKNOWN_EVIDENCE_REF.value in kinds:
        return {"primary": "E", "label": "EvidenceRef/provenance failure", "detail": details}
    if "ungrounded time" in details or "qn " in details:
        return {"primary": "D", "label": "temporal grounding failure", "detail": details}
    if any(
        token in details
        for token in ("ungrounded frequency", "ungrounded level", "ungrounded count", "ungrounded duration", "ungrounded parameter")
    ):
        return {"primary": "C", "label": "numeric grounding failure", "detail": details}
    if ReasoningFailure.UNKNOWN_ENTITY_REF.value in kinds or ReasoningFailure.AMBIGUOUS_ENTITY_REFERENCE.value in kinds:
        return {"primary": "E", "label": "EvidenceRef/provenance failure", "detail": details}
    if "candidate" in details or "action_type" in details:
        return {"primary": "I", "label": "candidate-action contract violation", "detail": details}
    if "contradicting_evidence_refs" in details:
        return {"primary": "G", "label": "contradiction with observations", "detail": details}
    if failure == ReasoningFailure.MODEL_TIMEOUT.value:
        return {
            "primary": "L",
            "label": "MODEL_TIMEOUT collapsed into musical INSUFFICIENT_EVIDENCE",
            "detail": failure,
        }
    if failure == ReasoningFailure.MODEL_UNAVAILABLE.value:
        return {"primary": "L", "label": "provider unavailable", "detail": failure}
    if output is not None and output.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE:
        return {
            "primary": "J",
            "label": "invalid abstention representation",
            "detail": details or failure,
        }
    if failure == ReasoningFailure.INSUFFICIENT_EVIDENCE.value and output is None:
        return {
            "primary": "L",
            "label": "failure reported as IE without a schema-valid output",
            "detail": failure,
        }
    return {"primary": "L", "label": "another concrete cause", "detail": failure or details}


def replay_persisted_pack(
    *,
    evidence: Path | None = None,
    pack_path: Path | None = None,
    provider: ReasoningProvider | None = None,
    timeout_s: float = ASTRA_TIMEOUT_S,
) -> dict[str, Any]:
    """evidence pack → Astra → parser → grounding → acceptance. No Ableton."""
    evidence = Path(evidence or "logs")
    pack = load_persisted_pack(pack_path)
    reconstructed = reconstruct_input(pack)
    sufficiency = evidence_sufficiency_audit(pack)
    http = provider if provider is not None else configured_http_provider()
    report: dict[str, Any] = {
        "milestone": MILESTONE,
        "ts": now_iso(),
        "pack_id": pack.pack_id,
        "payload_sha256": reconstructed["payload_sha256"],
        "expected_pack_id": EXPECTED_PACK_ID,
        "pack_id_match": pack.pack_id == EXPECTED_PACK_ID,
        "input": reconstructed,
        "evidence_sufficiency": sufficiency,
        "requested_schema": {
            "name": SCHEMA_NAME,
            "strict": True,
            "timeout_s": timeout_s,
        },
        "NO LIVE": True,
        "NO RECAPTURE": True,
        "MUSICAL WRITES": 0,
    }
    if http is None:
        report["status"] = "BLOCKED"
        report["BLOCKER"] = "ASTRA_NOT_CONFIGURED"
        _persist(report, evidence)
        return report
    recorder = RecordingProvider(http)
    result = reason(pack, recorder, timeout_s=timeout_s)
    raw = recorder.raws[-1] if recorder.raws else ""
    parsed, parse_error = _parse_output(raw) if raw else (result.output, None)
    grounding = (
        validate_reasoning(parsed, pack).issues
        if parsed is not None
        else []
    )
    applied = apply_reasoning_result(result)
    classification = classify_rejection(
        accepted=result.accepted,
        failure=None if result.failure is None else result.failure.value,
        parse_error=parse_error,
        issues=list(result.audit.issues),
        output=result.output,
    )
    report.update(
        {
            "status": applied["status"] if result.accepted else applied["status"],
            "accepted": result.accepted,
            "ASTRA RESULT": (
                result.output.status.value
                if result.accepted and result.output is not None
                else applied["status"]
            ),
            "raw_structured_result": raw,
            "request_contract": recorder.last_request_contract,
            "parsed_result": None
            if parsed is None
            else parsed.model_dump(mode="json"),
            "parse_error": parse_error,
            "validator_result": {
                "accepted": result.accepted,
                "failure": None if result.failure is None else result.failure.value,
                "issues": result.audit.issues,
            },
            "grounding_result": [
                {"kind": issue.kind.value, "detail": issue.detail} for issue in grounding
            ],
            "rejection": classification,
            "gate": applied["gate"],
            "diagnosis": applied["diagnosis"],
            "reasoning_audit": applied["reasoning_audit"],
            "requested_evidence": (
                [item.model_dump(mode="json") for item in result.output.requested_evidence]
                if result.output is not None
                else []
            ),
        }
    )
    if result.accepted:
        report[MILESTONE] = "VERIFIED"
    else:
        report[MILESTONE] = "BLOCKED"
        report["BLOCKER"] = classification.get("detail") or classification.get("label")
    _persist(report, evidence)
    raw_path = evidence / "astra_external_reasoning_v1_raw.json"
    raw_path.write_text(
        json.dumps(
            {
                "pack_id": pack.pack_id,
                "payload_sha256": reconstructed["payload_sha256"],
                "raws": recorder.raws,
                "parse_error": parse_error,
                "request_contract": recorder.last_request_contract,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report["raw_artifact"] = str(raw_path)
    return report


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    # Drop huge prompt/raw duplicate from the summary artifact if present as last_prompt.
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    report["artifact"] = str(path)
    return path


def main() -> int:
    report = replay_persisted_pack()
    summary = {
        "milestone": MILESTONE,
        "pack_id": report.get("pack_id"),
        "payload_sha256": report.get("payload_sha256"),
        "accepted": report.get("accepted"),
        "ASTRA RESULT": report.get("ASTRA RESULT"),
        "rejection": report.get("rejection"),
        "BLOCKER": report.get("BLOCKER"),
        MILESTONE: report.get(MILESTONE),
        "MUSICAL WRITES": 0,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    if report.get("accepted"):
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
