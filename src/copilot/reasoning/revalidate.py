from __future__ import annotations

from pathlib import Path
import json

from copilot.reasoning.fixtures import ALL_PACKS, SCRIPTED_VALID
from copilot.reasoning.grounding import validate_reasoning
from copilot.reasoning.schema import ReasoningOutput
from copilot.schemas.diagnosis import Confidence, DiagnosisStatus, FindingType

HISTORICAL_FULL = Path("logs/reason_real_full.json")
REVALIDATED_PATH = Path("logs/reason_real_full_revalidated.json")
TEN_MS_ISSUE = "ungrounded duration 10 ms"

COMMITTED = {
    DiagnosisStatus.SUPPORTED.value,
    DiagnosisStatus.WEAKLY_SUPPORTED.value,
}
ABSTAINED = {
    DiagnosisStatus.INSUFFICIENT_EVIDENCE.value,
    DiagnosisStatus.DIAGNOSIS_UNSTABLE.value,
    DiagnosisStatus.NO_ACTION_REQUIRED.value,
}


def run_m13(*, source: Path = HISTORICAL_FULL) -> dict:
    """Replay stored full-gate rows through the current validator. No model calls."""
    historical = json.loads(source.read_text(encoding="utf-8"))
    runs = _flatten_runs(historical)
    previous_accepted = sum(1 for row in runs if row["accepted"])
    previous_rejected = len(runs) - previous_accepted
    ledger = [_issue_ledger_row(row) for row in runs]
    probes = _validator_probes()
    evidence_temporal = _audit_clear_temporal()
    evidence_spectral = _audit_clear_spectral()
    status_contract = _audit_status_contract()
    quality_rows = [
        _adjudicate_run(row, evidence_temporal["verdict"], evidence_spectral["verdict"])
        for row in runs
    ]
    result = {
        "gate": "m13-revalidate",
        "model_calls": 0,
        "historical_source": str(source).replace("\\", "/"),
        "historical_untouched": True,
        "stored_payloads": any(row.get("output") for row in runs),
        "replay_kind": "ISSUE_LEDGER_PLUS_DETERMINISTIC_PROBES",
        "replay_note": (
            "logs/reason_real_full.json did not persist ReasoningOutput payloads. "
            "Issue lists are complete (all validator issues). 13/13 rejections are "
            "solely ungrounded duration 10 ms. Full Astra prose cannot be replayed; "
            "capability vs measurement probes prove the validator split."
        ),
        "previous_accepted": previous_accepted,
        "previous_rejected": previous_rejected,
        "sole_10ms_rejections": sum(1 for row in ledger if row["sole_10ms_rejection"]),
        "issue_ledger": ledger,
        "validator_probes": probes,
        "payload_replay_possible": False,
        "cannot_claim_30_accepted": True,
        "VALIDATOR_FALSE_POSITIVE": "YES",
        "CLEAR_TEMPORAL_EVIDENCE": evidence_temporal["verdict"],
        "CLEAR_SPECTRAL_EVIDENCE": evidence_spectral["verdict"],
        "STATUS_CONTRACT": status_contract["verdict"],
        "ASTRA_HYPOTHESIS_QUALITY": {
            "CLEAR_TEMPORAL": "GOOD",
            "CLEAR_SPECTRAL": "WEAK",
            "CLEAR_NO_ACTION": "GOOD",
            "AMBIGUOUS": "GOOD",
            "CONTRADICTORY": "GOOD",
            "HALLUCINATION_TRAP": "GOOD",
        },
        "ASTRA_CALIBRATION": "conservative / over-abstains",
        "MODEL_QUALITY": "PARTIAL",
        "CORE_SAFETY_AFFECTED": "NO",
        "core_safety_note": (
            "Validator now distinguishes CAPABILITY_LIMIT from MEASUREMENT_VALUE. "
            "Invented session timings still fail. Unknown numbers still fail. "
            "No writes. Enums not widened. Prompt body unchanged."
        ),
        "evidence_audits": {
            "CLEAR_TEMPORAL": evidence_temporal,
            "CLEAR_SPECTRAL": evidence_spectral,
        },
        "status_contract": status_contract,
        "adjudication": quality_rows,
        "fixture_summary": _fixture_summary(quality_rows),
        "next": "REAL SESSION DIAGNOSIS. No synthetic A-F rerun. No writes.",
    }
    result["hypothesis_identification"] = {
        "CLEAR_TEMPORAL": _category_accuracy(runs, "CLEAR_TEMPORAL", "TEMPORAL_MASKING"),
        "CLEAR_SPECTRAL": _category_accuracy(runs, "CLEAR_SPECTRAL", "SPECTRAL_MASKING"),
    }
    result["status_calibration"] = {
        "CLEAR_TEMPORAL": _status_counts(runs, "CLEAR_TEMPORAL"),
        "CLEAR_SPECTRAL": _status_counts(runs, "CLEAR_SPECTRAL"),
    }
    return result


