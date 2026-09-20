from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from copilot.reasoning.claims import (
    capture_failed_as_silence,
    certain_key_claim,
    command_hits,
    invented_percentage,
    is_overbroad_request,
    muddy_as_measurement,
    ungrounded_device_cause,
)
from copilot.reasoning.errors import ReasoningFailure
from copilot.reasoning.evidence_input import (
    fusion_statuses,
    known_evidence_ids,
    limitation_codes_from_scoped,
    scoped_evidence,
    view_node_index,
)
from copilot.reasoning.schema import ReasoningOutput
from copilot.schemas.diagnosis import CandidateActionType, Confidence, DiagnosisStatus, FindingType
from copilot.schemas.evidence import (
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    EvidenceRequest,
    EvidenceRequestDecision,
    EvidenceRequestKind,
    NumericProvenance,
)
from copilot.schemas.observation import ClaimKind, MusicObservation

INTERPRETIVE_OBSERVATION_TERMS = (
    "kick is weak",
    "bass is muddy",
    "mix lacks punch",
    "release is too long",
    "lacks punch",
    "too muddy",
)

HALLUCINATION_DEVICE_NAMES = (
    "serum",
    "massive",
    "vital",
    "sylenth",
    "wavetable",
    "operator",
    "analog",
    "phase plant",
    "pigments",
)

