"""MIDI_READ_ONLY_V1 — arrangement MIDI facts for AUTO_36_68. No writes."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import xml.etree.ElementTree as ET
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
from copilot.audio.producer_analyze_v1 import apply_reasoning_result
from copilot.audio.tap_trust import JOURNAL_DIR
from copilot.daw.object_ref import PersistentObjectRef, ResolveStatus, resolve_track
from copilot.daw.state_tokens import token_of
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

MILESTONE = "MIDI_READ_ONLY_V1"
ARTIFACT = "midi_read_only_v1.json"
STATUS = "IMPLEMENTED"
ANALYSIS_VERSION = "midi-read-only-v1"
EXPECTED_PARENT_PACK_ID = "pack_a953de6bb4ad"
PARENT_PACK_PATH = Path("logs") / "external_evidence_enrichment_v1.json"
NEAR_SILENT_SIGNAL = frozenset({"NEAR_SILENCE", "SILENCE"})
NEAR_SILENT_EVENT_KINDS = frozenset({"SILENCE", "STRONG_ENERGY_DIP", "NEAR_SILENCE"})
MIDI_CAPABLE_ROLES = frozenset({"midi"})
MIDI_CAPABLE_ALS = frozenset({"MidiTrack"})
INSTRUMENT_TAGS = frozenset(
    {
        "OriginalSimpler",
        "MultiSampler",
        "PluginDevice",
        "InstrumentGroupDevice",
        "DrumGroupDevice",
        "InstrumentVector",
        "UltraAnalog",
        "Operator",
        "Collision",
        "Tension",
        "Electric",
        "Simpler",
        "InstrumentRack",
    }
)
TRACK_TAGS = {"MidiTrack", "AudioTrack", "GroupTrack", "ReturnTrack"}
FORBIDDEN_MIDI_JUDGMENT_RE = re.compile(
    r"\b(bad pattern|weak melody|wrong notes|needs more notes)\b",
    re.IGNORECASE,
)
TIME_BASIS_FORMULA = (
    "arrangement_qn = clip_current_start_qn + (clip_local_qn - start_relative_qn) "
    "for the first cycle; when LoopOn, the playhead wraps at LoopEnd to LoopStart "
    "and each in-loop onset retriggers with full duration, clipped to clip CurrentEnd. "
    "clip-local timestamps are not arrangement timestamps."
)

MIDI_HAS_MATERIAL = "MIDI_HAS_MATERIAL"
MIDI_NO_MATERIAL = "MIDI_NO_MATERIAL"
MIDI_NOT_APPLICABLE = "MIDI_NOT_APPLICABLE"
NO_MIDI_EXPECTED_DURING_INTERVAL = "NO_MIDI_EXPECTED_DURING_INTERVAL"
MIDI_PRESENT_DURING_INTERVAL = "MIDI_PRESENT_DURING_INTERVAL"
RELATION_UNCERTAIN = "RELATION_UNCERTAIN"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _attr_value(elem: ET.Element, name: str) -> str | None:
    raw = elem.attrib.get(name)
    if raw is not None:
        return raw
    child = next((item for item in list(elem) if _local(item.tag) == name), None)
    if child is None:
        return None
    return child.attrib.get("Value")


def _float_or_none(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _truthy(value: str | None, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"true", "1", "yes"}


def _load_als_root(path: Path) -> ET.Element:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    mapping: dict[ET.Element, ET.Element] = {}
    stack = [root]
    while stack:
        node = stack.pop()
        for child in list(node):
            mapping[child] = node
            stack.append(child)
    return mapping


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def identity_for_als_path(path: Path) -> str:
    return token_of(
        {
            "kind": "live_set_path",
            "path": str(path),
            "name": path.stem,
        }
    )


def locate_working_copy_als(
    project_identity: str,
    *,
    als_path: Path | None = None,
    search_roots: list[Path] | None = None,
) -> dict[str, Any]:
    if als_path is not None:
        path = Path(als_path)
        if not path.is_file():
            return {"ok": False, "error": "ALS_MISSING", "path": str(path)}
        identity = identity_for_als_path(path)
        if identity != project_identity:
            return {
                "ok": False,
                "error": "PROJECT_MISMATCH",
                "path": str(path),
                "als_identity": identity,
                "pack_identity": project_identity,
                "reason": "ALS path identity differs from pack project identity",
            }
        return {"ok": True, "path": str(path), "identity": identity}
    roots = list(search_roots or [Path.home() / "CopilotProjects"])
    matches: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in root.rglob("*.als"):
            if identity_for_als_path(candidate) == project_identity:
                matches.append(candidate)
    if len(matches) == 1:
        path = matches[0]
        return {"ok": True, "path": str(path), "identity": project_identity}
    if not matches:
        return {"ok": False, "error": "ALS_NOT_FOUND", "pack_identity": project_identity}
    return {
        "ok": False,
        "error": "ALS_AMBIGUOUS",
        "matches": [str(item) for item in matches],
    }


def load_persisted_ref(
    fingerprint: str,
    *,
    journal_dir: Path | None = None,
) -> PersistentObjectRef | None:
    directory = Path(journal_dir or JOURNAL_DIR)
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            ref = row.get("ref")
            if not isinstance(ref, dict):
                continue
            if ref.get("content_fingerprint") == fingerprint:
                return PersistentObjectRef.model_validate(ref)
    return None


def pack_project_identity(pack: EvidencePack) -> str:
    item = pack.by_id().get("ev.project.identity")
    if item is None:
        return ""
    return str(item.value or "")


def pack_region(pack: EvidencePack) -> dict[str, Any]:
    item = pack.by_id().get("ev.region")
    value = item.value if item is not None and isinstance(item.value, dict) else {}
    start = float(value.get("start_qn") if value.get("start_qn") is not None else 36.0)
    end = float(value.get("end_qn") if value.get("end_qn") is not None else 68.0)
    region_id = str(value.get("id") or pack.region.split(":")[0])
    return {"id": region_id, "start_qn": start, "end_qn": end, "label": pack.region}


def near_silent_sources(pack: EvidencePack) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    entities = pack.entity_by_id()
    for item in pack.items:
        if item.name != "source_capture":
            continue
        value = item.value if isinstance(item.value, dict) else {}
        signal = str(value.get("signal_class") or "")
        if signal not in NEAR_SILENT_SIGNAL:
            continue
        entity_id = str(item.source_ref or value.get("ref") or "")
        entity = entities.get(entity_id)
        rows.append(
            {
                "evidence_id": item.evidence_id,
                "entity_id": entity_id,
                "signal_class": signal,
                "role": None if entity is None else entity.role,
                "entity_name": None if entity is None else entity.name,
                "kind": None if entity is None else entity.kind.value,
                "region": item.region,
                "view": item.view,
            }
        )
    return rows


def bind_operational_ref(
    journal_ref: PersistentObjectRef,
    *,
    pack_identity: str,
    entity_id: str,
) -> dict[str, Any]:
    if journal_ref.content_fingerprint != entity_id:
        return {
            "ok": False,
            "error": "IDENTITY_RECONCILIATION_FAILED",
            "reason": "journal content_fingerprint != pack entity_id",
        }
    operational = journal_ref.model_copy(update={"project_identity": pack_identity})
    return {
        "ok": True,
        "ref": operational,
        "journal_project_identity": journal_ref.project_identity,
        "pack_project_identity": pack_identity,
        "journal_identity_used_as_project": False,
        "name_is_locator_only": True,
        "identity_keys": ["content_fingerprint", "role", "device_names", "device_classes"],
    }


def project_mismatch_still_fails_closed(
    session: SessionState, ref: PersistentObjectRef
) -> bool:
    return resolve_track(session, ref).status is ResolveStatus.PROJECT_MISMATCH


def iter_playhead_segments(
    *,
    current_start: float,
    current_end: float,
    start_relative: float,
    loop_on: bool,
    loop_start: float,
    loop_end: float,
) -> list[tuple[float, float, float, int]]:
    """(arrangement_start, arrangement_end, clip_local_at_start, iteration)."""
    if current_end <= current_start:
        return []
    if not loop_on:
        return [(current_start, current_end, start_relative, 0)]
    loop_len = loop_end - loop_start
    if loop_len <= 1e-12:
        return [(current_start, current_end, start_relative, 0)]
    segments: list[tuple[float, float, float, int]] = []
    arr = current_start
    clip_pos = start_relative
    iteration = 0
    guard = 0
    while arr < current_end - 1e-12 and guard < 100_000:
        guard += 1
        if clip_pos >= loop_end - 1e-12:
            clip_pos = loop_start + ((clip_pos - loop_start) % loop_len)
        remaining = loop_end - clip_pos
        if remaining <= 1e-12:
            clip_pos = loop_start
            remaining = loop_len
        seg_end = min(arr + remaining, current_end)
        segments.append((arr, seg_end, clip_pos, iteration))
        arr = seg_end
        clip_pos = loop_start
        iteration += 1
    return segments


def expand_note_occurrences(
    *,
    clip_local_start: float,
    duration: float,
    current_start: float,
    current_end: float,
    start_relative: float,
    loop_on: bool,
    loop_start: float,
    loop_end: float,
) -> list[dict[str, float | int]]:
    occurrences: list[dict[str, float | int]] = []
    for arr0, arr1, clip0, iteration in iter_playhead_segments(
        current_start=current_start,
        current_end=current_end,
        start_relative=start_relative,
        loop_on=loop_on,
        loop_start=loop_start,
        loop_end=loop_end,
    ):
        clip1 = clip0 + (arr1 - arr0)
        if not (clip0 - 1e-12 <= clip_local_start < clip1 - 1e-12):
            continue
        onset = arr0 + (clip_local_start - clip0)
        offset = onset + duration
        sound0 = max(onset, current_start)
        sound1 = min(offset, current_end)
        if sound1 <= sound0:
            continue
        occurrences.append(
            {
                "arrangement_start_qn": onset,
                "arrangement_end_qn": offset,
                "sounding_start_qn": sound0,
                "sounding_end_qn": sound1,
                "clip_local_start_qn": clip_local_start,
                "duration_qn": duration,
                "loop_iteration": iteration,
            }
        )
    return occurrences


def als_tempo_bpm(root: ET.Element) -> float | None:
    for elem in root.iter():
        if _local(elem.tag) != "Tempo":
            continue
        for child in elem.iter():
            if _local(child.tag) == "Manual" and child.attrib.get("Value"):
                return _float_or_none(child.attrib.get("Value"))
    return None


def _track_locator_name(track: ET.Element) -> str:
    for child in list(track):
        if _local(child.tag) != "Name":
            continue
        for node in child.iter():
            if _local(node.tag) in {"EffectiveName", "UserName", "MemorizedName"}:
                value = node.attrib.get("Value")
                if value:
                    return value
    return ""


def _als_device_inventory(track: ET.Element) -> tuple[list[str], list[str]]:
    names: list[str] = []
    classes: list[str] = []
    seen_names: set[str] = set()
    seen_classes: set[str] = set()
    chain = next((child for child in track.iter() if _local(child.tag) == "DeviceChain"), track)
    for elem in chain.iter():
        tag = _local(elem.tag)
        if tag in INSTRUMENT_TAGS and tag not in seen_classes:
            classes.append(tag)
            seen_classes.add(tag)
        if tag != "Name":
            continue
        value = (elem.attrib.get("Value") or "").strip()
        if not value:
            continue
        variants = [value]
        if value.lower().endswith(".wav"):
            variants.append(Path(value).stem)
        for item in variants:
            if item not in seen_names:
                names.append(item)
                seen_names.add(item)
    return names, classes


def _is_arrangement_clip(clip: ET.Element, parents: dict[ET.Element, ET.Element]) -> bool:
    ancestors: set[str] = set()
    node = parents.get(clip)
    while node is not None:
        ancestors.add(_local(node.tag))
        node = parents.get(node)
    if "ClipSlot" in ancestors or "ClipSlots" in ancestors:
        return False
    return "ArrangerAutomation" in ancestors or "ClipTimeable" in ancestors


def _clip_span(clip: ET.Element) -> tuple[float, float] | None:
    start = _float_or_none(_attr_value(clip, "CurrentStart"))
    end = _float_or_none(_attr_value(clip, "CurrentEnd"))
    if start is None or end is None or end <= start:
        return None
    return start, end


def _loop_fields(clip: ET.Element) -> dict[str, Any]:
    for child in clip:
        if _local(child.tag) != "Loop":
            continue
        out: dict[str, Any] = {}
        for item in child:
            out[_local(item.tag)] = item.attrib.get("Value")
        return out
    return {}


def _owning_track(node: ET.Element, parents: dict[ET.Element, ET.Element]) -> ET.Element | None:
    current = parents.get(node)
    while current is not None:
        if _local(current.tag) in TRACK_TAGS:
            return current
        current = parents.get(current)
    return None


def match_als_track(
    root: ET.Element,
    ref: PersistentObjectRef,
) -> dict[str, Any]:
    """Match by persisted device fingerprint. Display name is locator-only."""
    parents = _parent_map(root)
    role_capable = ref.role in MIDI_CAPABLE_ROLES
    candidates: list[dict[str, Any]] = []
    for track in root.iter():
        tag = _local(track.tag)
        if tag not in TRACK_TAGS:
            continue
        names, classes = _als_device_inventory(track)
        if ref.device_names and not set(ref.device_names).issubset(set(names)):
            continue
        if ref.device_classes and not set(ref.device_classes).issubset(set(classes)):
            continue
        candidates.append(
            {
                "element": track,
                "track_type": tag,
                "locator_name": _track_locator_name(track),
                "device_names": names,
                "device_classes": classes,
            }
        )
    if not candidates:
        return {
            "ok": False,
            "error": "TARGET_NOT_FOUND",
            "reason": "no ALS track matched persisted device fingerprint",
            "used_display_name": False,
        }
    if len(candidates) > 1:
        named = [
            row
            for row in candidates
            if ref.name and row["locator_name"] == ref.name
        ]
        if len(named) == 1:
            chosen = named[0]
            chosen["name_tie_break"] = True
        else:
            return {
                "ok": False,
                "error": "TARGET_AMBIGUOUS",
                "matches": len(candidates),
                "locator_names": [row["locator_name"] for row in candidates],
                "used_display_name": False,
            }
    else:
        chosen = candidates[0]
        chosen["name_tie_break"] = False
    track_type = str(chosen["track_type"])
    midi_capable = role_capable and track_type in MIDI_CAPABLE_ALS
    return {
        "ok": True,
        "track_type": track_type,
        "locator_name": chosen["locator_name"],
        "device_names": chosen["device_names"],
        "device_classes": chosen["device_classes"],
        "element": chosen["element"],
        "parents": parents,
        "midi_capable": midi_capable,
        "used_display_name": bool(chosen.get("name_tie_break")),
        "identity_from": "PersistentObjectRef.device_names+device_classes+role",
    }


def _keytrack_pitch(keytrack: ET.Element) -> int | None:
    for child in list(keytrack):
        if _local(child.tag) == "MidiKey" and child.attrib.get("Value") is not None:
            try:
                return int(float(child.attrib["Value"]))
            except ValueError:
                return None
    return None


def _note_muted(event: ET.Element) -> bool:
    enabled = event.attrib.get("IsEnabled")
    if enabled is not None and not _truthy(enabled, default=True):
        return True
    mute = event.attrib.get("Mute")
    if mute is not None:
        return _truthy(mute, default=False)
    return False


def read_arrangement_midi(
    root: ET.Element,
    track: ET.Element,
    parents: dict[ET.Element, ET.Element],
    *,
    region_start: float,
    region_end: float,
) -> dict[str, Any]:
    clips_out: list[dict[str, Any]] = []
    notes_out: list[dict[str, Any]] = []
    for clip in root.iter():
        if _local(clip.tag) != "MidiClip":
            continue
        if _owning_track(clip, parents) is not track:
            continue
        if not _is_arrangement_clip(clip, parents):
            continue
        span = _clip_span(clip)
        if span is None:
            continue
        c0, c1 = span
        if _overlap(c0, c1, region_start, region_end) <= 0:
            continue
        loop = _loop_fields(clip)
        loop_on = _truthy(str(loop.get("LoopOn")), default=False)
        loop_start = float(loop.get("LoopStart") or 0.0)
        loop_end = float(loop.get("LoopEnd") or 0.0)
        start_rel = float(loop.get("StartRelative") or 0.0)
        clip_muted = _truthy(_attr_value(clip, "Disabled"), default=False)
        clip_name = _attr_value(clip, "Name") or ""
        clip_id = clip.attrib.get("Id") or ""
        clip_row = {
            "clip_id": clip_id,
            "clip_name": clip_name,
            "clip_identity": f"clip:{clip_id}:{clip_name}:{c0}",
            "arrangement_start_qn": c0,
            "arrangement_end_qn": c1,
            "loop_on": loop_on,
            "loop_start_qn": loop_start,
            "loop_end_qn": loop_end,
            "start_relative_qn": start_rel,
            "muted": clip_muted,
            "time_basis": "arrangement_qn",
        }
        clips_out.append(clip_row)
        notes_node = next((child for child in clip if _local(child.tag) == "Notes"), None)
        if notes_node is None:
            continue
        for keytrack in notes_node.iter():
            if _local(keytrack.tag) != "KeyTrack":
                continue
            pitch = _keytrack_pitch(keytrack)
            for event in keytrack.iter():
                if _local(event.tag) != "MidiNoteEvent":
                    continue
                walk = parents.get(event)
                owned = False
                while walk is not None:
                    if walk is keytrack:
                        owned = True
                        break
                    if _local(walk.tag) == "KeyTrack":
                        break
                    walk = parents.get(walk)
                if not owned:
                    continue
                local_t = _float_or_none(event.attrib.get("Time"))
                duration = _float_or_none(event.attrib.get("Duration")) or 0.0
                velocity = _float_or_none(event.attrib.get("Velocity")) or 0.0
                if local_t is None:
                    continue
                muted = clip_muted or _note_muted(event)
                for occ in expand_note_occurrences(
                    clip_local_start=local_t,
                    duration=duration,
                    current_start=c0,
                    current_end=c1,
                    start_relative=start_rel,
                    loop_on=loop_on,
                    loop_start=loop_start,
                    loop_end=loop_end,
                ):
                    sound0 = float(occ["sounding_start_qn"])
                    sound1 = float(occ["sounding_end_qn"])
                    if _overlap(sound0, sound1, region_start, region_end) <= 0:
                        continue
                    notes_out.append(
                        {
                            "pitch": pitch,
                            "clip_local_start_qn": local_t,
                            "arrangement_start_qn": float(occ["arrangement_start_qn"]),
                            "duration_qn": duration,
                            "velocity": velocity,
                            "muted": muted,
                            "loop_iteration": int(occ["loop_iteration"]),
                            "clip_identity": clip_row["clip_identity"],
                            "region_overlap_start_qn": max(sound0, region_start),
                            "region_overlap_end_qn": min(sound1, region_end),
                            "time_basis": "arrangement_qn",
                        }
                    )
    notes_out.sort(
        key=lambda row: (
            float(row["arrangement_start_qn"]),
            int(row["pitch"] or -1),
            int(row["loop_iteration"]),
        )
    )
    return {"clips": clips_out, "notes": notes_out}


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_s, last_e = merged[-1]
        if start <= last_e + 1e-12:
            merged[-1] = (last_s, max(last_e, end))
        else:
            merged.append((start, end))
    return merged


def gaps_without_activity(
    region_start: float,
    region_end: float,
    intervals: list[tuple[float, float]],
) -> list[dict[str, float]]:
    clipped = [
        (max(start, region_start), min(end, region_end))
        for start, end in intervals
        if end > start and _overlap(start, end, region_start, region_end) > 0
    ]
    gaps: list[dict[str, float]] = []
    cursor = region_start
    for start, end in merge_intervals(clipped):
        if start > cursor + 1e-12:
            gaps.append({"start_qn": cursor, "end_qn": start})
        cursor = max(cursor, end)
    if region_end > cursor + 1e-12:
        gaps.append({"start_qn": cursor, "end_qn": region_end})
    return gaps


def derive_midi_status(notes: list[dict[str, Any]], *, midi_capable: bool) -> str:
    if not midi_capable:
        return MIDI_NOT_APPLICABLE
    active = [row for row in notes if not row.get("muted")]
    return MIDI_HAS_MATERIAL if active else MIDI_NO_MATERIAL


def load_main_events(pack: EvidencePack) -> list[dict[str, Any]]:
    by_id = pack.by_id()
    events: list[dict[str, Any]] = []
    idx = 0
    while True:
        kind_item = by_id.get(f"fm.event.{idx}.kind")
        start_item = by_id.get(f"fm.event.{idx}.start_s")
        end_item = by_id.get(f"fm.event.{idx}.end_s")
        if kind_item is None or start_item is None or end_item is None:
            break
        kind = str(kind_item.value)
        events.append(
            {
                "event_id": f"fm.event.{idx}",
                "kind": kind,
                "start_s": float(start_item.value),
                "end_s": float(end_item.value),
                "relevant": kind in NEAR_SILENT_EVENT_KINDS,
            }
        )
        idx += 1
    return events


def classify_audio_midi_relation(
    *,
    event_start_qn: float,
    event_end_qn: float,
    active_intervals: list[tuple[float, float]],
    envelope_qn: float,
) -> str:
    hits_core = any(
        _overlap(event_start_qn, event_end_qn, start, end) > 0
        for start, end in active_intervals
    )
    hits_expanded = any(
        _overlap(event_start_qn - envelope_qn, event_end_qn + envelope_qn, start, end) > 0
        for start, end in active_intervals
    )
    if hits_core:
        return MIDI_PRESENT_DURING_INTERVAL
    if hits_expanded:
        return RELATION_UNCERTAIN
    return NO_MIDI_EXPECTED_DURING_INTERVAL


def factual_outcome(relations: list[dict[str, Any]], midi_status: str) -> dict[str, Any]:
    if midi_status == MIDI_NOT_APPLICABLE:
        return {
            "A": False,
            "B": False,
            "C": True,
            "label": "C",
            "detail": MIDI_NOT_APPLICABLE,
        }
    present = [row for row in relations if row["relation"] == MIDI_PRESENT_DURING_INTERVAL]
    absent = [row for row in relations if row["relation"] == NO_MIDI_EXPECTED_DURING_INTERVAL]
    uncertain = [row for row in relations if row["relation"] == RELATION_UNCERTAIN]
    a = bool(absent)
    b = bool(present)
    c = bool(uncertain) and not a and not b
    if a and b:
        label = "A_AND_B"
    elif b:
        label = "B"
    elif a:
        label = "A"
    else:
        label = "C"
        c = True
    return {
        "A": a,
        "B": b,
        "C": c,
        "label": label,
        "present_count": len(present),
        "no_midi_count": len(absent),
        "uncertain_count": len(uncertain),
        "detail": (
            "A = near-silence where no MIDI material is expected; "
            "B = MIDI present while corresponding audio is near-silent; "
            "C = MIDI cannot resolve the ambiguity. These are facts, not faults."
        ),
    }


def _assert_factual(payload: Any) -> None:
    blob = json.dumps(payload, ensure_ascii=False)
    hit = FORBIDDEN_MIDI_JUDGMENT_RE.search(blob)
    if hit:
        raise ValueError(f"MIDI evidence leaked diagnosis language: {hit.group(0)}")


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
    limitations: list[str] | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        source_ref=source_ref,
        region=region,
        view="TRACK_ISOLATED",
        analysis_version=ANALYSIS_VERSION,
        name=name,
        value=value,
        unit=unit,
        quality=CaptureQuality.LIMITED,
        limitations=list(limitations or ["MEASURE_ONLY", "ALIGNMENT_LIMITED"]),
        project_token=pack.project_token,
        audible_token=pack.audible_token,
        target_token=pack.target_token,
    )


def midi_evidence_items(
    *,
    pack: EvidencePack,
    region: dict[str, Any],
    source: dict[str, Any],
    ref: PersistentObjectRef,
    track_info: dict[str, Any],
    midi_status: str,
    clips: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    active_intervals: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    tempo_bpm: float,
    time_basis: dict[str, Any],
    als_identity: str,
) -> list[EvidenceItem]:
    region_label = str(region["label"])
    source_ref = ref.content_fingerprint
    items = [
        _item(
            "midi.source.project_identity",
            "midi_project_identity",
            ref.project_identity,
            pack=pack,
            region=region_label,
            source_ref=source_ref,
            kind=EvidenceKind.STATE_TOKEN,
        ),
        _item(
            "midi.source.entity_id",
            "midi_source_entity_id",
            source["entity_id"],
            pack=pack,
            region=region_label,
            source_ref=source_ref,
        ),
        _item(
            "midi.source.role",
            "midi_source_role",
            source.get("role") or ref.role,
            pack=pack,
            region=region_label,
            source_ref=source_ref,
        ),
        _item(
            "midi.source.track_type",
            "midi_source_track_type",
            track_info.get("track_type"),
            pack=pack,
            region=region_label,
            source_ref=source_ref,
        ),
        _item(
            "midi.source.content_fingerprint",
            "midi_source_content_fingerprint",
            ref.content_fingerprint,
            pack=pack,
            region=region_label,
            source_ref=source_ref,
        ),
        _item(
            "midi.source.persistent_object_ref",
            "midi_persistent_object_ref",
            ref.model_dump(mode="json"),
            pack=pack,
            region=region_label,
            source_ref=source_ref,
        ),
        _item(
            "midi.source.region",
            "midi_region",
            region,
            pack=pack,
            region=region_label,
            source_ref=source_ref,
        ),
        _item(
            "midi.region.start_qn",
            "midi_region_start_qn",
            float(region["start_qn"]),
            pack=pack,
            region=region_label,
            source_ref=source_ref,
            unit="qn",
        ),
        _item(
            "midi.region.end_qn",
            "midi_region_end_qn",
            float(region["end_qn"]),
            pack=pack,
            region=region_label,
            source_ref=source_ref,
            unit="qn",
        ),
        _item(
            "midi.status",
            "midi_material_status",
            midi_status,
            pack=pack,
            region=region_label,
            source_ref=source_ref,
        ),
        _item(
            "midi.notes_in_region",
            "midi_notes_in_region",
            len(notes),
            pack=pack,
            region=region_label,
            source_ref=source_ref,
            unit="count",
        ),
        _item(
            "midi.active_note_intervals",
            "midi_active_note_intervals",
            active_intervals,
            pack=pack,
            region=region_label,
            source_ref=source_ref,
        ),
        _item(
            "midi.gaps_without_note_activity",
            "midi_gaps_without_note_activity",
            gaps,
            pack=pack,
            region=region_label,
            source_ref=source_ref,
        ),
        _item(
            "midi.time_basis",
            "midi_time_basis",
            time_basis,
            pack=pack,
            region=region_label,
            source_ref=source_ref,
        ),
        _item(
            "midi.tempo_bpm",
            "midi_tempo_bpm",
            tempo_bpm,
            pack=pack,
            region=region_label,
            source_ref=source_ref,
            unit="bpm",
        ),
        _item(
            "midi.als_identity",
            "midi_als_identity",
            als_identity,
            pack=pack,
            region=region_label,
            source_ref=source_ref,
            kind=EvidenceKind.STATE_TOKEN,
        ),
        _item(
            "midi.alignment_envelope_ms",
            "midi_alignment_envelope_ms",
            float(pack.alignment_envelope_ms),
            pack=pack,
            region=region_label,
            source_ref=source_ref,
            unit="ms",
        ),
    ]
    for idx, clip in enumerate(clips):
        clip_ref = str(clip.get("clip_identity") or source_ref)
        items.append(
            _item(
                f"midi.clip.{idx}",
                "midi_clip",
                clip,
                pack=pack,
                region=region_label,
                source_ref=clip_ref,
            )
        )
        items.extend(
            [
                _item(
                    f"midi.clip.{idx}.arrangement_start_qn",
                    "midi_clip_arrangement_start_qn",
                    float(clip["arrangement_start_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=clip_ref,
                    unit="qn",
                ),
                _item(
                    f"midi.clip.{idx}.arrangement_end_qn",
                    "midi_clip_arrangement_end_qn",
                    float(clip["arrangement_end_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=clip_ref,
                    unit="qn",
                ),
                _item(
                    f"midi.clip.{idx}.loop_start_qn",
                    "midi_clip_loop_start_qn",
                    float(clip["loop_start_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=clip_ref,
                    unit="qn",
                ),
                _item(
                    f"midi.clip.{idx}.loop_end_qn",
                    "midi_clip_loop_end_qn",
                    float(clip["loop_end_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=clip_ref,
                    unit="qn",
                ),
                _item(
                    f"midi.clip.{idx}.start_relative_qn",
                    "midi_clip_start_relative_qn",
                    float(clip["start_relative_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=clip_ref,
                    unit="qn",
                ),
            ]
        )
    note_starts = [
        float(note["arrangement_start_qn"]) for note in notes if not note.get("muted")
    ]
    items.append(
        _item(
            "midi.clip_note_starts",
            "clip_note_starts",
            note_starts,
            pack=pack,
            region=region_label,
            source_ref=source_ref,
            unit="qn",
        )
    )
    for idx, note in enumerate(notes):
        note_ref = str(note.get("clip_identity") or source_ref)
        items.append(
            _item(
                f"midi.note.{idx}",
                "midi_note",
                note,
                pack=pack,
                region=region_label,
                source_ref=note_ref,
            )
        )
        items.extend(
            [
                _item(
                    f"midi.note.{idx}.arrangement_start_qn",
                    "midi_note_arrangement_start_qn",
                    float(note["arrangement_start_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=note_ref,
                    unit="qn",
                ),
                _item(
                    f"midi.note.{idx}.duration_qn",
                    "midi_note_duration_qn",
                    float(note["duration_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=note_ref,
                    unit="qn",
                ),
                _item(
                    f"midi.note.{idx}.clip_local_start_qn",
                    "midi_note_clip_local_start_qn",
                    float(note["clip_local_start_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=note_ref,
                    unit="qn",
                ),
                _item(
                    f"midi.note.{idx}.region_overlap_start_qn",
                    "midi_note_region_overlap_start_qn",
                    float(note["region_overlap_start_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=note_ref,
                    unit="qn",
                ),
                _item(
                    f"midi.note.{idx}.region_overlap_end_qn",
                    "midi_note_region_overlap_end_qn",
                    float(note["region_overlap_end_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=note_ref,
                    unit="qn",
                ),
            ]
        )
    for idx, gap in enumerate(gaps):
        items.extend(
            [
                _item(
                    f"midi.gap.{idx}.start_qn",
                    "midi_gap_start_qn",
                    float(gap["start_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=source_ref,
                    unit="qn",
                ),
                _item(
                    f"midi.gap.{idx}.end_qn",
                    "midi_gap_end_qn",
                    float(gap["end_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=source_ref,
                    unit="qn",
                ),
            ]
        )
    for row in relations:
        items.append(
            _item(
                f"midi.relation.{row['event_id']}",
                "midi_audio_relation",
                row,
                pack=pack,
                region=region_label,
                source_ref=source_ref,
            )
        )
        if row.get("start_qn") is not None:
            items.append(
                _item(
                    f"midi.relation.{row['event_id']}.start_qn",
                    "midi_relation_start_qn",
                    float(row["start_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=source_ref,
                    unit="qn",
                )
            )
        if row.get("end_qn") is not None:
            items.append(
                _item(
                    f"midi.relation.{row['event_id']}.end_qn",
                    "midi_relation_end_qn",
                    float(row["end_qn"]),
                    pack=pack,
                    region=region_label,
                    source_ref=source_ref,
                    unit="qn",
                )
            )
    _assert_factual([item.model_dump(mode="json") for item in items])
    return items


def build_child_pack(
    parent: EvidencePack,
    added: list[EvidenceItem],
    *,
    midi_status: str,
) -> EvidencePack:
    limitations = [
        row
        for row in parent.limitations
        if row.code != "MIDI_UNREAD"
    ]
    limitations.append(
        ObservationLimitation(
            code="MIDI_READ_ONLY_V1",
            detail=(
                "Arrangement MIDI was read from the saved ALS. "
                "Note presence ≠ audible Main contribution. "
                f"status={midi_status}"
            ),
            precision_ms=float(parent.alignment_envelope_ms),
            capability_ms=[float(parent.alignment_envelope_ms)],
        )
    )
    limitations.append(
        ObservationLimitation(
            code="CROSS_SOURCE_ALIGNMENT_LIMITED",
            detail="Cross-source audio/MIDI relation remains LIMITED ±52 ms. No sample-accurate causal claim.",
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


def collect_midi(
    *,
    parent_path: Path | None = None,
    als_path: Path | None = None,
    journal_dir: Path | None = None,
    persisted_ref: PersistentObjectRef | None = None,
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
        "MUSICAL WRITES": 0,
        "alignment_claim": parent.alignment_claim,
        "alignment_envelope_ms": parent.alignment_envelope_ms,
    }
    sources = near_silent_sources(parent)
    report["near_silent_sources"] = [
        {key: value for key, value in row.items() if key != "element"}
        for row in sources
    ]
    if len(sources) != 1:
        report["status"] = "BLOCKED"
        report[MILESTONE] = "BLOCKED"
        report["BLOCKER"] = "NEAR_SILENT_SOURCE_NOT_UNIQUE"
        report["parent_file_unchanged"] = (
            hashlib.sha256(parent_path.read_bytes()).hexdigest() == parent_file_sha
        )
        return report
    source = sources[0]
    journal_ref = persisted_ref or load_persisted_ref(
        source["entity_id"], journal_dir=journal_dir
    )
    if journal_ref is None:
        report["status"] = "BLOCKED"
        report[MILESTONE] = "BLOCKED"
        report["BLOCKER"] = "PERSISTED_REF_MISSING"
        report["parent_file_unchanged"] = (
            hashlib.sha256(parent_path.read_bytes()).hexdigest() == parent_file_sha
        )
        return report
    bound = bind_operational_ref(
        journal_ref, pack_identity=pack_identity, entity_id=source["entity_id"]
    )
    if not bound.get("ok"):
        report["status"] = "BLOCKED"
        report[MILESTONE] = "BLOCKED"
        report["BLOCKER"] = bound.get("error")
        report["source_resolve"] = bound
        report["parent_file_unchanged"] = (
            hashlib.sha256(parent_path.read_bytes()).hexdigest() == parent_file_sha
        )
        return report
    ref: PersistentObjectRef = bound["ref"]
    report["source_resolve"] = {
        "project": pack_identity,
        "track_ref": ref.model_dump(mode="json"),
        "entity_id": source["entity_id"],
        "role": source.get("role") or ref.role,
        "region": region,
        "journal_project_identity": bound["journal_project_identity"],
        "name_is_locator_only": True,
        "used_display_name": False,
        "identity_from": "PersistentObjectRef",
    }
    if session is not None:
        resolved = resolve_track(session, ref)
        report["source_resolve"]["session_resolve"] = resolved.model_dump(mode="json")
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
    report["als"] = {key: value for key, value in located.items() if key != "root"}
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
    tempo = als_tempo_bpm(root)
    matched = match_als_track(root, ref)
    report["source_resolve"]["track_type"] = matched.get("track_type")
    report["source_resolve"]["locator_name"] = matched.get("locator_name")
    report["source_resolve"]["used_display_name"] = matched.get("used_display_name", False)
    report["source_resolve"]["identity_from"] = matched.get("identity_from")
    if not matched.get("ok"):
        report["status"] = "BLOCKED"
        report[MILESTONE] = "BLOCKED"
        report["BLOCKER"] = matched.get("error")
        report["als_sha256_before"] = als_sha_before
        report["als_sha256_after"] = sha256_file(als) or ""
        report["ALS_UNCHANGED"] = report["als_sha256_after"] == als_sha_before
        report["parent_file_unchanged"] = (
            hashlib.sha256(parent_path.read_bytes()).hexdigest() == parent_file_sha
        )
        return report
    midi_capable = bool(matched.get("midi_capable"))
    midi_status = MIDI_NOT_APPLICABLE
    clips: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    if midi_capable:
        read = read_arrangement_midi(
            root,
            matched["element"],
            matched["parents"],
            region_start=float(region["start_qn"]),
            region_end=float(region["end_qn"]),
        )
        clips = read["clips"]
        notes = read["notes"]
        midi_status = derive_midi_status(notes, midi_capable=True)
    active = [
        {
            "start_qn": float(row["region_overlap_start_qn"]),
            "end_qn": float(row["region_overlap_end_qn"]),
            "pitch": row.get("pitch"),
            "clip_identity": row.get("clip_identity"),
        }
        for row in notes
        if not row.get("muted")
    ]
    active_pairs = [(float(row["start_qn"]), float(row["end_qn"])) for row in active]
    gaps = gaps_without_activity(
        float(region["start_qn"]), float(region["end_qn"]), active_pairs
    )
    envelope_ms = float(parent.alignment_envelope_ms)
    relations: list[dict[str, Any]] = []
    if tempo is None:
        tempo_bpm = 0.0
        envelope_qn = 0.0
    else:
        tempo_bpm = float(tempo)
        envelope_qn = (envelope_ms / 1000.0) * (tempo_bpm / 60.0)
    events = load_main_events(parent)
    for event in events:
        if not event["relevant"]:
            continue
        if tempo is None or midi_status == MIDI_NOT_APPLICABLE:
            relation = RELATION_UNCERTAIN
            start_qn = None
            end_qn = None
        else:
            start_qn = float(region["start_qn"]) + float(event["start_s"]) * (
                tempo_bpm / 60.0
            )
            end_qn = float(region["start_qn"]) + float(event["end_s"]) * (
                tempo_bpm / 60.0
            )
            relation = classify_audio_midi_relation(
                event_start_qn=start_qn,
                event_end_qn=end_qn,
                active_intervals=active_pairs,
                envelope_qn=envelope_qn,
            )
        relations.append(
            {
                "event_id": event["event_id"],
                "kind": event["kind"],
                "start_s": event["start_s"],
                "end_s": event["end_s"],
                "start_qn": start_qn,
                "end_qn": end_qn,
                "relation": relation,
                "alignment_claim": parent.alignment_claim,
                "alignment_envelope_ms": envelope_ms,
            }
        )
    time_basis = {
        "arrangement": "qn",
        "clip_local": "qn",
        "formula": TIME_BASIS_FORMULA,
        "main_local_s_to_arrangement_qn": "region_start_qn + t_s * tempo_bpm / 60",
        "tempo_bpm": tempo_bpm,
        "region_qn": [region["start_qn"], region["end_qn"]],
        "alignment_envelope_ms": envelope_ms,
        "alignment_envelope_qn": envelope_qn,
    }
    added = midi_evidence_items(
        pack=parent,
        region=region,
        source=source,
        ref=ref,
        track_info=matched,
        midi_status=midi_status,
        clips=clips,
        notes=notes,
        active_intervals=active,
        gaps=gaps,
        relations=relations,
        tempo_bpm=tempo_bpm,
        time_basis=time_basis,
        als_identity=str(located.get("identity") or ""),
    )
    child = build_child_pack(parent, added, midi_status=midi_status)
    als_sha_after = sha256_file(als) or ""
    after_parent = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    outcome = factual_outcome(relations, midi_status)
    report.update(
        {
            "status": "VERIFIED",
            MILESTONE: "PACK_READY",
            "new_pack_id": child.pack_id,
            "new_payload_sha256": pack_payload_hash(child),
            "midi_status": midi_status,
            "notes_in_region": len(notes),
            "clips_in_region": len(clips),
            "MIDI_EVIDENCE_OUTCOME": outcome,
            "relations": relations,
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
    if midi_status == MIDI_NOT_APPLICABLE:
        report["branch"] = "MIDI_NOT_APPLICABLE"
    return report


def _next_evidence_request(
    accepted: bool,
    status: str,
    requested: list[dict[str, Any]],
    outcome: dict[str, Any] | None,
) -> tuple[str | None, dict[str, Any] | None]:
    if not accepted or status != DiagnosisStatus.INSUFFICIENT_EVIDENCE.value:
        return None, None
    kinds = [str(item.get("request_kind") or "") for item in requested]
    has_view = "CAPTURE_VIEW" in kinds
    has_region = "ANALYZE_REGION" in kinds
    explanation: dict[str, Any] = {}
    if has_view:
        explanation["CAPTURE_VIEW"] = (
            "Resolves why a MIDI-capable source can be near-silent while notes "
            "are present: devices, clips, automation, and causal device state."
        )
    if has_region:
        explanation["ANALYZE_REGION"] = (
            "Resolves whether near-silent intervals with no MIDI are intended "
            "arrangement context rather than a local source fault."
        )
    if has_view and has_region:
        return "CAPTURE_VIEW+ANALYZE_REGION", explanation
    if has_view:
        return "CAPTURE_VIEW", explanation
    if has_region:
        return "ANALYZE_REGION", explanation
    if requested:
        return json.dumps(requested, ensure_ascii=False), explanation or None
    if outcome and outcome.get("B") and not outcome.get("A"):
        return None, {
            "note": "Astra did not request more evidence; B is a fact, not an automatic CAPTURE_VIEW."
        }
    return None, None


def replay_midi(
    *,
    evidence: Path | None = None,
    parent_path: Path | None = None,
    als_path: Path | None = None,
    journal_dir: Path | None = None,
    persisted_ref: PersistentObjectRef | None = None,
    search_roots: list[Path] | None = None,
    provider: ReasoningProvider | None = None,
    timeout_s: float = ASTRA_TIMEOUT_S,
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    built = collect_midi(
        parent_path=parent_path,
        als_path=als_path,
        journal_dir=journal_dir,
        persisted_ref=persisted_ref,
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
        result.accepted,
        astra_status,
        requested,
        built.get("MIDI_EVIDENCE_OUTCOME"),
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
    report = replay_midi()
    summary = {
        "milestone": MILESTONE,
        "parent_pack_id": report.get("parent_pack_id"),
        "new_pack_id": report.get("new_pack_id"),
        "midi_status": report.get("midi_status"),
        "MIDI_EVIDENCE_OUTCOME": report.get("MIDI_EVIDENCE_OUTCOME"),
        "accepted": report.get("accepted"),
        "ASTRA RESULT": report.get("ASTRA RESULT"),
        "NEXT_EVIDENCE_REQUEST": report.get("NEXT_EVIDENCE_REQUEST"),
        MILESTONE: report.get(MILESTONE),
        "ALS_UNCHANGED": report.get("ALS_UNCHANGED"),
        "parent_file_unchanged": report.get("parent_file_unchanged"),
        "NO MIDI WRITE": True,
        "NO RECAPTURE": True,
        "NO MUSICAL WRITE": True,
        "MUSICAL WRITES": 0,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get(MILESTONE) == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