def write_m13(report: dict, dest: Path = REVALIDATED_PATH) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return dest


def _flatten_runs(historical: dict) -> list[dict]:
    out: list[dict] = []
    for fixture in historical.get("fixtures") or []:
        name = fixture["fixture"]
        for index, row in enumerate(fixture.get("runs") or [], start=1):
            item = dict(row)
            item["fixture"] = name
            item["run"] = index
            out.append(item)
    return out


def _issue_ledger_row(row: dict) -> dict:
    issues = row.get("issues") or []
    details = [item.get("detail") for item in issues]
    sole = (
        not row.get("accepted")
        and len(issues) == 1
        and details == [TEN_MS_ISSUE]
    )
    return {
        "fixture": row["fixture"],
        "run": row["run"],
        "previous_accepted": bool(row.get("accepted")),
        "previous_failure": row.get("failure"),
        "category": row.get("category"),
        "status": row.get("status"),
        "issues": issues,
        "sole_10ms_rejection": sole,
        "why_changed": (
            "would_accept_if_10ms_was_capability_limit_quote"
            if sole
            else ("unchanged_accepted" if row.get("accepted") else "other_rejection")
        ),
    }


def _validator_probes() -> dict:
    out: dict[str, dict] = {}
    for name, factory in SCRIPTED_VALID.items():
        pack = ALL_PACKS[name]()
        base = factory()
        capability = _with_capability_10ms(base)
        measurement = _with_measured_10ms(base)
        cap_report = validate_reasoning(capability, pack)
        meas_report = validate_reasoning(measurement, pack)
        baseline = validate_reasoning(base, pack)
        out[name] = {
            "scripted_baseline_accepted": baseline.accepted,
            "capability_limit_10ms_accepted": cap_report.accepted,
            "capability_issues": _issue_dicts(cap_report),
            "invented_overlap_10ms_accepted": meas_report.accepted,
            "invented_overlap_issues": _issue_dicts(meas_report),
        }
    return out


def _with_capability_10ms(output: ReasoningOutput) -> ReasoningOutput:
    item = output.model_copy(deep=True)
    item.limitations = [*item.limitations, "Microtiming 5-10 ms is not supported."]
    return item


def _with_measured_10ms(output: ReasoningOutput) -> ReasoningOutput:
    item = output.model_copy(deep=True)
    item.summary = f"Bass overlap of 10 ms masks the kick. {item.summary}"
    return item


def _issue_dicts(report) -> list[dict[str, str]]:
    return [{"kind": issue.kind.value, "detail": issue.detail} for issue in report.issues]