ABLETON_OP_RE = re.compile(
    r"\b(set_device_parameter|get_track_info|call_function|live\.object|song\.|eval\(|lom\b)\b",
    re.IGNORECASE,
)
MS_RE = re.compile(r"(?<![\d.])(?P<val>-?\d+(?:\.\d+)?)\s*(?:ms|milliseconds)\b", re.IGNORECASE)
MS_RANGE_RE = re.compile(
    r"(?P<lo>-?\d+(?:\.\d+)?)\s*[-–]\s*(?P<hi>-?\d+(?:\.\d+)?)\s*(?:ms|milliseconds)\b",
    re.IGNORECASE,
)
CAPABILITY_CUES = re.compile(
    r"(?:not\s+supported|unsupported|not\s+assessable|cannot\s+resolve|"
    r"microtiming|ALIGNMENT_LIMITED)",
    re.IGNORECASE,
)
# Analyzer/config FACT window bounds (e.g. ev.persist.window [50,200] ms).
# Must beat MEASUREMENT_CUES when the model cites window limits next to
# persist/decay language — those numbers are not measured durations.
SESSION_STATE_CUES = re.compile(
    r"(?:analysis\s+window|window\s+defined|defined\s+by\s+ev\.|"
    r"persist\.window|fm\.window_ms|fm\.hop_ms|"
    r"not\s+a\s+measured(?:\s+decay)?|not\s+measured\s+decay|"
    r"not\s+a\s+decay\s+duration|persist_analysis_window|"
    r"normalized\s+energy\s+in\s+a|"
    r"ms\s+window)",
    re.IGNORECASE,
)
MEASUREMENT_CUES = re.compile(
    r"\b(?:persist|overlap|release|decay|duration|median|lasted|lasting|"
    r"late|delay|quantiz|measured|attack\s+length|note\s+length)\b",
    re.IGNORECASE,
)
SESSION_STATE_KINDS = {
    EvidenceKind.SESSION_ENTITY,
    EvidenceKind.STATE_TOKEN,
    EvidenceKind.FACT,
}
HZ_RE = re.compile(r"(?<![\d.-])(?P<val>\d+(?:\.\d+)?)\s*(?:hz|khz)\b", re.IGNORECASE)
COUNT_RE = re.compile(
    r"(?P<val>\d+)\s*(?:kick\s*)?(?:attacks?|collisions?|overlaps?|events?|notes?)\b",
    re.IGNORECASE,
)
DB_RE = re.compile(r"(?P<val>-?\d+(?:\.\d+)?)\s*dB\b", re.IGNORECASE)
EQ_RE = re.compile(r"(?P<val>-?\d+(?:\.\d+)?)\s*=\s*(?P<rhs>-?\d+(?:\.\d+)?)", re.IGNORECASE)
PARAM_RE = re.compile(
    r"\b(?:value|release|attack|decay|sustain|cutoff|frequency)\s*[=:]\s*(?P<val>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
# Absolute time-position claims (not ms durations — those use MS_RE).
TIMECODE_RE = re.compile(
    r"(?<![\d.:])(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?(?:\.(?P<frac>\d+))?(?![\d:])"
)
SECONDS_POS_RE = re.compile(
    r"(?<![\d.-])(?P<val>\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)\b",
    re.IGNORECASE,
)
MINUTE_SEC_RE = re.compile(
    r"\b(?P<m>\d+)\s*m\s*(?P<s>\d+(?:\.\d+)?)\s*s\b",
    re.IGNORECASE,
)
QN_POS_RE = re.compile(
    r"\b(?:qn|beat)\s*(?P<val>\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
TIME_POSITION_UNITS = {"s", "sec", "secs", "seconds", "qn", "beat", "beats", "timecode"}
CONFIDENCE_RANK = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
TIMING_CATEGORIES = {
    FindingType.TEMPORAL_MASKING,
    FindingType.KICK_DECAY_COLLISION,
    FindingType.EXCESSIVE_BASS_DECAY,
}


@dataclass
class ValidationIssue:
    kind: ReasoningFailure
    detail: str


@dataclass
class GroundingReport:
    accepted: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    request_decisions: list[dict[str, str]] = field(default_factory=list)
    capped_confidence: Confidence | None = None

    @property
    def primary_failure(self) -> ReasoningFailure | None:
        if not self.issues:
            return None
        return self.issues[0].kind


def validate_pack_facts(pack: EvidencePack) -> list[str]:
    problems: list[str] = []
    for item in pack.items:
        text = f"{item.name} {item.value}".lower()
        for term in INTERPRETIVE_OBSERVATION_TERMS:
            if term in text:
                problems.append(f"{item.evidence_id} contains interpretation: {term}")
    return problems


def observation_fact_problems(observation: MusicObservation) -> list[str]:
    problems: list[str] = []
    for claim in observation.claims:
        text = f"{claim.name} {claim.value} {claim.notes or ''}".lower()
        for term in INTERPRETIVE_OBSERVATION_TERMS:
            if term in text:
                problems.append(f"{claim.name}: interpretation '{term}'")
        if claim.kind is not ClaimKind.MEASURED:
            problems.append(f"{claim.name}: observation claims must be MEASURED, got {claim.kind.value}")
    return problems


def validate_reasoning(
    output: ReasoningOutput,
    pack: EvidencePack,
    view: object | None = None,
) -> GroundingReport:
    issues: list[ValidationIssue] = []
    scoped = scoped_evidence(pack, view)
    known = known_evidence_ids(pack, scoped)
    index = pack.by_id()
    nodes = view_node_index(scoped)
    _check_refs(output, index, issues, known=known)
    _check_hypotheses(output, index, issues, known=known)
    _check_measurements(output, pack, issues)
    _check_numeric_refs(output, pack, issues)
    _check_entities(output, pack, issues)
    _check_precision(output, pack, issues)
    _check_actions(output, pack, issues)
    _check_contradiction_contract(output, issues)
    _check_commands(output, issues)
    _check_limitation_propagation(output, pack, scoped, issues)
    _check_stale_and_identity(output, pack, nodes, issues)
    _check_fusion_contradictions(output, scoped, issues)
    _check_status_and_requests(output, issues)
    _check_dsp_discipline(output, pack, scoped, issues)
    _check_confidence_contract(output, issues)
    decisions = decide_evidence_requests(output.requested_evidence, pack)
    capped = cap_confidence(output, pack)
    accepted = not issues
    return GroundingReport(
        accepted=accepted,
        issues=issues,
        request_decisions=decisions,
        capped_confidence=capped,
    )


def cap_confidence(output: ReasoningOutput, pack: EvidencePack) -> Confidence:
    recommended = output.confidence
    upper = Confidence.HIGH
    limitation_codes = {item.code for item in pack.limitations}
    if pack.alignment_claim == "LIMITED" or "ALIGNMENT_LIMITED" in limitation_codes:
        if output.category in TIMING_CATEGORIES or _has_timing_claim(output):
            upper = _min_conf(upper, Confidence.MEDIUM)
    if "MISSING_ISOLATE" in limitation_codes:
        upper = _min_conf(upper, Confidence.LOW)
    if "CONFLICTING_OBSERVATIONS" in limitation_codes:
        upper = _min_conf(upper, Confidence.LOW)
    if output.status in {
        DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        DiagnosisStatus.DIAGNOSIS_UNSTABLE,
    }:
        upper = _min_conf(upper, Confidence.LOW)
    if _duplicate_refs_only(output):
        upper = _min_conf(upper, recommended)
    return _min_conf(recommended, upper)


def decide_evidence_requests(
    requests: list[EvidenceRequest],
    pack: EvidencePack,
) -> list[dict[str, str]]:
    present_views = {item.view for item in pack.items if item.view}
    present_names = {item.name for item in pack.items}
    limitation_codes = {item.code for item in pack.limitations}
    out: list[dict[str, str]] = []
    for request in requests:
        decision = EvidenceRequestDecision.ALLOWED
        reason = "core may fulfill later; llm does not execute"
        if request.request_kind is EvidenceRequestKind.CAPTURE_VIEW:
            token = request.target.upper()
            if token in present_views or request.target in present_views:
                decision = EvidenceRequestDecision.ALREADY_CACHED
                reason = "requested view already in evidence pack"
            elif token in {"VOCALS", "MASTERING", "HARMONY"}:
                decision = EvidenceRequestDecision.UNSUPPORTED
                reason = "outside current reasoning domain"
            elif token == "STEREO":
                if any(item.name.startswith("fullmix_stereo") for item in pack.items):
                    decision = EvidenceRequestDecision.ALREADY_CACHED
                    reason = "fullmix stereo descriptors already present"
                elif pack.domain.startswith("fullmix"):
                    decision = EvidenceRequestDecision.ALLOWED
                    reason = "fullmix domain may refine stereo descriptors"
                else:
                    decision = EvidenceRequestDecision.UNSUPPORTED
                    reason = "outside low-end reasoning domain"
        elif request.request_kind is EvidenceRequestKind.READ_MIDI:
            if "clip_note_starts" in present_names or "MIDI_PRESENT" in present_names:
                decision = EvidenceRequestDecision.ALREADY_CACHED
                reason = "midi facts already present"
            elif "MIDI_UNREAD" not in limitation_codes and "clip_note_starts" in present_names:
                decision = EvidenceRequestDecision.REDUNDANT
                reason = "midi already known"
        elif request.request_kind is EvidenceRequestKind.READ_DEVICE_PARAMETERS:
            if any(name.startswith("device_param_") for name in present_names):
                decision = EvidenceRequestDecision.ALREADY_CACHED
                reason = "device parameters already present"
        elif request.request_kind is EvidenceRequestKind.READ_ROUTING:
            if "ROUTING_UNKNOWN" not in limitation_codes and any(
                item.name == "routing" for item in pack.items
            ):
                decision = EvidenceRequestDecision.ALREADY_CACHED
                reason = "routing already present"
        elif request.request_kind is EvidenceRequestKind.ANALYZE_REGION:
            if _region_too_expensive(request.region, pack.region):
                decision = EvidenceRequestDecision.TOO_EXPENSIVE
                reason = "requested region exceeds current bounded envelope"
            elif request.region == pack.region:
                decision = EvidenceRequestDecision.REDUNDANT
                reason = "requested region already analyzed"
        out.append(
            {
                "request_kind": request.request_kind.value,
                "target": request.target,
                "decision": decision.value,
                "reason": reason,
            }
        )
    return out


def _check_refs(
    output: ReasoningOutput,
    index: dict[str, EvidenceItem],
    issues: list[ValidationIssue],
    *,
    known: set[str] | None = None,
) -> None:
    allowed = known if known is not None else set(index)
    for ref in output.evidence_refs + output.contradicting_evidence_refs:
        if ref not in allowed:
            issues.append(ValidationIssue(ReasoningFailure.UNKNOWN_EVIDENCE_REF, ref))
    for strategy in output.candidate_strategies:
        for ref in strategy.evidence_refs:
            if ref not in allowed:
                issues.append(ValidationIssue(ReasoningFailure.UNKNOWN_EVIDENCE_REF, ref))


def _check_hypotheses(
    output: ReasoningOutput,
    index: dict[str, EvidenceItem],
    issues: list[ValidationIssue],
    *,
    known: set[str] | None = None,
) -> None:
    allowed = known if known is not None else set(index)
    for hypo in output.hypotheses:
        if not hypo.evidence_refs:
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    f"hypothesis has no evidence_refs: {hypo.claim[:80]}",
                )
            )
            continue
        for ref in hypo.evidence_refs + hypo.contradicting_evidence_refs:
            if ref not in allowed:
                issues.append(ValidationIssue(ReasoningFailure.UNKNOWN_EVIDENCE_REF, ref))


