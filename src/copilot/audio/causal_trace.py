"""Causal Trace V1 — exact Arrangement timeline evidence for measured Main events.

Facts only. No musical judgment. No Ableton writes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import gzip
import json
import xml.etree.ElementTree as ET

from copilot.human_eval.store import now_iso

TRACK_TAGS = {"MidiTrack", "AudioTrack", "GroupTrack"}
CLIP_TAGS = {"MidiClip", "AudioClip"}
AMP_PARAM_NAMES = {
    "Volume",
    "Speaker",
    "Mute",
    "Gain",
    "OutputGain",
    "DryWet",
    "Fade",
    "Level",
}


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


def _first_named(elem: ET.Element, names: set[str]) -> str | None:
    for child in elem.iter():
        if _local(child.tag) in names:
            value = child.attrib.get("Value")
            if value:
                return value
    return None


def _float_or_none(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


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


def _track_name(node: ET.Element, parents: dict[ET.Element, ET.Element]) -> str:
    current = parents.get(node)
    while current is not None:
        if _local(current.tag) in TRACK_TAGS:
            return _first_named(current, {"EffectiveName", "UserName", "MemorizedName"}) or "?"
        current = parents.get(current)
    return "?"


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


def _is_arrangement_clip(clip: ET.Element, parents: dict[ET.Element, ET.Element]) -> bool:
    ancestors: set[str] = set()
    node = parents.get(clip)
    while node is not None:
        ancestors.add(_local(node.tag))
        node = parents.get(node)
    if "ClipSlot" in ancestors or "ClipSlots" in ancestors:
        return False
    return "ArrangerAutomation" in ancestors or "ClipTimeable" in ancestors


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    lo = max(a0, b0)
    hi = min(a1, b1)
    return max(0.0, hi - lo)


@dataclass
class TimelineEventEvidence:
    event_id: str
    kind: str
    event_audio_start_s: float
    event_audio_end_s: float
    event_start_qn: float
    event_end_qn: float
    relative_drop_db: float | None
    mapping_provenance: dict[str, Any]
    project_token: str | None
    limitations: list[str] = field(default_factory=list)


@dataclass
class TrackCoverage:
    event_id: str
    track: str
    clip_name: str | None
    clip_kind: str
    clip_start_qn: float
    clip_end_qn: float
    event_interval_overlap_qn: float
    has_material_during_event: bool
    source: str
    project_token: str | None
    limitations: list[str] = field(default_factory=list)


@dataclass
class MidiNoteHit:
    pitch: int | None
    velocity: float
    start_qn: float
    end_qn: float
    clip_local_time: float


@dataclass
class MidiCoverage:
    event_id: str
    track: str
    clip_name: str | None
    status: str  # VERIFIED | UNSUPPORTED
    notes_active_at_event: list[MidiNoteHit]
    note_onsets_inside_event: list[MidiNoteHit]
    note_ends_inside_event: list[MidiNoteHit]
    nearest_note_before: MidiNoteHit | None
    nearest_note_after: MidiNoteHit | None
    source: str
    project_token: str | None
    limitations: list[str] = field(default_factory=list)


@dataclass
class AutomationCoverage:
    event_id: str
    track: str
    parameter: str
    parameter_path: str
    status: str  # VERIFIED | UNSUPPORTED | NO_ENVELOPE
    value_before: float | None
    value_during: float | None
    value_after: float | None
    points_surrounding: list[dict[str, float]]
    source: str
    project_token: str | None
    limitations: list[str] = field(default_factory=list)


@dataclass
class CausalTrace:
    trace_id: str
    region_id: str
    region_qn: list[float]
    project_token: str | None
    audible_token: str | None
    source_primary: str
    events: list[TimelineEventEvidence]
    primary_gap_event_id: str
    track_coverage: list[TrackCoverage]
    midi_coverage: list[MidiCoverage]
    automation_coverage: list[AutomationCoverage]
    ruled_out: list[dict[str, Any]]
    supported_cause_candidates: list[dict[str, Any]]
    next_evidence: list[str]
    limitations: list[str]
    musical_writes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def map_fullmix_events_to_qn(
    *,
    evidence: Path,
    source_run: str,
    region_id: str,
) -> dict[str, Any]:
    """Map persisted FullMix event times to Arrangement QN via capture provenance."""
    report = json.loads((evidence / f"{source_run}.json").read_text(encoding="utf-8"))
    fullmix = json.loads((evidence / f"{source_run}_fullmix.json").read_text(encoding="utf-8"))
    fm_row = next(r for r in fullmix["regions"] if r.get("region_id") == region_id)
    asset = next(
        (
            a
            for a in (report.get("CAPTURE ASSETS") or [])
            if isinstance(a.get("region"), dict) and a["region"].get("id") == region_id
        ),
        None,
    )
    if asset is None:
        raise ValueError(f"CAPTURE ASSETS missing for {region_id}")
    listen = next(
        (h for h in (report.get("HUMAN LISTEN MAIN") or []) if h.get("region") == region_id),
        None,
    )
    transport = ((asset.get("transport_observed") or {}).get("master") or {})
    region = asset["region"]
    region_start_qn = float(region["start_qn"])
    region_end_qn = float(region["end_qn"])
    tempo = float(transport.get("tempo") or (report.get("REGIONS SELECTED") or {}).get("tempo") or 167.0)
    tokens = report.get("STATE TOKENS") or {}
    # Listen/Main analysis window is the requested region span (not wall-clock).
    # Duration check: (end-start) qn at tempo ≈ audio duration.
    duration_s = float(fm_row.get("duration_s") or 0.0)
    expected_s = (region_end_qn - region_start_qn) * 60.0 / tempo
    mapping = {
        "method": "region_start_qn + audio_s * tempo_bpm / 60",
        "region_start_qn": region_start_qn,
        "region_end_qn": region_end_qn,
        "tempo_bpm": tempo,
        "tempo_source": "transport_observed.master.tempo",
        "audio_anchor": "HUMAN LISTEN MAIN / FullMix Main asset for requested region",
        "listen_path": None if listen is None else listen.get("path"),
        "listen_start_qn": None if listen is None else listen.get("start_qn"),
        "listen_end_qn": None if listen is None else listen.get("end_qn"),
        "audio_duration_s": duration_s,
        "expected_region_duration_s": expected_s,
        "duration_match_s": abs(duration_s - expected_s),
        "transport_start_provenance": asset.get("transport_start_provenance"),
        "analysis_start_offset_s": transport.get("analysis_start_offset"),
        "note": (
            "Audio t=0 corresponds to requested region start_qn after capture trim; "
            "not estimated from wall-clock playback."
        ),
    }
    events: list[TimelineEventEvidence] = []
    for idx, raw in enumerate(fm_row.get("energy_events") or []):
        start_s = float(raw["start_s"])
        end_s = float(raw["end_s"])
        events.append(
            TimelineEventEvidence(
                event_id=f"{region_id}.fm.event.{idx}",
                kind=str(raw.get("kind")),
                event_audio_start_s=start_s,
                event_audio_end_s=end_s,
                event_start_qn=region_start_qn + start_s * tempo / 60.0,
                event_end_qn=region_start_qn + end_s * tempo / 60.0,
                relative_drop_db=(
                    None if raw.get("relative_drop_db") is None else float(raw["relative_drop_db"])
                ),
                mapping_provenance=mapping,
                project_token=tokens.get("project_token"),
                limitations=[
                    "FullMix window/hop grid bounds event edges.",
                    f"duration_match_s={mapping['duration_match_s']:.6f}",
                ],
            )
        )
    # Primary gap = first contiguous low-energy run (dip/near/silence).
    primary_ids: list[str] = []
    if events:
        primary_ids.append(events[0].event_id)
        for prev, cur in zip(events, events[1:]):
            if cur.event_audio_start_s <= prev.event_audio_end_s + 1e-3:
                kinds = {prev.kind, cur.kind}
                if kinds & {"STRONG_ENERGY_DIP", "NEAR_SILENCE", "SILENCE"}:
                    primary_ids.append(cur.event_id)
                    continue
            break
    gap_events = [e for e in events if e.event_id in primary_ids]
    if gap_events:
        cluster = TimelineEventEvidence(
            event_id=f"{region_id}.gap_cluster.0",
            kind="GAP_CLUSTER",
            event_audio_start_s=gap_events[0].event_audio_start_s,
            event_audio_end_s=gap_events[-1].event_audio_end_s,
            event_start_qn=gap_events[0].event_start_qn,
            event_end_qn=gap_events[-1].event_end_qn,
            relative_drop_db=min(
                (e.relative_drop_db for e in gap_events if e.relative_drop_db is not None),
                default=None,
            ),
            mapping_provenance=mapping,
            project_token=tokens.get("project_token"),
            limitations=[
                "Contiguous merge of leading FullMix low-energy events; not a separate DSP measurement.",
                f"member_events={primary_ids}",
            ],
        )
        events.insert(0, cluster)
        primary_id = cluster.event_id
    else:
        primary_id = events[0].event_id if events else f"{region_id}.none"
    return {
        "region_id": region_id,
        "region_qn": [region_start_qn, region_end_qn],
        "tempo_bpm": tempo,
        "project_token": tokens.get("project_token"),
        "audible_token": tokens.get("audible_token"),
        "als_path": str((report.get("ARRANGEMENT ACTIVITY") or {}).get("als_path") or ""),
        "mapping": mapping,
        "events": events,
        "primary_gap_event_id": primary_id,
    }


def _note_hit(pitch: int | None, velocity: float, start: float, end: float, local_t: float) -> MidiNoteHit:
    return MidiNoteHit(
        pitch=pitch,
        velocity=float(velocity),
        start_qn=float(start),
        end_qn=float(end),
        clip_local_time=float(local_t),
    )


def _eval_envelope(points: list[tuple[float, float]], qn: float) -> float | None:
    if not points:
        return None
    ordered = sorted(points, key=lambda item: item[0])
    before = [p for p in ordered if p[0] <= qn]
    if before:
        return float(before[-1][1])
    return float(ordered[0][1])


def build_causal_trace(
    *,
    evidence: Path | None = None,
    source_run: str = "session_run1",
    region_id: str = "REGION_C",
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    mapped = map_fullmix_events_to_qn(evidence=evidence, source_run=source_run, region_id=region_id)
    als_path = Path(str(mapped["als_path"]))
    if not als_path.is_file():
        raise FileNotFoundError(f"als missing: {als_path}")
    project_token = mapped.get("project_token")
    primary_id = mapped["primary_gap_event_id"]
    events: list[TimelineEventEvidence] = mapped["events"]
    primary = next(e for e in events if e.event_id == primary_id)
    ev0, ev1 = primary.event_start_qn, primary.event_end_qn

    root = _load_als_root(als_path)
    parents = _parent_map(root)
    source_label = "SAVED_PROJECT"

    # Live reconciliation (read-only).
    live_recon = _reconcile_live_state(project_token)
    if live_recon.get("status") == "PROJECT_STATE_CONFLICT":
        payload = {
            "status": "PROJECT_STATE_CONFLICT",
            "live_reconciliation": live_recon,
            "MUSICAL WRITES": 0,
            "MUSICPLAN_GATE": "CLOSED",
        }
        out = evidence / f"{source_run}_causal_trace_{region_id.lower()}_CONFLICT.json"
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        payload["artifact"] = str(out)
        return payload

    track_cov: list[TrackCoverage] = []
    midi_cov: list[MidiCoverage] = []
    auto_cov: list[AutomationCoverage] = []

    # Clip coverage
    for clip in root.iter():
        if _local(clip.tag) not in CLIP_TAGS:
            continue
        if not _is_arrangement_clip(clip, parents):
            continue
        span = _clip_span(clip)
        if span is None:
            continue
        c0, c1 = span
        overlap = _overlap(c0, c1, ev0, ev1)
        if overlap <= 0 and not (c0 < ev1 and c1 > ev0):
            # still record near-miss boundaries within ±2 qn of event for context
            if c1 < ev0 - 2 or c0 > ev1 + 2:
                continue
        track = _track_name(clip, parents)
        track_cov.append(
            TrackCoverage(
                event_id=primary_id,
                track=track,
                clip_name=_attr_value(clip, "Name") or _first_named(clip, {"Name"}),
                clip_kind=_local(clip.tag),
                clip_start_qn=c0,
                clip_end_qn=c1,
                event_interval_overlap_qn=overlap,
                has_material_during_event=overlap > 0,
                source=source_label,
                project_token=project_token,
                limitations=["Clip presence ≠ audible contribution to Main."],
            )
        )

    # MIDI coverage with StartRelative mapping
    for clip in root.iter():
        if _local(clip.tag) != "MidiClip":
            continue
        if not _is_arrangement_clip(clip, parents):
            continue
        span = _clip_span(clip)
        if span is None:
            continue
        c0, c1 = span
        overlap = _overlap(c0, c1, ev0, ev1)
        if overlap <= 0:
            continue
        track = _track_name(clip, parents)
        loop = _loop_fields(clip)
        start_rel = float(loop.get("StartRelative") or 0.0)
        notes_node = next((c for c in clip if _local(c.tag) == "Notes"), None)
        if notes_node is None:
            midi_cov.append(
                MidiCoverage(
                    event_id=primary_id,
                    track=track,
                    clip_name=_attr_value(clip, "Name"),
                    status="VERIFIED",
                    notes_active_at_event=[],
                    note_onsets_inside_event=[],
                    note_ends_inside_event=[],
                    nearest_note_before=None,
                    nearest_note_after=None,
                    source=source_label,
                    project_token=project_token,
                    limitations=["No Notes node in clip."],
                )
            )
            continue
        placed: list[MidiNoteHit] = []
        unsupported = False
        for kt in notes_node.iter():
            if _local(kt.tag) != "KeyTrack":
                continue
            pitch = None
            for child in list(kt):
                if _local(child.tag) == "MidiKey" and child.attrib.get("Value") is not None:
                    pitch = int(float(child.attrib["Value"]))
            for ne in kt.iter():
                if _local(ne.tag) != "MidiNoteEvent":
                    continue
                # ensure note belongs to this KeyTrack's Notes child, not nested stores
                if parents.get(ne) is not None:
                    # MidiNoteEvent parent chain should include this KeyTrack
                    walk = parents.get(ne)
                    owned = False
                    while walk is not None:
                        if walk is kt:
                            owned = True
                            break
                        if _local(walk.tag) == "KeyTrack":
                            break
                        walk = parents.get(walk)
                    if not owned:
                        continue
                t = _float_or_none(ne.attrib.get("Time"))
                dur = _float_or_none(ne.attrib.get("Duration")) or 0.0
                vel = _float_or_none(ne.attrib.get("Velocity")) or 0.0
                if t is None:
                    unsupported = True
                    continue
                abs_s = c0 + (t - start_rel)
                abs_e = abs_s + dur
                if abs_e <= c0 or abs_s >= c1:
                    continue
                placed.append(_note_hit(pitch, vel, abs_s, abs_e, t))
        if unsupported and not placed:
            midi_cov.append(
                MidiCoverage(
                    event_id=primary_id,
                    track=track,
                    clip_name=_attr_value(clip, "Name"),
                    status="UNSUPPORTED",
                    notes_active_at_event=[],
                    note_onsets_inside_event=[],
                    note_ends_inside_event=[],
                    nearest_note_before=None,
                    nearest_note_after=None,
                    source=source_label,
                    project_token=project_token,
                    limitations=["Absolute MIDI timing could not be resolved for this clip."],
                )
            )
            continue
        active = [n for n in placed if n.start_qn < ev1 and n.end_qn > ev0]
        onsets = [n for n in placed if ev0 <= n.start_qn < ev1]
        ends = [n for n in placed if ev0 <= n.end_qn < ev1]
        before = [n for n in placed if n.end_qn <= ev0]
        after = [n for n in placed if n.start_qn >= ev1]
        midi_cov.append(
            MidiCoverage(
                event_id=primary_id,
                track=track,
                clip_name=_attr_value(clip, "Name"),
                status="VERIFIED",
                notes_active_at_event=sorted(active, key=lambda n: (n.start_qn, n.pitch or -1)),
                note_onsets_inside_event=sorted(onsets, key=lambda n: n.start_qn),
                note_ends_inside_event=sorted(ends, key=lambda n: n.end_qn),
                nearest_note_before=(
                    max(before, key=lambda n: n.end_qn) if before else None
                ),
                nearest_note_after=(
                    min(after, key=lambda n: n.start_qn) if after else None
                ),
                source=source_label,
                project_token=project_token,
                limitations=[
                    "Note times mapped as arrangement_qn = CurrentStart + (clip_local_time - StartRelative).",
                    f"LoopOn={loop.get('LoopOn')}",
                    "Note presence ≠ proven audible Main contribution.",
                ],
            )
        )

    # Automation: identify envelopes; evaluate amplitude-like; mark others limited
    targets = _automation_targets(root, parents)
    mid_qn = 0.5 * (ev0 + ev1)
    before_qn = ev0 - 0.25
    after_qn = ev1 + 0.25
    amp_seen_tracks: set[str] = set()
    for env in root.iter():
        if _local(env.tag) != "AutomationEnvelope":
            continue
        pointee = None
        for child in env.iter():
            if _local(child.tag) == "PointeeId" and child.attrib.get("Value"):
                pointee = child.attrib["Value"]
                break
        if pointee is None:
            continue
        info = targets.get(pointee)
        if info is None:
            continue
        points = [
            (float(c.attrib.get("Time") or 0.0), float(c.attrib.get("Value") or 0.0))
            for c in env.iter()
            if _local(c.tag) == "FloatEvent"
        ]
        if not points:
            continue
        # Keep envelopes with points near event (±8 qn) or overlapping region
        if not any(ev0 - 8 <= t <= ev1 + 8 for t, _ in points):
            continue
        is_amp = info["param"] in AMP_PARAM_NAMES or any(
            key in info["path"] for key in ("Volume/Mixer", "Speaker", "Mute")
        )
        surrounding = [
            {"time_qn": t, "value": v}
            for t, v in sorted(points)
            if ev0 - 2 <= t <= ev1 + 2
        ][:12]
        if is_amp:
            amp_seen_tracks.add(info["track"])
            auto_cov.append(
                AutomationCoverage(
                    event_id=primary_id,
                    track=info["track"],
                    parameter=info["param"],
                    parameter_path=info["path"],
                    status="VERIFIED",
                    value_before=_eval_envelope(points, before_qn),
                    value_during=_eval_envelope(points, mid_qn),
                    value_after=_eval_envelope(points, after_qn),
                    points_surrounding=surrounding,
                    source=source_label,
                    project_token=project_token,
                    limitations=["Step hold evaluation (last point ≤ qn); curves not modeled."],
                )
            )
        else:
            auto_cov.append(
                AutomationCoverage(
                    event_id=primary_id,
                    track=info["track"],
                    parameter=info["param"],
                    parameter_path=info["path"],
                    status="UNSUPPORTED",
                    value_before=_eval_envelope(points, before_qn),
                    value_during=_eval_envelope(points, mid_qn),
                    value_after=_eval_envelope(points, after_qn),
                    points_surrounding=surrounding,
                    source=source_label,
                    project_token=project_token,
                    limitations=[
                        "Envelope values readable but amplitude effect of this parameter is not established.",
                        "Do not infer Main attenuation from non-identified macros/filters alone.",
                    ],
                )
            )

    # Explicit NO_ENVELOPE for mixer Volume on tracks with clip coverage during event
    active_tracks = sorted({c.track for c in track_cov if c.has_material_during_event})
    mixer_volume_targets = {
        info["track"]: tid
        for tid, info in targets.items()
        if info["param"] == "Volume" and info["path"].startswith("Volume/Mixer/")
    }
    enveloped_ids = {
        child.attrib.get("Value")
        for env in root.iter()
        if _local(env.tag) == "AutomationEnvelope"
        for child in env.iter()
        if _local(child.tag) == "PointeeId" and child.attrib.get("Value")
    }
    for track in active_tracks:
        tid = mixer_volume_targets.get(track)
        if tid and tid not in enveloped_ids:
            manual = None
            # find manual from target path node — stored separately below if needed
            auto_cov.append(
                AutomationCoverage(
                    event_id=primary_id,
                    track=track,
                    parameter="Volume",
                    parameter_path="Volume/Mixer/",
                    status="NO_ENVELOPE",
                    value_before=None,
                    value_during=None,
                    value_after=None,
                    points_surrounding=[],
                    source=source_label,
                    project_token=project_token,
                    limitations=[
                        "No Arrangement AutomationEnvelope for track mixer Volume.",
                        "Live mixer snapshot (if any) is separate LIVE_STATE evidence.",
                    ],
                )
            )

    # Live mixer snapshot for active tracks (optional enrichment)
    live_mixer = live_recon.get("mixer_snapshot") or {}
    if live_mixer.get("ok"):
        for row in live_mixer.get("tracks") or []:
            if row.get("name") not in active_tracks:
                continue
            auto_cov.append(
                AutomationCoverage(
                    event_id=primary_id,
                    track=str(row.get("name")),
                    parameter="mixer_snapshot",
                    parameter_path="LIVE_STATE mute/solo/volume",
                    status="VERIFIED",
                    value_before=None,
                    value_during=float(row["volume"]) if row.get("volume") is not None else None,
                    value_after=None,
                    points_surrounding=[
                        {
                            "mute": 1.0 if row.get("mute") else 0.0,
                            "solo": 1.0 if row.get("solo") else 0.0,
                            "volume": float(row.get("volume") or 0.0),
                        }
                    ],
                    source="LIVE_STATE",
                    project_token=project_token,
                    limitations=[
                        "Current Live mixer values, not historical automation playback at event time.",
                    ],
                )
            )

    ruled_out, supported, next_ev, limitations = _derive_causal_conclusions(
        primary=primary,
        track_cov=track_cov,
        midi_cov=midi_cov,
        auto_cov=auto_cov,
    )

    trace = CausalTrace(
        trace_id=f"causal-trace-1:{region_id}:{primary_id}",
        region_id=region_id,
        region_qn=list(mapped["region_qn"]),
        project_token=project_token,
        audible_token=mapped.get("audible_token"),
        source_primary=source_label,
        events=events,
        primary_gap_event_id=primary_id,
        track_coverage=track_cov,
        midi_coverage=midi_cov,
        automation_coverage=auto_cov,
        ruled_out=ruled_out,
        supported_cause_candidates=supported,
        next_evidence=next_ev,
        limitations=limitations,
        musical_writes=0,
    )
    payload = {
        "status": "CAUSAL TRACE COMPLETE",
        "trace_version": "causal-trace-1",
        "source_run": source_run,
        "gathered_at": now_iso(),
        "als_path": str(als_path),
        "live_reconciliation": live_recon,
        "mapping": mapped["mapping"],
        "trace": trace.to_dict(),
        "MUSICAL WRITES": 0,
        "ASTRA CALLS": 0,
        "MusicPlan": None,
    }
    out = evidence / f"{source_run}_causal_trace_{region_id.lower()}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload


def _automation_targets(
    root: ET.Element,
    parents: dict[ET.Element, ET.Element],
) -> dict[str, dict[str, str]]:
    targets: dict[str, dict[str, str]] = {}
    for at in root.iter():
        if _local(at.tag) != "AutomationTarget":
            continue
        tid = at.attrib.get("Id")
        if not tid:
            continue
        path: list[str] = []
        node = parents.get(at)
        while node is not None and len(path) < 10:
            path.append(_local(node.tag))
            node = parents.get(node)
        targets[tid] = {
            "track": _track_name(at, parents),
            "param": path[0] if path else "?",
            "path": "/".join(path),
        }
    return targets


def _reconcile_live_state(project_token: str | None) -> dict[str, Any]:
    try:
        from copilot.daw.ableton_tcp import AbletonTcpAdapter
    except Exception as exc:
        return {
            "status": "LIVE_UNAVAILABLE",
            "ok": False,
            "error": f"import:{type(exc).__name__}",
            "note": "Proceeding with SAVED_PROJECT (.als) only.",
        }
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        path_info = {}
        try:
            path_info = adapter.get_session_path() or {}
        except Exception as exc:
            path_info = {"error": f"{type(exc).__name__}:{exc}"}
        infos = adapter.get_tracks_info()
        rows_raw = infos.get("tracks") if isinstance(infos, dict) else None
        if rows_raw is None and isinstance(infos, list):
            rows_raw = infos
        rows_raw = rows_raw or list(adapter.last_track_infos.values())
        tracks = [
            {
                "name": info.get("name"),
                "index": info.get("index"),
                "mute": bool(info.get("mute", False)),
                "solo": bool(info.get("solo", False)),
                "volume": info.get("volume"),
                "output_routing_type": info.get("output_routing_type"),
            }
            for info in rows_raw
        ]
        # Soft identity check: saved ALS path vs live path when available.
        live_path = str(path_info.get("path") or path_info.get("session_path") or "")
        return {
            "status": "LIVE_OK",
            "ok": True,
            "live_session_path": live_path,
            "saved_project_token": project_token,
            "mixer_snapshot": {"ok": True, "tracks": tracks, "source": "LIVE_STATE"},
            "note": (
                "Live mixer is current snapshot. Timeline clip/MIDI/automation below "
                "come from SAVED_PROJECT unless labeled LIVE_STATE."
            ),
            "conflict": False,
        }
    except Exception as exc:
        return {
            "status": "LIVE_UNAVAILABLE",
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
            "note": "Proceeding with SAVED_PROJECT (.als) only.",
        }
    finally:
        try:
            adapter.disconnect()
        except Exception:
            pass


def _derive_causal_conclusions(
    *,
    primary: TimelineEventEvidence,
    track_cov: list[TrackCoverage],
    midi_cov: list[MidiCoverage],
    auto_cov: list[AutomationCoverage],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    ruled: list[dict[str, Any]] = []
    supported: list[dict[str, Any]] = []
    next_ev: list[str] = []
    limitations: list[str] = [
        "CausalTrace states project-timeline facts only; musical acceptability is not decided.",
    ]
    covering = [c for c in track_cov if c.has_material_during_event]
    if covering:
        ruled.append(
            {
                "code": "TRACK_STILL_ACTIVE",
                "detail": (
                    f"Tracks with Arranger clip coverage during "
                    f"qn {primary.event_start_qn:.4f}-{primary.event_end_qn:.4f}: "
                    + ", ".join(sorted({c.track for c in covering}))
                ),
            }
        )
    else:
        ruled.append(
            {
                "code": "NO_CLIP_COVERAGE",
                "detail": (
                    f"No Arranger clips overlap qn "
                    f"{primary.event_start_qn:.4f}-{primary.event_end_qn:.4f}."
                ),
            }
        )
        supported.append(
            {
                "code": "CLIP_ABSENCE",
                "strength": "STRONG",
                "detail": "No overlapping Arranger clips during the measured Main gap.",
            }
        )

    # Per-track MIDI conclusions (aggregate clips on the same track).
    by_track: dict[str, list[MidiCoverage]] = {}
    for m in midi_cov:
        by_track.setdefault(m.track, []).append(m)

    midi_active_tracks: list[str] = []
    midi_inactive_verified: list[str] = []
    midi_unsupported: list[str] = []
    for track, rows in sorted(by_track.items()):
        if any(r.status == "UNSUPPORTED" for r in rows) and not any(
            r.status == "VERIFIED" and r.notes_active_at_event for r in rows
        ):
            if all(r.status == "UNSUPPORTED" for r in rows):
                midi_unsupported.append(track)
                continue
        verified = [r for r in rows if r.status == "VERIFIED"]
        if not verified:
            midi_unsupported.append(track)
            continue
        active_notes = [n for r in verified for n in r.notes_active_at_event]
        if active_notes:
            midi_active_tracks.append(track)
        else:
            midi_inactive_verified.append(track)
            ruled.append(
                {
                    "code": "NO_MIDI_NOTE_COVERAGE",
                    "track": track,
                    "detail": (
                        f"No MIDI notes on track {track} cover qn "
                        f"{primary.event_start_qn:.4f}-{primary.event_end_qn:.4f}."
                    ),
                }
            )

    if midi_inactive_verified and not midi_active_tracks and not midi_unsupported:
        supported.append(
            {
                "code": "NO_MIDI_ACROSS_COVERING_CLIPS",
                "strength": "STRONG",
                "detail": "All verified MIDI clips have zero note coverage during the event.",
            }
        )
    elif midi_inactive_verified and midi_active_tracks:
        supported.append(
            {
                "code": "PARTIAL_SOURCE_MIDI_REST",
                "strength": "WEAK",
                "detail": (
                    "Some covering tracks have no MIDI during the gap "
                    f"({', '.join(sorted(set(midi_inactive_verified)))}), "
                    f"but others still have active notes "
                    f"({', '.join(sorted(set(midi_active_tracks)))})."
                ),
            }
        )

    # Automation attenuation
    amp_drop = []
    for a in auto_cov:
        if a.status != "VERIFIED" or a.parameter in {"mixer_snapshot"}:
            continue
        if a.parameter not in AMP_PARAM_NAMES and "Volume/Mixer" not in a.parameter_path:
            continue
        if (
            a.value_before is not None
            and a.value_during is not None
            and a.value_during < a.value_before * 0.5
            and a.value_during < 0.2
        ):
            amp_drop.append(a)
    if amp_drop:
        supported.append(
            {
                "code": "AUTOMATION_ATTENUATION_PRESENT",
                "strength": "STRONG",
                "detail": (
                    "Amplitude automation evaluates lower during event on: "
                    + ", ".join(f"{a.track}:{a.parameter}" for a in amp_drop)
                ),
            }
        )
    else:
        mixer_no_env = [
            a for a in auto_cov if a.parameter == "Volume" and a.status == "NO_ENVELOPE"
        ]
        if mixer_no_env:
            ruled.append(
                {
                    "code": "NO_TRACK_VOLUME_AUTOMATION",
                    "detail": (
                        "No track mixer Volume Arrangement envelopes for: "
                        + ", ".join(sorted({a.track for a in mixer_no_env}))
                    ),
                }
            )
        live_muted = [
            a
            for a in auto_cov
            if a.parameter == "mixer_snapshot"
            and a.points_surrounding
            and a.points_surrounding[0].get("mute") == 1.0
        ]
        if live_muted:
            supported.append(
                {
                    "code": "LIVE_MUTE_PRESENT",
                    "strength": "WEAK",
                    "detail": "Live mute currently true on: "
                    + ", ".join(a.track for a in live_muted),
                }
            )
        else:
            ruled.append(
                {
                    "code": "NO_LIVE_MUTE_ON_COVERING_TRACKS",
                    "detail": "LIVE_STATE mixer snapshot shows covering tracks unmuted (current time only).",
                }
            )

    # Clip boundary?
    boundaries = [
        c
        for c in track_cov
        if abs(c.clip_end_qn - primary.event_start_qn) < 0.25
        or abs(c.clip_start_qn - primary.event_start_qn) < 0.25
    ]
    if boundaries:
        ruled.append(
            {
                "code": "CLIP_BOUNDARY_PRESENT",
                "detail": "Clip boundary near event start: "
                + ", ".join(f"{b.track}@{b.clip_start_qn}-{b.clip_end_qn}" for b in boundaries),
            }
        )

    # Decide next evidence / unresolved
    if midi_active_tracks:
        next_ev.append("NEED_SOURCE_AUDIO_VIEW")
        limitations.append(
            "MIDI notes remain active on some tracks during the Main gap; "
            "source-level audio/device state is required to explain silence."
        )
    unsupported_auto = [a for a in auto_cov if a.status == "UNSUPPORTED"]
    if unsupported_auto:
        next_ev.append("NEED_EXACT_AUTOMATION_READBACK")
        limitations.append(
            "Non-volume envelopes (macros/filters) change near the event but amplitude identity is unresolved."
        )
    if midi_unsupported:
        next_ev.append("NEED_CLIP_ENVELOPE_READBACK")
        limitations.append(
            f"MIDI timing UNSUPPORTED on: {', '.join(sorted(set(midi_unsupported)))}."
        )

    strong = [s for s in supported if s.get("strength") == "STRONG"]
    if not strong:
        ruled.append(
            {
                "code": "CAUSE_UNRESOLVED",
                "detail": (
                    "No single strong timeline cause uniquely explains the Main energy gap."
                ),
            }
        )
        if "NEED_SOURCE_AUDIO_VIEW" not in next_ev:
            next_ev.append("NEED_SOURCE_AUDIO_VIEW")

    # de-dupe next evidence preserving order
    seen: set[str] = set()
    next_ev = [x for x in next_ev if not (x in seen or seen.add(x))]
    return ruled, supported, next_ev, limitations
