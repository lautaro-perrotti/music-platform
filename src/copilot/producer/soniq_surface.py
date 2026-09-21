from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
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
    write_mode = "batch_native"
    try:
        daw.set_device_parameters(items)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "unknown command" not in msg and "unsupported" not in msg:
            raise
        write_mode = "sequential_fallback"
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
        "write_mode": write_mode,
        "readback": readback,
    }


def coalesce_writes(writes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Soniq-style write coalescing: last value wins per parameter index."""
    by_idx: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for w in writes or []:
        idx = int(w["index"])
        if idx not in by_idx:
            order.append(idx)
        by_idx[idx] = {
            "index": idx,
            "value": float(w["value"]),
            "normalized": bool(w.get("normalized", True)),
        }
    return [by_idx[i] for i in order]


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
) -> list[dict[str, Any]]:
    by_name = { _normalize_name(str(p.get("name") or "")): int(p.get("index", -1)) for p in schema_params }
    resolved: list[dict[str, float]] = []
    for w in writes or []:
        if "index" in w:
            resolved.append({"index": int(w["index"]), "value": float(w["value"]), "normalized": bool(w.get("normalized", True))})
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
        resolved.append({"index": int(idx), "value": float(w["value"]), "normalized": bool(w.get("normalized", True))})
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



def _normalized_from_param(value_native: float, p: dict[str, Any]) -> float:
    lo = float(p.get("min", 0.0))
    hi = float(p.get("max", 1.0))
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (float(value_native) - lo) / (hi - lo)))


def apply_patch_contract(
    daw,
    *,
    session: SessionState,
    contract: dict[str, Any],
    throttle_ms: int = 40,
) -> dict[str, Any]:
    """High-level autonomous patch wrapper with musical constraints.

    Contract shape:
    {
      "track": "Bass",
      "device": "Compressor",
      "writes": [{"index"|"name":..., "value":0..1}],
      "constraints": {
        "max_delta_norm": 0.35,
        "max_writes": 8,
        "forbid_device_on_toggle": true
      }
    }
    """
    track = str(contract.get("track") or "")
    device = str(contract.get("device") or "")
    writes = list(contract.get("writes") or [])
    constraints = dict(contract.get("constraints") or {})

    if not track or not device or not writes:
        return {"ok": False, "error": "invalid_contract", "contract": contract}

    max_delta = float(constraints.get("max_delta_norm", 0.35))
    max_writes = int(constraints.get("max_writes", 8))
    forbid_device_on_toggle = bool(constraints.get("forbid_device_on_toggle", True))

    schema = read_vst_schema(
        daw,
        session=session,
        track_name=track,
        device_name=device,
        filter_midi_passthrough=False,
    )
    resolved = _resolve_patch_indices(schema.get("parameters") or [], writes)
    resolved = coalesce_writes(resolved)

    violations: list[str] = []
    if len(resolved) > max_writes:
        violations.append(f"write_count>{max_writes}; truncated")
        resolved = resolved[:max_writes]

    by_idx = {int(p.get("index", -1)): p for p in schema.get("parameters") or []}
    adjusted: list[dict[str, float]] = []

    for w in resolved:
        idx = int(w["index"])
        normalized = bool(w.get("normalized", True))
        target = float(w["value"])
        if normalized:
            target = max(0.0, min(1.0, target))
        p = by_idx.get(idx)
        if p is None:
            violations.append(f"unknown_param_index:{idx}")
            continue
        pname = _normalize_name(str(p.get("name") or ""))

        if forbid_device_on_toggle and (idx == 0 or pname == "device on"):
            violations.append(f"blocked_device_on_toggle:{idx}")
            continue

        if normalized:
            current_norm = _normalized_from_param(float(p.get("value", 0.0)), p)
            if abs(target - current_norm) > max_delta:
                step = max_delta if target > current_norm else -max_delta
                target = current_norm + step
                violations.append(f"delta_clamped:{idx}")

        adjusted.append({"index": idx, "value": target, "normalized": normalized})

    patch = apply_patch(
        daw,
        session=session,
        track_name=track,
        device_name=device,
        writes=adjusted,
        throttle_ms=throttle_ms,
        filter_midi_passthrough=False,
    )

    return {
        "ok": bool(patch.get("ok", False)),
        "track": track,
        "device": device,
        "requested": len(writes),
        "applied": len(adjusted),
        "violations": violations,
        "patch": patch,
    }



def resolve_device(session: SessionState, *, track_name: str, device_name: str) -> dict[str, Any]:
    """Resolve track/device indices for an existing device."""
    ti = _resolve_track_index(session, track_name)
    di = _resolve_device_index(session, ti, device_name)
    return {"track_index": ti, "device_index": di}


def load_preset(
    daw,
    *,
    session: SessionState,
    track_name: str,
    device_name: str,
    preset_uri: str,
) -> dict[str, Any]:
    """Load plugin preset using bridge hot-swap when available."""
    ref = resolve_device(session, track_name=track_name, device_name=device_name)
    result = daw.load_device_preset(int(ref["track_index"]), int(ref["device_index"]), preset_uri)
    ok = bool(result.get("loaded", False)) and not result.get("error")
    return {
        "ok": ok,
        "track": track_name,
        "device": device_name,
        "preset_uri": preset_uri,
        "result": result,
    }


def capture_param_snapshot(
    daw,
    *,
    session: SessionState,
    track_name: str,
    device_name: str,
    filter_midi_passthrough: bool = True,
) -> dict[str, Any]:
    """Capture full parameter snapshot for A/B rollback style preset workflow."""
    schema = read_vst_schema(
        daw,
        session=session,
        track_name=track_name,
        device_name=device_name,
        filter_midi_passthrough=filter_midi_passthrough,
    )
    writes = [{"index": int(p["index"]), "value": float(p["value"]), "normalized": False} for p in schema.get("parameters", [])]
    return {
        "ok": True,
        "track": track_name,
        "device": device_name,
        "schema_param_count": int(schema.get("parameter_count", 0)),
        "writes": writes,
    }


def restore_param_snapshot(
    daw,
    *,
    session: SessionState,
    snapshot: dict[str, Any],
    throttle_ms: int = 40,
) -> dict[str, Any]:
    """Restore captured param snapshot through apply_patch (native values, no normalization)."""
    contract = {
        "track": snapshot.get("track"),
        "device": snapshot.get("device"),
        "writes": snapshot.get("writes") or [],
        "constraints": {
            "max_delta_norm": 1.0,
            "max_writes": 4096,
            "forbid_device_on_toggle": False,
        },
    }
    return apply_patch_contract(daw, session=session, contract=contract, throttle_ms=throttle_ms)


_EXPECTED_PARAM_COUNTS = {
    # Soniq reference for Serum 2 full surface
    "serum 2": 2623,
    "serum2": 2623,
    # Indicative references for other deep synths (kept conservative).
    "pigments": 1200,
    "vital": 500,
    "massive": 400,
    # Serum 1 count can vary by version/build; keep a conservative floor.
    "serum": 1000,
}

_COMPLEX_PLUGIN_HINTS = (
    "serum",
    "pigments",
    "vital",
    "massive",
    "phase plant",
)


def _expected_count_for_device(device_name: str) -> int | None:
    n = _normalize_name(device_name)
    for key, val in _EXPECTED_PARAM_COUNTS.items():
        if key in n:
            return int(val)
    return None


def _plugin_is_complex(device_name: str) -> bool:
    n = _normalize_name(device_name)
    return any(k in n for k in _COMPLEX_PLUGIN_HINTS)


def _soniq_ws_url() -> str | None:
    url = os.getenv("SONIQ_WS_URL", "").strip()
    return url or None


def _rpc_try_methods(ws, methods: list[str], params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    last_err: str | None = None
    for method in methods:
        rid = str(uuid.uuid4())
        ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        raw = ws.recv()
        data = json.loads(raw)
        if data.get("id") != rid:
            # tolerate out-of-order: keep trying this response if it is success-shaped
            if "result" in data and not data.get("error"):
                return method, data["result"]
        if data.get("error"):
            last_err = str(data.get("error"))
            continue
        return method, data.get("result") or {}
    raise DawError(f"Soniq RPC failed for methods={methods}: {last_err or 'no compatible method'}")


def _apply_patch_via_soniq_ws(contract: dict[str, Any], *, timeout_s: float = 4.0) -> dict[str, Any]:
    """Optional Soniq-style path over WS JSON-RPC (when SONIQ_WS_URL is configured)."""
    ws_url = _soniq_ws_url()
    if not ws_url:
        return {"ok": False, "error": "soniq_ws_url_not_configured"}
    try:
        import websocket  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"ok": False, "error": f"websocket_client_missing:{exc}"}

    track = str(contract.get("track") or "")
    device = str(contract.get("device") or "")
    writes = list(contract.get("writes") or [])
    if not track or not device or not writes:
        return {"ok": False, "error": "invalid_contract"}

    ws = websocket.create_connection(ws_url, timeout=timeout_s)
    try:
        common = {
            "track": track,
            "device": device,
            "track_name": track,
            "device_name": device,
        }
        schema_method, schema = _rpc_try_methods(
            ws,
            ["read_vst_schema", "vst.schema", "soniq.vst.schema"],
            common,
        )
        params = list(schema.get("parameters") or [])
        by_name = {_normalize_name(str(p.get("name") or "")): int(p.get("index", -1)) for p in params}

        resolved: list[dict[str, Any]] = []
        for w in writes:
            idx = w.get("index")
            if idx is None and w.get("name"):
                idx = by_name.get(_normalize_name(str(w.get("name") or "")))
            if idx is None or int(idx) < 0:
                continue
            resolved.append({
                "index": int(idx),
                "value": float(w.get("value", 0.0)),
                "normalized": bool(w.get("normalized", True)),
            })

        resolved = coalesce_writes(resolved)
        if not resolved:
            return {"ok": False, "error": "no_resolved_writes", "schema_method": schema_method}

        write_params = {
            **common,
            "writes": resolved,
            "items": resolved,
            "parameters": resolved,
        }
        write_method, write_result = _rpc_try_methods(
            ws,
            ["set_vst_params_batch", "vst.write", "soniq.vst.write"],
            write_params,
        )

        read_params = {
            **common,
            "indices": [int(w["index"]) for w in resolved],
            "params": [int(w["index"]) for w in resolved],
        }
        readback_method, readback = _rpc_try_methods(
            ws,
            ["read_vst_params", "vst.read", "soniq.vst.read"],
            read_params,
        )

        return {
            "ok": True,
            "schema_method": schema_method,
            "write_method": write_method,
            "readback_method": readback_method,
            "requested": len(writes),
            "applied": len(resolved),
            "write": write_result,
            "readback": readback,
        }
    except Exception as exc:
        return {"ok": False, "error": f"soniq_rpc_error:{exc}"}
    finally:
        try:
            ws.close()
        except Exception:
            pass


def detect_surface_completeness(
    daw,
    *,
    session: SessionState,
    track_name: str,
    device_name: str,
    filter_midi_passthrough: bool = False,
) -> dict[str, Any]:
    """Detect whether the visible parameter surface is close to a full plugin surface.

    For Serum2, Soniq reports ~2623 reachable params; we use that as the target.
    """
    schema = read_vst_schema(
        daw,
        session=session,
        track_name=track_name,
        device_name=device_name,
        filter_midi_passthrough=filter_midi_passthrough,
    )
    visible = int(schema.get("parameter_count", 0))
    expected = _expected_count_for_device(str(schema.get("plugin") or device_name))

    if expected is None:
        return {
            "ok": True,
            "mode": "unknown_plugin",
            "plugin": schema.get("plugin") or device_name,
            "visible": visible,
            "expected": None,
            "ratio": None,
            "is_full_surface": False,
            "reason": "no expected reference for this plugin",
            "schema": schema,
        }

    ratio = (visible / expected) if expected > 0 else 0.0
    # High bar for "full surface": at least 85% of reference count.
    is_full = ratio >= 0.85
    return {
        "ok": True,
        "mode": "full_surface" if is_full else "limited_surface",
        "plugin": schema.get("plugin") or device_name,
        "visible": visible,
        "expected": expected,
        "ratio": ratio,
        "is_full_surface": is_full,
        "reason": "serum-like full surface detected" if is_full else "surface below full threshold",
        "schema": schema,
    }


def apply_patch_contract_auto_mode(
    daw,
    *,
    session: SessionState,
    contract: dict[str, Any],
    throttle_ms: int = 40,
) -> dict[str, Any]:
    """Auto-route patch contracts by detected device surface completeness.

    Routing priority:
    1) full_surface via Live MCP when completeness is high.
    2) soniq_full_surface via optional WS bridge for complex plugins (Serum/Pigments/...)
       when Live MCP surface is limited.
    3) fallback_surface via conservative live wrapper.
    """
    track = str(contract.get("track") or "")
    device = str(contract.get("device") or "")
    det = detect_surface_completeness(
        daw,
        session=session,
        track_name=track,
        device_name=device,
        filter_midi_passthrough=False,
    )

    c = dict(contract)
    c_constraints = dict(c.get("constraints") or {})

    if det.get("is_full_surface"):
        c_constraints.setdefault("max_writes", 24)
        c_constraints.setdefault("max_delta_norm", 0.5)
        c_constraints.setdefault("forbid_device_on_toggle", True)
        c["constraints"] = c_constraints
        patch = apply_patch_contract(daw, session=session, contract=c, throttle_ms=throttle_ms)
        return {
            "ok": bool(patch.get("ok", False)),
            "routing_mode": "full_surface",
            "detector": det,
            "patch": patch,
        }

    plugin_name = str(det.get("plugin") or device)
    soniq_attempted = False
    soniq_report: dict[str, Any] | None = None
    if _plugin_is_complex(plugin_name) and _soniq_ws_url():
        soniq_attempted = True
        soniq_report = _apply_patch_via_soniq_ws(c)
        if soniq_report.get("ok"):
            return {
                "ok": True,
                "routing_mode": "soniq_full_surface",
                "detector": det,
                "soniq": soniq_report,
            }

    c_constraints.setdefault("max_writes", 8)
    c_constraints.setdefault("max_delta_norm", 0.35)
    c_constraints.setdefault("forbid_device_on_toggle", True)
    c["constraints"] = c_constraints
    patch = apply_patch_contract(daw, session=session, contract=c, throttle_ms=throttle_ms)
    return {
        "ok": bool(patch.get("ok", False)),
        "routing_mode": "fallback_surface",
        "detector": det,
        "soniq_attempted": soniq_attempted,
        "soniq": soniq_report,
        "patch": patch,
    }
