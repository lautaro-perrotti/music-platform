"""Core-owned execution boundary for Lucas mix/master strategy.

Lucas supplies the strategy as grounded, stable-target intent. This module
only resolves that intent against the authoritative session and sends typed
actions through ProductionCompiler and SafeWriteExecutor. It does not contain
mixing taste or a second DAW writer.
"""

from __future__ import annotations

from pathlib import Path
from math import isfinite
from typing import Any, Callable

from copilot.daw.state_tokens import attach_tokens
from copilot.audio.arrangement_activity import load_arrangement_clips
from copilot.human_eval.store import now_iso
from copilot.integration.lucas_core_v1 import (
    _single_action_plan,
)
from copilot.musicplan import (
    build_device_load_action,
    build_device_tweak_action,
    build_set_track_volume_action,
)
from copilot.producer.parameter_registry import introspect_mix_parameter
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.runtime.safe_write import build_safe_write_executor
from copilot.schemas.musicplan import (
    DiagnosisBinding,
    MusicPlan,
    PlanIntentClass,
    PlanStatus,
    ProductionActionKind,
    VolumeOperation,
)


def _optional_parameter_max(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if isfinite(parsed) else None
from copilot.schemas.session import (
    DeviceParameter,
    DeviceState,
    MixerState,
    RoutingState,
    SessionState,
    TrackState,
)


class ActiveRegionSelectionError(RuntimeError):
    """No evidence-backed non-silent arrangement region can be selected."""

    code = "NO_AUDIBLE_PROJECT_CONTENT"


def _merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def select_capturable_active_region(
    session: SessionState,
    *,
    duration_beats: float = 16.0,
    minimum_coverage_ratio: float = 0.75,
) -> dict[str, Any]:
    """Select a region supported by current arrangement evidence.

    The selector deliberately does not infer audibility from track names or
    fixed song positions. It uses persisted arrangement clip spans intersected
    with the authoritative mute/solo state. The capture layer still performs
    the final Main non-silence check.
    """
    if duration_beats <= 0:
        raise ValueError("duration_beats must be positive")
    if not session.project_path:
        raise ActiveRegionSelectionError("PROJECT_PATH_MISSING")
    project_path = Path(session.project_path)
    if not project_path.is_file():
        raise ActiveRegionSelectionError(f"PROJECT_NOT_FOUND: {project_path}")

    tracks = [track for track in session.tracks if track.role in {"midi", "audio", "unknown"}]
    solo_names = {track.name for track in tracks if track.mixer.solo}
    if solo_names:
        allowed_names = solo_names
    else:
        allowed_names = {track.name for track in tracks if not track.mixer.mute}
    rows = [
        row for row in load_arrangement_clips(project_path)
        if str(row.get("track") or "") in allowed_names
    ]
    if not rows:
        raise ActiveRegionSelectionError("NO_ACTIVE_ARRANGEMENT_CLIPS")

    candidates = {
        max(0.0, float(row["start_qn"]))
        for row in rows
        if float(row["end_qn"]) > float(row["start_qn"])
    }
    candidates.update(
        max(0.0, float(row["end_qn"]) - duration_beats)
        for row in rows
        if float(row["end_qn"]) > float(row["start_qn"])
    )
    scored: list[tuple[float, int, float, float, list[str]]] = []
    for start in sorted(candidates):
        end = start + duration_beats
        overlapping = [
            row for row in rows
            if float(row["start_qn"]) < end and float(row["end_qn"]) > start
        ]
        spans = _merge_spans([
            (max(start, float(row["start_qn"])), min(end, float(row["end_qn"])))
            for row in overlapping
        ])
        coverage = sum(right - left for left, right in spans)
        active_tracks = sorted({str(row.get("track") or "") for row in overlapping})
        scored.append((coverage, len(active_tracks), -start, end, active_tracks))
    if not scored:
        raise ActiveRegionSelectionError("NO_ACTIVE_ARRANGEMENT_REGION")
    coverage, track_count, negative_start, end, active_tracks = max(scored)
    start = -negative_start
    ratio = coverage / duration_beats
    if ratio < minimum_coverage_ratio:
        raise ActiveRegionSelectionError(
            f"INSUFFICIENT_ACTIVE_COVERAGE: ratio={ratio:.3f} required={minimum_coverage_ratio:.3f}"
        )
    return {
        "start_beat": float(start),
        "end_beat": float(end),
        "duration_beats": float(duration_beats),
        "coverage_beats": float(coverage),
        "coverage_ratio": float(ratio),
        "active_track_count": int(track_count),
        "active_tracks": active_tracks,
        "source": "persisted_arrangement_clips_and_authoritative_mixer_state",
        "project_path": str(project_path),
    }


def capture_and_analyze_master(
    daw,
    session: SessionState,
    *,
    label: str,
    start_beat: float = 0.0,
    end_beat: float = 16.0,
) -> dict[str, Any]:
    """Capture Main through the existing verified tap and run read-only DSP."""
    from copilot.audio.live_capture import capture_master_segment
    from copilot.audio.music_analyzer import analyze_audio_input
    from copilot.audio.physical_dsp_v2.pipeline import analyze_path
    from copilot.schemas.music_analysis import AudioAnalysisInput

    region = select_capturable_active_region(
        session,
        duration_beats=float(end_beat - start_beat),
    )
    asset = capture_master_segment(
        daw,
        start_beat=float(region["start_beat"]),
        end_beat=float(region["end_beat"]),
        require_signal=True,
    )
    dsp = analyze_path(asset.file_path, use_cache=False)
    capture_token = f"{session.audible_token}:{label}"
    pack = analyze_audio_input(
        AudioAnalysisInput(
            main_path=asset.file_path,
            reference_state_token=capture_token,
            target_state_token=session.audible_token,
            tempo_bpm=float(session.transport.tempo),
            project_identity=session.project_identity,
            capture_id=asset.capture_id,
        ),
        use_cache=False,
    )
    return {
        "label": label,
        "capture_id": asset.capture_id,
        "path": str(asset.file_path),
        "dsp": dsp,
        "music_analysis": pack,
        "rms": float(asset.rms or 0.0),
        "peak": float(asset.peak or 0.0),
        "duration": float(asset.duration or 0.0),
        "sample_rate": int(asset.sample_rate or 0),
        "capture_writes": 0,
        "region": region,
    }


class MasterAwareDaw:
    """Read-only session augmentation for the existing ``track_index=-1`` API.

    The bridge already exposes Main devices through ``get_master_info`` and
    ``get_device_parameters(-1, ...)``. The normal SessionState intentionally
    contains only ordinary tracks, so this proxy adds one synthetic master
    TrackState for compiler/identity purposes while delegating all writes to the
    same underlying DawAdapter used by SafeWrite.
    """

    def __init__(self, daw) -> None:
        self._daw = daw

    def __getattr__(self, name: str):
        return getattr(self._daw, name)

    def snapshot(self, *args, **kwargs) -> SessionState:
        base = self._daw.snapshot(*args, **kwargs)
        augmented = base.model_copy(update={"tracks": [*base.tracks, self.master_track(base)]})
        return attach_tokens(augmented)

    def master_track(self, session: SessionState) -> TrackState:
        info = self._daw.get_master_info() or {}
        devices: list[DeviceState] = []
        for raw in info.get("devices") or []:
            index = int(raw.get("index", len(devices)))
            name = str(raw.get("name") or "")
            params_payload = self._daw.get_device_parameters(-1, index)
            params = [
                DeviceParameter(
                    index=int(row.get("index", position)),
                    name=str(row.get("name") or ""),
                    value=float(row.get("value", 0.0) or 0.0),
                    min=float(row.get("min", 0.0) or 0.0),
                    max=_optional_parameter_max(row.get("max")),
                )
                for position, row in enumerate(params_payload.get("parameters") or [])
            ]
            devices.append(
                DeviceState(
                    stable_id=self._master_device_id(session, index, name),
                    index=index,
                    name=name,
                    class_name=str(raw.get("class_name") or ""),
                    enabled=bool(raw.get("is_active", True)),
                    parameters=params,
                )
            )
        return TrackState(
            stable_id=self._master_id(session),
            index=-1,
            name="Master",
            role="master",
            mixer=MixerState(volume=float(info.get("volume", 0.85) or 0.85), pan=float(info.get("panning", 0.0) or 0.0)),
            routing=RoutingState(output_type="Main", monitoring="NOT_APPLICABLE"),
            devices=devices,
            clips=[],
        )

    @staticmethod
    def _master_id(session: SessionState) -> str:
        return f"master:{session.project_identity}"

    @classmethod
    def _master_device_id(cls, session: SessionState, index: int, name: str) -> str:
        return f"{cls._master_id(session)}:device:{index}:{name}"


def build_master_session(daw) -> tuple[MasterAwareDaw, SessionState]:
    proxy = daw if isinstance(daw, MasterAwareDaw) else MasterAwareDaw(daw)
    session = proxy.snapshot()
    attach_tokens(session)
    return proxy, session


def _track_by_stable_or_name(session: SessionState, spec: dict[str, Any]) -> TrackState | None:
    stable_id = str(spec.get("track_stable_id") or "")
    if stable_id:
        matches = [track for track in session.tracks if track.stable_id == stable_id]
        return matches[0] if len(matches) == 1 else None
    name = str(spec.get("track_name") or "")
    return session.track_by_name(name) if name else None


def _device_by_stable_or_name(track: TrackState, spec: dict[str, Any]) -> DeviceState | None:
    stable_id = str(spec.get("device_stable_id") or "")
    if stable_id:
        matches = [device for device in track.devices if device.stable_id == stable_id]
        return matches[0] if len(matches) == 1 else None
    name = str(spec.get("device_name") or "")
    matches = [device for device in track.devices if device.name == name] if name else []
    return matches[0] if len(matches) == 1 else None


def _parameter_by_identity(device: DeviceState, spec: dict[str, Any]) -> DeviceParameter | None:
    stable_index = spec.get("parameter_index")
    name = str(spec.get("parameter_name") or "")
    if stable_index is not None:
        matches = [parameter for parameter in device.parameters if parameter.index == int(stable_index)]
    elif name:
        matches = [parameter for parameter in device.parameters if parameter.name == name]
    else:
        matches = []
    return matches[0] if len(matches) == 1 else None


def _native_value(parameter: DeviceParameter, spec: dict[str, Any]) -> float:
    if parameter.max is None:
        raise ValueError("PARAMETER_RANGE_UNAVAILABLE")
    if "native_value" in spec:
        return float(spec["native_value"])
    normalized = float(spec["normalized_value"])
    if not 0.0 <= normalized <= 1.0:
        raise ValueError("VALUE_OUT_OF_RANGE")
    return float(parameter.min) + normalized * (float(parameter.max) - float(parameter.min))


def _typed_mix_value(spec: dict[str, Any], *, daw, track: TrackState, device: DeviceState) -> tuple[DeviceParameter | None, float | None, str | None]:
    if (
        "device.physical_units_v1" not in getattr(daw, "capabilities", set())
        or getattr(daw, "handshake_info", {}).get("mode") == "LEGACY"
    ):
        return None, None, "PHYSICAL_UNIT_CAPABILITY_UNAVAILABLE"
    control = str(spec.get("control") or "")
    band = spec.get("band")
    if band is not None and (isinstance(band, bool) or not isinstance(band, int)):
        return None, None, "CONTROL_UNSUPPORTED"
    try:
        payload = daw.get_device_parameters(track.index, device.index)
    except Exception:
        return None, None, "PARAMETER_INTROSPECTION_UNAVAILABLE"
    if not isinstance(payload, dict) or payload.get("device_class") != device.class_name or payload.get("device_name") != device.name:
        return None, None, "DEVICE_IDENTITY_UNCERTIFIED"
    if payload.get("track_index") != track.index or payload.get("device_index") != device.index:
        return None, None, "DEVICE_IDENTITY_UNCERTIFIED"
    rows = payload.get("parameters")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None, None, "PARAMETER_INTROSPECTION_UNAVAILABLE"
    certified, reason = introspect_mix_parameter(device.class_name, rows, control=control, band=band)
    if certified is None:
        return None, None, reason
    matches = [p for p in device.parameters if p.index == certified.index and p.name == certified.name]
    if len(matches) != 1:
        return None, None, "PARAMETER_SNAPSHOT_MISMATCH"
    parameter = matches[0]
    if parameter.max is None:
        return None, None, "PARAMETER_RANGE_UNAVAILABLE"
    if (
        device.index < 0 or device.index >= len(track.devices)
        or track.devices[device.index] is not device
        or certified.index >= len(device.parameters)
        or device.parameters[certified.index] is not parameter
    ):
        return None, None, "PARAMETER_INDEX_UNCERTIFIED"
    if any(abs(a - b) > 1e-6 for a, b in (
        (parameter.value, certified.value), (parameter.min, certified.minimum), (parameter.max, certified.maximum),
    )):
        return None, None, "PARAMETER_SNAPSHOT_MISMATCH"
    if certified.unit == "boolean":
        if not isinstance(spec.get("value"), bool) or spec.get("unit") not in (None, "boolean"):
            return None, None, "VALUE_UNIT_UNCERTIFIED"
        intended = float(spec["value"])
    else:
        if str(spec.get("unit") or "").lower() != certified.unit:
            return None, None, "VALUE_UNIT_UNCERTIFIED"
        value = spec.get("value")
        if isinstance(value, bool):
            return None, None, "VALUE_OUT_OF_RANGE"
        try:
            intended = float(value)
        except (TypeError, ValueError):
            return None, None, "VALUE_OUT_OF_RANGE"
    if not isfinite(intended) or not certified.minimum <= intended <= certified.maximum:
        return None, None, "VALUE_OUT_OF_RANGE"
    return parameter, intended, None


def _action_from_spec(spec: dict[str, Any], *, phase: str, session: SessionState, daw=None) -> tuple[Any | None, str | None]:
    operation = str(spec.get("operation") or "set_device_parameter")
    if operation == "set_track_volume":
        track = _track_by_stable_or_name(session, spec)
        if track is None or track.role == "master":
            return None, "TARGET_NOT_FOUND_OR_AMBIGUOUS"
        if "target_value" in spec:
            target = float(spec["target_value"])
        else:
            target = float(track.mixer.volume) + float(spec.get("delta", 0.0))
        if not 0.0 <= target <= 1.0:
            return None, "VALUE_OUT_OF_RANGE"
        return build_set_track_volume_action(
            track=track,
            project_identity=session.project_identity,
            operation=VolumeOperation.SET,
            expected_before=float(track.mixer.volume),
            target_value=target,
            reason=str(spec.get("reason") or f"Lucas {phase} balance strategy"),
            evidence_refs=list(spec.get("evidence_refs") or []),
            session_incarnation_id=session.session_incarnation_id,
        ), None

    if operation == "load_device":
        track = session.track_by_name("Master") if phase == "master" and not spec.get("track_name") else _track_by_stable_or_name(session, spec)
        device_name = str(spec.get("device_name") or "")
        if track is None or not device_name:
            return None, "TARGET_NOT_FOUND_OR_AMBIGUOUS"
        if any(device.name == device_name for device in track.devices):
            return None, "DEVICE_ALREADY_PRESENT"
        uri = str(spec.get("device_uri") or device_name)
        return build_device_load_action(
            track=track,
            project_identity=session.project_identity,
            device_name=device_name,
            device_uri=uri,
            reason=str(spec.get("reason") or f"Lucas {phase} device strategy"),
            evidence_refs=list(spec.get("evidence_refs") or []),
            session_incarnation_id=session.session_incarnation_id,
        ), None

    if operation in {"set_device_parameter", "parameter_patch", "set_typed_parameter"}:
        track = session.track_by_name("Master") if phase == "master" and not spec.get("track_name") else _track_by_stable_or_name(session, spec)
        if track is None:
            return None, "TARGET_NOT_FOUND_OR_AMBIGUOUS"
        device = _device_by_stable_or_name(track, spec)
        if device is None:
            return None, "DEVICE_NOT_FOUND_OR_AMBIGUOUS"
        if operation == "set_typed_parameter":
            if daw is None:
                return None, "PARAMETER_INTROSPECTION_UNAVAILABLE"
            parameter, intended, reason = _typed_mix_value(spec, daw=daw, track=track, device=device)
            if reason:
                return None, reason
        else:
            if device.class_name.lower() in {"eq8", "eq eight", "limiter"} and str(spec.get("unit") or "").lower() in {"hz", "db", "q", "boolean"}:
                return None, "PHYSICAL_UNIT_UNCERTIFIED"
            parameter = _parameter_by_identity(device, spec)
            if parameter is None:
                return None, "PARAMETER_NOT_FOUND_OR_AMBIGUOUS"
            try:
                intended = _native_value(parameter, spec)
            except (KeyError, TypeError, ValueError) as exc:
                return None, str(exc)
        if not isfinite(intended):
            return None, "VALUE_OUT_OF_RANGE"
        span = float(parameter.max) - float(parameter.min)
        max_delta = 1.0 if operation == "set_typed_parameter" and str(spec.get("control") or "") == "enabled" else float(spec.get("max_delta_norm", 0.35))
        if span > 0 and abs(intended - float(parameter.value)) / span > max_delta:
            return None, "MAX_DELTA_EXCEEDED"
        return build_device_tweak_action(
            track=track,
            project_identity=session.project_identity,
            device_index=device.index,
            parameter_name=parameter.name,
            expected_before=float(parameter.value),
            intended_after=intended,
            unit=("native" if operation == "set_typed_parameter" else str(spec.get("unit") or "native")),
            reason=str(spec.get("reason") or f"Lucas {phase} device strategy"),
            evidence_refs=list(spec.get("evidence_refs") or []),
            session_incarnation_id=session.session_incarnation_id,
            allowed_min=float(parameter.min),
            allowed_max=float(parameter.max),
        ), None

    return None, "EXECUTION_DEFERRED_UNSUPPORTED_OPERATION"


def _phase_specs(strategy: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    section = strategy.get(phase) or {}
    if isinstance(section, list):
        return list(section)
    return [
        *list(section.get("volume_actions") or []),
        *list(section.get("device_loads") or []),
        *list(section.get("parameter_actions") or []),
        *list(section.get("parameter_writes") or []),
    ]


def _phase_plan(actions: list, session: SessionState, phase: str) -> MusicPlan:
    return MusicPlan(
        plan_id=f"lucas_{phase}_iteration",
        status=PlanStatus.DRAFT,
        intent_class=PlanIntentClass.AUTONOMOUS_MUSICAL_IMPROVEMENT,
        diagnosis=DiagnosisBinding(
            diagnosis_id=f"lucas_{phase}_strategy",
            diagnosis_status="SUPPORTED",
            diagnosis_accepted=True,
            cause_status="CAUSE_SUPPORTED",
        ),
        project_state_token=session.project_token,
        audible_state_token=session.audible_token,
        created_at=now_iso(),
        actions=actions,
    )


def execute_lucas_mix_master_iteration(
    *,
    strategy: dict[str, Any],
    daw,
    session: SessionState,
    persist_dir: Path,
    post_apply: Callable[[str, MusicPlan, SessionState, SessionState], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run exactly one bounded mix and one bounded master iteration.

    ``post_apply`` is the read-only recapture/analysis/critique hook. Its
    returned decision is KEEP, ADJUST, or ROLLBACK. ADJUST is terminal for this
    milestone; this function never starts an optimizer loop.
    """
    proxy, initial = build_master_session(daw)
    expected_project = str(strategy.get("project_token") or "")
    known_project_tokens = {str(session.project_token or ""), str(initial.project_token or "")}
    if expected_project and expected_project not in known_project_tokens:
        return {"status": "STALE_PLAN", "reason": "PROJECT_TOKEN_MISMATCH", "phases": {}}
    phase_reports: dict[str, Any] = {}
    for phase in ("mix", "master"):
        pre = proxy.snapshot()
        attach_tokens(pre)
        specs = _phase_specs(strategy, phase)
        deferred: list[dict[str, Any]] = []
        actions: list[Any] = []
        executor = build_safe_write_executor(
            proxy,
            journal_path=persist_dir / f"{phase}_safe_write.jsonl",
            persist_dir=persist_dir,
        )
        applied: list[tuple[Any, Any]] = []
        writes: list[dict[str, Any]] = []
        failure: str | None = None
        current = pre
        for index, action_spec in enumerate(specs):
            current = proxy.snapshot()
            attach_tokens(current)
            action, reason = _action_from_spec(action_spec, phase=phase, session=current, daw=proxy)
            if action is None:
                deferred.append({"index": index, "status": "EXECUTION_DEFERRED", "reason": reason})
                continue
            refreshed = action
            one = _single_action_plan(refreshed, session=current, plan_id=f"lucas_{phase}_{refreshed.action_id}")
            compiled = ProductionCompiler().compile(one, session=current)
            if compiled.status != "COMPILED" or compiled.intent is None:
                failure = "; ".join(compiled.reasons) or compiled.status
                break
            result = executor.run(compiled.intent)
            if not result.ok:
                failure = result.error or "SAFE_WRITE_FAILED"
                break
            applied.append((result, compiled.intent))
            if action_spec.get("operation") == "set_typed_parameter":
                observed = proxy.snapshot()
                observed_track = (
                    observed.track_by_name("Master")
                    if phase == "master" and not action_spec.get("track_name")
                    else _track_by_stable_or_name(observed, action_spec)
                )
                observed_device = (
                    _device_by_stable_or_name(observed_track, action_spec)
                    if observed_track is not None else None
                )
                if observed_device is None:
                    failure = "POST_WRITE_DEVICE_IDENTITY_UNCERTIFIED"
                    break
                physical, target, reason = _typed_mix_value(
                    action_spec, daw=proxy, track=observed_track, device=observed_device,
                )
                if reason or physical is None or abs(physical.value - target) > 0.001:
                    failure = reason or "POST_WRITE_PHYSICAL_READBACK_MISMATCH"
                    break
            writes.append({
                "action_type": refreshed.action_type.value,
                "action_id": refreshed.action_id,
                "readbacks": [row.model_dump(mode="json") for row in result.readbacks],
            })
            actions.append(refreshed)
        post = proxy.snapshot()
        attach_tokens(post)
        callback = {"decision": "ROLLBACK", "reason": "no_post_apply_callback"}
        if failure is None and post_apply is not None:
            try:
                callback = dict(post_apply(phase, _phase_plan(actions, pre, phase), pre, post) or {})
            except Exception as exc:  # fail closed and roll back every applied action
                callback = {
                    "decision": "ROLLBACK",
                    "reason": "POST_APPLY_FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        decision = str(callback.get("decision") or "ROLLBACK").upper()
        rollback_error = ""
        if failure is not None or decision != "KEEP":
            for result, intent in reversed(applied):
                rollback_error = executor._rollback_applied(result, intent)
                if rollback_error:
                    break
        phase_reports[phase] = {
            "status": "SAFE_WRITE_COMPLETE" if failure is None and not rollback_error else "FAILED",
            "planned": len(specs),
            "compiled": len(actions) if failure is None else len(writes),
            "writes_verified": len(writes),
            "writes": writes,
            "deferred": deferred,
            "post_apply": callback,
            "decision": decision,
            "rollback_verified": (failure is not None or decision != "KEEP") and not rollback_error,
            "rollback_error": rollback_error,
            "error": failure,
        }
        if failure is not None or rollback_error:
            return {"status": "FAILED", "phases": phase_reports, "direct_lucas_writes": 0, "write_authority": "SafeWriteExecutor"}
    return {"status": "MIX_MASTER_ITERATION_COMPLETE", "phases": phase_reports, "direct_lucas_writes": 0, "write_authority": "SafeWriteExecutor"}
