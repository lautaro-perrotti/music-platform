"""Read-only relationship checks for one real combined FAST_LAB capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.schemas.chord_events import HarmonicPhrase


def run(capture_path: Path, phrase_path: Path, output_dir: Path, *, arrangement: bool = False) -> dict:
    if not capture_path.is_file():
        raise FileNotFoundError(capture_path)
    phrase = HarmonicPhrase.model_validate_json(phrase_path.read_text(encoding="utf-8"))
    daw = AbletonTcpAdapter()
    daw.connect()
    try:
        session = daw.snapshot(include_notes=False)
        attach_tokens(session)
        if session.transport.tempo != 125 or session.project_identity is None:
            raise RuntimeError("PROJECT_OR_TEMPO_MISMATCH")
        names = (("LAB_FULL_KICK", "LAB_FULL_HAT", "LAB_FULL_BASS", "LAB_FULL_HARMONY") if arrangement else
                 ("LAB_KICK_SIMPLER", "LAB_HAT_SIMPLER", "LAB_BASS_V2", "LAB_HARMONY"))
        tracks = {name: session.track_by_name(name) for name in names}
        if any(track is None for track in tracks.values()):
            raise RuntimeError("GROOVE_TRACK_MISSING")
        notes = {name: daw.get_clip_notes(track.index, 0)["notes"] for name, track in tracks.items()}
    finally:
        daw.disconnect()
    audio, sr = sf.read(str(capture_path), always_2d=True, dtype="float32")
    if abs(len(audio) / sr - 15.36) > .01 or not np.isfinite(audio).all():
        raise RuntimeError("GROOVE_CAPTURE_INVALID")
    if np.max(np.abs(audio)) < .001:
        raise RuntimeError("GROOVE_CAPTURE_SILENT")
    output_dir.mkdir(parents=True, exist_ok=True)
    review_path = output_dir / "full_groove_8bars_v1.wav"
    report_path = output_dir / "full_groove_8bars_v1.json"
    for path in (review_path, report_path):
        if path.exists():
            raise FileExistsError(path)
    left = audio[:, 0]
    right = audio[:, 1]
    review = np.repeat(audio[:, :1], 2, axis=1) if np.sqrt(np.mean(right ** 2)) < .01 * np.sqrt(np.mean(left ** 2)) else audio
    # The raw float capture is evidence. The human-review copy alone receives
    # enough attenuation to avoid clipping in ordinary fixed-point players.
    review_peak = float(np.max(np.abs(review)))
    review_gain = min(1.0, .95 / review_peak) if review_peak else 1.0
    review = review * review_gain
    sf.write(str(review_path), review, sr, subtype="FLOAT")
    def eight_bars(track_name: str) -> list[dict]:
        if arrangement:
            return notes[track_name]
        return [{**row, "start_time": float(row["start_time"]) + repeat}
                for repeat in (0, 16) for row in notes[track_name]]

    kick_qn = [float(row["start_time"]) for row in eight_bars(names[0])]
    hat_qn = [float(row["start_time"]) for row in eight_bars(names[1])]
    bass_qn = [float(row["start_time"]) for row in eight_bars(names[2])]
    distances = [min(abs(bass - kick) for kick in kick_qn) * 60 / 125 * 1000 for bass in bass_qn]
    simultaneous = sum(distance <= 60 for distance in distances)
    chord_classes = {pc for event in phrase.chord_events for pc in event.pitch_classes}
    bass_classes = {("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")[int(note["pitch"]) % 12]
                    for note in notes[names[2]]}
    half = len(left) // 2
    first, second = left[:half], left[half:]
    correlation = float(np.corrcoef(first, second)[0, 1])
    bar_frames = int(round(1.92 * sr))
    energy = [float(np.sqrt(np.mean(left[i * bar_frames:(i + 1) * bar_frames] ** 2))) for i in range(8)]
    report = {
        "status": "REAL_COMBINED_CAPTURE_VERIFIED" if float(np.max(np.abs(left))) < 1.0 else "REAL_COMBINED_CAPTURE_HEADROOM_WARNING",
        "duration_s": len(audio) / sr, "tempo_bpm": 125,
        "playback_source": "ARRANGEMENT_8BAR" if arrangement else "SESSION_4BAR_LOOPS",
        "session_loop_status": "NOT_APPLICABLE" if arrangement else "INFERRED_FROM_NON_SILENT_BOTH_HALVES",
        "tracks": {name: {"track_index": tracks[name].index, "clip_note_count": len(notes[name])}
                   for name in names},
        "expected_events_8bars": {"kick": len(kick_qn), "hat": len(hat_qn), "bass": len(bass_qn),
                                  "harmony_chord_voicings": len(eight_bars(names[3]))},
        "kick_bass": {"bass_attacks_within_60ms_of_kick": simultaneous,
                      "bass_attacks_total": len(bass_qn),
                      "median_nearest_kick_distance_ms": float(np.median(distances))},
        "bass_harmony": {"bass_pitch_classes": sorted(bass_classes),
                         "chord_pitch_classes": sorted(chord_classes),
                         "non_chord_bass_pitch_classes": sorted(bass_classes - chord_classes),
                         "interpretation": "NON_CHORD_TONE_IS_NOT_AUTOMATIC_ERROR"},
        "rhythmic_density": {"kick_per_bar": len(kick_qn) / 8,
                             "hat_per_bar": len(hat_qn) / 8,
                             "bass_per_bar": len(bass_qn) / 8},
        "repetition": {"first_second_4bar_waveform_correlation": correlation,
                       "bar_rms_left": energy},
        "capture": {"raw": str(capture_path), "review": str(review_path),
                    "rms_left": float(np.sqrt(np.mean(left ** 2))),
                    "peak_left": float(np.max(np.abs(left))),
                    "rms_right_raw": float(np.sqrt(np.mean(right ** 2))),
                    "review_gain_linear": review_gain,
                    "headroom_status": "FLOAT_PEAK_OVER_1" if float(np.max(np.abs(left))) >= 1.0 else "PASS"},
        "limitations": (["One combined MusicPlan compiled and executed; drum sample loads followed as separate certified SafeWrite actions."] if arrangement else
                        ["Combined playback uses existing verified session clips; no single combined MusicPlan was compiled."]) + [
                        "Symbolic event counts are not per-event audio labels.",
                        "Capture temporal alignment is limited; no sample-accurate attack attribution.",
                        "Raw float peak above 1 may clip in fixed-point playback; review copy is attenuated without altering raw evidence.",
                        "Non-chord bass may be an intended slash/suspension relation.",
                        "No human quality judgment or producer-level revision yet."],
        "model_api_calls": 0,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def build_ab_bundle(v1_raw: Path, v2_raw: Path, output_dir: Path) -> dict:
    """Shared-gain listening copy; raw float captures remain immutable."""
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / "full_groove_AB_v1_v2.wav"
    manifest_path = output_dir / "full_groove_AB_v1_v2.json"
    if wav_path.exists() or manifest_path.exists():
        raise FileExistsError("AB_BUNDLE_EXISTS")
    a, sr_a = sf.read(str(v1_raw), always_2d=True, dtype="float32")
    b, sr_b = sf.read(str(v2_raw), always_2d=True, dtype="float32")
    if sr_a != sr_b or a.shape != b.shape:
        raise RuntimeError("AB_CAPTURE_SHAPE_MISMATCH")
    a = np.repeat(a[:, :1], 2, axis=1) if np.max(np.abs(a[:, 1])) < 1e-8 else a
    b = np.repeat(b[:, :1], 2, axis=1) if np.max(np.abs(b[:, 1])) < 1e-8 else b
    peak = max(float(np.max(np.abs(a))), float(np.max(np.abs(b))))
    gain = min(1.0, .95 / peak)
    silence = np.zeros((int(.5 * sr_a), 2), dtype="float32")
    sf.write(str(wav_path), np.concatenate((a * gain, silence, b * gain)), sr_a, subtype="FLOAT")
    manifest = {"order": ["V1 original balance", "V2 harmony track volume -0.25"],
                "sources": [str(v1_raw), str(v2_raw)], "shared_review_gain_linear": gain,
                "wav": str(wav_path), "raw_captures_preserved": True,
                "human_preference": "PENDING"}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("phrase", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--arrangement", action="store_true")
    args = parser.parse_args()
    report = run(args.capture, args.phrase, args.output_dir, arrangement=args.arrangement)
    print(json.dumps({key: report[key] for key in ("status", "duration_s", "tracks", "kick_bass", "bass_harmony", "repetition", "capture")}, indent=2))
