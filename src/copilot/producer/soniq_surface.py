from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
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




def _to_param_scale(value: float, p: dict[str, Any], *, normalized: bool = True) -> float:
    """Convert normalized 0..1 write to parameter native scale using min/max metadata."""
    if not normalized:
        return float(value)
    lo = float(p.get("min", 0.0))
    hi = float(p.get("max", 1.0))
    v = max(0.0, min(1.0, float(value)))
    native = lo + (hi - lo) * v
    if bool(p.get("is_quantized", False)):
        native = round(native)
    return native


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

    writes = coalesce_writes(writes)
    meta = daw.get_device_parameters(ti, di)
    by_idx_meta = {int(p.get("index", -1)): p for p in meta.get("parameters") or []}
    items = []
    for w in writes:
        idx = int(w["index"])
        pm = by_idx_meta.get(idx, {"min": 0.0, "max": 1.0})
        value_native = _to_param_scale(float(w["value"]), pm, normalized=bool(w.get("normalized", True)))
        items.append(
            {
                "track_index": ti,
                "device_index": di,
                "parameter_index": idx,
                "value": float(value_native),
            }
        )
    try:
        daw.set_device_parameters(items)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "unknown command" not in msg and "unsupported" not in msg:
            raise
        # Bridge fallback: sequential writes when batch endpoint is unavailable.
        for it in items:
            daw.set_device_parameter(
                int(it["track_index"]),
                int(it["device_index"]),
                int(it["parameter_index"]),
                float(it["value"]),
            )

    after = daw.get_device_parameters(ti, di)
    by_idx = {int(p.get("index", -1)): p for p in after.get("parameters") or []}

    readback: list[dict[str, Any]] = []
    ok = True
    by_idx_item = {int(it["parameter_index"]): float(it["value"]) for it in items}
    for w in writes:
        idx = int(w["index"])
        p = by_idx.get(idx)
        if p is None:
            ok = False
            readback.append({"index": idx, "ok": False, "error": "missing"})
            continue
        actual = float(p.get("value", 0.0))
        intended_native = by_idx_item.get(idx, float(w["value"]))
        matched = abs(actual - intended_native) <= 1e-6
        if not matched:
            # Quantized heuristics: many Live params snap to enum/integer steps,
            # and devices may floor/round/ceil depending on implementation.
            cands = {round(intended_native), math.floor(intended_native), math.ceil(intended_native)}
            matched = any(abs(actual - float(c)) <= 1e-6 for c in cands)
        ok = ok and matched
        readback.append(
            {
                "index": idx,
                "name": p.get("name", ""),
                "intended": float(w["value"]),
                "intended_native": intended_native,
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


def coalesce_writes(writes: list[dict[str, float]]) -> list[dict[str, float]]:
    """Soniq-style write coalescing: last value wins per parameter index."""
    by_idx: dict[int, float] = {}
    order: list[int] = []
    for w in writes or []:
        idx = int(w["index"])
        if idx not in by_idx:
            order.append(idx)
        by_idx[idx] = float(w["value"])
    return [{"index": i, "value": by_idx[i]} for i in order]


@dataclass
class VstParamWatcher:
    """Polling-based param change notifications (param_changed events model).

    Bridge push-notifications do not exist yet in our stack; this provides the same
    consumer contract by diffing successive reads.
    """

    track_name: str
    device_name: str
    indices: list[int]
    _last: dict[int, float] = field(default_factory=dict)

    def bootstrap(self, daw, *, session: SessionState) -> dict[str, Any]:
        snap = read_vst_params(
            daw,
            session=session,
            track_name=self.track_name,
            device_name=self.device_name,
            indices=self.indices,
        )
        self._last = {int(p["index"]): float(p["value"]) for p in snap.get("params", [])}
        return {"ok": True, "tracked": len(self._last)}

    def poll(self, daw, *, session: SessionState, tolerance: float = 1e-6) -> dict[str, Any]:
        snap = read_vst_params(
            daw,
            session=session,
            track_name=self.track_name,
            device_name=self.device_name,
            indices=self.indices,
        )
        changed: list[dict[str, Any]] = []
        for p in snap.get("params", []):
            idx = int(p["index"])
            val = float(p["value"])
            prev = self._last.get(idx)
            if prev is None or abs(val - prev) > tolerance:
                changed.append(
                    {
                        "event": "param_changed",
                        "index": idx,
                        "name": p.get("name", ""),
                        "previous": prev,
                        "value": val,
                    }
                )
            self._last[idx] = val
        return {"ok": True, "events": changed, "count": len(changed)}


_LAST_PATCH_TS: dict[tuple[int, int], float] = {}


def _resolve_patch_indices(
    schema_params: list[dict[str, Any]],
    writes: list[dict[str, Any]],
) -> list[dict[str, float]]:
    by_name = { _normalize_name(str(p.get("name") or "")): int(p.get("index", -1)) for p in schema_params }
    resolved: list[dict[str, float]] = []
    for w in writes or []:
        if "index" in w:
            resolved.append({"index": int(w["index"]), "value": float(w["value"])})
            continue
        needle = _normalize_name(str(w.get("name") or ""))
        if not needle:
            continue
        idx = None
        for pname, pi in by_name.items():
            if needle == pname or needle in pname:
                idx = pi
                break
        if idx is None:
            continue
        resolved.append({"index": int(idx), "value": float(w["value"])})
    return resolved


def apply_patch(
    daw,
    *,
    session: SessionState,
    track_name: str,
    device_name: str,
    writes: list[dict[str, Any]],
    throttle_ms: int = 40,
    filter_midi_passthrough: bool = True,
) -> dict[str, Any]:
    """Tool-level patch primitive: schema -> coalesce -> batch write -> readback -> watcher poll.

    `writes` accepts either `{index, value}` or `{name, value}`.
    """
    schema = read_vst_schema(
        daw,
        session=session,
        track_name=track_name,
        device_name=device_name,
        filter_midi_passthrough=filter_midi_passthrough,
    )
    resolved = _resolve_patch_indices(schema.get("parameters") or [], writes)
    resolved = coalesce_writes(resolved)

    watcher = VstParamWatcher(
        track_name=track_name,
        device_name=device_name,
        indices=[int(w["index"]) for w in resolved],
    )
    watcher.bootstrap(daw, session=session)

    # short write throttle (Soniq-style anti-spam)
    key = (int(schema["track_index"]), int(schema["device_index"]))
    now = time.monotonic()
    last = _LAST_PATCH_TS.get(key)
    slept_ms = 0
    if last is not None and throttle_ms > 0:
        elapsed_ms = int((now - last) * 1000)
        if elapsed_ms < throttle_ms:
            sleep_s = (throttle_ms - elapsed_ms) / 1000.0
            time.sleep(sleep_s)
            slept_ms = int(sleep_s * 1000)

    write_report = set_vst_params_batch(
        daw,
        session=session,
        track_name=track_name,
        device_name=device_name,
        writes=resolved,
    )
    _LAST_PATCH_TS[key] = time.monotonic()

    post = watcher.poll(daw, session=daw.snapshot())
    return {
        "ok": bool(write_report.get("ok", False)),
        "track": track_name,
        "device": device_name,
        "schema_param_count": int(schema.get("parameter_count", 0)),
        "requested": len(writes or []),
        "resolved": len(resolved),
        "slept_ms": slept_ms,
        "write": write_report,
        "events": post.get("events", []),
        "event_count": int(post.get("count", 0)),
    }
