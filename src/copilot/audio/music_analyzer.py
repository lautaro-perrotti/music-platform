"""High-level read-only reference music analyzer.

The analyzer uses the frozen FullMix measurement layer plus deterministic local
features.  Thirty-two-bar windows are aggregation windows only; section
boundaries come from an independent novelty/change-point pass.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import hashlib
from pathlib import Path
from time import perf_counter

import numpy as np
import soundfile as sf
from scipy.signal import find_peaks

from copilot.audio.fullmix import compute_fullmix_observation
from copilot.audio.music_analyzer_v1 import build_music_analysis_pack
from copilot.audio.reference_analysis_v1 import pack_from_fullmix_observation
from copilot.schemas.music_analysis import (
    AudioAnalysisInput,
    MusicAnalysisPack,
    SectionEvidence,
    SectionHypothesis,
    StructuralRegion,
)


def analyze_reference_file(
    path: Path | str,
    *,
    reference_state_token: str,
    target_state_token: str,
    tempo_bpm: float,
    use_cache: bool = True,
) -> MusicAnalysisPack:
    """Analyze one reference file and return the complete factual pack.

    This function is deliberately DAW-free. It cannot mutate the target
    project and does not expose raw audio to Astra.
    """
    return analyze_reference_music(
        path,
        reference_state_token=reference_state_token,
        target_state_token=target_state_token,
        tempo_bpm=tempo_bpm,
        use_cache=use_cache,
    )


def analyze_audio_input(
    audio_input: AudioAnalysisInput,
    *,
    use_cache: bool = True,
) -> MusicAnalysisPack:
    """Run the same Analyzer for a file or a captured project asset.

    Ingestors own how a path was obtained.  The Analyzer only consumes this
    typed boundary, so reference WAVs and Ableton captures cannot drift into
    separate musical semantics.
    """
    pack = analyze_reference_music(
        audio_input.main_path,
        reference_state_token=audio_input.reference_state_token,
        target_state_token=audio_input.target_state_token,
        tempo_bpm=audio_input.tempo_bpm,
        kick_path=audio_input.source_paths.get("kick"),
        bass_path=audio_input.source_paths.get("bass"),
        use_cache=use_cache,
    )
    pack.provenance.update({
        "project_identity": audio_input.project_identity,
        "capture_id": audio_input.capture_id,
        "ingest_boundary": "AudioAnalysisInput",
    })
    return pack


def analyze_reference_music(
    path: Path | str,
    *,
    reference_state_token: str,
    target_state_token: str,
    tempo_bpm: float,
    kick_path: Path | str | None = None,
    bass_path: Path | str | None = None,
    use_cache: bool = True,
):
    """Run the real MUSIC_ANALYZER_V1 pass over a reference WAV.

    ``path`` is measured locally and never placed in the returned pack.  Kick
    and bass are optional isolated stems: when absent, the result says
    ``MASTER_ONLY`` instead of pretending to know source-level relationships.
    """
    if tempo_bpm <= 0:
        raise ValueError("tempo_bpm must be positive")
    audio_path = Path(path)
    started = perf_counter()
    samples, sample_rate = _load_mono(audio_path)
    decoded_at = perf_counter()
    observation = compute_fullmix_observation(
        audio_path,
        region_id="REFERENCE_FULL_TRACK",
        region_label="reference",
        use_cache=use_cache,
    )
    physical_at = perf_counter()
    reference = pack_from_fullmix_observation(
        observation,
        reference_state_token=reference_state_token,
        target_state_token=target_state_token,
        tempo_bpm=tempo_bpm,
    )
    frame_rows = _frame_features(samples, sample_rate, tempo_bpm)
    sections = infer_reference_sections(
        frame_rows,
        tempo_bpm=tempo_bpm,
        duration_s=len(samples) / float(sample_rate),
    )
    structure_at = perf_counter()
    kick_rows, bass_rows, lowend_limitations = _optional_lowend_stems(
        kick_path, bass_path, sample_rate=sample_rate, tempo_bpm=tempo_bpm,
        duration_s=len(samples) / float(sample_rate),
    )
    lowend_rows: list[dict] = []
    groove_rows: list[dict] = []
    harmony_rows: list[dict] = []
    timbre_rows: list[dict] = []
    texture_rows: list[dict] = []
    prominence_rows: list[dict] = []
    total_beats = len(samples) / float(sample_rate) * tempo_bpm / 60.0
    window_beats = reference.window_beats
    for index, start_beat in enumerate(np.arange(0.0, total_beats, window_beats)):
        end_beat = min(float(start_beat + window_beats), total_beats)
        start_s, end_s = start_beat * 60.0 / tempo_bpm, end_beat * 60.0 / tempo_bpm
        rows = [row for row in frame_rows if start_s <= row["t_s"] < end_s]
        section_label = _measurement_window_section_label(
            start_beat,
            end_beat,
            sections,
        )
        low_values = [row["low_band_energy"] for row in rows]
        onset_times = [row["t_s"] for row in rows if row["onset"]]
        chroma = _mean_chroma(rows)
        key, confidence = _key_candidate(chroma)
        groove_metrics = _groove_metrics(onset_times, tempo_bpm)
        groove = {
            "onset_count": len(onset_times),
            "onset_density_per_s": len(onset_times) / max(end_s - start_s, 1e-9),
            "median_ioi_s": _median_ioi(onset_times),
            **groove_metrics,
            "event_locations": [t * tempo_bpm / 60.0 for t in onset_times],
        }
        low_row = {
            "section_label": section_label,
            "low_band_energy": _mean(low_values),
            "decay_trajectory": _decay_trajectory(samples, sample_rate, onset_times),
            "lowend_measurement_status": "MASTER_ONLY",
        }
        if index < len(kick_rows) and index < len(bass_rows):
            low_row.update(_lowend_relationship(kick_rows[index], bass_rows[index]))
        lowend_rows.append(low_row)
        groove_rows.append(groove)
        harmony_rows.append({"key_candidate": key, "key_confidence": confidence, "chroma_profile": chroma})
        timbre_rows.append({
            "spectral_centroid_hz": _mean([row["centroid_hz"] for row in rows]),
            "spectral_rolloff_hz": _mean([row["rolloff_hz"] for row in rows]),
            "spectral_flatness": _mean([row["flatness"] for row in rows]),
            "zero_crossing_rate": _mean([row["zcr"] for row in rows]),
        })
        texture_rows.append({
            "active_source_count": None,
            "active_sources": [],
            "relative_low_band_energy": _mean(low_values),
        })
        prominence_rows.append({"method": "UNAVAILABLE_NO_SOURCE_SEPARATION"})

    pack = build_music_analysis_pack(
        reference,
        tempo_bpm=tempo_bpm,
        lowend_windows=lowend_rows,
        groove_windows=groove_rows,
        harmony_windows=harmony_rows,
        timbre_windows=timbre_rows,
        texture_windows=texture_rows,
        prominence_windows=prominence_rows,
        sections=[section.model_dump(mode="json") for section in sections],
        structural_regions=[region.model_dump(mode="json") for region in _structural_regions(sections)],
        section_hypotheses=[hypothesis.model_dump(mode="json") for hypothesis in _section_hypotheses(sections)],
        transitions=_transitions(sections, frame_rows, tempo_bpm),
        analyzer_ids={
            "fullmix": "fullmix-obs-1",
            "section_inference": "music-analyzer-v1.change-point-1",
            "groove": "music-analyzer-v1.onset-1",
            "harmony": "music-analyzer-v1.chroma-1",
            "timbre": "music-analyzer-v1.spectral-1",
        },
        evidence_refs=[
            "fullmix.energy_frames",
            "fullmix.spectral_trajectory",
            "music_analyzer.change_points",
            "music_analyzer.onsets",
            "music_analyzer.chroma",
        ],
        provenance={
            "audio_sha256": _file_sha256(audio_path),
            "audio_path": str(audio_path.resolve()),
            "sample_rate": sample_rate,
            "duration_s": len(samples) / float(sample_rate),
            "source_views": {
                "main": str(audio_path.resolve()),
                "kick": str(Path(kick_path).resolve()) if kick_path else None,
                "bass": str(Path(bass_path).resolve()) if bass_path else None,
            },
        },
        contradictions=[],
        limitations=[
            "32-bar windows aggregate evidence and are not section boundaries.",
            "LUFS is unavailable in frozen FullMix V1; energy and crest are reported instead.",
            "Source activity and prominence require isolated sources or separation; not inferred from Main.",
            *lowend_limitations,
        ],
    )
    pack.metadata["timings_s"] = {
        "audio_decode_s": decoded_at - started,
        "physical_dsp_fullmix_s": physical_at - decoded_at,
        "section_and_feature_extraction_s": structure_at - physical_at,
        "pack_assembly_s": perf_counter() - structure_at,
        "total_s": perf_counter() - started,
    }
    return pack


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def infer_reference_sections(
    frame_rows: list[dict], *, tempo_bpm: float, duration_s: float
) -> list[SectionEvidence]:
    """Infer sections from energy/spectral novelty, independently of 32 bars."""
    if not frame_rows:
        return [SectionEvidence(name="UNKNOWN", start_beat=0, end_beat=max(duration_s * tempo_bpm / 60.0, 0.001), confidence=0.0)]
    novelty = np.asarray([row["novelty"] for row in frame_rows], dtype=float)
    if novelty.size >= 5:
        distance = max(1, int(round((8.0 * 60.0 / tempo_bpm) / 0.25)))
        threshold = max(float(np.percentile(novelty, 85)), 0.04)
        peaks, _ = find_peaks(novelty, distance=distance, prominence=threshold * 0.25)
    else:
        peaks = np.asarray([], dtype=int)
    boundaries_s = [0.0]
    min_section_s = max(2.0, 4.0 * 60.0 / tempo_bpm)
    for peak in peaks:
        candidate = float(frame_rows[int(peak)]["t_s"])
        if candidate - boundaries_s[-1] >= min_section_s and duration_s - candidate >= min_section_s:
            boundaries_s.append(candidate)
    boundaries_s.append(duration_s)
    sections: list[SectionEvidence] = []
    global_energy = _mean([row["energy_db"] for row in frame_rows]) or -60.0
    for index, (start_s, end_s) in enumerate(zip(boundaries_s, boundaries_s[1:])):
        rows = [row for row in frame_rows if start_s <= row["t_s"] < end_s]
        energy = _mean([row["energy_db"] for row in rows])
        before = [row["energy_db"] for row in frame_rows if max(0.0, start_s - 1.0) <= row["t_s"] < start_s]
        after = [row["energy_db"] for row in frame_rows if end_s <= row["t_s"] < min(duration_s, end_s + 1.0)]
        slope = ((_mean(after) or energy or global_energy) - (_mean(before) or energy or global_energy)) / max(end_s - start_s, 1e-9)
        contrast = (energy or global_energy) - global_energy
        onset_density = sum(1 for row in rows if row["onset"]) / max(end_s - start_s, 1e-9)
        function = _section_function(index, len(boundaries_s) - 1, energy or global_energy, slope, global_energy, onset_density)
        confidence = min(0.9, max(0.0, 0.45 + abs(contrast) / 40.0 + (0.1 if index in {0, len(boundaries_s) - 2} else 0.0)))
        evidence = ["energy_change_point", "spectral_flux_change_point"]
        if onset_density > 0.25:
            evidence.append("onset_density")
        sections.append(SectionEvidence(
            name=function,
            start_beat=start_s * tempo_bpm / 60.0,
            end_beat=end_s * tempo_bpm / 60.0,
            function=function,
            energy_mean_db=energy,
            energy_slope_db_per_s=slope,
            contrast_db=contrast,
            confidence=confidence,
            evidence=evidence,
        ))
    return sections


def _structural_regions(sections: Sequence[SectionEvidence]) -> list[StructuralRegion]:
    """Project factual change-point regions without semantic labels."""
    return [
        StructuralRegion(
            region_id=f"region-{index}",
            start_beat=section.start_beat,
            end_beat=section.end_beat,
            energy_mean_db=section.energy_mean_db,
            energy_slope_db_per_s=section.energy_slope_db_per_s,
            contrast_db=section.contrast_db,
            feature_summary={"change_point": True},
            evidence_refs=list(section.evidence),
        )
        for index, section in enumerate(sections)
    ]


def _section_hypotheses(sections: Sequence[SectionEvidence]) -> list[SectionHypothesis]:
    """Keep label evidence separate from the observed structural region."""
    hypotheses: list[SectionHypothesis] = []
    for index, section in enumerate(sections):
        contradicting = []
        if section.function in {"DROP", "BUILD"} and section.energy_slope_db_per_s is not None:
            if section.function == "DROP" and section.energy_slope_db_per_s > 0.8:
                contradicting.append("energy_rising_more_than_stable")
            if section.function == "BUILD" and section.energy_slope_db_per_s <= 0.8:
                contradicting.append("energy_slope_below_build_threshold")
        hypotheses.append(SectionHypothesis(
            region_id=f"region-{index}",
            label=section.function or "UNKNOWN",
            confidence=section.confidence,
            supporting_evidence=list(section.evidence),
            contradicting_evidence=contradicting,
            limitations=["Semantic label is a hypothesis over structural measurements."],
        ))
    return hypotheses


def _load_mono(path: Path) -> tuple[np.ndarray, int]:
    data, sample_rate = sf.read(path, always_2d=True, dtype="float32")
    return np.mean(np.asarray(data, dtype=np.float64), axis=1), int(sample_rate)


def _frame_features(samples: np.ndarray, sample_rate: int, tempo_bpm: float) -> list[dict]:
    frame_size = max(256, int(round(sample_rate * 0.5)))
    hop = max(128, int(round(sample_rate * 0.25)))
    previous: np.ndarray | None = None
    rows: list[dict] = []
    for start in range(0, max(1, len(samples) - frame_size + 1), hop):
        frame = samples[start:start + frame_size]
        if len(frame) < frame_size // 2:
            continue
        windowed = frame * np.hanning(len(frame))
        spectrum = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(len(frame), 1.0 / sample_rate)
        power = spectrum ** 2
        total = float(np.sum(power)) + 1e-12
        low = float(np.sum(power[(freqs >= 20) & (freqs < 200)]) / total)
        centroid = float(np.sum(freqs * power) / total)
        cumulative = np.cumsum(power)
        rolloff = float(freqs[min(len(freqs) - 1, int(np.searchsorted(cumulative, total * 0.85)))])
        flatness = float(np.exp(np.mean(np.log(spectrum + 1e-12))) / (np.mean(spectrum) + 1e-12))
        flux = 0.0 if previous is None else float(np.mean(np.maximum(spectrum - previous, 0.0)))
        previous = spectrum
        rms = float(np.sqrt(np.mean(frame ** 2)) + 1e-12)
        rows.append({
            "t_s": (start + len(frame) / 2.0) / sample_rate,
            "energy_db": float(20.0 * np.log10(rms)),
            "low_band_energy": low,
            "centroid_hz": centroid,
            "rolloff_hz": rolloff,
            "flatness": flatness,
            "zcr": float(np.mean(np.abs(np.diff(np.signbit(frame))))),
            "flux": flux,
            "chroma": _chroma(power, freqs),
            "onset": False,
        })
    if rows:
        flux = np.asarray([row["flux"] for row in rows], dtype=float)
        threshold = max(float(np.percentile(flux, 75)), float(np.mean(flux) + np.std(flux)))
        for row in rows:
            row["onset"] = row["flux"] >= threshold and row["flux"] > 0
        for index, row in enumerate(rows):
            previous_energy = rows[index - 1]["energy_db"] if index else row["energy_db"]
            row["novelty"] = abs(row["energy_db"] - previous_energy) / 12.0 + row["flux"] / max(threshold, 1e-9)
    return rows


def _chroma(power: np.ndarray, freqs: np.ndarray) -> list[float]:
    chroma = np.zeros(12, dtype=float)
    valid = (freqs >= 55.0) & (freqs <= 2000.0)
    for frequency, value in zip(freqs[valid], power[valid]):
        midi = int(round(69.0 + 12.0 * np.log2(float(frequency) / 440.0)))
        chroma[midi % 12] += float(value)
    maximum = float(np.max(chroma))
    return [float(value / maximum) if maximum else 0.0 for value in chroma]


def _key_candidate(chroma: list[float]) -> tuple[str | None, float | None]:
    if not any(chroma):
        return None, None
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    major = np.asarray([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor = np.asarray([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    values = np.asarray(chroma, dtype=float)
    scores = []
    for root in range(12):
        scores.append((float(np.dot(values, np.roll(major, root))), f"{names[root]} major"))
        scores.append((float(np.dot(values, np.roll(minor, root))), f"{names[root]} minor"))
    scores.sort(reverse=True)
    best, label = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    confidence = max(0.0, min(1.0, (best - second) / max(abs(best), 1e-9)))
    return label, confidence


def _section_function(index: int, count: int, energy: float, slope: float, baseline: float, onset_density: float) -> str:
    if count > 1 and index == 0:
        return "INTRO"
    if count > 1 and index == count - 1:
        return "OUTRO"
    if energy < baseline - 5.0:
        return "BREAK"
    if slope > 0.8:
        return "BUILD"
    if energy > baseline + 3.0 and onset_density >= 0.25:
        return "DROP"
    return "GROOVE"


def _measurement_window_section_label(
    start_beat: float,
    end_beat: float,
    sections: Iterable[SectionEvidence],
) -> str:
    """Label an aggregation window without pretending it is a section.

    A 32-bar measurement window may contain several independently inferred
    sections.  In that case its label is intentionally ``MIXED``; the
    authoritative section boundaries remain in ``pack.sections``.
    """
    overlapping = [
        section
        for section in sections
        if section.end_beat > start_beat and section.start_beat < end_beat
    ]
    if not overlapping:
        return "UNKNOWN"
    if len(overlapping) > 1:
        return "MIXED"
    return overlapping[0].name


def _median_ioi(times: list[float]) -> float | None:
    if len(times) < 2:
        return None
    return float(np.median(np.diff(times)))


def _groove_metrics(times: list[float], tempo_bpm: float) -> dict[str, float | None]:
    """Factual grid-relative groove descriptors, not a groove judgment."""
    if not times or tempo_bpm <= 0:
        return {"offbeat_ratio": None, "syncopation_proxy": None, "swing_ratio": None,
                "repetition_strength": None, "variation_score": None}
    beats = np.asarray(times, dtype=float) * tempo_bpm / 60.0
    phases = np.mod(beats, 1.0)
    offbeat = float(np.mean((phases >= 0.25) & (phases < 0.75)))
    syncopation = float(np.mean((phases >= 0.125) & (phases < 0.375) | (phases >= 0.625) & (phases < 0.875)))
    ioi = np.diff(np.sort(times))
    swing = None
    if len(ioi) >= 4:
        pairs = ioi[: len(ioi) - (len(ioi) % 2)].reshape(-1, 2)
        ratios = np.maximum(pairs[:, 0], pairs[:, 1]) / np.maximum(np.minimum(pairs[:, 0], pairs[:, 1]), 1e-9)
        swing = float(np.median(ratios))
    bars = np.floor(beats / 4.0).astype(int)
    signatures = [tuple(np.round(np.mod(beats[bars == bar], 4.0) * 4.0).astype(int)) for bar in sorted(set(bars))]
    repetition = None
    variation = None
    if len(signatures) >= 2:
        equal = [signatures[i] == signatures[i - 1] for i in range(1, len(signatures))]
        repetition = float(np.mean(equal))
        variation = 1.0 - repetition
    return {
        "offbeat_ratio": offbeat,
        "syncopation_proxy": syncopation,
        "swing_ratio": swing,
        "repetition_strength": repetition,
        "variation_score": variation,
    }


def _mean(values: Iterable[float]) -> float | None:
    values = [float(value) for value in values if value is not None and np.isfinite(value)]
    return float(np.mean(values)) if values else None


def _mean_chroma(rows: list[dict]) -> list[float]:
    chroma = [row["chroma"] for row in rows if row.get("chroma")]
    if not chroma:
        return []
    return [float(value) for value in np.mean(np.asarray(chroma, dtype=float), axis=0)]


def _decay_trajectory(samples: np.ndarray, sample_rate: int, onset_times: list[float]) -> list[float]:
    if not onset_times:
        return []
    values = []
    for time_s in onset_times[:16]:
        start = int(time_s * sample_rate)
        base = np.sqrt(np.mean(samples[max(0, start):min(len(samples), start + int(0.025 * sample_rate))] ** 2)) + 1e-12
        values.append(float(np.sqrt(np.mean(samples[max(0, start + int(0.1 * sample_rate)):min(len(samples), start + int(0.2 * sample_rate))] ** 2)) / base))
    return values


def _optional_lowend_stems(kick_path, bass_path, *, sample_rate: int, tempo_bpm: float, duration_s: float):
    if kick_path is None or bass_path is None:
        return [], [], ["Kick/bass matched relationship unavailable: isolated stems were not supplied."]
    kick, kick_sr = _load_mono(Path(kick_path)); bass, bass_sr = _load_mono(Path(bass_path))
    if kick_sr != bass_sr or kick_sr != sample_rate:
        return [], [], ["Kick/bass matched relationship unavailable: stem sample rates differ."]
    count = max(1, int(np.ceil(duration_s * tempo_bpm / 60.0 / 128.0)))
    kick_envelope = np.abs(kick)
    bass_envelope = np.abs(bass)
    kick_threshold = max(float(np.percentile(kick_envelope, 75) * 0.5), 1e-7)
    bass_threshold = max(float(np.percentile(bass_envelope, 60) * 0.5), 1e-7)
    attacks, _ = find_peaks(
        kick_envelope,
        distance=max(1, int(sample_rate * 0.08)),
        prominence=kick_threshold * 0.25,
    )

    def rows(signal, *, with_overlap=False):
        out = []
        window_s = 128.0 * 60.0 / tempo_bpm
        for index in range(count):
            start = int(index * window_s * sample_rate)
            end = int(min(len(signal), (index + 1) * window_s * sample_rate))
            segment = signal[start:end]
            row = {"rms": float(np.sqrt(np.mean(segment ** 2))) if len(segment) else 0.0}
            if with_overlap:
                local_attacks = [attack for attack in attacks if start <= attack < end]
                overlap_mask = np.zeros(max(end - start, 1), dtype=bool)
                for attack in local_attacks:
                    left = max(start, attack - int(0.12 * sample_rate)) - start
                    right = min(end, attack + int(0.25 * sample_rate)) - start
                    if right > left:
                        overlap_mask[left:right] |= bass_envelope[start + left:start + right] >= bass_threshold
                overlap_samples = int(np.sum(overlap_mask))
                row["overlap_ratio"] = float(overlap_samples / max(end - start, 1)) if local_attacks else None
                row["overlap_duration_s"] = float(overlap_samples / sample_rate) if local_attacks else None
            out.append(row)
        return out
    return rows(kick, with_overlap=True), rows(bass), []


def _lowend_relationship(kick: dict, bass: dict) -> dict:
    kick_energy, bass_energy = float(kick["rms"]), float(bass["rms"])
    return {
        "kick_energy": kick_energy,
        "bass_energy": bass_energy,
        "kick_bass_overlap_ratio": kick.get("overlap_ratio"),
        "kick_bass_overlap_duration_s": kick.get("overlap_duration_s"),
        "lowend_measurement_status": "STEMS_ENERGY_TIMING",
    }


def _transitions(sections: list[SectionEvidence], rows: list[dict], tempo_bpm: float) -> list[dict]:
    result = []
    for left, right in zip(sections, sections[1:]):
        boundary = left.end_beat
        before = [row["energy_db"] for row in rows if boundary * 60.0 / tempo_bpm - 1.0 <= row["t_s"] < boundary * 60.0 / tempo_bpm]
        after = [row["energy_db"] for row in rows if boundary * 60.0 / tempo_bpm <= row["t_s"] < boundary * 60.0 / tempo_bpm + 1.0]
        delta = (_mean(after) or 0.0) - (_mean(before) or 0.0)
        result.append({"start_beat": max(0.0, boundary - 1.0), "end_beat": boundary + 1.0, "kind": f"{left.function}_TO_{right.function}", "energy_delta_db": delta, "event_locations": [boundary]})
    return result