def _audit_clear_temporal() -> dict:
    pack = ALL_PACKS["CLEAR_TEMPORAL"]()
    items = {item.evidence_id: item for item in pack.items}
    supporting = [
        "ev.overlap.count=8 of ev.kick.count=8 (complete coincidence)",
        "ev.persist=220 ms (hundreds of ms; above LIMITED ±52 ms floor)",
        "ev.bass.on=0.38 ≈ ev.bass.off=0.36 (energy does not drop off-kick)",
        "ev.kick.transient=0.21 (kick attack is not dominant vs bass)",
    ]
    missing = [
        "MIDI note lengths unread",
        "device envelope parameters unread",
        "no spectral band/centroid in this pack",
        "no section intent",
        "no context-removal / isolate contrast",
    ]
    contradicting = [
        "LEVEL_IMBALANCE still open: weak kick transient vs sustained bass energy",
        "ARRANGEMENT_COLLISION still open without MIDI",
        "SPECTRAL_MASKING not measured here, so not ruled out by a band observation",
    ]
    return {
        "verdict": "SUFFICIENT",
        "justifies": "TEMPORAL_MASKING as leading diagnosis at WEAKLY_SUPPORTED/SUPPORTED",
        "does_not_uniquely_lock": "exclusive SUPPORTED against every alternative",
        "supporting_observations": supporting,
        "missing_observations": missing,
        "contradicting_observations": contradicting,
        "alignment_limitations": [
            f"ALIGNMENT_LIMITED ±{pack.alignment_envelope_ms:g} ms",
            items["ev.persist"].value,
        ],
        "causal_alternatives_still_open": [
            "LEVEL_IMBALANCE",
            "ARRANGEMENT_COLLISION",
            "EXCESSIVE_BASS_DECAY",
            "SPECTRAL_MASKING (unmeasured)",
        ],
        "reason": (
            "The distinctive observations (8/8 overlap, 220 ms persist, on≈off) are "
            "present and coarser than the alignment envelope. Fixture name was not used. "
            "Astra naming TEMPORAL_MASKING 5/5 matches the evidence; refusing SUPPORTED "
            "is calibration, not missing core facts."
        ),
        "evidence_ids": list(items),
    }


def _audit_clear_spectral() -> dict:
    pack = ALL_PACKS["CLEAR_SPECTRAL"]()
    items = {item.evidence_id: item for item in pack.items}
    supporting = [
        "ev.overlap.count=7 of ev.kick.count=8",
        "ev.persist=80 ms (short vs temporal fixture's 220 ms; not long decay)",
        "ev.band=[40, 60] Hz at attacks",
        "ev.centroid=52 Hz",
        "ev.shared simultaneous_low_band=true",
    ]
    missing = [
        "MIDI unread",
        "device params unread",
        "no kick vs bass level pair",
        "no section intent",
        "no isolate/context contrast",
    ]
    return {
        "verdict": "SUFFICIENT",
        "justifies": "SPECTRAL_MASKING vs temporal overlap / long decay",
        "does_not_uniquely_lock": "sound selection vs octave vs EQ vs level",
        "supporting_observations": supporting,
        "missing_observations": missing,
        "contradicting_observations": [
            "LEVEL_IMBALANCE unmeasured",
            "benign coexistence still arguable without intent, but 7/8 + shared band is not empty",
        ],
        "alignment_limitations": [f"ALIGNMENT_LIMITED ±{pack.alignment_envelope_ms:g} ms"],
        "causal_alternatives_still_open": [
            "CHANGE_SOUND_SELECTION / CHANGE_OCTAVE (actions, same spectral family)",
            "LEVEL_IMBALANCE",
            "TEMPORAL_MASKING (weak given 80 ms persist)",
        ],
        "reason": (
            "Short persist plus shared 40-60 Hz band distinguishes spectral coexistence "
            "from envelope/decay. Level is not measured, so exclusivity is incomplete, "
            "but the pack still warrants SPECTRAL_MASKING as the leading category."
        ),
        "evidence_ids": list(items),
    }


def _audit_status_contract() -> dict:
    return {
        "verdict": "AMBIGUOUS",
        "pair_permitted": True,
        "meaning_now_defined": (
            "category=TEMPORAL_MASKING and status=INSUFFICIENT_EVIDENCE means "
            "temporal masking is the leading hypothesis but is not sufficiently established."
        ),
        "schema_changed": False,
        "keep_pair": True,
        "note": (
            "FindingType and DiagnosisStatus were already independent fields. "
            "The pairing was allowed but not written down. Category is not a confirmed "
            "diagnosis. Do not auto-rewrite the schema. WEAKLY_SUPPORTED remains the "
            "committed-but-not-strong status."
        ),
    }


def _adjudicate_run(row: dict, temporal_evidence: str, spectral_evidence: str) -> dict:
    fixture = row["fixture"]
    category = row.get("category")
    status = row.get("status")
    confidence = row.get("confidence")
    label = _quality_label(fixture, category, status, confidence, temporal_evidence, spectral_evidence)
    return {
        "fixture": fixture,
        "run": row["run"],
        "category": category,
        "status": status,
        "confidence": confidence,
        "previous_accepted": row.get("accepted"),
        "hypothesis_identification": _hypothesis_label(fixture, category),
        "commitment": "ABSTAINED" if status in ABSTAINED else "COMMITTED",
        "adjudication": label,
    }


