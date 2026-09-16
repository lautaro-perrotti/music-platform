"""Read-only causal evidence for measured Main energy events.

Layers:
  OBSERVED  — FullMix measurements (already in pack)
  CAUSAL    — arrangement / MIDI / mixer / routing facts (this module)
  JUDGMENT  — never produced here

No musical writes. No new audio capture unless explicitly required later.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
import gzip
import json
import xml.etree.ElementTree as ET

from copilot.audio.arrangement_activity import (
    BASS_TARGET,
    DRUMS_TRACK,
    ROSE_BASS,
    clips_overlap_region,
    load_arrangement_clips,
)
from copilot.human_eval.store import now_iso

TRACK_TAGS = {"MidiTrack", "AudioTrack", "GroupTrack"}
CLIP_TAGS = {"MidiClip", "AudioClip"}


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


def read_als_tempo_bpm(als_path: Path) -> float | None:
    root = _load_als_root(als_path)
    for elem in root.iter():
        if _local(elem.tag) != "Tempo":
            continue
        for child in elem.iter():
            if _local(child.tag) == "Manual" and child.attrib.get("Value"):
                return float(child.attrib["Value"])
    return None


def _clip_span(clip: ET.Element) -> tuple[float, float] | None:
    start = _float_or_none(_attr_value(clip, "CurrentStart"))
    end = _float_or_none(_attr_value(clip, "CurrentEnd"))
    if start is None:
        start = _float_or_none(clip.attrib.get("Time"))
    if start is None:
        return None
    if end is None:
        length = _float_or_none(_attr_value(clip, "Length"))
        if length is None:
            return None
        end = start + length
    if end <= start:
        return None
    return start, end


def _track_name_for(node: ET.Element, parents: dict[ET.Element, ET.Element]) -> str:
    current = parents.get(node)
    while current is not None:
        if _local(current.tag) in TRACK_TAGS:
            return _first_named(current, {"EffectiveName", "UserName", "MemorizedName"}) or "?"
        current = parents.get(current)
    return "?"


def inspect_midi_activity_als(
    als_path: Path,
    *,
    start_qn: float,
    end_qn: float,
    bin_qn: float = 1.0,
) -> dict[str, Any]:
    """Read-only Arrangement MIDI note counts from .als. Session slots ignored."""
    root = _load_als_root(als_path)
    parents = _parent_map(root)
    tracks: dict[str, dict[str, Any]] = {}
    for clip in root.iter():
        if _local(clip.tag) != "MidiClip":
            continue
        ancestors: set[str] = set()
        node = parents.get(clip)
        while node is not None:
            ancestors.add(_local(node.tag))
            node = parents.get(node)
        if "ClipSlot" in ancestors or "ClipSlots" in ancestors:
            continue
        if "ArrangerAutomation" not in ancestors and "ClipTimeable" not in ancestors:
            continue
        span = _clip_span(clip)
        if span is None:
            continue
        clip_start, clip_end = span
        if clip_end <= start_qn or clip_start >= end_qn:
            continue
        track = _track_name_for(clip, parents)
        raw_times: list[float] = []
        for note in clip.iter():
            if _local(note.tag) != "MidiNoteEvent":
                continue
            t = _float_or_none(note.attrib.get("Time"))
            if t is None:
                continue
            raw_times.append(t)
        if not raw_times:
            continue
        med = sorted(raw_times)[len(raw_times) // 2]
        relative = med < (clip_end - clip_start)
        abs_times = [clip_start + t if relative else t for t in raw_times]
        in_region = [t for t in abs_times if start_qn <= t < end_qn]
        bins: dict[str, int] = defaultdict(int)
        for t in in_region:
            key = int((t - start_qn) // bin_qn)
            bins[str(key)] += 1
        row = tracks.setdefault(
            track,
            {
                "track": track,
                "clip_count": 0,
                "note_count_in_region": 0,
                "note_bins_1qn": {},
                "clips": [],
            },
        )
        row["clip_count"] += 1
        row["note_count_in_region"] += len(in_region)
        for key, count in bins.items():
            row["note_bins_1qn"][key] = int(row["note_bins_1qn"].get(key, 0)) + int(count)
        row["clips"].append(
            {
                "name": _attr_value(clip, "Name") or _first_named(clip, {"Name"}),
                "start_qn": clip_start,
                "end_qn": clip_end,
                "notes_in_region": len(in_region),
                "time_basis": "clip_relative" if relative else "absolute_or_mixed",
            }
        )
    return {
        "observation_type": "MidiActivityObservation",
        "source": "als_arranger_midi_notes",
        "als_path": str(als_path),
        "start_qn": start_qn,
        "end_qn": end_qn,
        "bin_qn": bin_qn,
        "tracks": sorted(tracks.values(), key=lambda item: item["track"]),
        "limitations": [
            "Arrangement MIDI only; Session clip slots ignored.",
            "Note times may be clip-relative or absolute; basis is heuristic.",
            "Counts are presence/density only — not musical judgment.",
        ],
    }


def inspect_automation_presence_als(
    als_path: Path,
    *,
    start_qn: float,
    end_qn: float,
) -> dict[str, Any]:
    """Read-only: which tracks have FloatEvent automation points in the qn range."""
    root = _load_als_root(als_path)
    parents = _parent_map(root)
    by_track: dict[str, int] = defaultdict(int)
    for envelope in root.iter():
        if _local(envelope.tag) != "AutomationEnvelope":
            continue
        count = 0
        for event in envelope.iter():
            if _local(event.tag) != "FloatEvent":
                continue
            t = _float_or_none(event.attrib.get("Time"))
            if t is None:
                continue
            if start_qn <= t < end_qn:
                count += 1
        if count:
            by_track[_track_name_for(envelope, parents)] += count
    return {
        "observation_type": "AutomationObservation",
        "source": "als_automation_envelopes",
        "als_path": str(als_path),
        "start_qn": start_qn,
        "end_qn": end_qn,
        "tracks_with_float_events": [
            {"track": name, "float_event_count_in_region": count}
            for name, count in sorted(by_track.items())
        ],
        "limitations": [
            "Counts FloatEvent points only; does not identify parameter identity reliably.",
            "Presence of automation ≠ proven cause of Main energy gap.",
            "No envelope curve interpretation performed.",
        ],
    }


def inspect_clip_activity_detail(
    als_path: Path,
    *,
    start_qn: float,
    end_qn: float,
) -> dict[str, Any]:
    clips = load_arrangement_clips(als_path)
    hits = clips_overlap_region(clips, start_qn, end_qn)
    focused = {
        "drums": [c for c in hits if c.get("track") == DRUMS_TRACK or c.get("group") == DRUMS_TRACK],
        "sub_sub_bass": [c for c in hits if c.get("track") == BASS_TARGET],
        "rose_bass": [c for c in hits if c.get("track") == ROSE_BASS],
        "all_overlapping": hits,
    }
    return {
        "observation_type": "ClipActivityObservation",
        "source": "als_arranger_clips",
        "als_path": str(als_path),
        "start_qn": start_qn,
        "end_qn": end_qn,
        "overlapping_clip_count": len(hits),
        "tracks_active": sorted({str(c.get("track")) for c in hits}),
        "drums_active": bool(focused["drums"]),
        "sub_sub_bass_active": bool(focused["sub_sub_bass"]),
        "rose_bass_active": bool(focused["rose_bass"]),
        "clips": [
            {
                "track": c.get("track"),
                "group": c.get("group"),
                "kind": c.get("kind"),
                "name": c.get("name"),
                "start_qn": c.get("start_qn"),
                "end_qn": c.get("end_qn"),
            }
            for c in sorted(hits, key=lambda row: (str(row.get("track")), float(row["start_qn"])))
        ],
        "limitations": [
            "Clip presence only; does not prove audible contribution to Main.",
            "SOURCE_INACTIVE means no overlapping Arranger clip.",
        ],
    }


def try_ableton_mixer_routing_readonly(
    track_names: list[str],
) -> dict[str, Any]:
    """Best-effort Ableton TCP read of mute/solo/volume/routing. Never writes."""
    try:
        from copilot.daw.ableton_tcp import AbletonTcpAdapter
    except Exception as exc:  # pragma: no cover - import surface
        return {
            "observation_type": "RoutingObservation",
            "ok": False,
            "source": "ableton_tcp",
            "error": f"import_failed:{type(exc).__name__}",
            "tracks": [],
            "limitations": ["Ableton TCP unavailable; ALS-only causal path used."],
            "musical_writes": 0,
        }
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        infos = adapter.get_tracks_info()
        rows_raw = infos.get("tracks") if isinstance(infos, dict) else None
        if rows_raw is None and isinstance(infos, list):
            rows_raw = infos
        rows_raw = rows_raw or list(adapter.last_track_infos.values())
        wanted = {name.lower() for name in track_names}
        selected = []
        for info in rows_raw:
            name = str(info.get("name") or "")
            if name.lower() not in wanted and not any(w in name.lower() for w in wanted):
                continue
            selected.append(
                {
                    "name": name,
                    "index": info.get("index"),
                    "mute": bool(info.get("mute", False)),
                    "solo": bool(info.get("solo", False)),
                    "volume": info.get("volume"),
                    "input_routing_type": info.get("input_routing_type"),
                    "input_routing_channel": info.get("input_routing_channel"),
                    "output_routing_type": info.get("output_routing_type"),
                    "output_routing_channel": info.get("output_routing_channel"),
                }
            )
        master = {}
        try:
            master = adapter.get_master_info() or {}
        except Exception:
            master = {}
        return {
            "observation_type": "RoutingObservation",
            "ok": True,
            "source": "ableton_tcp_readonly",
            "tracks": selected,
            "master": {
                "volume": master.get("volume"),
                "mute": master.get("mute"),
            },
            "limitations": [
                "Live mixer snapshot at read time; not historical automation playback state.",
            ],
            "musical_writes": 0,
        }
    except Exception as exc:
        return {
            "observation_type": "RoutingObservation",
            "ok": False,
            "source": "ableton_tcp",
            "error": f"{type(exc).__name__}:{exc}",
            "tracks": [],
            "limitations": [
                "Ableton not connected; mute/solo/volume/routing live read skipped.",
                "ALS arrangement/MIDI/automation still valid.",
            ],
            "musical_writes": 0,
        }
    finally:
        try:
            adapter.disconnect()
        except Exception:
            pass


def map_event_times_to_qn(
    *,
    region_start_qn: float,
    tempo_bpm: float,
    event_times_s: list[float],
) -> list[dict[str, float]]:
    qn_per_s = float(tempo_bpm) / 60.0
    return [
        {
            "t_s": float(t),
            "abs_qn": float(region_start_qn) + float(t) * qn_per_s,
            "rel_qn": float(t) * qn_per_s,
        }
        for t in event_times_s
    ]


def gather_region_c_causal_evidence(
    *,
    evidence: Path | None = None,
    source_run: str = "session_run1",
    region_id: str = "REGION_C",
) -> dict[str, Any]:
    """Satisfy Astra r2 C evidence requests with typed read-only observations."""
    evidence = Path(evidence or "logs")
    report = json.loads((evidence / f"{source_run}.json").read_text(encoding="utf-8"))
    als_path = Path(str((report.get("ARRANGEMENT ACTIVITY") or {}).get("als_path") or ""))
    if not als_path.is_file():
        raise FileNotFoundError(f"als missing: {als_path}")
    regions_block = report.get("REGIONS SELECTED") or {}
    region_rows = (
        regions_block.get("regions")
        if isinstance(regions_block, dict)
        else regions_block
    ) or []
    region_row = next(
        (r for r in region_rows if isinstance(r, dict) and r.get("id") == region_id),
        None,
    )
    if region_row is None:
        # Fallback: ARRANGEMENT ACTIVITY region rows.
        for r in ((report.get("ARRANGEMENT ACTIVITY") or {}).get("regions") or []):
            if isinstance(r, dict) and r.get("id") == region_id:
                region_row = r
                break
    if region_row is None:
        raise ValueError(f"{region_id} missing from REGIONS SELECTED")
    start_qn = float(region_row["start_qn"])
    end_qn = float(region_row["end_qn"])
    tokens = report.get("STATE TOKENS") or {}
    tempo = read_als_tempo_bpm(als_path) or 167.0

    # FullMix event times for C (observed layer — referenced, not re-measured).
    fullmix_path = evidence / f"{source_run}_fullmix.json"
    event_times_s: list[float] = []
    event_summaries: list[dict[str, Any]] = []
    if fullmix_path.is_file():
        fullmix = json.loads(fullmix_path.read_text(encoding="utf-8"))
        for row in fullmix.get("regions") or []:
            if row.get("region_id") != region_id:
                continue
            for ev in row.get("energy_events") or []:
                event_times_s.append(float(ev.get("start_s") or 0.0))
                event_summaries.append(
                    {
                        "kind": ev.get("kind"),
                        "start_s": ev.get("start_s"),
                        "end_s": ev.get("end_s"),
                        "relative_drop_db": ev.get("relative_drop_db"),
                    }
                )

    # Astra r2 requested evidence (exact requests preserved).
    r2_path = evidence / f"{source_run}_astra_r2.json"
    requested: list[dict[str, Any]] = []
    if r2_path.is_file():
        r2 = json.loads(r2_path.read_text(encoding="utf-8"))
        row = next((r for r in r2.get("regions") or [] if r.get("region_id") == region_id), None)
        if row and row.get("output"):
            requested = list(row["output"].get("requested_evidence") or [])

    clip_obs = inspect_clip_activity_detail(als_path, start_qn=start_qn, end_qn=end_qn)
    midi_obs = inspect_midi_activity_als(als_path, start_qn=start_qn, end_qn=end_qn)
    auto_obs = inspect_automation_presence_als(als_path, start_qn=start_qn, end_qn=end_qn)
    track_names = list(clip_obs.get("tracks_active") or []) + [
        DRUMS_TRACK,
        BASS_TARGET,
        ROSE_BASS,
        "Main",
    ]
    routing_obs = try_ableton_mixer_routing_readonly(track_names)

    event_qn = map_event_times_to_qn(
        region_start_qn=start_qn,
        tempo_bpm=tempo,
        event_times_s=sorted(set(round(t, 3) for t in event_times_s)),
    )

    # Focused MIDI density around measured events (±1 qn).
    focus_bins: list[dict[str, Any]] = []
    for mapped in event_qn:
        center = mapped["abs_qn"]
        lo, hi = center - 1.0, center + 1.0
        per_track = []
        for track in midi_obs.get("tracks") or []:
            count = 0
            for clip in track.get("clips") or []:
                # approximate from 1qn bins
                pass
            bins = track.get("note_bins_1qn") or {}
            for key, n in bins.items():
                abs_bin = start_qn + int(key) * 1.0
                if lo <= abs_bin < hi:
                    count += int(n)
            if track.get("note_count_in_region", 0) or count:
                per_track.append({"track": track["track"], "notes_near_event": count})
        focus_bins.append(
            {
                "event_t_s": mapped["t_s"],
                "event_abs_qn": mapped["abs_qn"],
                "window_qn": [lo, hi],
                "tracks": per_track,
            }
        )

    arrangement_obs = {
        "observation_type": "ArrangementObservation",
        "source": "als_arranger_clips+session_report",
        "als_path": str(als_path),
        "region_id": region_id,
        "start_qn": start_qn,
        "end_qn": end_qn,
        "tempo_bpm": tempo,
        "project_token": tokens.get("project_token"),
        "audible_token": tokens.get("audible_token"),
        "drums_class": "HAS_MATERIAL" if clip_obs["drums_active"] else "SOURCE_INACTIVE",
        "sub_sub_bass_class": (
            "HAS_MATERIAL" if clip_obs["sub_sub_bass_active"] else "SOURCE_INACTIVE"
        ),
        "rose_bass_class": "HAS_MATERIAL" if clip_obs["rose_bass_active"] else "SOURCE_INACTIVE",
        "measured_fullmix_events": event_summaries,
        "measured_event_qn": event_qn,
        "limitations": [
            "Observed FullMix events are measure-only; this block adds causal context only.",
            "Musical intent (intentional rest vs fault) is not decided here.",
        ],
    }

    payload = {
        "status": "CAUSAL EVIDENCE GATHERED",
        "source_run": source_run,
        "region_id": region_id,
        "region": f"{start_qn:g}->{end_qn:g}qn",
        "gathered_at": now_iso(),
        "project_token": tokens.get("project_token"),
        "audible_token": tokens.get("audible_token"),
        "MUSICAL WRITES": 0,
        "ASTRA CALLS": 0,
        "MusicPlan": None,
        "requested_evidence_from_r2": requested,
        "evidence_obtained": {
            "ArrangementObservation": arrangement_obs,
            "ClipActivityObservation": clip_obs,
            "MidiActivityObservation": midi_obs,
            "AutomationObservation": auto_obs,
            "RoutingObservation": routing_obs,
            "midi_density_near_fullmix_events": focus_bins,
        },
        "not_obtained": [],
        "layers": {
            "OBSERVED": "FullMix energy events on Main (unchanged)",
            "CAUSAL": "ALS clip/MIDI/automation + optional Ableton mixer/routing",
            "JUDGMENT": "not produced",
        },
    }
    if not routing_obs.get("ok"):
        payload["not_obtained"].append(
            {
                "kind": "ABLETON_LIVE_MIXER_ROUTING",
                "reason": routing_obs.get("error") or "ableton_unavailable",
            }
        )
    # ANALYZE_REGION new capture intentionally not performed.
    payload["not_obtained"].append(
        {
            "kind": "ANALYZE_REGION_NEW_CAPTURE",
            "reason": "Do not capture new audio unless evidence genuinely requires it; ALS+FullMix used.",
        }
    )
    out = evidence / f"{source_run}_causal_{region_id.lower()}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload
