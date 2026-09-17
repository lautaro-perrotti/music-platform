"""EXTERNAL_CAUSAL_CONTEXT_V1 — other-source MIDI, matched windows, routing. No writes."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.audio.active_source_region_analysis_v1 import (
    SOURCE_ENERGY_DIP,
    SOURCE_EVIDENCE_UNAVAILABLE,
    SOURCE_HAS_SIGNAL,
    SOURCE_NEAR_SILENCE,
    SOURCE_NO_MATERIAL,
    analyze_source_event_window,
    arrangement_context,
    captured_locator_index,
    clip_index,
    coverage_for_event,
    journal_for_source_hash,
    locate_wav_by_sha256,
    pack_source_captures,
)
from copilot.audio.analyze_region_v1 import load_region_events, target_source_id
from copilot.audio.astra_external_reasoning_v1 import (
    RecordingProvider,
    classify_rejection,
    load_persisted_pack,
    pack_payload_hash,
)
from copilot.audio.file_hash import sha256_file
from copilot.audio.midi_read_only_v1 import (
    INSTRUMENT_TAGS,
    MIDI_NOT_APPLICABLE,
    MIDI_PRESENT_DURING_INTERVAL,
    NO_MIDI_EXPECTED_DURING_INTERVAL,
    RELATION_UNCERTAIN,
    als_tempo_bpm,
    bind_operational_ref,
    classify_audio_midi_relation,
    derive_midi_status,
    gaps_without_activity,
    identity_for_als_path,
    load_persisted_ref,
    locate_working_copy_als,
    match_als_track,
    pack_project_identity,
    pack_region,
    read_arrangement_midi,
    _load_als_root,
    _local,
    _parent_map,
    _track_locator_name,
    _attr_value,
    TRACK_TAGS,
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
from copilot.schemas.session import SessionState, TrackState

MILESTONE = "EXTERNAL_CAUSAL_CONTEXT_V1"
ARTIFACT = "external_causal_context_v1.json"
STATUS = "IMPLEMENTED"
ANALYSIS_VERSION = "external-causal-context-v1"
EXPECTED_PARENT_PACK_ID = "pack_e78a65ab902e"
PARENT_PACK_PATH = Path("logs") / "active_source_region_analysis_v1.json"
CAPTURE_ROOTS = (Path("logs") / "captures",)
REQUESTED_MIDI_LOCATORS = ("Filter Kick", "Filtered Bassline", "Open Hi Hat")
MISSING_ACTIVE_LOCATORS = ("Vox FX", "C'mon", "Vinyl Noise FX")
MIDI_RELATION_UNCERTAIN = RELATION_UNCERTAIN
DIRECT_SIGNAL_PATH = "DIRECT_SIGNAL_PATH"
GROUP_PATH = "GROUP_PATH"
RETURN_PATH = "RETURN_PATH"
SIDECHAIN_CONTROL_PATH = "SIDECHAIN_CONTROL_PATH"
NO_DIRECT_MAIN_PATH = "NO_DIRECT_MAIN_PATH"
ROUTING_UNKNOWN = "ROUTING_UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"
MAIN_ALIASES = {"main", "master", "ext. out", "external out"}
FORBIDDEN_RE = re.compile(
    r"\b(mix fault|device failure|weak pattern|bad groove|wrong note|missing musical idea)\b",
    re.IGNORECASE,
)


def _assert_factual(payload: Any) -> None:
    blob = json.dumps(payload, ensure_ascii=False)
    hit = FORBIDDEN_RE.search(blob)
    if hit:
        raise ValueError(f"causal evidence leaked diagnosis language: {hit.group(0)}")


MIXER_DEVICE_LABELS = frozenset(
    {"EQ Eight", "Utility", "Saturator", "Reverb", "Delay", "Erosion Legacy"}
)


def match_requested_als_track(root: Any, ref: PersistentObjectRef) -> dict[str, Any]:
    """Persisted identity first. Drop mixer devices if ALS omits them."""
    matched = match_als_track(root, ref)
    if matched.get("ok"):
        return matched
    inst_classes = [item for item in ref.device_classes if item in INSTRUMENT_TAGS]
    inst_names = [item for item in ref.device_names if item not in MIXER_DEVICE_LABELS]
    slim = ref.model_copy(update={"device_classes": inst_classes, "device_names": inst_names})
    retry = match_als_track(root, slim)
    if retry.get("ok"):
        retry["identity_from"] = "PersistentObjectRef.instrument_devices+locator_tiebreak"
        retry["als_omitted_mixer_devices"] = True
        return retry
    empty = slim.model_copy(update={"device_names": [], "device_classes": []})
    retry = match_als_track(root, empty)
    if retry.get("ok"):
        retry["identity_from"] = "PersistentObjectRef.role+unique_locator_after_mixer_devices_omitted"
        retry["als_omitted_mixer_devices"] = True
        return retry
    return matched


def requested_midi_targets(
    pack: EvidencePack, *, journal_dir: Path | None = None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for capture in pack_source_captures(pack):
        fingerprint = str(capture.get("content_fingerprint") or "")
        if not fingerprint or fingerprint in seen:
            continue
        ref = load_persisted_ref(fingerprint, journal_dir=journal_dir)
        locator = "" if ref is None else str(ref.name or "")
        if locator not in REQUESTED_MIDI_LOCATORS:
            continue
        seen.add(fingerprint)
        rows.append(
            {
                "content_fingerprint": fingerprint,
                "locator_name": locator,
                "evidence_id": capture["evidence_id"],
                "ref": ref,
            }
        )
    return rows


def filter_notes_to_windows(
    notes: list[dict[str, Any]],
    windows: list[tuple[float, float]],
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for note in notes:
        start = float(note.get("region_overlap_start_qn") or note.get("arrangement_start_qn") or 0.0)
        end = float(note.get("region_overlap_end_qn") or (start + float(note.get("duration_qn") or 0.0)))
        if any(max(start, lo) < min(end, hi) for lo, hi in windows):
            kept.append(note)
    return kept


def classify_midi_interval(
    *,
    midi_capable: bool,
    event_start_qn: float,
    event_end_qn: float,
    active_intervals: list[tuple[float, float]],
    envelope_qn: float,
) -> str:
    if not midi_capable:
        return MIDI_NOT_APPLICABLE
    relation = classify_audio_midi_relation(
        event_start_qn=event_start_qn,
        event_end_qn=event_end_qn,
        active_intervals=active_intervals,
        envelope_qn=envelope_qn,
    )
    if relation == RELATION_UNCERTAIN:
        return MIDI_RELATION_UNCERTAIN
    return relation


def _output_looks_main(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    return any(alias in text for alias in MAIN_ALIASES)


def classify_routing_kind(
    *,
    output_type: str | None,
    grouped: bool = False,
    parent_group: str | None = None,
    return_names: list[str] | None = None,
    send_enabled: bool = False,
    sidechain_control: bool = False,
) -> str:
    if sidechain_control:
        return SIDECHAIN_CONTROL_PATH
    output = str(output_type or "").strip()
    if not output:
        return ROUTING_UNKNOWN
    returns = {name.lower() for name in (return_names or []) if name}
    if output.lower() in returns:
        return RETURN_PATH
    if "group" in output.lower() or grouped or parent_group:
        return GROUP_PATH
    if _output_looks_main(output):
        return DIRECT_SIGNAL_PATH
    if send_enabled and not _output_looks_main(output):
        return RETURN_PATH
    return NO_DIRECT_MAIN_PATH


def routing_hops(
    *,
    source_locator: str,
    grouped: bool,
    parent_group: str | None,
    output_type: str | None,
) -> list[str]:
    hops = [source_locator]
    if grouped and parent_group:
        hops.append(parent_group)
    target = str(output_type or "").strip() or ROUTING_UNKNOWN
    if target and target not in hops:
        hops.append(target)
    if _output_looks_main(target) and "Main" not in hops:
        hops.append("Main")
    return hops


def session_parent_groups(tracks: list[TrackState]) -> dict[int, str | None]:
    parents: dict[int, str | None] = {}
    current: str | None = None
    for track in sorted(tracks, key=lambda item: item.index):
        if track.foldable:
            current = track.name
            parents[track.index] = None
            continue
        if not track.grouped:
            current = None
        parents[track.index] = current if track.grouped else None
    return parents


def read_session_routing(
    session: SessionState,
    *,
    fingerprints: dict[str, PersistentObjectRef],
    sidechain_sources: set[str] | None = None,
) -> list[dict[str, Any]]:
    parents = session_parent_groups(list(session.tracks))
    return_names = [track.name for track in session.tracks if track.role == "return"]
    rows: list[dict[str, Any]] = []
    sidechain = sidechain_sources or set()
    by_index = {track.index: track for track in session.tracks}
    for fingerprint, ref in fingerprints.items():
        resolved = resolve_track(session, ref)
        if resolved.status is ResolveStatus.PROJECT_MISMATCH:
            rows.append(
                {
                    "content_fingerprint": fingerprint,
                    "ok": False,
                    "error": "PROJECT_MISMATCH",
                    "kind": ROUTING_UNKNOWN,
                }
            )
            continue
        track = None
        if resolved.status is ResolveStatus.RESOLVED and resolved.track_index is not None:
            track = by_index.get(resolved.track_index)
        if track is None:
            rows.append(
                {
                    "content_fingerprint": fingerprint,
                    "ok": False,
                    "error": resolved.status.value,
                    "kind": ROUTING_UNKNOWN,
                    "output_type": NOT_APPLICABLE,
                    "capability": NOT_APPLICABLE,
                }
            )
            continue
        parent = parents.get(track.index)
        sends = [
            {"index": send.index, "name": send.name, "value": send.value, "enabled": send.value > 1e-6}
            for send in track.sends
        ]
        send_enabled = any(item["enabled"] for item in sends)
        kind = classify_routing_kind(
            output_type=track.routing.output_type,
            grouped=track.grouped,
            parent_group=parent,
            return_names=return_names,
            send_enabled=send_enabled,
            sidechain_control=False,
        )
        control_only = fingerprint in sidechain or track.name in sidechain
        hops = routing_hops(
            source_locator=track.name,
            grouped=track.grouped,
            parent_group=parent,
            output_type=track.routing.output_type,
        )
        rows.append(
            {
                "content_fingerprint": fingerprint,
                "ok": True,
                "locator_name": track.name,
                "name_is_locator_only": True,
                "persistent_object_ref": ref.model_dump(mode="json"),
                "direct_output_target": track.routing.output_type or NOT_APPLICABLE,
                "output_channel": track.routing.output_channel or NOT_APPLICABLE,
                "group_membership": parent,
                "grouped": track.grouped,
                "sends": sends,
                "kind": kind,
                "control_kind": SIDECHAIN_CONTROL_PATH if control_only else None,
                "hops": hops,
                "sidechain_control": control_only,
                "send_is_not_audible_contribution": True,
                "midi_capable": track.role == "midi",
                "track_type": track.role,
            }
        )
    return rows


def als_track_output(track: Any) -> str:
    for elem in track.iter():
        if _local(elem.tag) not in {"AudioOutputRouting", "MidiOutputRouting"}:
            continue
        for child in elem.iter():
            if _local(child.tag) in {"UpperDisplayString", "Target"}:
                value = child.attrib.get("Value")
                if value:
                    return value
    return ""


def als_group_parents(root: Any) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    current: str | None = None
    idx = 0
    for track in root.iter():
        tag = _local(track.tag)
        if tag not in TRACK_TAGS:
            continue
        locator = _track_locator_name(track)
        grouped = str(_attr_value(track, "IsGrouped") or "").lower() in {"true", "1"}
        foldable = tag == "GroupTrack" or str(_attr_value(track, "IsFoldable") or "").lower() in {
            "true",
            "1",
        }
        if foldable:
            current = locator
            parent = None
        elif not grouped:
            current = None
            parent = None
        else:
            parent = current
        rows[id(track)] = {
            "locator_name": locator,
            "track_type": tag,
            "grouped": grouped,
            "foldable": foldable,
            "parent_group": parent,
            "output": als_track_output(track),
            "element": track,
        }
        idx += 1
    return rows


def als_sidechain_sources(root: Any) -> set[str]:
    names: set[str] = set()
    for elem in root.iter():
        if _local(elem.tag) != "SideChain":
            continue
        for child in elem.iter():
            if _local(child.tag) in {"Source", "InputRoutable", "UpperDisplayString"}:
                value = (child.attrib.get("Value") or "").strip()
                if value:
                    names.add(value)
    return names


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
        view="TRACK_ISOLATED",
        analysis_version=ANALYSIS_VERSION,
        name=name,
        value=value,
        unit=unit,
        quality=CaptureQuality.LIMITED,
        limitations=["MEASURE_ONLY", "ALIGNMENT_LIMITED", "CAUSAL_CONTEXT_ONLY"],
        project_token=pack.project_token,
        audible_token=pack.audible_token,
        target_token=pack.target_token,
    )


def build_child_pack(parent: EvidencePack, added: list[EvidenceItem]) -> EvidencePack:
    limitations = list(parent.limitations)
    limitations.append(
        ObservationLimitation(
            code="EXTERNAL_CAUSAL_CONTEXT_V1",
            detail=(
                "Other-source MIDI, matched Post Mixer windows, and read-only routing. "
                "MIDI present with quiet Post Mixer does not establish a device cause. "
                "A send is not an audible contribution."
            ),
            precision_ms=float(parent.alignment_envelope_ms),
            capability_ms=[float(parent.alignment_envelope_ms)],
        )
    )
    limitations.append(
        ObservationLimitation(
            code="SIDECHAIN_CONTROL_PATH_DISTINCT",
            detail="Sidechain taps are control/context, not mix sources.",
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


def journal_locator_captures(
    locators: tuple[str, ...],
    *,
    journal_dir: Path | None,
    capture_roots: list[Path] | None,
    existing: set[str],
) -> list[dict[str, Any]]:
    directory = Path(journal_dir or JOURNAL_DIR)
    rows: list[dict[str, Any]] = []
    if not directory.is_dir():
        return rows
    wanted = set(locators)
    for path in sorted(directory.glob("*.jsonl")):
        ref = None
        digest = None
        status = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row.get("ref"), dict):
                ref = PersistentObjectRef.model_validate(row["ref"])
            hashes = row.get("hashes") or {}
            if hashes.get("source"):
                digest = str(hashes["source"])
            status = row.get("status") or status
        if ref is None or ref.name not in wanted or ref.content_fingerprint in existing:
            continue
        wav = locate_wav_by_sha256(digest or "", capture_roots)
        ok = wav is not None and bool(digest) and sha256_file(wav) == digest
        rows.append(
            {
                "evidence_id": f"ev.source.extra.{ref.content_fingerprint[:12]}.capture",
                "content_fingerprint": ref.content_fingerprint,
                "declared_sha256": digest or "",
                "ok": ok,
                "wav_path": None if wav is None else str(wav),
                "sha256": None if wav is None else sha256_file(wav),
                "journal_status": status,
                "locator_name": ref.name,
                "name_is_locator_only": True,
            }
        )
        existing.add(ref.content_fingerprint)
    return rows


def collect_causal_context(
    *,
    parent_path: Path | None = None,
    als_path: Path | None = None,
    journal_dir: Path | None = None,
    capture_roots: list[Path] | None = None,
    search_roots: list[Path] | None = None,
    session: SessionState | None = None,
    sidechain_sources: set[str] | None = None,
) -> dict[str, Any]:
    parent_path = Path(parent_path or PARENT_PACK_PATH)
    parent_bytes = parent_path.read_bytes()
    parent_file_sha = hashlib.sha256(parent_bytes).hexdigest()
    parent = load_persisted_pack(parent_path)
    region = pack_region(parent)
    pack_identity = pack_project_identity(parent)
    envelope_ms = float(parent.alignment_envelope_ms)
    report: dict[str, Any] = {
        "milestone": MILESTONE,
        "ts": now_iso(),
        "parent_pack_id": parent.pack_id,
        "parent_payload_sha256": pack_payload_hash(parent),
        "parent_file_sha256": parent_file_sha,
        "parent_path": str(parent_path),
        "NO MIDI WRITE": True,
        "NO ROUTING WRITE": True,
        "NO MUSICAL WRITE": True,
        "MUSICAL WRITES": 0,
        "CAPTURE_VIEW": False,
        "alignment_claim": parent.alignment_claim,
        "alignment_envelope_ms": envelope_ms,
    }
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
    if session is not None and session.project_identity and session.project_identity != pack_identity:
        report["status"] = "BLOCKED"
        report[MILESTONE] = "BLOCKED"
        report["BLOCKER"] = "PROJECT_MISMATCH"
        report["parent_file_unchanged"] = True
        return report
    root = _load_als_root(als)
    tempo = als_tempo_bpm(root) or 0.0
    envelope_qn = (envelope_ms / 1000.0) * (tempo / 60.0) if tempo else 0.0
    events = [event for event in load_region_events(parent) if event.get("relevant")]
    event_windows: list[tuple[float, float]] = []
    event_qn: dict[str, dict[str, Any]] = {}
    for event in events:
        start_qn = float(region["start_qn"]) + float(event["start_s"]) * (tempo / 60.0 if tempo else 0.0)
        end_qn = float(region["start_qn"]) + float(event["end_s"]) * (tempo / 60.0 if tempo else 0.0)
        event_qn[event["event_id"]] = {**event, "start_qn": start_qn, "end_qn": end_qn}
        event_windows.append((start_qn - envelope_qn, end_qn + envelope_qn))

    midi_rows: list[dict[str, Any]] = []
    midi_refs: dict[str, PersistentObjectRef] = {}
    for target in requested_midi_targets(parent, journal_dir=journal_dir):
        journal_ref = target["ref"]
        fingerprint = target["content_fingerprint"]
        if journal_ref is None:
            midi_rows.append(
                {
                    "content_fingerprint": fingerprint,
                    "locator_name": target["locator_name"],
                    "ok": False,
                    "error": "PERSISTED_REF_MISSING",
                    "midi_capable": False,
                    "midi_status": MIDI_RELATION_UNCERTAIN,
                    "relations": [],
                    "notes": [],
                    "clips": [],
                }
            )
            continue
        bound = bind_operational_ref(
            journal_ref, pack_identity=pack_identity, entity_id=fingerprint
        )
        if not bound.get("ok"):
            midi_rows.append(
                {
                    "content_fingerprint": fingerprint,
                    "locator_name": target["locator_name"],
                    "ok": False,
                    "error": bound.get("error"),
                    "midi_capable": False,
                    "midi_status": MIDI_RELATION_UNCERTAIN,
                    "relations": [],
                    "notes": [],
                    "clips": [],
                }
            )
            continue
        ref: PersistentObjectRef = bound["ref"]
        midi_refs[fingerprint] = ref
        matched = match_requested_als_track(root, ref)
        midi_capable = bool(matched.get("midi_capable"))
        clips: list[dict[str, Any]] = []
        notes: list[dict[str, Any]] = []
        if matched.get("ok") and midi_capable:
            read = read_arrangement_midi(
                root,
                matched["element"],
                matched["parents"],
                region_start=float(region["start_qn"]),
                region_end=float(region["end_qn"]),
            )
            clips = read["clips"]
            notes = filter_notes_to_windows(read["notes"], event_windows)
        elif matched.get("ok") and not midi_capable:
            midi_capable = False
        midi_status = (
            MIDI_NOT_APPLICABLE
            if not midi_capable
            else derive_midi_status(notes, midi_capable=True)
        )
        active_pairs = [
            (float(row["region_overlap_start_qn"]), float(row["region_overlap_end_qn"]))
            for row in notes
            if not row.get("muted")
        ]
        relations = []
        for event in events:
            mapped = event_qn[event["event_id"]]
            relation = classify_midi_interval(
                midi_capable=midi_capable,
                event_start_qn=float(mapped["start_qn"]),
                event_end_qn=float(mapped["end_qn"]),
                active_intervals=active_pairs,
                envelope_qn=envelope_qn,
            )
            relations.append(
                {
                    "event_id": event["event_id"],
                    "kind": event["kind"],
                    "start_qn": mapped["start_qn"],
                    "end_qn": mapped["end_qn"],
                    "relation": relation,
                    "active_note_count": sum(
                        1
                        for start, end in active_pairs
                        if max(start, mapped["start_qn"]) < min(end, mapped["end_qn"])
                    ),
                    "gaps": gaps_without_activity(
                        float(mapped["start_qn"]),
                        float(mapped["end_qn"]),
                        active_pairs,
                    ),
                }
            )
        midi_rows.append(
            {
                "content_fingerprint": fingerprint,
                "locator_name": matched.get("locator_name") or target["locator_name"],
                "ok": bool(matched.get("ok")),
                "error": None if matched.get("ok") else matched.get("error"),
                "track_type": matched.get("track_type"),
                "midi_capable": midi_capable,
                "midi_status": midi_status,
                "name_is_locator_only": True,
                "used_display_name": matched.get("used_display_name", False),
                "clips": clips,
                "notes": notes,
                "relations": relations,
                "persistent_object_ref": ref.model_dump(mode="json"),
            }
        )

    captures = pack_source_captures(parent)
    provenances: list[dict[str, Any]] = []
    for capture in captures:
        digest = capture["declared_sha256"]
        wav = locate_wav_by_sha256(digest, capture_roots)
        journal = journal_for_source_hash(digest, journal_dir=journal_dir)
        ref = load_persisted_ref(capture["content_fingerprint"], journal_dir=journal_dir)
        ok = wav is not None and bool(digest) and sha256_file(wav) == digest
        provenances.append(
            {
                **capture,
                "ok": ok,
                "wav_path": None if wav is None else str(wav),
                "sha256": None if wav is None else sha256_file(wav),
                "journal_status": journal.get("status"),
                "locator_name": None if ref is None else ref.name,
                "name_is_locator_only": True,
            }
        )
        if ok and ref is not None:
            midi_refs.setdefault(capture["content_fingerprint"], ref)
    provenances.extend(
        journal_locator_captures(
            MISSING_ACTIVE_LOCATORS,
            journal_dir=journal_dir,
            capture_roots=capture_roots,
            existing={str(row.get("content_fingerprint") or "") for row in provenances},
        )
    )
    for capture in provenances:
        fp = str(capture.get("content_fingerprint") or "")
        if not fp or fp in midi_refs or not capture.get("ok"):
            continue
        extra_ref = load_persisted_ref(fp, journal_dir=journal_dir)
        if extra_ref is not None:
            midi_refs[fp] = extra_ref

    clips = clip_index(parent)
    captured_fps = {row["content_fingerprint"] for row in provenances if row.get("ok")}
    locator_to_fp = captured_locator_index(provenances)
    audio_by_event: list[dict[str, Any]] = []
    for event in events:
        mapped = event_qn[event["event_id"]]
        context = arrangement_context(parent, event["event_id"])
        identities = list(context.get("active_clip_identities") or [])
        coverage = coverage_for_event(
            active_identities=identities,
            clips=clips,
            captured_fingerprints=captured_fps,
            locator_to_fingerprint=locator_to_fp,
        )
        sources: list[dict[str, Any]] = []
        for capture in provenances:
            base = {
                "evidence_id": capture["evidence_id"],
                "content_fingerprint": capture["content_fingerprint"],
                "locator_name": capture.get("locator_name"),
            }
            if not capture.get("ok"):
                sources.append({**base, "relation": SOURCE_EVIDENCE_UNAVAILABLE})
                continue
            stats = analyze_source_event_window(
                Path(capture["wav_path"]),
                event_audio_start_s=float(event["start_s"]),
                event_audio_end_s=float(event["end_s"]),
                envelope_s=envelope_ms / 1000.0,
            )
            sources.append({**base, **stats})
        missing = [
            {
                "locator_name": row.get("locator_name"),
                "clip_identity": row.get("clip_identity"),
                "status": "TRUTHFULLY_UNAVAILABLE",
                "relation": SOURCE_EVIDENCE_UNAVAILABLE,
            }
            for row in coverage.get("uncaptured_active") or []
        ]
        audio_by_event.append(
            {
                "event_id": event["event_id"],
                "kind": event["kind"],
                "start_qn": mapped["start_qn"],
                "end_qn": mapped["end_qn"],
                "start_s": event["start_s"],
                "end_s": event["end_s"],
                "ARRANGEMENT_ACTIVE_SOURCE_COUNT": coverage["ACTIVE_SOURCE_COUNT"],
                "CAPTURED_SOURCE_COUNT": coverage["CAPTURED_SOURCE_COUNT"],
                "UNCAPTURED_SOURCE_COUNT": coverage["UNCAPTURED_ACTIVE_SOURCE_COUNT"],
                "incomplete": coverage["incomplete"],
                "sources": sources,
                "uncaptured_active": missing,
                "coverage": coverage,
            }
        )
    requested_active_uncaptured = {
        str(row.get("locator_name") or "")
        for event in audio_by_event
        for row in event.get("uncaptured_active") or []
        if str(row.get("locator_name") or "") in MISSING_ACTIVE_LOCATORS
    }
    source_audio_coverage = (
        "INCOMPLETE" if any(event["incomplete"] for event in audio_by_event) else "COMPLETE"
    )

    als_parents = als_group_parents(root)
    als_sidechain = als_sidechain_sources(root)
    if sidechain_sources:
        als_sidechain |= set(sidechain_sources)
    routing_rows: list[dict[str, Any]] = []
    if session is not None:
        routing_rows = read_session_routing(
            session, fingerprints=midi_refs, sidechain_sources=als_sidechain
        )
    else:
        for fingerprint, ref in midi_refs.items():
            matched = match_requested_als_track(root, ref)
            if not matched.get("ok"):
                routing_rows.append(
                    {
                        "content_fingerprint": fingerprint,
                        "ok": False,
                        "error": matched.get("error"),
                        "kind": ROUTING_UNKNOWN,
                        "output_type": NOT_APPLICABLE,
                        "capability": NOT_APPLICABLE,
                    }
                )
                continue
            meta = als_parents.get(id(matched["element"])) or {}
            output = str(meta.get("output") or "")
            kind = classify_routing_kind(
                output_type=output,
                grouped=bool(meta.get("grouped")),
                parent_group=meta.get("parent_group"),
                sidechain_control=False,
            )
            control_only = ref.name in als_sidechain or fingerprint in als_sidechain
            routing_rows.append(
                {
                    "content_fingerprint": fingerprint,
                    "ok": True,
                    "locator_name": matched.get("locator_name"),
                    "name_is_locator_only": True,
                    "persistent_object_ref": ref.model_dump(mode="json"),
                    "direct_output_target": output or NOT_APPLICABLE,
                    "group_membership": meta.get("parent_group"),
                    "grouped": bool(meta.get("grouped")),
                    "kind": kind,
                    "control_kind": SIDECHAIN_CONTROL_PATH if control_only else None,
                    "hops": routing_hops(
                        source_locator=str(matched.get("locator_name") or ref.name),
                        grouped=bool(meta.get("grouped")),
                        parent_group=meta.get("parent_group"),
                        output_type=output,
                    ),
                    "sidechain_control": control_only,
                    "send_is_not_audible_contribution": True,
                    "sends": [],
                    "midi_capable": bool(matched.get("midi_capable")),
                    "track_type": matched.get("track_type"),
                    "capability": None if output else NOT_APPLICABLE,
                }
            )

    midi_by_fp = {row["content_fingerprint"]: row for row in midi_rows}
    routing_by_fp = {row["content_fingerprint"]: row for row in routing_rows}
    target_id = target_source_id(parent)
    tables: list[dict[str, Any]] = []
    for event in audio_by_event:
        rows = []
        seen_fps: set[str] = set()
        for src in event["sources"]:
            fp = str(src.get("content_fingerprint") or "")
            seen_fps.add(fp)
            midi = midi_by_fp.get(fp) or {}
            midi_rel = next(
                (
                    item["relation"]
                    for item in midi.get("relations") or []
                    if item["event_id"] == event["event_id"]
                ),
                None,
            )
            if midi_rel is None:
                routing_meta = routing_by_fp.get(fp) or {}
                capable = midi.get("midi_capable")
                if capable is None:
                    capable = routing_meta.get("midi_capable")
                midi_rel = (
                    MIDI_NOT_APPLICABLE
                    if capable is False
                    else MIDI_RELATION_UNCERTAIN
                )
            if fp == target_id:
                parent_rel = parent.by_id().get(f"midi.relation.{event['event_id']}")
                if parent_rel is not None and parent_rel.value:
                    if isinstance(parent_rel.value, dict):
                        midi_rel = str(parent_rel.value.get("relation") or midi_rel)
                    else:
                        midi_rel = str(parent_rel.value)
            routing = routing_by_fp.get(fp) or {}
            rows.append(
                {
                    "SOURCE": src.get("locator_name") or fp,
                    "content_fingerprint": fp,
                    "ARRANGEMENT_ACTIVE": any(
                        item.get("content_fingerprint") == fp
                        for item in (event.get("coverage") or {}).get("captured_active") or []
                    ),
                    "MIDI_EXPECTED": midi_rel,
                    "POST_MIXER_SIGNAL": src.get("relation"),
                    "ROUTING_TO_MAIN": routing.get("kind") or ROUTING_UNKNOWN,
                    "LIMITATIONS": [
                        "ALIGNMENT_LIMITED",
                        "SEND_IS_NOT_AUDIBLE_CONTRIBUTION",
                    ],
                }
            )
        for missing in event.get("uncaptured_active") or []:
            rows.append(
                {
                    "SOURCE": missing.get("locator_name"),
                    "content_fingerprint": None,
                    "ARRANGEMENT_ACTIVE": True,
                    "MIDI_EXPECTED": MIDI_NOT_APPLICABLE,
                    "POST_MIXER_SIGNAL": SOURCE_EVIDENCE_UNAVAILABLE,
                    "ROUTING_TO_MAIN": ROUTING_UNKNOWN,
                    "LIMITATIONS": ["TRUTHFULLY_UNAVAILABLE", "INCOMPLETE_SOURCE_COVERAGE"],
                }
            )
        tables.append({"event_id": event["event_id"], "start_qn": event["start_qn"], "rows": rows})

    questions = {
        "Q1": next((row for row in tables if row["event_id"] == "fm.event.3"), None),
        "Q2": next((row for row in tables if row["event_id"] == "fm.event.4"), None),
        "Q3": next((row for row in tables if row["event_id"] == "fm.event.5"), None),
    }
    if questions["Q2"]:
        kick = next(
            (
                row
                for row in questions["Q2"]["rows"]
                if row.get("SOURCE") in {"Filter Kick", "Kick"}
            ),
            None,
        )
        questions["Q2"] = {
            **questions["Q2"],
            "kick_local_dip_with_note_activity": None
            if kick is None
            else {
                "POST_MIXER_SIGNAL": kick.get("POST_MIXER_SIGNAL"),
                "MIDI_EXPECTED": kick.get("MIDI_EXPECTED"),
            },
        }

    added: list[EvidenceItem] = []
    label = str(region["label"])
    added.append(
        _item(
            "causal.source_audio_coverage",
            "source_audio_coverage",
            source_audio_coverage,
            pack=parent,
            region=label,
            source_ref="causal",
        )
    )
    added.append(
        _item(
            "causal.requested_missing_active",
            "requested_missing_active_sources",
            sorted(requested_active_uncaptured),
            pack=parent,
            region=label,
            source_ref="causal",
        )
    )
    for row in midi_rows:
        prefix = f"causal.midi.{row['content_fingerprint'][:12]}"
        ref = row["content_fingerprint"]
        added.append(
            _item(
                f"{prefix}.status",
                "other_source_midi_status",
                row.get("midi_status"),
                pack=parent,
                region=label,
                source_ref=ref,
            )
        )
        added.append(
            _item(
                f"{prefix}.track_type",
                "other_source_track_type",
                row.get("track_type"),
                pack=parent,
                region=label,
                source_ref=ref,
            )
        )
        for clip in row.get("clips") or []:
            added.append(
                _item(
                    f"{prefix}.clip.{clip.get('clip_identity')}",
                    "other_source_midi_clip",
                    clip,
                    pack=parent,
                    region=label,
                    source_ref=ref,
                )
            )
        for idx, note in enumerate(row.get("notes") or []):
            added.append(
                _item(
                    f"{prefix}.note.{idx}",
                    "other_source_midi_note",
                    note,
                    pack=parent,
                    region=label,
                    source_ref=ref,
                )
            )
            added.append(
                _item(
                    f"{prefix}.note.{idx}.arrangement_start_qn",
                    "other_source_midi_note_arrangement_start_qn",
                    note.get("arrangement_start_qn"),
                    pack=parent,
                    region=label,
                    source_ref=ref,
                    unit="qn",
                )
            )
        for rel in row.get("relations") or []:
            added.append(
                _item(
                    f"{prefix}.relation.{rel['event_id']}",
                    "other_source_midi_relation",
                    rel["relation"],
                    pack=parent,
                    region=label,
                    source_ref=ref,
                )
            )
    for event in audio_by_event:
        eprefix = f"causal.audio.{event['event_id']}"
        added.extend(
            [
                _item(
                    f"{eprefix}.active_source_count",
                    "arrangement_active_source_count",
                    event["ARRANGEMENT_ACTIVE_SOURCE_COUNT"],
                    pack=parent,
                    region=label,
                    source_ref="causal",
                    unit="count",
                ),
                _item(
                    f"{eprefix}.captured_source_count",
                    "captured_source_count",
                    event["CAPTURED_SOURCE_COUNT"],
                    pack=parent,
                    region=label,
                    source_ref="causal",
                    unit="count",
                ),
                _item(
                    f"{eprefix}.uncaptured_source_count",
                    "uncaptured_source_count",
                    event["UNCAPTURED_SOURCE_COUNT"],
                    pack=parent,
                    region=label,
                    source_ref="causal",
                    unit="count",
                ),
            ]
        )
        for src in event["sources"]:
            sref = str(src.get("content_fingerprint") or "")
            added.append(
                _item(
                    f"{eprefix}.{src['evidence_id']}.relation",
                    "matched_source_window_relation",
                    src.get("relation"),
                    pack=parent,
                    region=label,
                    source_ref=sref,
                )
            )
            if src.get("event_rms") is not None:
                added.append(
                    _item(
                        f"{eprefix}.{src['evidence_id']}.event_rms",
                        "matched_source_window_event_rms",
                        src["event_rms"],
                        pack=parent,
                        region=label,
                        source_ref=sref,
                        kind=EvidenceKind.MEASUREMENT,
                    )
                )
    for row in routing_rows:
        prefix = f"causal.routing.{str(row.get('content_fingerprint') or 'unknown')[:12]}"
        ref = str(row.get("content_fingerprint") or "routing")
        added.append(
            _item(
                f"{prefix}.kind",
                "routing_kind",
                row.get("kind"),
                pack=parent,
                region=label,
                source_ref=ref,
            )
        )
        added.append(
            _item(
                f"{prefix}.topology",
                "routing_topology",
                row,
                pack=parent,
                region=label,
                source_ref=ref,
            )
        )
    for table in tables:
        added.append(
            _item(
                f"causal.table.{table['event_id']}",
                "causal_evidence_table",
                table,
                pack=parent,
                region=label,
                source_ref="causal",
            )
        )
    added.append(
        _item(
            "causal.questions",
            "causal_questions",
            questions,
            pack=parent,
            region=label,
            source_ref="causal",
        )
    )
    _assert_factual([item.model_dump(mode="json") for item in added])
    child = build_child_pack(parent, added)
    after_parent = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    als_sha_after = sha256_file(als) or ""
    report.update(
        {
            "status": "VERIFIED",
            MILESTONE: "PACK_READY",
            "new_pack_id": child.pack_id,
            "new_payload_sha256": pack_payload_hash(child),
            "midi_rows": midi_rows,
            "audio_by_event": audio_by_event,
            "routing_rows": routing_rows,
            "causal_tables": tables,
            "QUESTIONS": questions,
            "SOURCE_AUDIO_COVERAGE": source_audio_coverage,
            "requested_missing_active": sorted(requested_active_uncaptured),
            "parent_file_unchanged": after_parent == parent_file_sha,
            "ALS_UNCHANGED": als_sha_after == als_sha_before,
            "als_sha256_before": als_sha_before,
            "als_sha256_after": als_sha_after,
            "pack": child.model_dump(mode="json"),
        }
    )
    return report


def _next_evidence_request(
    accepted: bool,
    status: str,
    requested: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    if not accepted or status != DiagnosisStatus.INSUFFICIENT_EVIDENCE.value:
        return None, None
    kinds = [str(item.get("request_kind") or "") for item in requested]
    mapped: list[str] = []
    explanation: dict[str, Any] = {}
    for kind in ("CAPTURE_VIEW", "DEVICE_STATE", "AUTOMATION", "ANALYZE_REGION", "READ_MIDI", "READ_ROUTING"):
        if kind in kinds:
            mapped.append(kind)
            explanation[kind] = next(
                (item.get("why_needed") for item in requested if item.get("request_kind") == kind),
                kind,
            )
    if not mapped:
        if requested:
            return json.dumps(requested, ensure_ascii=False), explanation or None
        return None, None
    return "+".join(mapped), explanation


def replay_causal_context(
    *,
    evidence: Path | None = None,
    parent_path: Path | None = None,
    als_path: Path | None = None,
    journal_dir: Path | None = None,
    capture_roots: list[Path] | None = None,
    search_roots: list[Path] | None = None,
    session: SessionState | None = None,
    provider: ReasoningProvider | None = None,
    timeout_s: float = ASTRA_TIMEOUT_S,
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    built = collect_causal_context(
        parent_path=parent_path,
        als_path=als_path,
        journal_dir=journal_dir,
        capture_roots=capture_roots,
        search_roots=search_roots,
        session=session,
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
    next_request, next_explain = _next_evidence_request(result.accepted, astra_status, requested)
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
            "NO LIVE": True,
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
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    report["artifact"] = str(path)
    return path


def main() -> int:
    report = replay_causal_context()
    summary = {
        "milestone": MILESTONE,
        "parent_pack_id": report.get("parent_pack_id"),
        "new_pack_id": report.get("new_pack_id"),
        "SOURCE_AUDIO_COVERAGE": report.get("SOURCE_AUDIO_COVERAGE"),
        "accepted": report.get("accepted"),
        "ASTRA RESULT": report.get("ASTRA RESULT"),
        "NEXT_EVIDENCE_REQUEST": report.get("NEXT_EVIDENCE_REQUEST"),
        MILESTONE: report.get(MILESTONE),
        "parent_file_unchanged": report.get("parent_file_unchanged"),
        "ALS_UNCHANGED": report.get("ALS_UNCHANGED"),
        "NO MIDI WRITE": True,
        "NO ROUTING WRITE": True,
        "MUSICAL WRITES": 0,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get(MILESTONE) == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
