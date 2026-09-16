from __future__ import annotations

from pathlib import Path
from typing import Any
import gzip
import xml.etree.ElementTree as ET

from copilot.audio.session_diagnose import BASS_TARGET

DRUMS_TRACK = "Drums"
ROSE_BASS = "Rose Bass"
CLIP_TAGS = {"MidiClip", "AudioClip"}
TRACK_TAGS = {"MidiTrack", "AudioTrack", "GroupTrack"}
WINDOW_QN = 32.0
MIN_OVERLAP_QN = 16.0
BAR_QN = 4.0


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


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


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    mapping: dict[ET.Element, ET.Element] = {}
    stack = [root]
    while stack:
        node = stack.pop()
        for child in list(node):
            mapping[child] = node
            stack.append(child)
    return mapping


def _ancestor_tags(node: ET.Element, mapping: dict[ET.Element, ET.Element]) -> set[str]:
    tags: set[str] = set()
    current = mapping.get(node)
    while current is not None:
        tags.add(_local(current.tag))
        current = mapping.get(current)
    return tags


def _load_als_root(path: Path) -> ET.Element:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def load_arrangement_clips(als_path: Path) -> list[dict[str, Any]]:
    """Read Arrangement clips from a Live .als. Session clip slots are ignored."""
    root = _load_als_root(als_path)
    parents = _parent_map(root)
    rows: list[dict[str, Any]] = []
    current_group: str | None = None
    for elem in root.iter():
        tag = _local(elem.tag)
        if tag not in TRACK_TAGS:
            continue
        name = _first_named(elem, {"EffectiveName", "UserName", "MemorizedName"}) or ""
        grouped = str(_attr_value(elem, "IsGrouped")).lower() in {"true", "1"}
        is_group = tag == "GroupTrack" or str(_attr_value(elem, "IsFoldable")).lower() in {
            "true",
            "1",
        }
        if is_group:
            current_group = name or current_group
        elif not grouped:
            current_group = None
        parent_group = current_group if grouped and not is_group else (name if is_group else None)
        for clip in elem.iter():
            if _local(clip.tag) not in CLIP_TAGS:
                continue
            found_owner = False
            node = parents.get(clip)
            while node is not None:
                if _local(node.tag) in TRACK_TAGS:
                    found_owner = node is elem
                    break
                node = parents.get(node)
            if not found_owner:
                continue
            ancestors = _ancestor_tags(clip, parents)
            if "ClipSlot" in ancestors or "ClipSlots" in ancestors:
                continue
            if "ArrangerAutomation" not in ancestors and "ClipTimeable" not in ancestors:
                continue
            span = _clip_span(clip)
            if span is None:
                continue
            start, end = span
            rows.append(
                {
                    "track": name,
                    "group": parent_group,
                    "is_group": is_group,
                    "kind": _local(clip.tag),
                    "start_qn": start,
                    "end_qn": end,
                    "name": _attr_value(clip, "Name")
                    or _first_named(clip, {"Name", "EffectiveName"}),
                }
            )
    return rows


def clips_overlap_region(
    clips: list[dict[str, Any]], start_qn: float, end_qn: float
) -> list[dict[str, Any]]:
    hits = []
    for clip in clips:
        if float(clip["start_qn"]) < float(end_qn) and float(clip["end_qn"]) > float(start_qn):
            hits.append(clip)
    return hits


def _track_matches(clip: dict[str, Any], wanted: str, *, include_group_children: bool) -> bool:
    name = str(clip.get("track") or "")
    group = str(clip.get("group") or "")
    if name == wanted:
        return True
    return bool(include_group_children and group == wanted)


