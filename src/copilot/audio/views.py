from __future__ import annotations

import time

from copilot.audio.live_capture import (
    MASTER_INDEX,
    AudioAsset,
    AudioCaptureError,
    CaptureContext,
    assert_master_tap_final,
    capture_audio_segment,
    count_copilot_taps,
    ensure_master_tap,
    find_tap,
    set_tap_enabled,
    tap_from_info,
)
from copilot.audio.semantics import classify_signal_point
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.schemas.observation import CaptureView, ObservationSource, SignalPoint

CAPTURE_TRACK_NAME = "Copilot Capture"
PREFERRED_POINTS = ("Post Mixer", "Post FX", "Pre FX")


def ensure_capture_track(
    daw: AbletonTcpAdapter,
    context: CaptureContext | None = None,
) -> dict[str, object]:
    if context is not None and context.capture_track_index is not None:
        return {"created": False, "track_index": context.capture_track_index}
    session = daw.snapshot(include_notes=False)
    existing = session.track_by_name(CAPTURE_TRACK_NAME)
    created = False
    if existing is None:
        created_info = daw.create_audio_track(CAPTURE_TRACK_NAME, -1)
        track_index = int(created_info["index"])
        created = True
    else:
        track_index = existing.index
    try:
        daw.set_track_monitoring(track_index, "in")
    except DawError:
        pass
    return {"created": created, "track_index": track_index}


def find_off_main_tap(
    daw: AbletonTcpAdapter,
    context: CaptureContext | None = None,
) -> dict[str, object] | None:
    """Audio-track Copilot tap that is not on Main. MIDI hosts cannot Audio From."""
    if context is not None:
        return context.off_main_tap
    inventory = count_copilot_taps(daw)
    for item in inventory.get("tracks") or []:
        if int(item.get("count") or 0) <= 0:
            continue
        info = daw.get_track_info(int(item["index"]))
        if info.get("is_midi_track") and not info.get("is_audio_track"):
            continue
        return {"index": int(item["index"]), "name": item.get("name")}
    return None


def _pick_input_type(available: list[str], target_name: str) -> str | None:
    wanted = target_name.lower()
    for name in available:
        if str(name).lower() == wanted:
            return str(name)
    for name in available:
        if wanted in str(name).lower():
            return str(name)
    return None


def _pick_channel(available: list[str], preferred: str = "Post Mixer") -> str | None:
    pref = preferred.lower()
    for name in available:
        if str(name).lower() == pref:
            return str(name)
    for name in available:
        label = str(name).lower().replace("-", " ")
        if pref in label:
            return str(name)
    for fallback in PREFERRED_POINTS:
        for name in available:
            if fallback.lower() in str(name).lower().replace("-", " "):
                return str(name)
    return available[0] if available else None


def _pick_output(available: list[str], *wanted: str) -> str | None:
    lowered = [str(name) for name in available]
    for needle in wanted:
        for name in lowered:
            if needle.lower() == name.lower():
                return name
    for needle in wanted:
        for name in lowered:
            if needle.lower() in name.lower():
                return name
    return None


def route_capture_from_track(
    daw: AbletonTcpAdapter,
    capture_index: int,
    target_name: str,
    preferred_point: str = "Post Mixer",
) -> dict[str, object]:
    before = daw.get_available_inputs(capture_index)
    input_type = _pick_input_type(list(before.get("available_inputs") or []), target_name)
    if input_type is None:
        raise AudioCaptureError(
            "CAPTURE_ROUTING_UNSUPPORTED",
            f"no Audio From type matching {target_name!r}; have {before.get('available_inputs')}",
        )
    daw.set_track_input_routing(capture_index, input_type, "")
    after_type = daw.get_available_inputs(capture_index)
    channels = list(after_type.get("available_input_channels") or [])
    channel = _pick_channel(channels, preferred_point)
    if channel:
        daw.set_track_input_routing(capture_index, input_type, channel)
    routed = daw.get_track_input_routing(capture_index)
    current_type = str(routed.get("input_routing_type") or "")
    current_channel = str(routed.get("input_routing_channel") or "")
    point = classify_signal_point(current_channel)
    if point is SignalPoint.UNKNOWN and not current_channel:
        point = SignalPoint.TRACK_POST_MIXER
        current_channel = current_channel or "Post Mixer (Live default after type)"
    return {
        "input_type": current_type,
        "input_channel": current_channel,
        "signal_point": point,
        "available_channels": channels,
        "type_requested": input_type,
        "channel_requested": channel,
    }


def _snapshot_outputs(
    daw: AbletonTcpAdapter,
    context: CaptureContext | None = None,
) -> list[dict[str, object]]:
    if context is not None and context.outputs:
        return list(context.outputs)
    count = int(daw.health().get("track_count") or 0)
    states: list[dict[str, object]] = []
    for index in range(count):
        info = daw.get_track_info(index)
        routed = daw.get_track_output_routing(index)
        states.append(
            {
                "index": index,
                "name": info.get("name"),
                "type": routed.get("output_routing_type") or info.get("output_routing_type"),
                "channel": routed.get("output_routing_channel")
                or info.get("output_routing_channel"),
            }
        )
    return states


def _restore_outputs(
    daw: AbletonTcpAdapter, states: list[dict[str, object]]
) -> list[str]:
    errors: list[str] = []
    for state in states:
        index = int(state["index"])
        prev_type = str(state.get("type") or "")
        if not prev_type:
            continue
        try:
            check = daw.set_track_output_routing(
                index, prev_type, str(state.get("channel") or "")
            )
            if str(check.get("output_routing_type") or "") != prev_type:
                errors.append(f"output unrestored track {index}")
        except DawError:
            errors.append(f"output restore failed track {index}")
    return errors


def capture_master_context(
    daw: AbletonTcpAdapter,
    start_beat: float,
    end_beat: float,
    *,
    fire_track: int | None = None,
    fire_tracks: list[int] | None = None,
    fire_all: bool = True,
    require_signal: bool = True,
    preroll_beats: float = 0.5,
    require_tap_final: bool = True,
    context: CaptureContext | None = None,
    known_tempo: float | None = None,
) -> AudioAsset:
    if context is None or context.tap_device is None:
        ensure_master_tap(daw)
    tap_pos = None
    if require_tap_final:
        tap_pos = assert_master_tap_final(daw)
    point = SignalPoint.MAIN_FINAL
    label = "Main final (tap last)"
    if tap_pos is None:
        from copilot.audio.live_capture import master_tap_position

        if context is not None and context.master_info:
            pos = {
                "tap": tap_from_info(context.master_info),
                "devices": [
                    str(d.get("name")) for d in (context.master_info.get("devices") or [])
                ],
            }
            devices = list(context.master_info.get("devices") or [])
            tap = pos["tap"]
            tap_index = int(tap["index"]) if tap is not None else None
            pos["is_last"] = tap_index is not None and tap_index == len(devices) - 1
            pos["devices"] = [str(d.get("name")) for d in devices]
        else:
            pos = master_tap_position(daw)
        if pos["is_last"]:
            point = SignalPoint.MAIN_FINAL
        else:
            point = SignalPoint.MAIN_NOT_FINAL
            label = f"Main not final; devices={pos['devices']}"
    return capture_audio_segment(
        daw,
        "MASTER",
        start_beat,
        end_beat,
        require_signal=require_signal,
        fire_track=fire_track,
        fire_tracks=fire_tracks,
        fire_all=fire_all,
        isolate_solo=False,
        capture_view=CaptureView.MASTER_CONTEXT,
        observation_source=ObservationSource.MASTER_CONTEXT,
        signal_point=point,
        signal_point_label=label,
        preroll_beats=preroll_beats,
        context=context,
        known_tempo=known_tempo,
    )


def capture_track_isolated(
    daw: AbletonTcpAdapter,
    stable_id: str,
    start_beat: float,
    end_beat: float,
    *,
    require_signal: bool = True,
    preroll_beats: float = 0.5,
    preferred_point: str = "Post Mixer",
    fire_tracks: list[int] | None = None,
    context: CaptureContext | None = None,
    known_tempo: float | None = None,
) -> AudioAsset:
    """Isolate at track Post Mixer.

    Preferred path: record a Copilot tap that is NOT on Main, host output
    Sends Only. That is TRACK_POST_MIXER.

    Fallback: route a capture track into Main and record the Master tap.
    That is TRACK_POST_MIXER_THROUGH_MASTER_CHAIN — not a clean stem.
    """
    if context is None or context.tap_device is None:
        ensure_master_tap(daw)
    session = context.session if context is not None else daw.snapshot(include_notes=False)
    try:
        target = session.track_by_id(stable_id)
    except KeyError as exc:
        raise AudioCaptureError("TARGET_AMBIGUOUS", f"unknown {stable_id}") from exc

    off_main = find_off_main_tap(daw, context)
    if off_main is not None:
        return _isolate_off_main(
            daw,
            target,
            stable_id,
            start_beat,
            end_beat,
            host_index=int(off_main["index"]),
            require_signal=require_signal,
            preroll_beats=preroll_beats,
            preferred_point=preferred_point,
            fire_tracks=fire_tracks,
            context=context,
            known_tempo=known_tempo,
        )
    return _isolate_through_master(
        daw,
        target,
        stable_id,
        start_beat,
        end_beat,
        require_signal=require_signal,
        preroll_beats=preroll_beats,
        preferred_point=preferred_point,
        fire_tracks=fire_tracks,
        context=context,
        known_tempo=known_tempo,
    )


def _isolate_off_main(
    daw: AbletonTcpAdapter,
    target,
    stable_id: str,
    start_beat: float,
    end_beat: float,
    *,
    host_index: int,
    require_signal: bool,
    preroll_beats: float,
    preferred_point: str,
    fire_tracks: list[int] | None = None,
    context: CaptureContext | None = None,
    known_tempo: float | None = None,
) -> AudioAsset:
    routing_before = daw.get_track_input_routing(host_index)
    output_before = daw.get_track_output_routing(host_index)
    monitor_before = None
    try:
        monitor_before = daw.get_track_monitoring(host_index)
    except DawError:
        pass
    master_was_on = find_tap(daw, MASTER_INDEX) is not None
    try:
        routed = route_capture_from_track(
            daw, host_index, target.name, preferred_point
        )
        try:
            daw.set_track_monitoring(host_index, "in")
        except DawError:
            pass
        sends_only = _pick_output(
            list(daw.get_available_outputs(host_index).get("available_outputs") or []),
            "Sends Only",
            "No Output",
        )
        if not sends_only:
            raise AudioCaptureError(
                "CAPTURE_ROUTING_UNSUPPORTED",
                "isolated tap host cannot leave Main (no Sends Only / No Output)",
            )
        daw.set_track_output_routing(host_index, sends_only, "")
        if master_was_on:
            set_tap_enabled(daw, MASTER_INDEX, False)
        asset = capture_audio_segment(
            daw,
            stable_id,
            start_beat,
            end_beat,
            require_signal=require_signal,
            tap_track_index=host_index,
            isolate_solo=False,
            fire_all=fire_tracks is None,
            fire_tracks=fire_tracks,
            capture_view=CaptureView.TRACK_ISOLATED,
            observation_source=ObservationSource.TRACK_ISOLATED,
            signal_point=SignalPoint.TRACK_POST_MIXER,
            signal_point_label=str(routed["input_channel"] or routed["input_type"]),
            preroll_beats=preroll_beats,
            context=context,
            known_tempo=known_tempo,
        )
        return asset
    finally:
        if master_was_on:
            try:
                set_tap_enabled(daw, MASTER_INDEX, True)
            except DawError:
                pass
        try:
            prev_type = str(routing_before.get("input_routing_type") or "")
            if prev_type:
                daw.set_track_input_routing(
                    host_index,
                    prev_type,
                    str(routing_before.get("input_routing_channel") or ""),
                )
        except DawError:
            pass
        try:
            out_type = str(output_before.get("output_routing_type") or "")
            if out_type:
                daw.set_track_output_routing(
                    host_index,
                    out_type,
                    str(output_before.get("output_routing_channel") or ""),
                )
        except DawError:
            pass
        if monitor_before and monitor_before.get("monitoring"):
            try:
                daw.set_track_monitoring(host_index, str(monitor_before["monitoring"]))
            except DawError:
                pass


def _isolate_through_master(
    daw: AbletonTcpAdapter,
    target,
    stable_id: str,
    start_beat: float,
    end_beat: float,
    *,
    require_signal: bool,
    preroll_beats: float,
    preferred_point: str,
    fire_tracks: list[int] | None = None,
    context: CaptureContext | None = None,
    known_tempo: float | None = None,
) -> AudioAsset:
    capture = ensure_capture_track(daw, context)
    if context is not None and capture.get("created"):
        context.capture_track_index = int(capture["track_index"])
    capture_index = int(capture["track_index"])
    routing_before = daw.get_track_input_routing(capture_index)
    t_view = time.perf_counter()
    outputs_before = _snapshot_outputs(daw, context)
    snapshot_s = time.perf_counter() - t_view
    if context is not None and context.return_volumes:
        return_volumes = list(context.return_volumes)
    else:
        returns = daw.get_return_tracks()
        return_volumes = [
            (int(item["index"]), float(item.get("volume") or 0.0))
            for item in returns.get("return_tracks") or []
        ]
    isolated: AudioAsset | None = None
    mutated_outputs: list[dict[str, object]] = []
    try:
        routed = route_capture_from_track(
            daw, capture_index, target.name, preferred_point
        )
        try:
            daw.set_track_monitoring(capture_index, "in")
        except DawError:
            pass
        main = _pick_output(
            list(daw.get_available_outputs(capture_index).get("available_outputs") or []),
            "Main",
            "Master",
        )
        if not main:
            raise AudioCaptureError(
                "CAPTURE_ROUTING_UNSUPPORTED",
                "Copilot Capture cannot route to Main",
            )
        daw.set_track_output_routing(capture_index, main, "")
        capture_out = next(
            (s for s in outputs_before if int(s["index"]) == capture_index),
            None,
        )
        if capture_out is not None:
            mutated_outputs.append(capture_out)
        off_name = context.sends_only_name if context is not None else None
        for state in outputs_before:
            index = int(state["index"])
            if index == capture_index:
                continue
            current_type = str(state.get("type") or "")
            if current_type in {"Sends Only", "No Output"}:
                continue
            if off_name is None:
                outs = list(
                    daw.get_available_outputs(index).get("available_outputs") or []
                )
                off_name = _pick_output(outs, "Sends Only", "No Output")
                if context is not None:
                    context.sends_only_name = off_name
            if not off_name:
                continue
            check = daw.set_track_output_routing(index, off_name, "")
            if not check.get("type_matched", True):
                outs = list(
                    daw.get_available_outputs(index).get("available_outputs") or []
                )
                off = _pick_output(outs, "Sends Only", "No Output")
                if off:
                    daw.set_track_output_routing(index, off, "")
                    mutated_outputs.append(state)
                continue
            mutated_outputs.append(state)
        for ret_index, _vol in return_volumes:
            daw.set_return_volume(ret_index, 0.0)
        route_s = time.perf_counter() - t_view - snapshot_s
        asset = capture_audio_segment(
            daw,
            stable_id,
            start_beat,
            end_beat,
            require_signal=require_signal,
            isolate_solo=False,
            fire_all=fire_tracks is None,
            fire_tracks=fire_tracks,
            capture_view=CaptureView.TRACK_ISOLATED,
            observation_source=ObservationSource.TRACK_ISOLATED,
            signal_point=SignalPoint.TRACK_POST_MIXER_THROUGH_MASTER_CHAIN,
            signal_point_label=(
                f"{routed['input_channel'] or routed['input_type']} then Main chain"
            ),
            preroll_beats=preroll_beats,
            context=context,
            known_tempo=known_tempo,
        )
        isolated = asset
        isolated.stage_timings["view_transition_snapshot_outputs"] = snapshot_s
        isolated.stage_timings["view_transition_route_off"] = route_s
        return isolated
    finally:
        t_restore = time.perf_counter()
        restore_errors = _restore_outputs(
            daw,
            mutated_outputs
            if isolated is not None or mutated_outputs
            else outputs_before,
        )
        for ret_index, volume in return_volumes:
            try:
                daw.set_return_volume(ret_index, volume)
            except DawError:
                restore_errors.append(f"return {ret_index} volume unrestored")
        try:
            prev_type = str(routing_before.get("input_routing_type") or "")
            if prev_type:
                daw.set_track_input_routing(
                    capture_index,
                    prev_type,
                    str(routing_before.get("input_routing_channel") or ""),
                )
        except DawError:
            restore_errors.append("capture input unrestored")
        if isolated is not None:
            isolated.stage_timings["view_transition_restore_outputs"] = (
                time.perf_counter() - t_restore
            )
        if restore_errors:
            raise AudioCaptureError(
                "REMOTE_DISCONNECT",
                "isolated routing not restored: " + "; ".join(restore_errors),
            )


def capture_track_in_mix_context(
    daw: AbletonTcpAdapter,
    stable_id: str,
    start_beat: float,
    end_beat: float,
    *,
    preroll_beats: float = 0.5,
    require_tap_final: bool = True,
    fire_tracks: list[int] | None = None,
    full_mix: AudioAsset | None = None,
    context: CaptureContext | None = None,
    known_tempo: float | None = None,
) -> dict[str, AudioAsset | str]:
    session = context.session if context is not None else daw.snapshot(include_notes=False)
    try:
        target = session.track_by_id(stable_id)
    except KeyError as exc:
        raise AudioCaptureError("TARGET_AMBIGUOUS", f"unknown {stable_id}") from exc
    full = full_mix or capture_master_context(
        daw,
        start_beat,
        end_beat,
        preroll_beats=preroll_beats,
        require_tap_final=require_tap_final,
        fire_tracks=fire_tracks,
        fire_all=fire_tracks is None,
        context=context,
        known_tempo=known_tempo,
    )
    muted_result = daw.set_track_mute(target.index, True)
    muted = bool(muted_result.get("mute"))
    if not muted:
        raise AudioCaptureError(
            "REMOTE_DISCONNECT", "failed to mute target for mix context"
        )
    try:
        without = capture_master_context(
            daw,
            start_beat,
            end_beat,
            preroll_beats=preroll_beats,
            require_signal=False,
            require_tap_final=require_tap_final,
            fire_tracks=fire_tracks,
            fire_all=fire_tracks is None,
            context=context,
            known_tempo=known_tempo,
        )
    finally:
        unmuted = daw.set_track_mute(target.index, False)
        if bool(unmuted.get("mute")):
            raise AudioCaptureError("REMOTE_DISCONNECT", "failed to unmute target")
    for asset in (full, without):
        asset.capture_view = CaptureView.TRACK_CONTEXT_REMOVAL
        asset.observation_source = ObservationSource.TRACK_CONTEXT_REMOVAL
    return {
        "full_mix": full,
        "full_mix_target_muted": without,
        "note": (
            "TRACK_CONTEXT_REMOVAL on MAIN_FINAL. Not Master-minus-track. "
            "Nonlinear Master processing can change when the target leaves."
        ),
    }
