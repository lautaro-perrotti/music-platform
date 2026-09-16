"""STRUM DEVICE CAUSAL TRACE V1 — amplitude-relevant device/automation for REGION_C gap.

Read-only. No musical writes. Scoped AI Test exclusion via preflight API only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import gzip
import json
import xml.etree.ElementTree as ET

from copilot.audio.session_diagnose import preflight_session
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.human_eval.store import now_iso

TRACK_NAME = "Strum"
REGION_ID = "REGION_C"
TRACE_VERSION = "strum-device-trace-1"
EVENT_START_QN = 33.87789795918367
EVENT_END_QN = 35.89493102796674
LAB_EXCLUSIONS = frozenset({"AI Test"})

# Parameter names whose amplitude effect is definitionally known.
KNOWN_AMP_NAMES = {
    "device on",
    "volume",
    "gain",
    "output gain",
    "track volume",
    "mute",
    "speaker",
    "level",
}
# Names that may silence output but are not proven volume mappings.
PLAUSIBLE_AMP_NAMES = {
    "filter",
    "cutoff",
    "frequency",
    "dry/wet",
    "dry wet",
    "chain selector",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


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


def _track_of(node: ET.Element, parents: dict[ET.Element, ET.Element]) -> str:
    current = parents.get(node)
    while current is not None:
        if _local(current.tag) in {"MidiTrack", "AudioTrack", "GroupTrack"}:
            for child in current.iter():
                if _local(child.tag) in {"EffectiveName", "UserName"} and child.attrib.get("Value"):
                    return child.attrib["Value"]
            return "?"
        current = parents.get(current)
    return "?"


def _eval_envelope(points: list[tuple[float, float]], qn: float) -> tuple[float | None, float | None]:
    if not points:
        return None, None
    ordered = sorted(points, key=lambda item: item[0])
    before = [p for p in ordered if p[0] <= qn]
    if before:
        return float(before[-1][0]), float(before[-1][1])
    return float(ordered[0][0]), float(ordered[0][1])


def _amplitude_class(param_name: str) -> str:
    low = param_name.lower().strip()
    if low in KNOWN_AMP_NAMES or any(low == k or low.startswith(k + " ") for k in KNOWN_AMP_NAMES):
        return "KNOWN"
    if "volume" in low or low.endswith(" gain") or low == "gain" or low == "mute":
        return "KNOWN"
    if low in PLAUSIBLE_AMP_NAMES or any(k in low for k in ("cutoff", "filter", "dry", "wet")):
        return "PLAUSIBLE"
    # Named performance macros (Strum, Chorus, etc.) — do not invent loudness mapping.
    return "UNSUPPORTED"


def _macro_display_names(root: ET.Element, parents: dict[ET.Element, ET.Element]) -> dict[str, str]:
    """Map MacroControls.N -> display name for Strum instrument racks."""
    out: dict[str, str] = {}
    for elem in root.iter():
        if _track_of(elem, parents) != TRACK_NAME:
            continue
        tag = _local(elem.tag)
        if not tag.startswith("MacroDisplayNames."):
            continue
        idx = tag.split(".", 1)[-1]
        value = elem.attrib.get("Value")
        if value:
            # Later names in document override earlier (rack nesting).
            out[f"MacroControls.{idx}"] = value
    return out


def inspect_strum_als_automation(
    als_path: Path,
    *,
    event_start_qn: float,
    event_end_qn: float,
) -> list[dict[str, Any]]:
    root = _load_als_root(als_path)
    parents = _parent_map(root)
    macro_names = _macro_display_names(root, parents)
    targets: dict[str, dict[str, str]] = {}
    for at in root.iter():
        if _local(at.tag) != "AutomationTarget":
            continue
        tid = at.attrib.get("Id")
        if not tid or _track_of(at, parents) != TRACK_NAME:
            continue
        path: list[str] = []
        node = parents.get(at)
        while node is not None and len(path) < 10:
            path.append(_local(node.tag))
            node = parents.get(node)
        param = path[0] if path else "?"
        display = macro_names.get(param, param)
        targets[tid] = {
            "param": param,
            "display_name": display,
            "path": "/".join(path),
        }

    mid = 0.5 * (event_start_qn + event_end_qn)
    before_q = event_start_qn - 0.25
    after_q = event_end_qn + 0.25
    rows: list[dict[str, Any]] = []
    for env in root.iter():
        if _local(env.tag) != "AutomationEnvelope":
            continue
        pointee = None
        for child in env.iter():
            if _local(child.tag) == "PointeeId" and child.attrib.get("Value"):
                pointee = child.attrib["Value"]
                break
        info = targets.get(pointee or "")
        if not info:
            continue
        points = [
            (float(c.attrib.get("Time") or 0.0), float(c.attrib.get("Value") or 0.0))
            for c in env.iter()
            if _local(c.tag) == "FloatEvent"
        ]
        if not points:
            continue
        if not any(event_start_qn - 8 <= t <= event_end_qn + 8 for t, _ in points):
            continue
        display = info["display_name"]
        amp = _amplitude_class(display)
        # Always include KNOWN/PLAUSIBLE; include UNSUPPORTED only if values change across event.
        bt, bv = _eval_envelope(points, before_q)
        dt, dv = _eval_envelope(points, mid)
        at, av = _eval_envelope(points, after_q)
        changed = (
            bv is not None
            and av is not None
            and (abs((bv or 0) - (dv or 0)) > 1e-6 or abs((dv or 0) - (av or 0)) > 1e-6)
        )
        if amp == "UNSUPPORTED" and not changed:
            continue
        surrounding = [
            {"time_qn": t, "value": v}
            for t, v in sorted(points)
            if event_start_qn - 1 <= t <= event_end_qn + 1
        ][:24]
        rows.append(
            {
                "device_identity": (
                    "Strum-o-Matic / InstrumentGroupDevice"
                    if info["param"].startswith("MacroControls")
                    else (
                        "Auto Filter"
                        if "AutoFilter" in info["path"] or info["param"] == "Cutoff"
                        else info["path"].split("/")[1] if "/" in info["path"] else "Strum"
                    )
                ),
                "parameter_identity": display,
                "parameter_raw": info["param"],
                "parameter_path": info["path"],
                "value_before": bv,
                "value_during": dv,
                "value_after": av,
                "point_time_before_qn": bt,
                "point_time_during_qn": dt,
                "point_time_after_qn": at,
                "points_surrounding_event": surrounding,
                "automation_provenance": "SAVED_PROJECT Arrangement AutomationEnvelope (step hold)",
                "amplitude_causality": amp,
                "changed_across_event": changed,
                "source": "SAVED_PROJECT",
            }
        )
    return rows


def inspect_strum_live_devices(daw: AbletonTcpAdapter) -> dict[str, Any]:
    session = daw.snapshot(include_notes=False)
    path_info = {}
    try:
        path_info = daw.get_session_path() or {}
    except Exception as exc:
        path_info = {"error": str(exc)}
    path = str(path_info.get("path") or path_info.get("session_path") or session.project_path or "")
    name = str(path_info.get("name") or session.project_name or "")
    attach_tokens(session, path=path or None, name=name or None)
    track = session.track_by_name(TRACK_NAME)
    if track is None:
        raise ValueError(f"track not found: {TRACK_NAME}")
    info = daw.get_track_info(int(track.index))
    devices = list(info.get("devices") or [])
    device_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for i, device in enumerate(devices):
        device_index = int(device.get("index", i))
        params_raw = daw.get_device_parameters(int(track.index), device_index)
        params = list(params_raw.get("parameters") or [])
        device_name = str(device.get("name") or f"device_{i}")
        class_name = str(device.get("class_name") or "")
        param_rows = []
        for param in params:
            pname = str(param.get("name") or "")
            amp = _amplitude_class(pname)
            low = pname.lower()
            keep = amp in {"KNOWN", "PLAUSIBLE"} or any(
                k in low for k in ("macro", "strum", "filter", "device on", "chain")
            )
            if not keep:
                continue
            row = {
                "device_identity": f"{device_name} ({class_name})",
                "device_index": device_index,
                "parameter_identity": pname,
                "value_live_now": param.get("value"),
                "min": param.get("min"),
                "max": param.get("max"),
                "amplitude_causality": amp,
                "automation_provenance": "LIVE_STATE get_device_parameters (current transport position, not event-time)",
                "source": "LIVE_STATE",
                "limitations": [
                    "Live parameter values are current snapshot, not Arrangement event-time evaluation.",
                ],
            }
            param_rows.append(row)
            if amp in {"KNOWN", "PLAUSIBLE"} or pname.lower() in {"strum", "filter", "device on"}:
                candidates.append(row)
        device_rows.append(
            {
                "name": device_name,
                "class_name": class_name,
                "index": device_index,
                "parameters": param_rows,
            }
        )
    mixer = {
        "device_identity": "Track Mixer",
        "parameter_identity": "Volume",
        "value_live_now": info.get("volume"),
        "mute": info.get("mute"),
        "solo": info.get("solo"),
        "amplitude_causality": "KNOWN",
        "automation_provenance": "LIVE_STATE mixer snapshot",
        "source": "LIVE_STATE",
    }
    return {
        "track": TRACK_NAME,
        "track_index": int(track.index),
        "project_token": session.project_token,
        "audible_token": session.audible_token,
        "target_state_token": target_token(track),
        "project_path": path,
        "devices": device_rows,
        "mixer": mixer,
        "live_candidates": candidates + [mixer],
    }


def _derive_cause(
    automation_rows: list[dict[str, Any]],
    live: dict[str, Any],
    *,
    event_start_qn: float,
    event_end_qn: float,
) -> dict[str, Any]:
    """Deterministic cause classification. No musical judgment."""
    ruled: list[dict[str, Any]] = []
    leads: list[dict[str, Any]] = []

    mixer = live.get("mixer") or {}
    if mixer.get("mute"):
        leads.append(
            {
                "code": "LIVE_MUTE_ON",
                "strength": "WEAK",
                "amplitude_causality": "KNOWN",
                "detail": "LIVE_STATE mute is true (current snapshot only).",
            }
        )
    else:
        ruled.append(
            {
                "code": "NO_LIVE_MUTE",
                "detail": "LIVE_STATE Strum mute is false (current snapshot only).",
            }
        )

    # Device On live
    for row in live.get("live_candidates") or []:
        if str(row.get("parameter_identity") or "").lower() == "device on":
            if float(row.get("value_live_now") or 1.0) < 0.5:
                leads.append(
                    {
                        "code": "DEVICE_OFF_LIVE",
                        "strength": "WEAK",
                        "amplitude_causality": "KNOWN",
                        "detail": f"{row.get('device_identity')} Device On is off (LIVE_STATE now).",
                    }
                )
            else:
                ruled.append(
                    {
                        "code": "DEVICE_ON_LIVE",
                        "detail": f"{row.get('device_identity')} Device On is on (LIVE_STATE now).",
                    }
                )

    # Track volume automation in ALS
    vol_auto = [
        r
        for r in automation_rows
        if r.get("amplitude_causality") == "KNOWN"
        and str(r.get("parameter_identity") or "").lower() in {"volume", "mute", "device on"}
    ]
    if not vol_auto:
        ruled.append(
            {
                "code": "NO_KNOWN_AMP_AUTOMATION_ON_STRUM",
                "detail": "No Arrangement envelopes for Volume/Mute/Device On on Strum near the event.",
            }
        )

    # Strong temporal candidates: value near floor during event, higher before/after
    for row in automation_rows:
        amp = row.get("amplitude_causality")
        during = row.get("value_during")
        before = row.get("value_before")
        after = row.get("value_after")
        if during is None:
            continue
        # Macro/filter at near-zero during gap while non-zero around it
        low_during = float(during) <= 1.0
        rose = (before is not None and float(before) > 20.0) or (
            after is not None and float(after) > 20.0
        )
        if low_during and rose and row.get("changed_across_event"):
            leads.append(
                {
                    "code": "EVENT_TIME_PARAM_AT_FLOOR",
                    "strength": "STRONG" if amp == "KNOWN" else "WEAK",
                    "amplitude_causality": amp,
                    "parameter_identity": row.get("parameter_identity"),
                    "parameter_raw": row.get("parameter_raw"),
                    "device_identity": row.get("device_identity"),
                    "value_before": before,
                    "value_during": during,
                    "value_after": after,
                    "detail": (
                        f"{row.get('device_identity')} / {row.get('parameter_identity')} "
                        f"evaluates ~{during} during qn {event_start_qn:.3f}-{event_end_qn:.3f} "
                        f"(before={before}, after={after}). "
                        f"Amplitude causality={amp}."
                    ),
                }
            )

    known_strong = [
        lead
        for lead in leads
        if lead.get("strength") == "STRONG" and lead.get("amplitude_causality") == "KNOWN"
    ]
    if len(known_strong) == 1:
        return {
            "status": "CAUSE_SUPPORTED",
            "cause": known_strong[0],
            "leads": leads,
            "ruled_out": ruled,
            "detail": "Exactly one KNOWN amplitude parameter uniquely explains the event-time attenuation.",
        }
    if len(known_strong) > 1:
        return {
            "status": "CAUSE_UNRESOLVED",
            "cause": None,
            "leads": leads,
            "ruled_out": ruled,
            "detail": "Multiple KNOWN amplitude candidates remain.",
        }
    # No KNOWN unique cause — WEAK/PLAUSIBLE leads do not upgrade to CAUSE_SUPPORTED.
    return {
        "status": "CAUSE_UNRESOLVED",
        "cause": None,
        "leads": leads,
        "ruled_out": ruled,
        "detail": (
            "No unique KNOWN amplitude parameter explains the -52 dB Strum Post Mixer drop. "
            "PLAUSIBLE/UNSUPPORTED macro or filter motion may correlate but is not proven loudness mapping."
        ),
    }


def run_strum_device_trace(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path | None = None,
    source_run: str = "session_run1",
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    # Event bounds: prefer source-audio / causal-trace artifacts.
    event_start_qn, event_end_qn = EVENT_START_QN, EVENT_END_QN
    event_id = f"{REGION_ID}.gap_cluster.0"
    sa_path = evidence / f"{source_run}_source_audio_trace_region_c.json"
    if sa_path.is_file():
        sa = json.loads(sa_path.read_text(encoding="utf-8"))
        if sa.get("event_qn_range"):
            event_start_qn = float(sa["event_qn_range"][0])
            event_end_qn = float(sa["event_qn_range"][1])
        event_id = str(sa.get("event_id") or event_id)

    preflight = preflight_session(daw, lab_track_exclusions=LAB_EXCLUSIONS)
    if not preflight.get("pass"):
        return {
            "status": "PRE-FLIGHT STOP",
            "preflight": {
                "pass": False,
                "missing": preflight.get("missing"),
                "lab_track_exclusions": preflight.get("lab_track_exclusions"),
                "lab_hits_excluded": preflight.get("lab_hits_excluded"),
            },
            "MUSICAL WRITES": 0,
            "MUSICPLAN_GATE": "CLOSED",
        }

    live = inspect_strum_live_devices(daw)
    als_path = Path(str(preflight.get("live_set_path") or ""))
    if not als_path.is_file():
        run = json.loads((evidence / f"{source_run}.json").read_text(encoding="utf-8"))
        als_path = Path(str((run.get("ARRANGEMENT ACTIVITY") or {}).get("als_path") or ""))
    if not als_path.is_file():
        raise FileNotFoundError(f"als missing for Strum automation: {als_path}")

    automation_rows = inspect_strum_als_automation(
        als_path,
        event_start_qn=event_start_qn,
        event_end_qn=event_end_qn,
    )
    cause = _derive_cause(
        automation_rows,
        live,
        event_start_qn=event_start_qn,
        event_end_qn=event_end_qn,
    )

    prior_tokens = {
        "project_token": preflight.get("project_token"),
        "audible_token": preflight.get("audible_token"),
    }
    # Refresh tokens from the live snapshot used for Strum target.
    state_snapshot = {
        "PROJECT_STATE_TOKEN": live["project_token"],
        "AUDIBLE_STATE_TOKEN": live["audible_token"],
        "TARGET_STATE_TOKEN": live["target_state_token"],
        "TARGET_TRACK": TRACK_NAME,
        "project_path": live.get("project_path"),
        "prior_preflight_tokens": prior_tokens,
        "token_refresh": "LIVE_SNAPSHOT_NOW",
        "path_identity_only": True,
        "note": (
            "Fresh tokens from current Live snapshot. Path identifies the set file only; "
            "it does not override token mismatch with prior RUN artifacts."
        ),
    }

    payload = {
        "status": "STRUM DEVICE TRACE COMPLETE",
        "trace_version": TRACE_VERSION,
        "source_run": source_run,
        "region_id": REGION_ID,
        "track": TRACK_NAME,
        "event_id": event_id,
        "event_qn_range": [event_start_qn, event_end_qn],
        "gathered_at": now_iso(),
        "lab_track_exclusions": sorted(LAB_EXCLUSIONS),
        "preflight_pass": True,
        "state_snapshot": state_snapshot,
        "live_devices": live.get("devices"),
        "live_mixer": live.get("mixer"),
        "automation_candidates": automation_rows,
        "cause_result": cause,
        "MUSICAL WRITES": 0,
        "ASTRA CALLS": 0,
        "MusicPlan": None,
        "limitations": [
            "LIVE_STATE parameter values are current transport snapshot, not event-time.",
            "SAVED_PROJECT automation uses step-hold evaluation of FloatEvent points.",
            "Macro display names are not assumed to control loudness unless amplitude_causality=KNOWN.",
            "Clip gain/fades: MidiClip — no clip gain envelope inspected beyond Arrangement automation.",
        ],
    }
    out = evidence / f"{source_run}_strum_device_trace_{REGION_ID.lower()}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload
