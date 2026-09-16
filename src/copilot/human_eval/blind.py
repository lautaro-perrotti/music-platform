from __future__ import annotations

from copilot.human_eval.schema import BlindMetadata, EvalCase

HIDDEN_PUBLIC_KEYS = {
    "blind_metadata",
    "region",
    "song_id",
    "project_id",
    "dsp",
    "source_activity",
    "astra",
    "expected_labels",
    "candidate_actions",
    "fixture_metadata",
    "genre",
    "diagnostic_family",
    "section",
    "source_configuration",
}


def public_case(case: EvalCase, *, index: int, total: int, remaining: int) -> dict:
    """Labeling payload. Server-side blind: no DSP / Astra / expected labels."""
    stimulus = case.stimulus.model_dump(mode="json")
    for asset in stimulus.get("assets") or []:
        asset.pop("path", None)
    draft = None if case.draft is None else case.draft.model_dump(mode="json")
    response = None if case.response is None else case.response.model_dump(mode="json")
    return {
        "case_id": case.case_id,
        "evaluation_run_id": case.evaluation_run_id,
        "audio_asset_id": case.audio_asset_id,
        "audio_sha256": case.audio_sha256,
        "duration_s": case.duration_s,
        "status": case.status.value,
        "presentation_order": case.presentation_order,
        "index": index,
        "total": total,
        "remaining": remaining,
        "stimulus_kind": case.stimulus.kind.value,
        "audio_url": f"/api/runs/{case.evaluation_run_id}/cases/{case.case_id}/audio",
        "draft": draft,
        "response": response if case.status.value in {"COMPLETED", "LOCKED"} else None,
        "locked": case.status.value == "LOCKED",
        "schema_version": "human-eval-1",
        "skip_reason": None if case.skip_reason is None else case.skip_reason.value,
    }


def assert_blind(payload: dict) -> None:
    leaked = sorted(set(payload) & HIDDEN_PUBLIC_KEYS)
    if leaked:
        raise ValueError(f"blind payload leaked {leaked}")
    nested = json_walk_keys(payload)
    forbidden = {
        "dsp",
        "source_activity",
        "astra",
        "expected_labels",
        "candidate_actions",
        "fixture_metadata",
        "genre",
        "MusicDiagnosis",
        "MusicPlan",
    }
    hit = sorted(set(nested) & forbidden)
    if hit:
        raise ValueError(f"blind payload nested leak {hit}")


def json_walk_keys(value: object, acc: set[str] | None = None) -> set[str]:
    acc = acc if acc is not None else set()
    if isinstance(value, dict):
        for key, item in value.items():
            acc.add(str(key))
            json_walk_keys(item, acc)
    elif isinstance(value, list):
        for item in value:
            json_walk_keys(item, acc)
    return acc


def strip_to_blind_metadata(raw: dict) -> BlindMetadata:
    return BlindMetadata.model_validate(
        {key: raw[key] for key in BlindMetadata.model_fields if key in raw}
    )