def inspect_arrangement_activity(
    als_path: Path,
    regions: list[dict[str, Any]],
    *,
    drums_name: str = DRUMS_TRACK,
    bass_name: str = BASS_TARGET,
) -> dict[str, Any]:
    clips = load_arrangement_clips(als_path)
    drums_clips = [
        clip for clip in clips if _track_matches(clip, drums_name, include_group_children=True)
    ]
    bass_clips = [
        clip for clip in clips if _track_matches(clip, bass_name, include_group_children=False)
    ]
    rose_clips = [
        clip for clip in clips if _track_matches(clip, ROSE_BASS, include_group_children=False)
    ]
    rows = []
    for region in regions:
        start = float(region["start_qn"])
        end = float(region["end_qn"])
        drums_hits = clips_overlap_region(drums_clips, start, end)
        bass_hits = clips_overlap_region(bass_clips, start, end)
        rose_hits = clips_overlap_region(rose_clips, start, end)
        rows.append(
            {
                "id": region.get("id"),
                "start_qn": start,
                "end_qn": end,
                "drums": {
                    "has_material": bool(drums_hits),
                    "class": "HAS_MATERIAL" if drums_hits else "SOURCE_INACTIVE",
                    "overlapping_clips": len(drums_hits),
                },
                "rose_bass": {
                    "has_material": bool(rose_hits),
                    "class": "HAS_MATERIAL" if rose_hits else "SOURCE_INACTIVE",
                    "overlapping_clips": len(rose_hits),
                    "isolated_in_this_run": False,
                },
                "sub_sub_bass": {
                    "has_material": bool(bass_hits),
                    "class": "HAS_MATERIAL" if bass_hits else "SOURCE_INACTIVE",
                    "overlapping_clips": len(bass_hits),
                },
            }
        )
    return {
        "ok": True,
        "source": "als_arranger_clips",
        "als_path": str(als_path),
        "method": (
            "Read-only .als ArrangerAutomation clips. "
            "Session clip_slots are not used. Presence only, not quality."
        ),
        "clip_count": len(clips),
        "drums_clip_count": len(drums_clips),
        "rose_bass_clip_count": len(rose_clips),
        "bass_clip_count": len(bass_clips),
        "regions": rows,
    }