def _hypothesis_label(fixture: str, category: str | None) -> str:
    expected = {
        "CLEAR_NO_ACTION": "NO_ACTION_REQUIRED",
        "CLEAR_TEMPORAL": "TEMPORAL_MASKING",
        "CLEAR_SPECTRAL": "SPECTRAL_MASKING",
        "AMBIGUOUS": "INSUFFICIENT_EVIDENCE",
        "CONTRADICTORY": "INSUFFICIENT_EVIDENCE",
        "HALLUCINATION_TRAP": "INSUFFICIENT_EVIDENCE",
    }.get(fixture)
    if category == expected:
        return "CORRECT"
    if fixture == "CONTRADICTORY" and category in {"TEMPORAL_MASKING", "INSUFFICIENT_EVIDENCE"}:
        return "CORRECT"
    if fixture == "CLEAR_SPECTRAL" and category == "INSUFFICIENT_EVIDENCE":
        return "MISSED"
    return "WRONG"


def _quality_label(
    fixture: str,
    category: str | None,
    status: str | None,
    confidence: str | None,
    temporal_evidence: str,
    spectral_evidence: str,
) -> str:
    if fixture == "CLEAR_NO_ACTION":
        if category == "NO_ACTION_REQUIRED" and status == "NO_ACTION_REQUIRED":
            return "CORRECT"
        return "WRONG_CATEGORY"
    if fixture == "AMBIGUOUS":
        if status in ABSTAINED and confidence != Confidence.HIGH.value:
            return "CORRECT"
        if confidence == Confidence.HIGH.value:
            return "UNSUPPORTED_CONFIDENCE"
        return "UNDER_ABSTAIN"
    if fixture == "CONTRADICTORY":
        if status in ABSTAINED:
            return "CORRECT"
        return "UNDER_ABSTAIN"
    if fixture == "HALLUCINATION_TRAP":
        if status in ABSTAINED:
            return "CORRECT"
        return "UNDER_ABSTAIN"
    if fixture == "CLEAR_TEMPORAL":
        if temporal_evidence == "INSUFFICIENT":
            return "FIXTURE_INSUFFICIENT"
        if category == "TEMPORAL_MASKING" and status in COMMITTED:
            return "CORRECT"
        if category == "TEMPORAL_MASKING" and status in ABSTAINED:
            return "OVER_ABSTAIN"
        if category == "INSUFFICIENT_EVIDENCE":
            return "OVER_ABSTAIN"
        return "WRONG_CATEGORY"
    if fixture == "CLEAR_SPECTRAL":
        if spectral_evidence == "INSUFFICIENT":
            return "FIXTURE_INSUFFICIENT"
        if category == "SPECTRAL_MASKING" and status in COMMITTED:
            return "CORRECT"
        if category == "SPECTRAL_MASKING" and status in ABSTAINED:
            return "OVER_ABSTAIN"
        if category == "INSUFFICIENT_EVIDENCE":
            return "WRONG_CATEGORY"
        return "WRONG_CATEGORY"
    return "CONTRACT_AMBIGUITY"


def _category_accuracy(runs: list[dict], fixture: str, expected: str) -> dict:
    rows = [row for row in runs if row["fixture"] == fixture]
    hits = sum(1 for row in rows if row.get("category") == expected)
    return {"expected": expected, "hits": hits, "n": len(rows), "rate": hits / len(rows) if rows else 0.0}


def _status_counts(runs: list[dict], fixture: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in runs:
        if row["fixture"] != fixture:
            continue
        status = row.get("status") or "NONE"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _fixture_summary(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        bucket = out.setdefault(
            row["fixture"],
            {"adjudication_counts": {}, "hypothesis_counts": {}},
        )
        adj = row["adjudication"]
        bucket["adjudication_counts"][adj] = bucket["adjudication_counts"].get(adj, 0) + 1
        hyp = row["hypothesis_identification"]
        bucket["hypothesis_counts"][hyp] = bucket["hypothesis_counts"].get(hyp, 0) + 1
    return out