def _check_measurements(
    output: ReasoningOutput,
    pack: EvidencePack,
    issues: list[ValidationIssue],
) -> None:
    measurement_catalog = _numeric_catalog(pack, kinds={EvidenceKind.MEASUREMENT})
    session_catalog = _numeric_catalog(pack, kinds=SESSION_STATE_KINDS)
    blobs = _text_blobs(output)
    for blob in blobs:
        for match in HZ_RE.finditer(blob):
            raw = match.group(0).lower()
            val = float(match.group("val"))
            if "khz" in raw:
                val *= 1000.0
            if not _in_catalog(measurement_catalog, val, {"Hz", "hz"}) and not _in_catalog(
                session_catalog, val, {"Hz", "hz"}
            ):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"ungrounded frequency {match.group(0)}",
                    )
                )
        covered_ms_spans = _authorize_ms_ranges(
            blob,
            pack,
            measurement_catalog,
            session_catalog,
            issues,
        )
        for match in MS_RE.finditer(blob):
            if any(start <= match.start() < end for start, end in covered_ms_spans):
                continue
            val = float(match.group("val"))
            provenance = _classify_ms_provenance(blob, match)
            if not _ms_authorized(pack, val, provenance, measurement_catalog, session_catalog):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"ungrounded duration {match.group(0)}",
                    )
                )
        for match in COUNT_RE.finditer(blob):
            val = float(match.group("val"))
            if not _in_catalog(measurement_catalog, val, {None, "count"}) and not _in_catalog(
                session_catalog, val, {None, "count"}
            ):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"ungrounded count {match.group(0)}",
                    )
                )
        for match in DB_RE.finditer(blob):
            val = float(match.group("val"))
            if not _in_catalog(measurement_catalog, val, {"dB", "db"}) and not _in_catalog(
                session_catalog, val, {"dB", "db"}
            ):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"ungrounded level {match.group(0)}",
                    )
                )
        for match in PARAM_RE.finditer(blob):
            val = float(match.group("val"))
            if not _in_catalog(measurement_catalog, val, None) and not _in_catalog(
                session_catalog, val, None
            ):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"ungrounded parameter value {match.group(0)}",
                    )
                )
        _check_time_positions(blob, measurement_catalog, session_catalog, issues)


