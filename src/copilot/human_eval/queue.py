from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import random

import soundfile as sf

from copilot.audio.file_hash import sha256_file
from copilot.human_eval.schema import (
    AudioRef,
    CaseStatus,
    EvalCase,
    EvalMode,
    EvalRun,
    EvaluationStimulus,
    BlindMetadata,
    StimulusKind,
)
from copilot.human_eval.store import EvalStore, now_iso

DEFAULT_SEED = 20260915


def _section_label(row: dict[str, Any]) -> str | None:
    start = row.get("start_qn")
    end = row.get("end_qn")
    if start is None or end is None:
        return None
    return f"{start}->{end}qn"


def _duration_s(path: Path) -> float:
    info = sf.info(str(path))
    if info.samplerate:
        return float(info.frames) / float(info.samplerate)
    return 0.0


def _case_id(run_id: str, region: str, audio_sha: str) -> str:
    raw = f"{run_id}:{region}:{audio_sha}"
    return "case_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def create_run_from_session(
    source_run: str = "session_run1",
    *,
    evidence: Path | None = None,
    store: EvalStore | None = None,
    seed: int = DEFAULT_SEED,
    default_mode: EvalMode = EvalMode.QUICK,
) -> EvalRun:
    store = store or EvalStore()
    evidence = Path(evidence or "logs")
    report_path = evidence / f"{source_run}.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"session report missing: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    listen = list(report.get("HUMAN LISTEN MAIN") or [])
    if not listen:
        raise ValueError("HUMAN LISTEN MAIN missing; cannot create eval queue")
    captures: dict[str, dict[str, Any]] = {}
    for row in report.get("CAPTURE ASSETS") or []:
        region_val = row.get("region")
        if isinstance(region_val, dict):
            rid = str(region_val.get("id") or "")
        else:
            rid = str(region_val or "")
        if rid:
            captures[rid] = row
    tokens = report.get("STATE TOKENS") or {}
    preflight = report.get("PRE-FLIGHT") or {}
    project_token = str(tokens.get("project_token") or preflight.get("project_token") or "")
    project_id = str(preflight.get("live_set_path") or "")
    song_id = Path(project_id or "pista").stem
    run_id = source_run
    created = now_iso()
    items: list[EvalCase] = []
    for row in listen:
        region = str(row["region"])
        path = Path(str(row["path"]))
        if not path.is_file():
            raise FileNotFoundError(f"listen wav missing: {path}")
        digest = sha256_file(path)
        if not digest:
            raise ValueError(f"audio hash missing: {path}")
        duration = _duration_s(path)
        cap = captures.get(region) or {}
        case_id = _case_id(run_id, region, digest)
        stimulus = EvaluationStimulus(
            kind=StimulusKind.SINGLE,
            assets=[
                AudioRef(
                    audio_asset_id=f"{region}_main",
                    path=str(path),
                    audio_sha256=digest,
                    duration_s=duration,
                    role="main",
                )
            ],
        )
        items.append(
            EvalCase(
                case_id=case_id,
                evaluation_run_id=run_id,
                project_id=project_id,
                audio_asset_id=f"{region}_main",
                audio_sha256=digest,
                region=region,
                song_id=song_id,
                duration_s=duration,
                stimulus=stimulus,
                blind_metadata=BlindMetadata(
                    region=region,
                    song_id=song_id,
                    project_id=project_id,
                    source_run=source_run,
                    section=_section_label(row),
                    expected_labels={},
                    dsp={},
                    source_activity=dict(cap.get("source_class") or {}),
                    astra={},
                    candidate_actions=[],
                    fixture_metadata={},
                ),
                status=CaseStatus.PENDING,
                created_at=created,
                updated_at=created,
            )
        )
    rng = random.Random(int(seed))
    order = list(range(len(items)))
    rng.shuffle(order)
    for presentation, source_index in enumerate(order):
        items[source_index].presentation_order = presentation
    ordered = sorted(items, key=lambda item: item.presentation_order)
    run = EvalRun(
        evaluation_run_id=run_id,
        source_run=source_run,
        project_id=project_id,
        project_token=project_token,
        seed=int(seed),
        default_mode=default_mode,
        comparison_requires_human_gt=True,
        case_ids=[case.case_id for case in ordered],
        created_at=created,
        updated_at=created,
        musical_writes=0,
        astra_calls=0,
    )
    existing = store.load_run(run_id)
    if existing is not None:
        # Keep answers only when case identity (run + region + audio hash) matches.
        old_by_id = {
            case.case_id: case
            for case in store.load_cases(run_id)
        }
        merged = []
        for case in ordered:
            prev = old_by_id.get(case.case_id)
            if prev is not None:
                prev.presentation_order = case.presentation_order
                prev.stimulus = case.stimulus
                prev.audio_sha256 = case.audio_sha256
                merged.append(prev)
            else:
                merged.append(case)
        ordered = sorted(merged, key=lambda item: item.presentation_order)
        run.case_ids = [case.case_id for case in ordered]
        run.created_at = existing.created_at
        run.locked_at = existing.locked_at
    store.save_run(run)
    for case in ordered:
        store.save_case(case)
    store.append_session(run_id, {"event": "queue_created", "seed": seed, "n": len(ordered)})
    return store.load_run(run_id) or run


def run_status(run_id: str, *, store: EvalStore | None = None) -> dict[str, Any]:
    store = store or EvalStore()
    run = store.load_run(run_id)
    if run is None:
        return {"ok": False, "evaluation_run_id": run_id, "error": "missing"}
    counts = store.counts(run_id)
    return {
        "ok": True,
        "evaluation_run_id": run_id,
        "source_run": run.source_run,
        "seed": run.seed,
        "default_mode": run.default_mode.value,
        "human_label_mode": run.human_label_mode,
        "locked_at": run.locked_at,
        "comparison_requires_human_gt": run.comparison_requires_human_gt,
        "queue_size": counts["total"],
        "completed": counts["labeled"],
        "skipped": counts["skipped"],
        "pending": counts["pending"],
        "locked": counts["locked"],
        "all_required_locked": store.all_required_locked(run_id),
        "ASTRA CALLS": run.astra_calls,
        "MUSICAL WRITES": run.musical_writes,
    }


def export_run(run_id: str, *, store: EvalStore | None = None) -> list[dict[str, Any]]:
    store = store or EvalStore()
    run = store.load_run(run_id)
    if run is None:
        raise FileNotFoundError(run_id)
    rows = []
    for case in store.load_cases(run_id):
        rows.append(
            {
                "schema_version": run.schema_version,
                "evaluation_run_id": run_id,
                "case_id": case.case_id,
                "status": case.status.value,
                "presentation_order": case.presentation_order,
                "region": case.region,
                "song_id": case.song_id,
                "project_id": case.project_id,
                "audio_asset_id": case.audio_asset_id,
                "audio_sha256": case.audio_sha256,
                "duration_s": case.duration_s,
                "stimulus": case.stimulus.model_dump(mode="json"),
                "response": None if case.response is None else case.response.model_dump(mode="json"),
                "skip_reason": None if case.skip_reason is None else case.skip_reason.value,
                "skip_notes": case.skip_notes,
                "human_label_hash": case.human_label_hash,
                "created_at": case.created_at,
                "completed_at": case.completed_at,
                "locked_at": case.locked_at,
                "revisions": [item.model_dump(mode="json") for item in case.revisions],
                "human_label_mode": run.human_label_mode,
                "seed": run.seed,
            }
        )
    return rows
