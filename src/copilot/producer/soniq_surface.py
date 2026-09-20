from __future__ import annotations

import re
from typing import Any

from copilot.daw.adapter import DawError
from copilot.schemas.session import SessionState

_MIDI_PASSTHROUGH_RE = re.compile(r"^cc\d+\s+chan\s+\d+$", re.I)


def _normalize_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _resolve_track_index(session: SessionState, track_name: str) -> int:
    track = session.track_by_name(track_name)
    if track is None:
        raise DawError(f"Track not found or ambiguous: {track_name!r}")
    return int(track.index)


def _resolve_device_index(session: SessionState, track_index: int, device_name: str) -> int:
    track = next((t for t in session.tracks if int(t.index) == int(track_index)), None)
    if track is None:
        raise DawError(f"Track index not found in session: {track_index}")
    want = _normalize_name(device_name)
    # exact name first
    for d in track.devices:
        if _normalize_name(d.name) == want:
            return int(d.index)
    # substring fallback
    for d in track.devices:
        if want in _normalize_name(d.name):
            return int(d.index)
    raise DawError(f"Device not found on track[{track_index}]: {device_name!r}")


def _filter_params(parameters: list[dict[str, Any]], *, filter_midi_passthrough: bool) -> list[dict[str, Any]]:
    if not filter_midi_passthrough:
        return list(parameters or [])
    out: list[dict[str, Any]] = []
    for p in parameters or []:
        n = str(p.get("name") or "")
        if _MIDI_PASSTHROUGH_RE.match(_normalize_name(n)):
            continue
        out.append(p)
    return out


def read_vst_schema(
    daw,
    *,
    session: SessionState,
    track_name: str,
    device_name: str,
    filter_midi_passthrough: bool = True,
) -> dict[str, Any]:
    """Soniq-style plugin schema read from a loaded track device."""
    ti = _resolve_track_index(session, track_name)
    di = _resolve_device_index(session, ti, device_name)
    params = daw.get_device_parameters(ti, di)
    filtered = _filter_params(params.get("parameters") or [], filter_midi_passthrough=filter_midi_passthrough)
    return {
        "track_index": ti,
        "device_index": di,
        "plugin": params.get("device_name") or device_name,
        "parameter_count": len(filtered),
        "parameters": [
            {
                "index": int(p.get("index", -1)),
                "name": p.get("name", ""),
                "min": float(p.get("min", 0.0)),
                "max": float(p.get("max", 1.0)),
                "value": float(p.get("value", 0.0)),
            }
            for p in filtered
        ],
    }


def read_vst_params(
    daw,
    *,
    session: SessionState,
    track_name: str,
    device_name: str,
    indices: list[int],
) -> dict[str, Any]:
    ti = _resolve_track_index(session, track_name)
    di = _resolve_device_index(session, ti, device_name)
    params = daw.get_device_parameters(ti, di)
    by_idx = {int(p.get("index", -1)): p for p in params.get("parameters") or []}
    out: list[dict[str, Any]] = []
    for idx in indices:
        p = by_idx.get(int(idx))
        if p is None:
            continue
        out.append(
            {
                "index": int(p.get("index", idx)),
                "name": p.get("name", ""),
                "value": float(p.get("value", 0.0)),
                "min": float(p.get("min", 0.0)),
                "max": float(p.get("max", 1.0)),
            }
        )
    return {"track_index": ti, "device_index": di, "params": out}


def set_vst_params_batch(
    daw,
    *,
    session: SessionState,
    track_name: str,
    device_name: str,
    writes: list[dict[str, float]],
) -> dict[str, Any]:
    """Soniq-style batch parameter write + readback verification."""
    ti = _resolve_track_index(session, track_name)
    di = _resolve_device_index(session, ti, device_name)

    items = [
        {
            "track_index": ti,
            "device_index": di,
            "parameter_index": int(w["index"]),
            "value": float(w["value"]),
        }
        for w in writes
    ]
    daw.set_device_parameters(items)

    after = daw.get_device_parameters(ti, di)
    by_idx = {int(p.get("index", -1)): p for p in after.get("parameters") or []}

    readback: list[dict[str, Any]] = []
    ok = True
    for w in writes:
        idx = int(w["index"])
        p = by_idx.get(idx)
        if p is None:
            ok = False
            readback.append({"index": idx, "ok": False, "error": "missing"})
            continue
        actual = float(p.get("value", 0.0))
        intended = float(w["value"])
        matched = abs(actual - intended) <= 1e-6
        ok = ok and matched
        readback.append(
            {
                "index": idx,
                "name": p.get("name", ""),
                "intended": intended,
                "actual": actual,
                "ok": matched,
            }
        )

    return {
        "ok": ok,
        "track_index": ti,
        "device_index": di,
        "writes": len(writes),
        "readback": readback,
    }