def _timecode_to_seconds(match: re.Match[str]) -> float:
    hours = match.group("h")
    minutes = match.group("m")
    seconds = match.group("s")
    frac = match.group("frac")
    # mm:ss(.frac) or hh:mm:ss(.frac)
    if seconds is None:
        # Interpreted as mm:ss(.frac) — group h is minutes, m is seconds.
        total = int(hours) * 60.0 + int(minutes)
        if frac:
            total += float(f"0.{frac}")
        return total
    total = int(hours) * 3600.0 + int(minutes) * 60.0 + int(seconds)
    if frac:
        total += float(f"0.{frac}")
    return total


def _check_time_positions(
    blob: str,
    measurement_catalog: list[tuple[float, str | None]],
    session_catalog: list[tuple[float, str | None]],
    issues: list[ValidationIssue],
) -> None:
    """Factual timestamps/timecodes/qn positions must resolve to typed evidence."""
    covered: list[tuple[int, int]] = []

    for match in TIMECODE_RE.finditer(blob):
        val = _timecode_to_seconds(match)
        if not _time_authorized(val, measurement_catalog, session_catalog):
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    f"ungrounded time position {match.group(0)}",
                )
            )
        covered.append((match.start(), match.end()))

    for match in MINUTE_SEC_RE.finditer(blob):
        if any(start <= match.start() < end for start, end in covered):
            continue
        val = float(match.group("m")) * 60.0 + float(match.group("s"))
        if not _time_authorized(val, measurement_catalog, session_catalog):
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    f"ungrounded time position {match.group(0)}",
                )
            )
        covered.append((match.start(), match.end()))

    for match in SECONDS_POS_RE.finditer(blob):
        if any(start <= match.start() < end for start, end in covered):
            continue
        val = float(match.group("val"))
        if not _time_authorized(val, measurement_catalog, session_catalog):
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    f"ungrounded time position {match.group(0)}",
                )
            )

    for match in QN_POS_RE.finditer(blob):
        val = float(match.group("val"))
        if not _time_authorized(val, measurement_catalog, session_catalog, qn=True):
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    f"ungrounded time position {match.group(0)}",
                )
            )


def _time_authorized(
    value: float,
    measurement_catalog: list[tuple[float, str | None]],
    session_catalog: list[tuple[float, str | None]],
    *,
    qn: bool = False,
) -> bool:
    units = {"qn", "beat", "beats"} if qn else TIME_POSITION_UNITS
    return _in_catalog(measurement_catalog, value, units) or _in_catalog(
        session_catalog, value, units
    )


def _check_entities(
    output: ReasoningOutput,
    pack: EvidencePack,
    issues: list[ValidationIssue],
) -> None:
    by_id = pack.entity_by_id()
    declared = list(output.entity_refs)
    for hypo in output.hypotheses:
        declared.extend(hypo.entity_refs)
    for action in output.candidate_actions:
        declared.extend(action.entity_refs)
        if action.target and action.target in by_id:
            declared.append(action.target)
    for entity_id in declared:
        if entity_id not in by_id:
            issues.append(ValidationIssue(ReasoningFailure.UNKNOWN_ENTITY_REF, entity_id))
    text = " ".join(_text_blobs(output)).lower()
    name_map: dict[str, list[str]] = {}
    for entity in pack.entities:
        name_map.setdefault(entity.name.lower(), []).append(entity.entity_id)
        if entity.class_name:
            name_map.setdefault(entity.class_name.lower(), []).append(entity.entity_id)
    mentioned_names = set()
    for name, ids in name_map.items():
        if len(name) < 3:
            continue
        if re.search(rf"\b{re.escape(name)}\b", text):
            mentioned_names.add(name)
            unique = list(dict.fromkeys(ids))
            if len(unique) > 1 and not any(item in declared for item in unique):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.AMBIGUOUS_ENTITY_REFERENCE,
                        name,
                    )
                )
    for trap in HALLUCINATION_DEVICE_NAMES:
        if re.search(rf"\b{re.escape(trap)}\b", text) and trap not in name_map:
            issues.append(ValidationIssue(ReasoningFailure.UNKNOWN_ENTITY_REF, trap))


def _check_precision(
    output: ReasoningOutput,
    pack: EvidencePack,
    issues: list[ValidationIssue],
) -> None:
    envelope = float(pack.alignment_envelope_ms)
    if pack.alignment_claim != "LIMITED":
        return
    for blob in _text_blobs(output):
        for match in MS_RE.finditer(blob):
            val = abs(float(match.group("val")))
            provenance = _classify_ms_provenance(blob, match)
            if provenance in {NumericProvenance.CAPABILITY_LIMIT, NumericProvenance.CONTRACT_CONSTANT}:
                continue
            if provenance is NumericProvenance.UNKNOWN and _is_alignment_envelope(pack, val):
                continue
            if val < envelope and output.status is DiagnosisStatus.SUPPORTED:
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.UNSUPPORTED_PRECISION,
                        f"timing claim {match.group(0)} tighter than LIMITED ±{envelope:g} ms",
                    )
                )


def _check_actions(
    output: ReasoningOutput,
    pack: EvidencePack,
    issues: list[ValidationIssue],
) -> None:
    catalog = _numeric_catalog(pack)
    for action in output.candidate_actions:
        blob = f"{action.reason} {action.expected_effect} {action.risk}"
        if ABLETON_OP_RE.search(blob) or ABLETON_OP_RE.search(action.action_type.value):
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    f"raw Ableton command in candidate {action.action_type.value}",
                )
            )
        for match in PARAM_RE.finditer(blob):
            val = float(match.group("val"))
            if not _in_catalog(catalog, val, None):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"candidate parameter value not in evidence: {match.group(0)}",
                    )
                )
        if action.action_type not in CandidateActionType:
            issues.append(
                ValidationIssue(
                    ReasoningFailure.MODEL_OUTPUT_INVALID,
                    f"unknown action_type {action.action_type}",
                )
            )
    for strategy in output.candidate_strategies:
        blob = f"{strategy.strategy} {strategy.reason}"
        if ABLETON_OP_RE.search(blob):
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    f"raw Ableton command in strategy {strategy.strategy[:80]}",
                )
            )
        for match in PARAM_RE.finditer(blob):
            val = float(match.group("val"))
            if not _in_catalog(catalog, val, None):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"strategy parameter value not in evidence: {match.group(0)}",
                    )
                )


def _check_contradiction_contract(
    output: ReasoningOutput,
    issues: list[ValidationIssue],
) -> None:
    needs = output.status in {
        DiagnosisStatus.SUPPORTED,
        DiagnosisStatus.WEAKLY_SUPPORTED,
        DiagnosisStatus.NO_ACTION_REQUIRED,
    }
    if not needs:
        return
    has = bool(output.contradicting_evidence_refs)
    has = has or any(item.contradicting_evidence_refs for item in output.hypotheses)
    if not has:
        issues.append(
            ValidationIssue(
                ReasoningFailure.LLM_GROUNDING_VIOLATION,
                "supported/no-action diagnosis missing contradicting_evidence_refs",
            )
        )


def _numeric_catalog(
    pack: EvidencePack,
    *,
    kinds: set[EvidenceKind] | None = None,
) -> list[tuple[float, str | None]]:
    out: list[tuple[float, str | None]] = []
    for item in pack.items:
        if kinds is not None and item.kind not in kinds:
            continue
        out.extend(_iter_numeric(item.value, item.unit))
        out.extend(_iter_numeric(item.name, item.unit))
    return out


def _iter_numeric(value: Any, unit: str | None) -> list[tuple[float, str | None]]:
    found: list[tuple[float, str | None]] = []
    if isinstance(value, bool):
        return found
    if isinstance(value, (int, float)):
        found.append((float(value), unit))
        return found
    if isinstance(value, str):
        for match in re.finditer(r"-?\d+(?:\.\d+)?", value):
            found.append((float(match.group(0)), unit))
        return found
    if isinstance(value, dict):
        for nested in value.values():
            found.extend(_iter_numeric(nested, unit))
        return found
    if isinstance(value, list):
        for nested in value:
            found.extend(_iter_numeric(nested, unit))
    return found


def _in_catalog(
    catalog: list[tuple[float, str | None]],
    value: float,
    units: set[str | None] | None,
) -> bool:
    allowed = None
    if units is not None:
        allowed = {item.lower() if isinstance(item, str) else item for item in units}
    for catalog_value, catalog_unit in catalog:
        if allowed is not None and None not in allowed:
            cat = catalog_unit.lower() if isinstance(catalog_unit, str) else catalog_unit
            if cat not in allowed:
                continue
        if _numbers_match(catalog_value, value):
            return True
    return False


def _numbers_match(left: float, right: float) -> bool:
    if abs(left - right) <= 0.51 and float(left).is_integer() and float(right).is_integer():
        return True
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) <= 1e-3 * scale or abs(left - right) <= 1e-6


def _is_alignment_envelope(pack: EvidencePack, value: float) -> bool:
    return abs(value - float(pack.alignment_envelope_ms)) <= 0.51


