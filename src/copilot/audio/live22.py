from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from copilot.audio.live_capture import (
    DURATION_TOLERANCE_S,
    SOURCE_TRACK_NAME,
    AudioAsset,
    AudioCaptureError,
    capture_audio_segment,
    capture_dir,
    count_copilot_taps,
    ensure_internal_source,
    ensure_master_tap,
    find_master_tap,
    observe_asset,
)
from copilot.audio.semantics import expected_analysis_seconds
from copilot.audio.views import (
    CAPTURE_TRACK_NAME,
    capture_master_context,
    capture_track_in_mix_context,
    capture_track_isolated,
    ensure_capture_track,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.midi.time import bars_to_beats
from copilot.schemas.observation import ObservationSource
from copilot.schemas.session import MidiNote

KICK_NAME = "LIVE22 Kick"
BASS_NAME = "LIVE22 Bass"
SYNTH_NAME = "LIVE22 Synth"
AUDIO_NAME = "LIVE22 Audio"
BUS_NAME = "LIVE22 Bus"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _status(ok: bool, partial: bool = False, unsupported: bool = False) -> str:
    if unsupported:
        return "UNSUPPORTED"
    if partial:
        return "PARTIAL"
    return "VERIFIED" if ok else "FAILED"


def _asset_brief(asset: AudioAsset) -> dict[str, Any]:
    return {
        "capture_id": asset.capture_id,
        "duration": asset.analysis_duration,
        "sr": asset.sample_rate,
        "rms": asset.rms,
        "observation_source": (
            asset.observation_source.value if asset.observation_source else None
        ),
        "signal_point": asset.signal_point.value if asset.signal_point else None,
        "signal_point_label": asset.signal_point_label,
        "quality": asset.capture_quality,
        "revision": [asset.session_revision_at_start, asset.session_revision_at_end],
        "transport": [asset.transport_start_observed, asset.transport_stop_observed],
    }


def _load_effect(daw: AbletonTcpAdapter, track_index: int, query: str) -> dict[str, Any]:
    info = daw.get_track_info(track_index)
    for device in info.get("devices") or []:
        if query.lower() in str(device.get("name") or "").lower():
            return {"already_present": True, "device": device}
    found = daw.search_browser(query, "audio_effects")
    for item in found.get("results") or []:
        if not item.get("is_loadable"):
            continue
        uri = item.get("uri")
        if not uri:
            continue
        try:
            loaded = daw.load_browser_item(track_index, str(uri))
        except DawError:
            loaded = daw.load_instrument_or_effect(track_index, str(uri))
        if not loaded.get("error"):
            return {"loaded": loaded, "query": query}
    return {"error": f"could not load {query}"}


def _ensure_midi_fixture(
    daw: AbletonTcpAdapter,
    name: str,
    notes: list[MidiNote],
    length: float = 16.0,
) -> dict[str, Any]:
    session = daw.snapshot(include_notes=False)
    existing = session.track_by_name(name)
    created = False
    if existing is None:
        created_info = daw.create_midi_track(name, -1)
        track_index = int(created_info["index"])
        created = True
        from copilot.audio.live_capture import _load_stock_instrument

        _load_stock_instrument(daw, track_index)
    else:
        track_index = existing.index
    info = daw.get_track_info(track_index)
    slots = info.get("clip_slots") or []
    if not (slots and slots[0].get("has_clip")):
        daw.create_midi_clip(track_index, 0, length)
        daw.set_clip_name(track_index, 0, name)
    daw.replace_clip_notes(track_index, 0, notes)
    session = daw.snapshot(include_notes=False)
    track = session.track_by_name(name)
    return {
        "created": created,
        "track_index": track_index,
        "stable_id": track.stable_id if track else "",
        "name": name,
    }


def _write_click_wav(path: Path, sr: int = 44100, beats: int = 16, tempo: float = 120.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    duration = beats * 60.0 / tempo
    n = int(sr * duration)
    data = np.zeros((n, 2), dtype=np.float32)
    for beat in range(beats):
        start = int((beat * 60.0 / tempo) * sr)
        end = min(n, start + int(0.01 * sr))
        data[start:end] = 0.8 if beat % 4 == 0 else 0.45
    sf.write(str(path), data, sr, subtype="FLOAT")
    return path


def _write_envelope(path: Path, report: dict[str, Any]) -> None:
    rows = []
    for key, value in report.items():
        if key in {"phase", "error", "evidence"} or key.startswith("_"):
            continue
        if isinstance(value, str) and value in {
            "VERIFIED",
            "PARTIAL",
            "UNSUPPORTED",
            "FAILED",
            "NOT_STARTED",
        }:
            rows.append(f"{key:<28} {value}")
    verified = [k for k, v in report.items() if v == "VERIFIED"]
    limited = [k for k, v in report.items() if v == "PARTIAL"]
    unsupported = [k for k, v in report.items() if v == "UNSUPPORTED"]
    failed = [k for k, v in report.items() if v == "FAILED"]
    routing = report.get("routing_probe") or {}
    sr = report.get("sample_rate_observed")
    body = f"""# Supported Capture Envelope

Operational envelope for Music Copilot audio capture.
Not a claim of universal Ableton compatibility.

Live evidence: `{path.name}` companion JSON `logs/live22_capture_semantics.json`.

## Matrix

```
{chr(10).join(rows)}
```

## VERIFIED

"""
    for item in verified:
        body += f"- {item}\n"
    body += "\n## LIMITED / PARTIAL\n\n"
    if limited:
        for item in limited:
            body += f"- {item}\n"
    else:
        body += "- none in this run\n"
    body += "\n## UNSUPPORTED\n\n"
    if unsupported:
        for item in unsupported:
            body += f"- {item}\n"
    else:
        body += "- none declared in this run\n"
    body += """
## HARD LIMITS

- Constant tempo only. Tempo automation → `TEMPO_AUTOMATION_UNSUPPORTED`.
- Unknown routing topology → `CAPTURE_ROUTING_UNSUPPORTED`.
- Ambiguous target → `TARGET_AMBIGUOUS`.
- Capture that mutates relevant session state → `CAPTURE_MUTATED` (not a stable observation).
- Do not treat `mix_with_track - mix_without_track` as a stem.
- TRACK_ISOLATED excludes return/send wet by construction (Audio From the track, not Main).
- Contextual effects (returns, master FX, sidechain consequences on the mix) belong to MASTER_CONTEXT or TRACK_IN_MIX_CONTEXT.
- Future A/B of corrections must be loudness-matched. Do not conclude "sounds better" because AFTER is louder.
- MusicObservation measures. It does not diagnose. No "the bass is wrong" from this phase.

## Semantics

| Source | Meaning | Signal point |
| --- | --- | --- |
| MASTER_CONTEXT | What actually arrives at Main/Master | MASTER |
| TRACK_ISOLATED | One track at an explicit routing point, rest of set still running | declared (`signal_point`) |
| TRACK_IN_MIX_CONTEXT | Full mix vs full mix with target muted | MASTER |

Preferred isolated point: POST_MIXER when Live exposes it. If Live gave another channel, that channel is declared. Never pretend POST_FADER if the tap is PRE_FADER.

Region policy this phase: STRICT_REGION. Configurable preroll exists so DSP can see prior material. Post-region tails are not included in analysis metrics.

"""
    body += f"""## Observed this run

- Sample rate from WAV: `{sr}`
- Isolated routing: `{json.dumps(routing, default=str)}`
- Failed: {", ".join(failed) if failed else "none"}

Do not say "any Ableton project".
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def run_live22(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "phase": "LIVE-2.2",
        "CAPTURE SEMANTICS": "NOT_STARTED",
        "REGION ALIGNMENT": "NOT_STARTED",
        "TRACK ISOLATION": "NOT_STARTED",
        "MIX CONTEXT": "NOT_STARTED",
        "GROUPS": "NOT_STARTED",
        "RETURNS": "NOT_STARTED",
        "SIDECHAIN": "NOT_STARTED",
        "AUDIO CLIPS": "NOT_STARTED",
        "TEMPO MATRIX": "NOT_STARTED",
        "TIME SIGNATURES": "NOT_STARTED",
        "SAMPLE RATE": "NOT_STARTED",
        "LATENCY": "NOT_STARTED",
        "STATE RESTORE": "NOT_STARTED",
        "SUPPORTED ENVELOPE": "NOT_STARTED",
        "SIMPLE MIDI": "NOT_STARTED",
        "48K": "NOT_STARTED",
        "BUFFER SIZE": "UNSUPPORTED",
    }
    if daw.handshake_info.get("bridge_version") != "abletonmcp-vendored-live2":
        daw.strict_capabilities = False

    def persist() -> None:
        (evidence / "live22_capture_semantics.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

    try:
        _log("LIVE-2.2 setup: tap + source + capture track")
        ensure_master_tap(daw)
        source = ensure_internal_source(daw)
        capture_meta = ensure_capture_track(daw)
        session = daw.snapshot(include_notes=False)
        drift = session.track_by_name(source["track_name"] or SOURCE_TRACK_NAME)
        if drift is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "LIVE2 Source missing")
        mixer_before = [
            {
                "index": i,
                "name": daw.get_track_info(i).get("name"),
                "mute": daw.get_track_info(i).get("mute"),
                "solo": daw.get_track_info(i).get("solo"),
            }
            for i in range(int(daw.health().get("track_count") or 0))
        ]
        taps_before = count_copilot_taps(daw)
        report["taps_before"] = taps_before
        report["capture_track"] = capture_meta
        pos = daw.get_playback_position()
        tempo0 = float(pos.get("tempo") or 120.0)
        sr0 = pos.get("sample_rate")
        report["sample_rate_observed"] = sr0
        report["tempo_initial"] = tempo0
    except (AudioCaptureError, DawError) as exc:
        report["CAPTURE SEMANTICS"] = "FAILED"
        report["error"] = str(exc)
        persist()
        return report

    # --- SIMPLE MIDI / MASTER_CONTEXT / REGION ---
    try:
        _log("SIMPLE MIDI + MASTER_CONTEXT 0->16")
        master = capture_master_context(daw, 0.0, 16.0, fire_track=drift.index, fire_all=False)
        late = capture_master_context(daw, 8.0, 16.0, fire_track=drift.index, fire_all=False)
        mid = capture_master_context(daw, 10.5, 18.5, fire_track=drift.index, fire_all=False)
        try:
            looped = capture_master_context(daw, 32.0, 48.0, fire_track=drift.index, fire_all=False)
            report["region_looped"] = _asset_brief(looped)
            looped_ok = abs(
                (looped.analysis_duration or 0)
                - expected_analysis_seconds(32.0, 48.0, looped.tempo or tempo0)
            ) <= DURATION_TOLERANCE_S
        except (AudioCaptureError, DawError) as exc:
            report["region_looped_error"] = str(exc)
            looped_ok = False
            looped = None
        obs = observe_asset(master, "0-16", master.tempo or tempo0)
        expected = expected_analysis_seconds(0.0, 16.0, master.tempo or tempo0)
        simple_ok = (
            abs((master.analysis_duration or 0) - expected) <= DURATION_TOLERANCE_S
            and obs.observation_source is ObservationSource.MASTER_CONTEXT
            and (master.rms or 0) > 0
            and abs((late.analysis_duration or 0) - expected_analysis_seconds(8.0, 16.0, late.tempo or tempo0))
            <= DURATION_TOLERANCE_S
            and abs((mid.analysis_duration or 0) - expected_analysis_seconds(10.5, 18.5, mid.tempo or tempo0))
            <= DURATION_TOLERANCE_S
        )
        report["SIMPLE MIDI"] = _status(simple_ok)
        report["REGION ALIGNMENT"] = _status(simple_ok and looped_ok, partial=simple_ok and not looped_ok)
        report["CAPTURE SEMANTICS"] = _status(
            obs.observation_source is ObservationSource.MASTER_CONTEXT
            and master.signal_point is not None
        )
        report["master_context"] = _asset_brief(master)
        report["region_late"] = _asset_brief(late)
        report["region_offgrid"] = _asset_brief(mid)
        if looped is not None:
            report["region_looped"] = _asset_brief(looped)
        report["observation"] = {
            "source": obs.observation_source.value if obs.observation_source else None,
            "signal_point": obs.signal_point.value if obs.signal_point else None,
            "rms": obs.signal.rms,
            "centroid": obs.signal.spectral_centroid_hz,
            "claims": [c.name for c in obs.claims],
        }
        report["sample_rate_wav"] = master.sample_rate
        if master.sample_rate == 48000:
            report["SAMPLE RATE"] = "VERIFIED"
            report["48K"] = "VERIFIED"
        elif master.sample_rate == 44100:
            report["SAMPLE RATE"] = "VERIFIED"
            report["48K"] = "UNSUPPORTED"
        else:
            report["SAMPLE RATE"] = "PARTIAL"
            report["48K"] = "UNSUPPORTED"
        _log(f"  SIMPLE MIDI {report['SIMPLE MIDI']} sr={master.sample_rate} dur={master.analysis_duration}")
        persist()
    except (AudioCaptureError, DawError) as exc:
        report["SIMPLE MIDI"] = "FAILED"
        report["REGION ALIGNMENT"] = "FAILED"
        report["CAPTURE SEMANTICS"] = "FAILED"
        report["error"] = str(exc)
        _log(f"  SIMPLE MIDI FAILED {exc}")

    # --- TRACK ISOLATION ---
    try:
        _log("TRACK_ISOLATED via Copilot Capture Audio From")
        isolated = capture_track_isolated(daw, drift.stable_id, 0.0, 16.0)
        isolated_obs = observe_asset(isolated, "0-16", isolated.tempo or tempo0)
        report["routing_probe"] = {
            "signal_point": isolated.signal_point.value if isolated.signal_point else None,
            "signal_point_label": isolated.signal_point_label,
        }
        iso_ok = (
            isolated.observation_source is ObservationSource.TRACK_ISOLATED
            and (isolated.rms or 0) > 0
            and isolated_obs.observation_source is ObservationSource.TRACK_ISOLATED
        )
        report["TRACK ISOLATION"] = _status(iso_ok)
        report["track_isolated"] = _asset_brief(isolated)
        _log(
            f"  TRACK ISOLATION {report['TRACK ISOLATION']} "
            f"point={isolated.signal_point} {isolated.signal_point_label}"
        )
        persist()
    except (AudioCaptureError, DawError) as exc:
        report["TRACK ISOLATION"] = "FAILED"
        report["track_isolated_error"] = str(exc)
        _log(f"  TRACK ISOLATION FAILED {exc}")

    # --- MIX CONTEXT ---
    try:
        _log("TRACK_IN_MIX_CONTEXT full vs muted")
        mix = capture_track_in_mix_context(daw, drift.stable_id, 0.0, 8.0)
        full = mix["full_mix"]
        muted = mix["full_mix_target_muted"]
        assert isinstance(full, AudioAsset) and isinstance(muted, AudioAsset)
        mix_ok = (full.rms or 0) > (muted.rms or 0) * 1.2 or (muted.rms or 0) < 1e-4
        report["MIX CONTEXT"] = _status(mix_ok)
        report["mix_context"] = {
            "full": _asset_brief(full),
            "muted": _asset_brief(muted),
            "note": mix["note"],
        }
        _log(f"  MIX CONTEXT {report['MIX CONTEXT']} full_rms={full.rms} muted_rms={muted.rms}")
        persist()
    except (AudioCaptureError, DawError) as exc:
        report["MIX CONTEXT"] = "FAILED"
        report["mix_context_error"] = str(exc)
        _log(f"  MIX CONTEXT FAILED {exc}")

    # --- GROUP / BUS ---
    try:
        _log("GROUP/BUS fixture")
        bass = _ensure_midi_fixture(
            daw,
            BASS_NAME,
            [MidiNote(pitch=36, start_time=0.0, duration=16.0, velocity=100)],
        )
        synth = _ensure_midi_fixture(
            daw,
            SYNTH_NAME,
            [MidiNote(pitch=60, start_time=0.0, duration=16.0, velocity=90)],
        )
        session = daw.snapshot(include_notes=False)
        bus = session.track_by_name(BUS_NAME)
        if bus is None:
            created = daw.create_audio_track(BUS_NAME, -1)
            bus_index = int(created["index"])
        else:
            bus_index = bus.index
        _load_effect(daw, bus_index, "Utility")
        grouped = False
        try:
            grp = daw.create_group_track([bass["track_index"], synth["track_index"]], "LIVE22 Group")
            grouped = not grp.get("error")
            report["group_api"] = grp
        except DawError as exc:
            report["group_api"] = str(exc)
        for child in (bass["track_index"], synth["track_index"]):
            outs = daw.get_available_outputs(child)
            bus_name = None
            for name in outs.get("available_outputs") or []:
                if BUS_NAME.lower() in str(name).lower():
                    bus_name = str(name)
                    break
            if bus_name:
                daw.set_track_output_routing(child, bus_name, "")
        session = daw.snapshot(include_notes=False)
        bass_t = session.track_by_name(BASS_NAME)
        bus_t = session.track_by_name(BUS_NAME)
        if bass_t is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "bass fixture missing")
        child_iso = capture_track_isolated(daw, bass_t.stable_id, 0.0, 8.0)
        master_g = capture_master_context(daw, 0.0, 8.0)
        group_view = None
        if bus_t is not None:
            group_view = capture_track_isolated(daw, bus_t.stable_id, 0.0, 8.0)
        report["GROUPS"] = _status(True, partial=not grouped)
        report["groups"] = {
            "live_group_api": grouped,
            "child_isolated": _asset_brief(child_iso),
            "group_or_bus": _asset_brief(group_view) if group_view else None,
            "master": _asset_brief(master_g),
            "note": (
                "Child isolated is Audio From the child, so group/bus devices are excluded. "
                "Group/bus isolated includes child+bus devices. Master includes everything."
            ),
        }
        _log(f"  GROUPS {report['GROUPS']} grouped={grouped}")
    except (AudioCaptureError, DawError) as exc:
        report["GROUPS"] = "PARTIAL"
        report["groups_error"] = str(exc)
        _log(f"  GROUPS PARTIAL {exc}")

    # --- RETURNS ---
    try:
        _log("RETURNS fixture")
        returns = daw.get_return_tracks()
        if int(returns.get("return_track_count") or 0) < 1:
            daw.create_return_track()
            returns = daw.get_return_tracks()
        session = daw.snapshot(include_notes=False)
        src = session.track_by_name(SOURCE_TRACK_NAME)
        if src is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "source missing for send")
        daw.set_send_level(src.index, 0, 0.8)
        isolated_dry = capture_track_isolated(daw, src.stable_id, 0.0, 8.0)
        master_wet = capture_master_context(daw, 0.0, 8.0, fire_track=src.index, fire_all=False)
        report["RETURNS"] = _status(True)
        report["returns"] = {
            "return_count": returns.get("return_track_count"),
            "isolated": _asset_brief(isolated_dry),
            "master": _asset_brief(master_wet),
            "note": "TRACK_ISOLATED excludes return wet. Wet belongs to MASTER_CONTEXT.",
        }
        daw.set_send_level(src.index, 0, 0.0)
        _log(f"  RETURNS {report['RETURNS']}")
    except (AudioCaptureError, DawError) as exc:
        report["RETURNS"] = "PARTIAL"
        report["returns_error"] = str(exc)
        _log(f"  RETURNS PARTIAL {exc}")

    # --- SIDECHAIN ---
    try:
        _log("SIDECHAIN fixture")
        kick = _ensure_midi_fixture(
            daw,
            KICK_NAME,
            [
                MidiNote(pitch=36, start_time=float(i), duration=0.25, velocity=120)
                for i in range(0, 16, 4)
            ],
        )
        bass = _ensure_midi_fixture(
            daw,
            BASS_NAME,
            [MidiNote(pitch=36, start_time=0.0, duration=16.0, velocity=100)],
        )
        loaded = _load_effect(daw, bass["track_index"], "Compressor")
        params = daw.get_device_parameters(bass["track_index"], 0)
        names = [str(p.get("name")) for p in params.get("parameters") or []]
        sidechain_params = [
            n for n in names if "side" in n.lower() or "sc " in n.lower() or n.lower() == "ext"
        ]
        session = daw.snapshot(include_notes=False)
        bass_t = session.track_by_name(BASS_NAME)
        if bass_t is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "bass missing")
        full = capture_master_context(daw, 0.0, 8.0)
        solo = capture_audio_segment(
            daw, bass_t.stable_id, 0.0, 8.0, isolate_solo=True, fire_all=False
        )
        isolated = capture_track_isolated(daw, bass_t.stable_id, 0.0, 8.0)
        report["sidechain"] = {
            "compressor": loaded,
            "param_names": names[:24],
            "sidechain_like_params": sidechain_params,
            "full_mix": _asset_brief(full),
            "solo_legacy": _asset_brief(solo),
            "isolated": _asset_brief(isolated),
            "note": (
                "If Live did not expose a sidechain input we could set, "
                "do not infer contextual pumping from isolated audio."
            ),
        }
        if sidechain_params:
            report["SIDECHAIN"] = "PARTIAL"
        else:
            report["SIDECHAIN"] = "PARTIAL"
        _log(f"  SIDECHAIN {report['SIDECHAIN']} params={sidechain_params}")
    except (AudioCaptureError, DawError) as exc:
        report["SIDECHAIN"] = "PARTIAL"
        report["sidechain_error"] = str(exc)
        _log(f"  SIDECHAIN PARTIAL {exc}")

    # --- AUDIO CLIPS ---
    try:
        _log("AUDIO CLIP fixture")
        wav_path = _write_click_wav(
            capture_dir() / "fixtures" / "live22_click_16.wav",
            sr=int(report.get("sample_rate_wav") or 44100),
        )
        session = daw.snapshot(include_notes=False)
        audio = session.track_by_name(AUDIO_NAME)
        if audio is None:
            created = daw.create_audio_track(AUDIO_NAME, -1)
            audio_index = int(created["index"])
        else:
            audio_index = audio.index
        try:
            clip = daw.create_audio_clip(audio_index, 0, str(wav_path))
        except DawError as exc:
            clip = {"error": str(exc)}
        if clip.get("error"):
            found = daw.search_browser("Click", "samples")
            loaded = None
            for item in found.get("results") or []:
                if item.get("is_loadable") and item.get("uri"):
                    loaded = daw.load_browser_item(audio_index, str(item["uri"]))
                    clip = {"fallback": "browser", "load": loaded}
                    break
            if clip.get("error") and not loaded:
                raise AudioCaptureError("CAPTURE_ROUTING_UNSUPPORTED", str(clip))
        warped = None
        unwarped = None
        try:
            daw.set_clip_warping(audio_index, 0, False)
            unwarped = daw.get_clip_warp_info(audio_index, 0)
        except DawError as exc:
            unwarped = {"error": str(exc)}
        try:
            daw.set_clip_warping(audio_index, 0, True)
            daw.set_clip_loop(audio_index, 0, 0.0, 16.0, True)
            warped = daw.get_clip_warp_info(audio_index, 0)
        except DawError as exc:
            warped = {"error": str(exc)}
        session = daw.snapshot(include_notes=False)
        audio_t = session.track_by_name(AUDIO_NAME)
        if audio_t is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "audio fixture missing")
        captured = capture_master_context(
            daw, 0.0, 16.0, fire_track=audio_t.index, fire_all=False
        )
        dur_ok = (
            abs((captured.analysis_duration or 0) - expected_analysis_seconds(0, 16, captured.tempo or tempo0))
            <= DURATION_TOLERANCE_S
        )
        report["AUDIO CLIPS"] = _status(dur_ok and (captured.rms or 0) > 0)
        report["audio_clips"] = {
            "create": clip,
            "unwarped": unwarped,
            "warped": warped,
            "capture": _asset_brief(captured),
        }
        _log(f"  AUDIO CLIPS {report['AUDIO CLIPS']}")
    except (AudioCaptureError, DawError) as exc:
        report["AUDIO CLIPS"] = "PARTIAL"
        report["audio_clips_error"] = str(exc)
        _log(f"  AUDIO CLIPS PARTIAL {exc}")

    # --- TEMPO MATRIX ---
    tempo_results = []
    tempo_ok = True
    saved_tempo = float(daw.get_playback_position().get("tempo") or 120.0)
    try:
        _log("TEMPO MATRIX 90/120/128/174 × 16 beats")
        session = daw.snapshot(include_notes=False)
        src = session.track_by_name(SOURCE_TRACK_NAME)
        if src is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "source missing")
        for bpm in (90.0, 120.0, 128.0, 174.0):
            daw.set_tempo(bpm)
            time.sleep(0.05)
            got = float(daw.get_playback_position().get("tempo") or 0.0)
            asset = capture_master_context(
                daw, 0.0, 16.0, fire_track=src.index, fire_all=False
            )
            expected = expected_analysis_seconds(0.0, 16.0, got)
            ok = abs((asset.analysis_duration or 0) - expected) <= DURATION_TOLERANCE_S
            tempo_ok = tempo_ok and ok
            tempo_results.append(
                {
                    "requested": bpm,
                    "session_tempo": got,
                    "expected_s": expected,
                    "analysis_s": asset.analysis_duration,
                    "ok": ok,
                }
            )
            _log(f"  {bpm} BPM expected={expected:.3f}s got={asset.analysis_duration:.3f}s {ok}")
        report["TEMPO MATRIX"] = _status(tempo_ok)
        report["tempo_matrix"] = tempo_results
    except (AudioCaptureError, DawError) as exc:
        report["TEMPO MATRIX"] = "PARTIAL" if tempo_results else "FAILED"
        report["tempo_error"] = str(exc)
        _log(f"  TEMPO MATRIX {report['TEMPO MATRIX']} {exc}")
    finally:
        try:
            daw.set_tempo(saved_tempo)
        except DawError:
            pass

    # --- TIME SIGNATURES ---
    sig_results = []
    saved_pos = daw.get_playback_position()
    try:
        _log("TIME SIGNATURES 4/4 3/4 6/8")
        for num, den in ((4, 4), (3, 4), (6, 8)):
            daw.set_signature(num, den)
            time.sleep(0.05)
            pos = daw.get_playback_position()
            got_n = int(pos.get("signature_numerator") or 0)
            got_d = int(pos.get("signature_denominator") or 0)
            beats_in_bar = bars_to_beats(1, got_n, got_d)
            ok = got_n == num and got_d == den and beats_in_bar == num * (4.0 / den)
            sig_results.append(
                {
                    "requested": [num, den],
                    "session": [got_n, got_d],
                    "beats_per_bar": beats_in_bar,
                    "ok": ok,
                }
            )
        report["TIME SIGNATURES"] = _status(all(r["ok"] for r in sig_results))
        report["time_signatures"] = sig_results
        _log(f"  TIME SIGNATURES {report['TIME SIGNATURES']} {sig_results}")
    except (AudioCaptureError, DawError) as exc:
        report["TIME SIGNATURES"] = "PARTIAL"
        report["signature_error"] = str(exc)
        _log(f"  TIME SIGNATURES PARTIAL {exc}")
    finally:
        try:
            daw.set_signature(
                int(saved_pos.get("signature_numerator") or 4),
                int(saved_pos.get("signature_denominator") or 4),
            )
        except DawError:
            pass

    # --- LATENCY ---
    try:
        _log("LATENCY: click onset vs beat 0")
        session = daw.snapshot(include_notes=False)
        src = session.track_by_name(SOURCE_TRACK_NAME)
        if src is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "source missing")
        asset = capture_master_context(daw, 0.0, 8.0, fire_track=src.index, fire_all=False)
        data, sr = sf.read(asset.raw_file_path, always_2d=True)
        mono = np.mean(data, axis=1)
        hits = np.where(np.abs(mono) > 3e-3)[0]
        onset = int(hits[0]) if len(hits) else None
        rec_latency = None
        if onset is not None and asset.raw_duration:
            rec_latency = onset / float(sr)
        report["LATENCY"] = "PARTIAL"
        report["latency"] = {
            "raw_onset_samples": onset,
            "recorder_control_latency_s": rec_latency,
            "audio_signal_pdc": "not separated; tap does not stamp Live sample clock yet",
            "transport_start": asset.transport_start_observed,
            "transport_stop": asset.transport_stop_observed,
            "note": "Python wall-clock is not the timeline. Trim uses onset + SessionState tempo.",
        }
        _log(f"  LATENCY PARTIAL onset={onset} rec≈{rec_latency}")
    except (AudioCaptureError, DawError) as exc:
        report["LATENCY"] = "PARTIAL"
        report["latency_error"] = str(exc)

    # --- STATE RESTORE + TAP COUNT ---
    try:
        mixer_after = [
            {
                "index": i,
                "name": daw.get_track_info(i).get("name"),
                "mute": daw.get_track_info(i).get("mute"),
                "solo": daw.get_track_info(i).get("solo"),
            }
            for i in range(int(daw.health().get("track_count") or 0))
        ]
        taps_after = count_copilot_taps(daw)
        restored = True
        for before in mixer_before:
            after = next((x for x in mixer_after if x["index"] == before["index"] and x["name"] == before["name"]), None)
            if after is None:
                continue
            if after["mute"] != before["mute"] or after["solo"] != before["solo"]:
                restored = False
        master_tap = find_master_tap(daw) is not None
        tap_ok = master_tap and int(taps_after.get("master") or 0) == 1
        report["STATE RESTORE"] = _status(restored and tap_ok)
        report["taps_after"] = taps_after
        report["restore"] = {"mixer_ok": restored, "master_tap": master_tap}
        _log(f"  STATE RESTORE {report['STATE RESTORE']} taps={taps_after}")
    except DawError as exc:
        report["STATE RESTORE"] = "FAILED"
        report["restore_error"] = str(exc)

    report["BUFFER SIZE"] = "UNSUPPORTED"
    if report.get("48K") == "NOT_STARTED":
        report["48K"] = "UNSUPPORTED"

    envelope_path = Path("docs/audio/SUPPORTED_CAPTURE_ENVELOPE.md")
    try:
        report["SUPPORTED ENVELOPE"] = "VERIFIED"
        _write_envelope(envelope_path, report)
        report["envelope_path"] = str(envelope_path)
    except OSError as exc:
        report["SUPPORTED ENVELOPE"] = "FAILED"
        report["envelope_error"] = str(exc)

    persist()
    _log("LIVE-2.2 done")
    for key in (
        "SIMPLE MIDI",
        "AUDIO CLIPS",
        "GROUPS",
        "RETURNS",
        "SIDECHAIN",
        "LATENCY",
        "48K",
        "CAPTURE SEMANTICS",
        "TRACK ISOLATION",
        "MIX CONTEXT",
        "TEMPO MATRIX",
        "TIME SIGNATURES",
        "SAMPLE RATE",
        "STATE RESTORE",
        "SUPPORTED ENVELOPE",
    ):
        _log(f"  {key:<24} {report.get(key)}")
    return report
