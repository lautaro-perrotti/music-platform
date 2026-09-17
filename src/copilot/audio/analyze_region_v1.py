"""ANALYZE_REGION_V1 — arrangement context for AUTO_36_68. No extra MIDI notes."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.audio.astra_external_reasoning_v1 import (
    RecordingProvider,
    classify_rejection,
    load_persisted_pack,
    pack_payload_hash,
)
from copilot.audio.file_hash import sha256_file
from copilot.audio.midi_read_only_v1 import (
    NEAR_SILENT_EVENT_KINDS,
    identity_for_als_path,
    load_persisted_ref,
    locate_working_copy_als,
    pack_project_identity,
    pack_region,
    project_mismatch_still_fails_closed,
    _als_device_inventory,
    _attr_value,
    _clip_span,
    _is_arrangement_clip,
    _load_als_root,
    _local,
    _owning_track,
    _parent_map,
    _track_locator_name,
    _truthy,
)
from copilot.audio.producer_analyze_v1 import apply_reasoning_result
from copilot.audio.tap_trust import JOURNAL_DIR
from copilot.daw.object_ref import PersistentObjectRef, ResolveStatus, resolve_track
from copilot.human_eval.store import now_iso
from copilot.reasoning.pipeline import _parse_output, reason
from copilot.reasoning.provider import ReasoningProvider, configured_http_provider
from copilot.reasoning.session_astra import ASTRA_TIMEOUT_S
from copilot.schemas.diagnosis import DiagnosisStatus
from copilot.schemas.evidence import (
    CaptureQuality,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)
from copilot.schemas.session import SessionState

MILESTONE = "ANALYZE_REGION_V1"
ARTIFACT = "analyze_region_v1.json"
STATUS = "IMPLEMENTED"
ANALYSIS_VERSION = "analyze-region-v1"
EXPECTED_PARENT_PACK_ID = "pack_03fccd526f2c"
PARENT_PACK_PATH = Path("logs") / "midi_read_only_v1.json"
CLIP_TAGS = {"MidiClip", "AudioClip"}
FORBIDDEN_REGION_JUDGMENT_RE = re.compile(
    r"\b(the break is too empty|the drop lacks energy|the arrangement is bad|"
    r"tech house should be denser|too empty|lacks energy)\b",
    re.IGNORECASE,
)
TIME_BASIS = (
    "Arrangement clip CurrentStart/CurrentEnd are qn from the saved ALS. "
    "Main energy events are Main-local seconds mapped with ALS tempo: "
    "arrangement_qn = region_start_qn + t_s * tempo_bpm / 60. "
    "Those two provenances are not sample-aligned. Cross-source remains LIMITED ±52 ms."
)

TARGET_SOURCE_EXPECTED = "TARGET_SOURCE_EXPECTED"
TARGET_SOURCE_NOT_EXPECTED = "TARGET_SOURCE_NOT_EXPECTED"
ARRANGEMENT_GAP = "ARRANGEMENT_GAP"
ACTIVITY_CHANGE_NEAR_EVENT = "ACTIVITY_CHANGE_NEAR_EVENT"
NO_OBVIOUS_ARRANGEMENT_CHANGE = "NO_OBVIOUS_ARRANGEMENT_CHANGE"
CONTEXT_INCOMPLETE = "CONTEXT_INCOMPLETE"
ROLE_UNKNOWN = "UNKNOWN"


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def track_speaker_on(track: Any) -> bool:
    for elem in track.iter():
        if _local(elem.tag) != "Mixer":
            continue
        for field in elem:
            if _local(field.tag) == "Speaker":
                return _truthy(_attr_value(field, "Manual"), default=True)
        break
    return True


def load_journal_refs(*, journal_dir: Path | None = None) -> dict[str, PersistentObjectRef]:
    directory = Path(journal_dir or JOURNAL_DIR)
    found: dict[str, PersistentObjectRef] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            raw = row.get("ref")
            if not isinstance(raw, dict) or not raw.get("content_fingerprint"):
                continue
            ref = PersistentObjectRef.model_validate(raw)
            found.setdefault(ref.content_fingerprint, ref)
    return found


def established_roles(pack: EvidencePack) -> dict[str, str]:
    return {
        entity.entity_id: (entity.role or ROLE_UNKNOWN)
        for entity in pack.entities
        if entity.entity_id
    }


def target_source_id(pack: EvidencePack) -> str:
    item = pack.by_id().get("midi.source.entity_id")
    if item is not None and item.value:
        return str(item.value)
    item = pack.by_id().get("midi.source.content_fingerprint")
    if item is not None and item.value:
        return str(item.value)
    return ""


def target_clip_span(pack: EvidencePack) -> tuple[float, float] | None:
    item = pack.by_id().get("midi.clip.0")
    if item is None or not isinstance(item.value, dict):
        start_item = pack.by_id().get("midi.clip.0.arrangement_start_qn")
        end_item = pack.by_id().get("midi.clip.0.arrangement_end_qn")
        if start_item is None or end_item is None:
            return None
        return float(start_item.value), float(end_item.value)
    return (
        float(item.value["arrangement_start_qn"]),
        float(item.value["arrangement_end_qn"]),
    )


def load_region_events(pack: EvidencePack) -> list[dict[str, Any]]:
    by_id = pack.by_id()
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in pack.items:
        evidence_id = item.evidence_id
        if not evidence_id.startswith("fm.event.") or not evidence_id.endswith(".kind"):
            continue
        prefix = evidence_id[: -len(".kind")]
        if prefix in seen:
            continue
        seen.add(prefix)
        start_item = by_id.get(f"{prefix}.start_s")
        end_item = by_id.get(f"{prefix}.end_s")
        if start_item is None or end_item is None:
            continue
        kind = str(item.value)
        events.append(
            {
                "event_id": prefix,
                "kind": kind,
                "start_s": float(start_item.value),
                "end_s": float(end_item.value),
                "relevant": kind in NEAR_SILENT_EVENT_KINDS,
            }
        )
    events.sort(key=lambda row: float(row["start_s"]))
    return events


def parent_midi_note_ids(pack: EvidencePack) -> set[str]:
    return {
        item.evidence_id
        for item in pack.items
        if item.evidence_id.startswith("midi.note.")
    }


def _ref_matches_devices(ref: PersistentObjectRef, names: list[str], classes: list[str]) -> bool:
    if ref.device_names and not set(ref.device_names).issubset(set(names)):
        return False
    if ref.device_classes and not set(ref.device_classes).issubset(set(classes)):
        return False
    return bool(ref.device_names or ref.device_classes)


def bind_track_identity(
    *,
    names: list[str],
    classes: list[str],
    pack_roles: dict[str, str],
    journal_refs: dict[str, PersistentObjectRef],
    pack_identity: str,
) -> dict[str, Any]:
    hits: list[tuple[str, PersistentObjectRef, str]] = []
    for fingerprint, role in pack_roles.items():
        ref = journal_refs.get(fingerprint)
        if ref is None:
            continue
        if _ref_matches_devices(ref, names, classes):
            hits.append((fingerprint, ref, role or ROLE_UNKNOWN))
    if len(hits) == 1:
        fingerprint, ref, role = hits[0]
        operational = ref.model_copy(update={"project_identity": pack_identity})
        return {
            "role": role,
            "content_fingerprint": fingerprint,
            "persistent_object_ref": operational.model_dump(mode="json"),
            "identity_from": "pack_entity+PersistentObjectRef",
        }
    return {
        "role": ROLE_UNKNOWN,
        "content_fingerprint": None,
        "persistent_object_ref": None,
        "identity_from": "als_locator_only",
    }


def read_arrangement_clips(
    root: Any,
    *,
    region_start: float,
    region_end: float,
    pack_roles: dict[str, str],
    journal_refs: dict[str, PersistentObjectRef],
    pack_identity: str,
) -> list[dict[str, Any]]:
    parents = _parent_map(root)
    rows: list[dict[str, Any]] = []
    track_cache: dict[int, dict[str, Any]] = {}
    for clip in root.iter():
        if _local(clip.tag) not in CLIP_TAGS:
            continue
        if not _is_arrangement_clip(clip, parents):
            continue
        track = _owning_track(clip, parents)
        if track is None:
            continue
        span = _clip_span(clip)
        if span is None:
            continue
        start, end = span
        if _overlap(start, end, region_start, region_end) <= 0:
            continue
        track_id = id(track)
        if track_id not in track_cache:
            names, classes = _als_device_inventory(track)
            bound = bind_track_identity(
                names=names,
                classes=classes,
                pack_roles=pack_roles,
                journal_refs=journal_refs,
                pack_identity=pack_identity,
            )
            track_cache[track_id] = {
                "track_type": _local(track.tag),
                "locator_name": _track_locator_name(track),
                "muted": not track_speaker_on(track),
                "device_names": names,
                "device_classes": classes,
                **bound,
            }
        info = track_cache[track_id]
        clip_name = _attr_value(clip, "Name") or ""
        clip_id = clip.attrib.get("Id") or ""
        rows.append(
            {
                "clip_id": clip_id,
                "clip_name": clip_name,
                "clip_identity": f"clip:{clip_id}:{clip_name}:{start}",
                "kind": _local(clip.tag),
                "arrangement_start_qn": start,
                "arrangement_end_qn": end,
                "muted": _truthy(_attr_value(clip, "Disabled"), default=False),
                "time_basis": "arrangement_qn",
                "track_type": info["track_type"],
                "locator_name": info["locator_name"],
                "track_muted": info["muted"],
                "role": info["role"],
                "content_fingerprint": info["content_fingerprint"],
                "persistent_object_ref": info["persistent_object_ref"],
                "identity_from": info["identity_from"],
            }
        )
    rows.sort(key=lambda row: (float(row["arrangement_start_qn"]), str(row["clip_identity"])))
    return rows


def clip_is_active(clip: dict[str, Any]) -> bool:
    return not clip.get("muted") and not clip.get("track_muted")


def segment_region(
    *,
    region_start: float,
    region_end: float,
    clips: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    marks = {float(region_start), float(region_end)}
    for clip in clips:
        for edge in (float(clip["arrangement_start_qn"]), float(clip["arrangement_end_qn"])):
            if region_start < edge < region_end:
                marks.add(edge)
    times = sorted(marks)
    segments: list[dict[str, Any]] = []
    for idx, start in enumerate(times[:-1]):
        end = times[idx + 1]
        if end <= start:
            continue
        overlapping = [
            clip
            for clip in clips
            if _overlap(float(clip["arrangement_start_qn"]), float(clip["arrangement_end_qn"]), start, end) > 0
        ]
        active = [clip for clip in overlapping if clip_is_active(clip)]
        starts_here = [
            clip
            for clip in clips
            if abs(float(clip["arrangement_start_qn"]) - start) <= 1e-9
        ]
        ends_here = [
            clip
            for clip in clips
            if abs(float(clip["arrangement_end_qn"]) - start) <= 1e-9
        ]
        active_tracks = sorted(
            {
                str(clip.get("content_fingerprint") or clip["locator_name"])
                for clip in active
            }
        )
        segments.append(
            {
                "index": len(segments),
                "start_qn": start,
                "end_qn": end,
                "overlapping_clip_count": len(overlapping),
                "active_clip_count": len(active),
                "active_track_count": len(active_tracks),
                "active_track_keys": active_tracks,
                "clip_starts_at_boundary": [clip["clip_identity"] for clip in starts_here],
                "clip_ends_at_boundary": [clip["clip_identity"] for clip in ends_here],
                "material_starts_at_boundary": [
                    clip["clip_identity"] for clip in starts_here if clip_is_active(clip)
                ],
                "arrangement_gap": len(active) == 0,
                "time_basis": "arrangement_qn",
            }
        )
    return segments


def activity_in_interval(
    clips: list[dict[str, Any]], start: float, end: float
) -> dict[str, Any]:
    overlapping = [
        clip
        for clip in clips
        if _overlap(float(clip["arrangement_start_qn"]), float(clip["arrangement_end_qn"]), start, end) > 0
    ]
    active = [clip for clip in overlapping if clip_is_active(clip)]
    tracks = sorted(
        {str(clip.get("content_fingerprint") or clip["locator_name"]) for clip in active}
    )
    return {
        "overlapping_clip_count": len(overlapping),
        "active_clip_count": len(active),
        "active_track_count": len(tracks),
        "active_clip_identities": [clip["clip_identity"] for clip in active],
        "active_track_keys": tracks,
    }


def boundaries_near(
    clips: list[dict[str, Any]],
    *,
    start_qn: float,
    end_qn: float,
    envelope_qn: float,
) -> list[dict[str, Any]]:
    lo = start_qn - envelope_qn
    hi = end_qn + envelope_qn
    hits: list[dict[str, Any]] = []
    for clip in clips:
        for edge_name, edge in (
            ("start", float(clip["arrangement_start_qn"])),
            ("end", float(clip["arrangement_end_qn"])),
        ):
            if lo <= edge <= hi:
                hits.append(
                    {
                        "clip_identity": clip["clip_identity"],
                        "edge": edge_name,
                        "qn": edge,
                        "material": clip_is_active(clip),
                        "track_muted": bool(clip.get("track_muted")),
                        "clip_muted": bool(clip.get("muted")),
                    }
                )
    hits.sort(key=lambda row: (float(row["qn"]), str(row["edge"])))
    return hits


def classify_event_context(
    *,
    event: dict[str, Any],
    clips: list[dict[str, Any]],
    target_span: tuple[float, float] | None,
    envelope_qn: float,
    tempo_ok: bool,
) -> dict[str, Any]:
    start_qn = event.get("start_qn")
    end_qn = event.get("end_qn")
    labels: list[str] = []
    if not tempo_ok or start_qn is None or end_qn is None:
        return {
            "event_id": event["event_id"],
            "kind": event["kind"],
            "labels": [CONTEXT_INCOMPLETE],
            "active_track_count": None,
            "active_clip_count": None,
            "activity_change_near_event": None,
            "target_source": CONTEXT_INCOMPLETE,
            "arrangement_gap": None,
        }
    activity = activity_in_interval(clips, float(start_qn), float(end_qn))
    near = boundaries_near(
        clips, start_qn=float(start_qn), end_qn=float(end_qn), envelope_qn=envelope_qn
    )
    material_near = [row for row in near if row["material"]]
    if target_span is None:
        target_state = CONTEXT_INCOMPLETE
        labels.append(CONTEXT_INCOMPLETE)
    elif _overlap(float(start_qn), float(end_qn), target_span[0], target_span[1]) > 0:
        target_state = TARGET_SOURCE_EXPECTED
        labels.append(TARGET_SOURCE_EXPECTED)
    else:
        target_state = TARGET_SOURCE_NOT_EXPECTED
        labels.append(TARGET_SOURCE_NOT_EXPECTED)
    labels.extend(["ACTIVE_TRACK_COUNT", "ACTIVE_CLIP_COUNT"])
    if activity["active_track_count"] == 0:
        labels.append(ARRANGEMENT_GAP)
    if near:
        labels.append(ACTIVITY_CHANGE_NEAR_EVENT)
    else:
        labels.append(NO_OBVIOUS_ARRANGEMENT_CHANGE)
    return {
        "event_id": event["event_id"],
        "kind": event["kind"],
        "start_s": event["start_s"],
        "end_s": event["end_s"],
        "start_qn": float(start_qn),
        "end_qn": float(end_qn),
        "active_track_count": activity["active_track_count"],
        "active_clip_count": activity["active_clip_count"],
        "overlapping_clip_count": activity["overlapping_clip_count"],
        "active_clip_identities": activity["active_clip_identities"],
        "clip_boundaries_near_event": near,
        "material_boundaries_near_event": material_near,
        "activity_change_near_event": bool(near),
        "material_activity_change_near_event": bool(material_near),
        "target_source": target_state,
        "arrangement_gap": activity["active_track_count"] == 0,
        "labels": labels,
        "alignment_envelope_qn": envelope_qn,
        "time_basis": "arrangement_qn",
    }


def answer_questions(
    *,
    events: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    clips: list[dict[str, Any]],
    target_id: str,
    period_s: float | None,
    tempo_bpm: float,
    envelope_qn: float,
) -> dict[str, Any]:
    early = [row for row in contexts if row.get("start_qn") is not None and row["start_qn"] < 56.0]
    target_absent = all(
        row.get("target_source") == TARGET_SOURCE_NOT_EXPECTED for row in early
    )
    sparse = [row for row in early if int(row.get("active_track_count") or 0) <= 1]
    multi = [
        row
        for row in early
        if int(row.get("active_track_count") or 0) >= 2
        and row.get("target_source") == TARGET_SOURCE_NOT_EXPECTED
    ]
    question_a = {
        "interval_qn": [36.0, 56.0],
        "target_source_expected_in_interval": (not target_absent) if early else None,
        "only_target_source_absent": bool(multi) and not sparse,
        "some_events_only_target_absent": bool(multi),
        "some_events_sparse_across_sources": bool(sparse),
        "reduced_activity_across_multiple_sources": bool(sparse),
        "active_track_counts": [
            {
                "event_id": row["event_id"],
                "active_track_count": row.get("active_track_count"),
                "target_source": row.get("target_source"),
            }
            for row in early
        ],
        "note": (
            "NO_MIDI on the inspected source is not a whole-mix rest. "
            "Counts are unmuted clips on unmuted tracks."
        ),
    }
    dip = next((row for row in contexts if row.get("event_id") == "fm.event.5"), None)
    question_b = {
        "event_id": None if dip is None else dip["event_id"],
        "active_clip_identities": None if dip is None else dip.get("active_clip_identities"),
        "active_track_count": None if dip is None else dip.get("active_track_count"),
        "clip_boundary_near_event": None if dip is None else dip.get("activity_change_near_event"),
        "material_activity_boundary_near_event": None
        if dip is None
        else dip.get("material_activity_change_near_event"),
        "target_source": None if dip is None else dip.get("target_source"),
        "clip_boundaries_near_event": None if dip is None else dip.get("clip_boundaries_near_event"),
        "note": "MIDI_PRESENT_DURING_INTERVAL does not imply audio should be loud.",
    }
    question_c: dict[str, Any] = {
        "reported": False,
        "compatible": None,
        "note": "No repeating arrangement timing was established within current limitations.",
    }
    if period_s and tempo_bpm > 0:
        period_qn = float(period_s) * (tempo_bpm / 60.0)
        starts = sorted({float(clip["arrangement_start_qn"]) for clip in clips})
        deltas = [b - a for a, b in zip(starts, starts[1:])]
        compatible = any(abs(delta - period_qn) <= envelope_qn for delta in deltas)
        near_silence_at_boundary = False
        ns_events = [
            row
            for row in contexts
            if row.get("kind") == "NEAR_SILENCE" and row.get("start_qn") is not None
        ]
        for event in ns_events:
            for start in starts:
                if abs(start - float(event["start_qn"])) <= envelope_qn:
                    near_silence_at_boundary = True
        question_c = {
            "reported": True,
            "main_near_silence_period_s": float(period_s),
            "period_qn": period_qn,
            "clip_start_qn": starts,
            "clip_start_deltas_qn": deltas,
            "compatible_with_clip_start_grid": compatible,
            "near_silence_events_at_clip_starts": near_silence_at_boundary,
            "limitation": (
                "Timing compatibility only. Not intent. Arrangement qn and Main-local "
                "audio events have different provenance. LIMITED ±52 ms."
            ),
        }
    return {
        "A": question_a,
        "B": question_b,
        "C": question_c,
        "target_source_id": target_id,
        "segment_count": len(segments),
    }


def _assert_factual(payload: Any) -> None:
    blob = json.dumps(payload, ensure_ascii=False)
    hit = FORBIDDEN_REGION_JUDGMENT_RE.search(blob)
    if hit:
        raise ValueError(f"region evidence leaked diagnosis language: {hit.group(0)}")


def _item(
    evidence_id: str,
    name: str,
    value: Any,
    *,
    pack: EvidencePack,
    region: str,
    source_ref: str,
    kind: EvidenceKind = EvidenceKind.FACT,
    unit: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        source_ref=source_ref,
        region=region,
        view="ARRANGEMENT",
        analysis_version=ANALYSIS_VERSION,
        name=name,
        value=value,
        unit=unit,
        quality=CaptureQuality.LIMITED,
        limitations=["MEASURE_ONLY", "ALIGNMENT_LIMITED", "ARRANGEMENT_METADATA_ONLY"],
        project_token=pack.project_token,
        audible_token=pack.audible_token,
        target_token=pack.target_token,
    )


def region_evidence_items(
    *,
    pack: EvidencePack,
    region: dict[str, Any],
    clips: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
    questions: dict[str, Any],
    time_basis: dict[str, Any],
    als_identity: str,
    pack_identity: str,
    extra_midi_notes: int,
) -> list[EvidenceItem]:
    label = str(region["label"])
    source_ref = "arrangement_region"
    items = [
        _item(
            "region.project_identity",
            "region_project_identity",
            pack_identity,
            pack=pack,
            region=label,
            source_ref=source_ref,
            kind=EvidenceKind.STATE_TOKEN,
        ),
        _item(
            "region.id",
            "region_id",
            region["id"],
            pack=pack,
            region=label,
            source_ref=source_ref,
        ),
        _item(
            "region.start_qn",
            "region_start_qn",
            float(region["start_qn"]),
            pack=pack,
            region=label,
            source_ref=source_ref,
            unit="qn",
        ),
        _item(
            "region.end_qn",
            "region_end_qn",
            float(region["end_qn"]),
            pack=pack,
            region=label,
            source_ref=source_ref,
            unit="qn",
        ),
        _item(
            "region.time_basis",
            "region_time_basis",
            time_basis,
            pack=pack,
            region=label,
            source_ref=source_ref,
        ),
        _item(
            "region.als_identity",
            "region_als_identity",
            als_identity,
            pack=pack,
            region=label,
            source_ref=source_ref,
            kind=EvidenceKind.STATE_TOKEN,
        ),
        _item(
            "region.alignment_envelope_ms",
            "region_alignment_envelope_ms",
            float(pack.alignment_envelope_ms),
            pack=pack,
            region=label,
            source_ref=source_ref,
            unit="ms",
        ),
        _item(
            "region.clip_count",
            "region_overlapping_clip_count",
            len(clips),
            pack=pack,
            region=label,
            source_ref=source_ref,
            unit="count",
        ),
        _item(
            "region.segment_count",
            "region_segment_count",
            len(segments),
            pack=pack,
            region=label,
            source_ref=source_ref,
            unit="count",
        ),
        _item(
            "region.extra_midi_notes_read",
            "region_extra_midi_notes_read",
            extra_midi_notes,
            pack=pack,
            region=label,
            source_ref=source_ref,
            unit="count",
        ),
        _item(
            "region.question_a",
            "region_question_a",
            questions["A"],
            pack=pack,
            region=label,
            source_ref=source_ref,
        ),
        _item(
            "region.question_b",
            "region_question_b",
            questions["B"],
            pack=pack,
            region=label,
            source_ref=source_ref,
        ),
        _item(
            "region.question_c",
            "region_question_c",
            questions["C"],
            pack=pack,
            region=label,
            source_ref=source_ref,
        ),
    ]
    if questions["C"].get("period_qn") is not None:
        items.append(
            _item(
                "region.question_c.period_qn",
                "region_repeat_period_qn",
                float(questions["C"]["period_qn"]),
                pack=pack,
                region=label,
                source_ref=source_ref,
                unit="qn",
            )
        )
    for idx, clip in enumerate(clips):
        clip_ref = str(clip["clip_identity"])
        items.append(
            _item(
                f"region.clip.{idx}",
                "region_clip",
                clip,
                pack=pack,
                region=label,
                source_ref=clip_ref,
            )
        )
        items.extend(
            [
                _item(
                    f"region.clip.{idx}.arrangement_start_qn",
                    "region_clip_arrangement_start_qn",
                    float(clip["arrangement_start_qn"]),
                    pack=pack,
                    region=label,
                    source_ref=clip_ref,
                    unit="qn",
                ),
                _item(
                    f"region.clip.{idx}.arrangement_end_qn",
                    "region_clip_arrangement_end_qn",
                    float(clip["arrangement_end_qn"]),
                    pack=pack,
                    region=label,
                    source_ref=clip_ref,
                    unit="qn",
                ),
            ]
        )
        if clip.get("persistent_object_ref"):
            items.append(
                _item(
                    f"region.clip.{idx}.persistent_object_ref",
                    "region_clip_persistent_object_ref",
                    clip["persistent_object_ref"],
                    pack=pack,
                    region=label,
                    source_ref=clip_ref,
                    kind=EvidenceKind.STATE_TOKEN,
                )
            )
        items.append(
            _item(
                f"region.clip.{idx}.role",
                "region_clip_role",
                clip.get("role") or ROLE_UNKNOWN,
                pack=pack,
                region=label,
                source_ref=clip_ref,
            )
        )
    for seg in segments:
        prefix = f"region.segment.{seg['index']}"
        items.append(
            _item(
                prefix,
                "region_segment",
                seg,
                pack=pack,
                region=label,
                source_ref=source_ref,
            )
        )
        items.extend(
            [
                _item(
                    f"{prefix}.start_qn",
                    "region_segment_start_qn",
                    float(seg["start_qn"]),
                    pack=pack,
                    region=label,
                    source_ref=source_ref,
                    unit="qn",
                ),
                _item(
                    f"{prefix}.end_qn",
                    "region_segment_end_qn",
                    float(seg["end_qn"]),
                    pack=pack,
                    region=label,
                    source_ref=source_ref,
                    unit="qn",
                ),
                _item(
                    f"{prefix}.active_track_count",
                    "region_segment_active_track_count",
                    int(seg["active_track_count"]),
                    pack=pack,
                    region=label,
                    source_ref=source_ref,
                    unit="count",
                ),
                _item(
                    f"{prefix}.active_clip_count",
                    "region_segment_active_clip_count",
                    int(seg["active_clip_count"]),
                    pack=pack,
                    region=label,
                    source_ref=source_ref,
                    unit="count",
                ),
            ]
        )
    for row in contexts:
        prefix = f"region.event.{row['event_id']}"
        items.append(
            _item(
                prefix,
                "region_event_context",
                row,
                pack=pack,
                region=label,
                source_ref=source_ref,
            )
        )
        if row.get("start_qn") is not None:
            items.append(
                _item(
                    f"{prefix}.start_qn",
                    "region_event_start_qn",
                    float(row["start_qn"]),
                    pack=pack,
                    region=label,
                    source_ref=source_ref,
                    unit="qn",
                )
            )
        if row.get("end_qn") is not None:
            items.append(
                _item(
                    f"{prefix}.end_qn",
                    "region_event_end_qn",
                    float(row["end_qn"]),
                    pack=pack,
                    region=label,
                    source_ref=source_ref,
                    unit="qn",
                )
            )
        if row.get("active_track_count") is not None:
            items.append(
                _item(
                    f"{prefix}.active_track_count",
                    "region_event_active_track_count",
                    int(row["active_track_count"]),
                    pack=pack,
                    region=label,
                    source_ref=source_ref,
                    unit="count",
                )
            )
        if row.get("active_clip_count") is not None:
            items.append(
                _item(
                    f"{prefix}.active_clip_count",
                    "region_event_active_clip_count",
                    int(row["active_clip_count"]),
                    pack=pack,
                    region=label,
                    source_ref=source_ref,
                    unit="count",
                )
            )
        items.append(
            _item(
                f"{prefix}.labels",
                "region_event_labels",
                row.get("labels") or [],
                pack=pack,
                region=label,
                source_ref=source_ref,
            )
        )
    _assert_factual([item.model_dump(mode="json") for item in items])
    return items


def build_child_pack(parent: EvidencePack, added: list[EvidenceItem]) -> EvidencePack:
    limitations = list(parent.limitations)
    limitations.append(
        ObservationLimitation(
            code="ANALYZE_REGION_V1",
            detail=(
                "Arrangement clip metadata for AUTO_36_68. Clip presence ≠ audible Main "
                "contribution. Additional tracks were not note-read."
            ),
            precision_ms=float(parent.alignment_envelope_ms),
            capability_ms=[float(parent.alignment_envelope_ms)],
        )
    )
    limitations.append(
        ObservationLimitation(
            code="CROSS_SOURCE_ALIGNMENT_LIMITED",
            detail="Arrangement qn boundaries and Main-local audio events have different provenance. LIMITED ±52 ms.",
            precision_ms=float(parent.alignment_envelope_ms),
            capability_ms=[float(parent.alignment_envelope_ms)],
        )
    )
    existing = {item.evidence_id for item in parent.items}
    extra = [item for item in added if item.evidence_id not in existing]
    return parent.model_copy(
        update={
            "pack_id": f"pack_{uuid4().hex[:12]}",
            "items": list(parent.items) + extra,
            "limitations": limitations,
        }
    )


def collect_region(
    *,
    parent_path: Path | None = None,
    als_path: Path | None = None,
    journal_dir: Path | None = None,
    search_roots: list[Path] | None = None,
    session: SessionState | None = None,
) -> dict[str, Any]:
    parent_path = Path(parent_path or PARENT_PACK_PATH)
    parent_bytes = parent_path.read_bytes()
    parent_file_sha = hashlib.sha256(parent_bytes).hexdigest()
    parent = load_persisted_pack(parent_path)
    region = pack_region(parent)
    pack_identity = pack_project_identity(parent)
    report: dict[str, Any] = {
        "milestone": MILESTONE,
        "ts": now_iso(),
        "parent_pack_id": parent.pack_id,
        "parent_payload_sha256": pack_payload_hash(parent),
        "parent_file_sha256": parent_file_sha,
        "parent_path": str(parent_path),
        "NO LIVE": True,
        "NO RECAPTURE": True,
        "NO MIDI WRITE": True,
        "NO MUSICAL WRITE": True,
        "NO EXTRA MIDI READ": True,
        "NO ROUTING READ": True,
        "NO CAPTURE_VIEW": True,
        "MUSICAL WRITES": 0,
        "alignment_claim": parent.alignment_claim,
        "alignment_envelope_ms": parent.alignment_envelope_ms,
    }
    if session is not None:
        target = target_source_id(parent)
        journal_ref = load_persisted_ref(target, journal_dir=journal_dir)
        if journal_ref is not None:
            operational = journal_ref.model_copy(update={"project_identity": pack_identity})
            resolved = resolve_track(session, operational)
            report["session_resolve"] = resolved.model_dump(mode="json")
            if resolved.status is ResolveStatus.PROJECT_MISMATCH:
                report["status"] = "BLOCKED"
                report[MILESTONE] = "BLOCKED"
                report["BLOCKER"] = "PROJECT_MISMATCH"
                report["parent_file_unchanged"] = (
                    hashlib.sha256(parent_path.read_bytes()).hexdigest() == parent_file_sha
                )
                return report
    located = locate_working_copy_als(
        pack_identity, als_path=als_path, search_roots=search_roots
    )
    report["als"] = {key: value for key, value in located.items()}
    if not located.get("ok"):
        report["status"] = "BLOCKED"
        report[MILESTONE] = "BLOCKED"
        report["BLOCKER"] = located.get("error")
        report["parent_file_unchanged"] = (
            hashlib.sha256(parent_path.read_bytes()).hexdigest() == parent_file_sha
        )
        return report
    als = Path(located["path"])
    als_sha_before = sha256_file(als) or ""
    root = _load_als_root(als)
    journal_refs = load_journal_refs(journal_dir=journal_dir)
    pack_roles = established_roles(parent)
    clips = read_arrangement_clips(
        root,
        region_start=float(region["start_qn"]),
        region_end=float(region["end_qn"]),
        pack_roles=pack_roles,
        journal_refs=journal_refs,
        pack_identity=pack_identity,
    )
    segments = segment_region(
        region_start=float(region["start_qn"]),
        region_end=float(region["end_qn"]),
        clips=clips,
    )
    tempo_item = parent.by_id().get("midi.tempo_bpm")
    tempo_bpm = float(tempo_item.value) if tempo_item is not None else 0.0
    envelope_ms = float(parent.alignment_envelope_ms)
    envelope_qn = (envelope_ms / 1000.0) * (tempo_bpm / 60.0) if tempo_bpm else 0.0
    events = load_region_events(parent)
    mapped: list[dict[str, Any]] = []
    tempo_ok = tempo_bpm > 0
    for event in events:
        if not event.get("relevant"):
            continue
        row = dict(event)
        if tempo_ok:
            row["start_qn"] = float(region["start_qn"]) + float(event["start_s"]) * (
                tempo_bpm / 60.0
            )
            row["end_qn"] = float(region["start_qn"]) + float(event["end_s"]) * (
                tempo_bpm / 60.0
            )
        mapped.append(row)
    target_span = target_clip_span(parent)
    contexts = [
        classify_event_context(
            event=event,
            clips=clips,
            target_span=target_span,
            envelope_qn=envelope_qn,
            tempo_ok=tempo_ok,
        )
        for event in mapped
    ]
    period_item = parent.by_id().get("fm.event.3.approx_period_s")
    period_s = float(period_item.value) if period_item is not None else None
    questions = answer_questions(
        events=mapped,
        contexts=contexts,
        segments=segments,
        clips=clips,
        target_id=target_source_id(parent),
        period_s=period_s,
        tempo_bpm=tempo_bpm,
        envelope_qn=envelope_qn,
    )
    time_basis = {
        "arrangement": "qn",
        "audio_events": "main_local_s_mapped_with_als_tempo",
        "formula": TIME_BASIS,
        "tempo_bpm": tempo_bpm,
        "alignment_envelope_ms": envelope_ms,
        "alignment_envelope_qn": envelope_qn,
        "region_qn": [region["start_qn"], region["end_qn"]],
    }
    added = region_evidence_items(
        pack=parent,
        region=region,
        clips=clips,
        segments=segments,
        contexts=contexts,
        questions=questions,
        time_basis=time_basis,
        als_identity=str(located.get("identity") or identity_for_als_path(als)),
        pack_identity=pack_identity,
        extra_midi_notes=0,
    )
    child = build_child_pack(parent, added)
    note_ids_parent = parent_midi_note_ids(parent)
    note_ids_child = parent_midi_note_ids(child)
    als_sha_after = sha256_file(als) or ""
    after_parent = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    unknown_roles = sum(1 for clip in clips if clip.get("role") == ROLE_UNKNOWN)
    report.update(
        {
            "status": "VERIFIED",
            MILESTONE: "PACK_READY",
            "new_pack_id": child.pack_id,
            "new_payload_sha256": pack_payload_hash(child),
            "clip_count": len(clips),
            "segment_count": len(segments),
            "segments": segments,
            "event_context": contexts,
            "QUESTIONS": questions,
            "unknown_role_clip_count": unknown_roles,
            "extra_midi_note_ids": sorted(note_ids_child - note_ids_parent),
            "NO EXTRA MIDI READ": note_ids_child == note_ids_parent,
            "time_basis": time_basis,
            "als_sha256_before": als_sha_before,
            "als_sha256_after": als_sha_after,
            "ALS_UNCHANGED": als_sha_after == als_sha_before,
            "parent_file_unchanged": after_parent == parent_file_sha,
            "alignment_claim": child.alignment_claim,
            "alignment_envelope_ms": child.alignment_envelope_ms,
            "pack": child.model_dump(mode="json"),
        }
    )
    return report


def _next_evidence_request(
    accepted: bool,
    status: str,
    requested: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    if not accepted:
        return None, None
    if status != DiagnosisStatus.INSUFFICIENT_EVIDENCE.value:
        return None, None
    kinds = [str(item.get("request_kind") or "") for item in requested]
    mapped: list[str] = []
    explanation: dict[str, Any] = {}
    if "READ_MIDI" in kinds:
        mapped.append("READ_MIDI_OTHER_SOURCES")
        explanation["READ_MIDI_OTHER_SOURCES"] = (
            "Note content on contributing tracks other than the already-read target source."
        )
    if "READ_ROUTING" in kinds:
        mapped.append("READ_ROUTING")
        explanation["READ_ROUTING"] = (
            "Path from the inspected source to Main before treating near-silence as a device fault."
        )
    if "CAPTURE_VIEW" in kinds:
        mapped.append("CAPTURE_VIEW")
        explanation["CAPTURE_VIEW"] = (
            "Devices, clips, automation, and causal state while MIDI is present and audio is quiet."
        )
    if "ANALYZE_REGION" in kinds:
        mapped.append("ANALYZE_REGION")
        explanation["ANALYZE_REGION"] = "Exact request repeated after region metadata was added."
    if not mapped:
        if requested:
            return json.dumps(requested, ensure_ascii=False), explanation or None
        return None, None
    return "+".join(mapped), explanation


def replay_region(
    *,
    evidence: Path | None = None,
    parent_path: Path | None = None,
    als_path: Path | None = None,
    journal_dir: Path | None = None,
    search_roots: list[Path] | None = None,
    provider: ReasoningProvider | None = None,
    timeout_s: float = ASTRA_TIMEOUT_S,
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    built = collect_region(
        parent_path=parent_path,
        als_path=als_path,
        journal_dir=journal_dir,
        search_roots=search_roots,
    )
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
    next_request, next_explain = _next_evidence_request(
        result.accepted, astra_status, requested
    )
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
            "NEXT_EVIDENCE_EXPLANATION": next_explain,
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


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    report["artifact"] = str(path)
    return path


def main() -> int:
    report = replay_region()
    summary = {
        "milestone": MILESTONE,
        "parent_pack_id": report.get("parent_pack_id"),
        "new_pack_id": report.get("new_pack_id"),
        "QUESTIONS": report.get("QUESTIONS"),
        "accepted": report.get("accepted"),
        "ASTRA RESULT": report.get("ASTRA RESULT"),
        "NEXT_EVIDENCE_REQUEST": report.get("NEXT_EVIDENCE_REQUEST"),
        MILESTONE: report.get(MILESTONE),
        "ALS_UNCHANGED": report.get("ALS_UNCHANGED"),
        "parent_file_unchanged": report.get("parent_file_unchanged"),
        "NO EXTRA MIDI READ": report.get("NO EXTRA MIDI READ"),
        "NO ROUTING READ": True,
        "NO CAPTURE_VIEW": True,
        "NO RECAPTURE": True,
        "MUSICAL WRITES": 0,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get(MILESTONE) == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