def _classify_ms_provenance(blob: str, match: re.Match[str]) -> NumericProvenance:
    context = _span_context(blob, match.start(), match.end())
    # Window/config FACT citations outrank adjacent persist/decay wording.
    if SESSION_STATE_CUES.search(context):
        return NumericProvenance.SESSION_STATE_VALUE
    has_capability = bool(CAPABILITY_CUES.search(context))
    has_measurement = bool(MEASUREMENT_CUES.search(context))
    if has_capability and has_measurement:
        return NumericProvenance.UNKNOWN
    if has_capability:
        return NumericProvenance.CAPABILITY_LIMIT
    if has_measurement:
        return NumericProvenance.MEASUREMENT_VALUE
    return NumericProvenance.UNKNOWN


def _span_context(blob: str, start: int, end: int, radius: int = 96) -> str:
    lo = max(0, start - radius)
    hi = min(len(blob), end + radius)
    return blob[lo:hi]


def _authorize_ms_ranges(
    blob: str,
    pack: EvidencePack,
    measurement_catalog: list[tuple[float, str | None]],
    session_catalog: list[tuple[float, str | None]],
    issues: list[ValidationIssue],
) -> list[tuple[int, int]]:
    """Authorize explicit N–M ms ranges; return covered character spans for MS_RE skip."""
    covered: list[tuple[int, int]] = []
    for match in MS_RANGE_RE.finditer(blob):
        lo = float(match.group("lo"))
        hi = float(match.group("hi"))
        provenance = _classify_ms_provenance(blob, match)
        for val in (lo, hi):
            if not _ms_authorized(pack, val, provenance, measurement_catalog, session_catalog):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"ungrounded duration {match.group(0)}",
                    )
                )
                break
        covered.append((match.start(), match.end()))
    return covered


def _ms_authorized(
    pack: EvidencePack,
    value: float,
    provenance: NumericProvenance,
    measurement_catalog: list[tuple[float, str | None]],
    session_catalog: list[tuple[float, str | None]],
) -> bool:
    if provenance is NumericProvenance.MEASUREMENT_VALUE:
        return _in_catalog(measurement_catalog, value, {"ms"})
    if provenance is NumericProvenance.SESSION_STATE_VALUE:
        return _in_catalog(session_catalog, value, {"ms"})
    if provenance is NumericProvenance.CAPABILITY_LIMIT:
        return _ms_in_capability(pack, value)
    if provenance is NumericProvenance.CONTRACT_CONSTANT:
        return _is_alignment_envelope(pack, value)
    if _in_catalog(measurement_catalog, value, {"ms"}):
        return True
    if _in_catalog(session_catalog, value, {"ms"}):
        return True
    return _is_alignment_envelope(pack, value)


def _ms_in_capability(pack: EvidencePack, value: float) -> bool:
    if _is_alignment_envelope(pack, value):
        return True
    for limitation in pack.limitations:
        if limitation.precision_ms is not None and _numbers_match(float(limitation.precision_ms), value):
            return True
        for declared in limitation.capability_ms:
            if _numbers_match(float(declared), value):
                return True
        for match in MS_RE.finditer(limitation.detail):
            if _numbers_match(float(match.group("val")), value):
                return True
        for match in MS_RANGE_RE.finditer(limitation.detail):
            lo = float(match.group("lo"))
            hi = float(match.group("hi"))
            low, high = (lo, hi) if lo <= hi else (hi, lo)
            if low - 0.51 <= value <= high + 0.51:
                return True
    return False


def _text_blobs(output: ReasoningOutput) -> list[str]:
    blobs = [output.summary, output.question, output.scope]
    for hypo in output.hypotheses:
        blobs.extend(
            [
                hypo.claim,
                hypo.reasoning_summary,
                *hypo.alternatives_considered,
                *hypo.missing_evidence,
                *hypo.limitations,
            ]
        )
    for action in output.candidate_actions:
        blobs.extend([action.reason, action.expected_effect, action.risk])
    for strategy in output.candidate_strategies:
        blobs.extend([strategy.strategy, strategy.reason])
    blobs.extend(output.limitations)
    for request in output.requested_evidence:
        blobs.extend(
            [
                request.why_needed,
                request.expected_information_gain,
                request.goal,
                request.target,
                request.region,
            ]
        )
    return [item for item in blobs if item]


def _has_timing_claim(output: ReasoningOutput) -> bool:
    for blob in _text_blobs(output):
        for match in MS_RE.finditer(blob):
            provenance = _classify_ms_provenance(blob, match)
            if provenance is NumericProvenance.CAPABILITY_LIMIT:
                continue
            if provenance is NumericProvenance.CONTRACT_CONSTANT:
                continue
            return True
    return False


def _duplicate_refs_only(output: ReasoningOutput) -> bool:
    refs = list(output.evidence_refs)
    for hypo in output.hypotheses:
        refs.extend(hypo.evidence_refs)
    return bool(refs) and len(set(refs)) == 1