def merge_intervals(clips: list[dict[str, Any]]) -> list[tuple[float, float]]:
    spans = sorted(
        (float(clip["start_qn"]), float(clip["end_qn"])) for clip in clips
    )
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def intersect_intervals(
    left: list[tuple[float, float]], right: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    hits: list[tuple[float, float]] = []
    for a0, a1 in left:
        for b0, b1 in right:
            lo = max(a0, b0)
            hi = min(a1, b1)
            if hi > lo:
                hits.append((lo, hi))
    return merge_intervals(
        [{"start_qn": start, "end_qn": end} for start, end in hits]
    )


def union_intervals(
    left: list[tuple[float, float]], right: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    return merge_intervals(
        [{"start_qn": start, "end_qn": end} for start, end in list(left) + list(right)]
    )


def subtract_intervals(
    left: list[tuple[float, float]], right: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    remaining = list(left)
    for b0, b1 in right:
        nxt: list[tuple[float, float]] = []
        for a0, a1 in remaining:
            if b1 <= a0 or b0 >= a1:
                nxt.append((a0, a1))
                continue
            if a0 < b0:
                nxt.append((a0, max(a0, min(a1, b0))))
            if a1 > b1:
                nxt.append((min(a1, max(a0, b1)), a1))
        remaining = [(s, e) for s, e in nxt if e > s]
    return remaining


def overlap_duration(
    intervals: list[tuple[float, float]], start_qn: float, end_qn: float
) -> float:
    total = 0.0
    for left, right in intervals:
        lo = max(left, float(start_qn))
        hi = min(right, float(end_qn))
        if hi > lo:
            total += hi - lo
    return total


def _track_clips(clips: list[dict[str, Any]], name: str, *, grouped: bool) -> list[dict[str, Any]]:
    return [clip for clip in clips if _track_matches(clip, name, include_group_children=grouped)]


def inspect_tempo_contract(
    als_path: Path,
    *,
    start_qn: float | None = None,
    end_qn: float | None = None,
) -> dict[str, Any]:
    """Read-only ALS tempo. Fail closed if mapping cannot be established."""
    if not als_path.is_file():
        return {
            "ok": False,
            "status": "TEMPO_MAPPING_UNAVAILABLE",
            "error": f"als missing: {als_path}",
        }
    try:
        root = _load_als_root(als_path)
    except Exception as exc:
        return {
            "ok": False,
            "status": "TEMPO_MAPPING_UNAVAILABLE",
            "error": str(exc),
        }
    manual: float | None = None
    events: list[dict[str, float]] = []
    for elem in root.iter():
        if _local(elem.tag) != "Tempo":
            continue
        manual_el = next((c for c in list(elem) if _local(c.tag) == "Manual"), None)
        if manual_el is not None:
            value = _float_or_none(manual_el.attrib.get("Value"))
            if value is not None:
                manual = value
        for child in elem.iter():
            if _local(child.tag) != "FloatEvent":
                continue
            time_v = _float_or_none(child.attrib.get("Time"))
            value = _float_or_none(child.attrib.get("Value"))
            if time_v is None or value is None:
                continue
            events.append({"time_qn": time_v, "tempo": value})
        break
    if manual is None and not events:
        return {
            "ok": False,
            "status": "TEMPO_MAPPING_UNAVAILABLE",
            "error": "no Tempo Manual or FloatEvent in als",
        }
    scoped = events
    if start_qn is not None and end_qn is not None:
        scoped = [
            event
            for event in events
            if float(start_qn) - 1e-9 <= event["time_qn"] < float(end_qn)
        ]
        if events:
            before = [event for event in events if event["time_qn"] <= float(start_qn)]
            if before:
                last_before = max(before, key=lambda item: item["time_qn"])
                if last_before not in scoped:
                    scoped = [last_before] + scoped
    values = [event["tempo"] for event in scoped] if scoped else ([manual] if manual is not None else [])
    if not values:
        values = [manual] if manual is not None else []
    if not values:
        return {"ok": False, "status": "TEMPO_MAPPING_UNAVAILABLE", "error": "empty tempo values"}
    span = max(values) - min(values)
    constant = span <= 0.05
    return {
        "ok": constant,
        "status": "CONSTANT" if constant else "TEMPO_AUTOMATION_UNSUPPORTED",
        "manual_tempo": manual,
        "event_count": len(events),
        "scoped_event_count": len(scoped),
        "tempos": values,
        "span_bpm": span,
    }


def map_lowend_candidates(
    als_path: Path,
    *,
    window_qn: float = WINDOW_QN,
    min_overlap_qn: float = MIN_OVERLAP_QN,
    bar_qn: float = BAR_QN,
) -> dict[str, Any]:
    clips = load_arrangement_clips(als_path)
    drums = merge_intervals(_track_clips(clips, DRUMS_TRACK, grouped=True))
    rose = merge_intervals(_track_clips(clips, ROSE_BASS, grouped=False))
    sub = merge_intervals(_track_clips(clips, BASS_TARGET, grouped=False))
    drums_rose = intersect_intervals(drums, rose)
    drums_sub = intersect_intervals(drums, sub)
    drums_both = intersect_intervals(drums_rose, sub)
    drums_rose_only = subtract_intervals(drums_rose, sub)
    drums_sub_only = subtract_intervals(drums_sub, rose)
    any_bass = union_intervals(rose, sub)
    drums_no_bass = subtract_intervals(drums, any_bass)
    end = 0.0
    for group in (drums, rose, sub):
        if group:
            end = max(end, max(item[1] for item in group))

    def windows_for(kind: str, intervals: list[tuple[float, float]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0.0
        while start + window_qn <= max(end, window_qn):
            ol = overlap_duration(intervals, start, start + window_qn)
            if ol >= min_overlap_qn:
                rows.append(
                    {
                        "start_qn": start,
                        "end_qn": start + window_qn,
                        "kind": kind,
                        "simultaneous_overlap_qn": ol,
                        "drums_overlap_qn": overlap_duration(drums, start, start + window_qn),
                        "rose_bass_overlap_qn": overlap_duration(rose, start, start + window_qn),
                        "sub_sub_bass_overlap_qn": overlap_duration(sub, start, start + window_qn),
                    }
                )
            start += bar_qn
        return rows

    def pick(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not rows:
            return None
        return max(rows, key=lambda row: row["simultaneous_overlap_qn"])

    region_a = pick(windows_for("drums_plus_rose_bass", drums_rose_only)) or pick(
        windows_for("drums_plus_both_bass", drums_both)
    )
    region_b = pick(windows_for("drums_plus_sub_sub_bass", drums_sub_only)) or pick(
        windows_for("drums_plus_both_bass", drums_both)
    )
    if (
        region_a
        and region_b
        and region_a["start_qn"] == region_b["start_qn"]
        and drums_sub_only
    ):
        region_b = pick(windows_for("drums_plus_sub_sub_bass", drums_sub_only))
    region_c = pick(windows_for("drums_bass_absent", drums_no_bass))
    return {
        "ok": True,
        "source": "als_arranger_clips",
        "als_path": str(als_path),
        "window_qn": window_qn,
        "min_overlap_qn": min_overlap_qn,
        "DRUMS_ACTIVITY": drums,
        "ROSE_BASS_ACTIVITY": rose,
        "SUB_SUB_BASS_ACTIVITY": sub,
        "simultaneous": {
            "drums_rose": drums_rose,
            "drums_sub": drums_sub,
            "drums_both": drums_both,
            "drums_rose_only": drums_rose_only,
            "drums_sub_only": drums_sub_only,
            "drums_no_bass": drums_no_bass,
        },
        "candidates": {
            "REGION_A": region_a,
            "REGION_B": region_b,
            "REGION_C": region_c,
        },
        "note": (
            "Simultaneous overlap only. No CLEAN/TEMPORAL/SPECTRAL labels. "
            "Sub Sub Bass is not assumed to be the only bass."
        ),
    }