def _min_conf(left: Confidence, right: Confidence) -> Confidence:
    return left if CONFIDENCE_RANK[left] <= CONFIDENCE_RANK[right] else right


def _region_too_expensive(requested: str, current: str) -> bool:
    del current
    match = re.search(r"(?P<span>\d+(?:\.\d+)?)\s*(?:qn|quarter)", requested, re.IGNORECASE)
    if not match:
        return False
    return float(match.group("span")) > 64.0


def _cited_ids(output: ReasoningOutput) -> list[str]:
    refs: list[str] = list(output.evidence_refs) + list(output.contradicting_evidence_refs)
    for hypo in output.hypotheses:
        refs.extend(hypo.evidence_refs)
        refs.extend(hypo.contradicting_evidence_refs)
    for action in output.candidate_actions:
        refs.extend(action.evidence_refs)
    for strategy in output.candidate_strategies:
        refs.extend(strategy.evidence_refs)
    return list(dict.fromkeys(refs))


def _catalog_for_ids(pack: EvidencePack, ids: list[str]) -> list[tuple[float, str | None]]:
    wanted = set(ids)
    out: list[tuple[float, str | None]] = []
    for item in pack.items:
        if item.evidence_id not in wanted:
            continue
        out.extend(_iter_numeric(item.value, item.unit))
        out.extend(_iter_numeric(item.name, item.unit))
    return out


def _check_numeric_refs(
    output: ReasoningOutput,
    pack: EvidencePack,
    issues: list[ValidationIssue],
) -> None:
    """Quantitative facts must map to cited EvidenceRefs, not the whole pack dump."""
    cited = _catalog_for_ids(pack, _cited_ids(output))
    session_catalog = _numeric_catalog(pack, kinds=SESSION_STATE_KINDS)
    measurement_catalog = _numeric_catalog(pack, kinds={EvidenceKind.MEASUREMENT})
    for blob in _text_blobs(output):
        for match in DB_RE.finditer(blob):
            val = float(match.group("val"))
            if _in_catalog(session_catalog, val, {"dB", "db"}):
                continue
            if _in_catalog(measurement_catalog, val, {"dB", "db"}) and not _in_catalog(
                cited, val, {"dB", "db"}
            ):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"numeric value {match.group(0)} is not in cited EvidenceRefs",
                    )
                )
        for match in HZ_RE.finditer(blob):
            raw = match.group(0).lower()
            val = float(match.group("val"))
            if "khz" in raw:
                val *= 1000.0
            if _in_catalog(session_catalog, val, {"Hz", "hz"}):
                continue
            if _in_catalog(measurement_catalog, val, {"Hz", "hz"}) and not _in_catalog(
                cited, val, {"Hz", "hz"}
            ):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"numeric value {match.group(0)} is not in cited EvidenceRefs",
                    )
                )


def _check_commands(output: ReasoningOutput, issues: list[ValidationIssue]) -> None:
    for blob in _text_blobs(output):
        hits = command_hits(blob)
        if hits:
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    f"manual command sequencing is forbidden: {hits[0]}",
                )
            )


def _limitation_declared(code: str, declared: str) -> bool:
    if not code:
        return True
    if code in declared:
        return True
    aliases = {
        "MIDI_UNREAD": "MIDI_UNAVAILABLE",
        "MIDI_UNAVAILABLE": "MIDI_UNREAD",
        "ROUTING_UNKNOWN": "ROUTING_UNRESOLVED",
        "ROUTING_UNRESOLVED": "ROUTING_UNKNOWN",
        "DEVICE_PARAMS_UNREAD": "AUTOMATION_UNREAD",
        "AUTOMATION_UNREAD": "DEVICE_PARAMS_UNREAD",
    }
    other = aliases.get(code)
    return bool(other and other in declared)


def _check_limitation_propagation(
    output: ReasoningOutput,
    pack: EvidencePack,
    scoped: dict,
    issues: list[ValidationIssue],
) -> None:
    declared = " ".join(
        [
            *output.limitations,
            *[item for hypo in output.hypotheses for item in hypo.limitations],
        ]
    )
    for code in limitation_codes_from_scoped(pack, scoped):
        if not _limitation_declared(code, declared):
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    f"limitation dropped: {code}",
                )
            )


QN_REGION_RE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)\s*->\s*(?P<b>\d+(?:\.\d+)?)\s*qn",
    re.IGNORECASE,
)


def _regions_compatible(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return True
    if left == right:
        return True
    if left in right or right in left:
        return True
    left_qn = QN_REGION_RE.search(str(left))
    right_qn = QN_REGION_RE.search(str(right))
    if left_qn and right_qn:
        return (
            left_qn.group("a") == right_qn.group("a")
            and left_qn.group("b") == right_qn.group("b")
        )
    return True


def _check_stale_and_identity(
    output: ReasoningOutput,
    pack: EvidencePack,
    nodes: dict[str, dict],
    issues: list[ValidationIssue],
) -> None:
    entities = pack.entity_by_id()
    pack_region = pack.region
    for ref in _cited_ids(output):
        node = nodes.get(ref)
        item = pack.by_id().get(ref)
        validity = str((node or {}).get("validity") or "VALID")
        if validity in {"STALE", "REJECTED"}:
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    f"fact derived from stale evidence: {ref}",
                )
            )
        region = (node or {}).get("region") or (None if item is None else item.region)
        if region and not _regions_compatible(str(region), pack_region):
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    f"region mismatch for {ref}: {region} vs {pack_region}",
                )
            )
        subject = str((node or {}).get("subject_identity") or "")
        if subject in entities:
            declared = set(output.entity_refs)
            for hypo in output.hypotheses:
                declared.update(hypo.entity_refs)
            if declared and subject not in declared:
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"subject mismatch for {ref}: {subject}",
                    )
                )


def _check_fusion_contradictions(
    output: ReasoningOutput,
    scoped: dict,
    issues: list[ValidationIssue],
) -> None:
    contradicting = set(output.contradicting_evidence_refs)
    for hypo in output.hypotheses:
        contradicting.update(hypo.contradicting_evidence_refs)
    for fusion in fusion_statuses(scoped):
        status = str(fusion.get("status") or "")
        node_ids = [str(item) for item in (fusion.get("node_ids") or [])]
        if status == "CONTRADICT":
            missing = [item for item in node_ids if item not in contradicting]
            if output.status is DiagnosisStatus.SUPPORTED:
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        "CONTRADICT fusion cannot be treated as a supported fact",
                    )
                )
            if missing and output.confidence is Confidence.HIGH:
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"HIGH confidence ignored contradictory evidence {missing}",
                    )
                )
            if missing:
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        f"unresolved contradiction not surfaced: {missing}",
                    )
                )


def _check_status_and_requests(
    output: ReasoningOutput,
    issues: list[ValidationIssue],
) -> None:
    if output.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE:
        if not output.requested_evidence:
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    "INSUFFICIENT_EVIDENCE requires a minimal next EvidenceRequest",
                )
            )
        for request in output.requested_evidence:
            blob = f"{request.why_needed} {request.expected_information_gain} {request.goal}"
            if is_overbroad_request(blob) or is_overbroad_request(request.target):
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        "next evidence request is over-broad",
                    )
                )
            if not request.target or not request.region or not request.why_needed:
                issues.append(
                    ValidationIssue(
                        ReasoningFailure.LLM_GROUNDING_VIOLATION,
                        "next evidence request missing target/region/reason",
                    )
                )
    if (
        output.status is DiagnosisStatus.NO_ACTION_REQUIRED
        and output.category is FindingType.INSUFFICIENT_EVIDENCE
        and not output.candidate_actions
    ):
        issues.append(
            ValidationIssue(
                ReasoningFailure.LLM_GROUNDING_VIOLATION,
                "NO_ACTION_REQUIRED is not INSUFFICIENT_EVIDENCE",
            )
        )


def _check_dsp_discipline(
    output: ReasoningOutput,
    pack: EvidencePack,
    scoped: dict,
    issues: list[ValidationIssue],
) -> None:
    text = " ".join(_text_blobs(output))
    codes = set(limitation_codes_from_scoped(pack, scoped))
    values = []
    for item in pack.items:
        values.append(str(item.value))
        values.append(item.name)
    blob_values = " ".join(values)
    if "POTENTIAL_OVERLAP" in blob_values or "POTENTIAL_OVERLAP_IS_MEASURED_RELATIONSHIP" in codes:
        if muddy_as_measurement(text) and output.status is DiagnosisStatus.SUPPORTED:
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    "POTENTIAL_OVERLAP cannot become muddy-as-measurement",
                )
            )
    if "CAPTURE_FAILED" in codes and capture_failed_as_silence(text):
        issues.append(
            ValidationIssue(
                ReasoningFailure.LLM_GROUNDING_VIOLATION,
                "CAPTURE_FAILED is not measured silence",
            )
        )
    if "KEY_IS_CANDIDATE_SET_NOT_CERTAIN" in codes and certain_key_claim(text):
        issues.append(
            ValidationIssue(
                ReasoningFailure.LLM_GROUNDING_VIOLATION,
                "KEY_IS_CANDIDATE_SET_NOT_CERTAIN forbids a certain key",
            )
        )
    if ungrounded_device_cause(text):
        issues.append(
            ValidationIssue(
                ReasoningFailure.LLM_GROUNDING_VIOLATION,
                "ungrounded device-causal measurement",
            )
        )


def _check_confidence_contract(
    output: ReasoningOutput,
    issues: list[ValidationIssue],
) -> None:
    for blob in _text_blobs(output):
        if invented_percentage(blob):
            issues.append(
                ValidationIssue(
                    ReasoningFailure.LLM_GROUNDING_VIOLATION,
                    "combined confidence percentage is forbidden",
                )
            )
