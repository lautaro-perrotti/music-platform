from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.music_analyzer import (
    analyze_audio_input,
    analyze_reference_file,
    analyze_reference_music,
)
from copilot.schemas.music_analysis import AudioAnalysisInput


def test_analyze_reference_file_is_windowed_and_no_write(tmp_path: Path):
    path = tmp_path / "reference.wav"
    samples = np.zeros(48000, dtype=np.float32)
    sf.write(path, samples, 48000)
    pack = analyze_reference_file(
        path,
        reference_state_token="reference:r1",
        target_state_token="target:t1",
        tempo_bpm=120,
        use_cache=False,
    )
    assert pack.no_write is True
    assert pack.raw_audio_included is False
    assert pack.tokens.reference_state_token != pack.tokens.target_state_token
    assert len(pack.windows) == 1
    assert pack.evidence_refs
    assert pack.provenance["audio_sha256"]
    assert any("isolated stems" in item for item in pack.limitations)


def test_real_music_analyzer_infers_sections_outside_measurement_windows(tmp_path: Path):
    sample_rate = 16000
    duration_s = 16.0
    time = np.arange(int(sample_rate * duration_s)) / sample_rate
    signal = np.zeros_like(time, dtype=np.float64)
    for start, end, amplitude, frequency in (
        (0.0, 4.0, 0.10, 110.0),
        (4.0, 8.0, 0.80, 220.0),
        (8.0, 12.0, 0.12, 110.0),
        (12.0, 16.0, 0.65, 330.0),
    ):
        mask = (time >= start) & (time < end)
        signal[mask] = amplitude * np.sin(2 * np.pi * frequency * time[mask])
    path = tmp_path / "reference_sections.wav"
    sf.write(path, signal.astype(np.float32), sample_rate)

    pack = analyze_reference_music(
        path,
        reference_state_token="reference:real",
        target_state_token="target:real",
        tempo_bpm=120,
        use_cache=False,
    )

    assert pack.no_write is True
    assert pack.windows[0].end_beat == 32.0  # bounded tail; the configured window is 32 bars
    assert len(pack.sections) >= 2  # independently inferred boundaries
    assert any(section.function in {"INTRO", "BREAK", "DROP", "BUILD"} for section in pack.sections)
    assert any(section.end_beat != 128.0 for section in pack.sections)
    assert pack.windows[0].section_label == "MIXED"
    assert pack.windows[0].evidence_refs == pack.evidence_refs
    assert pack.windows[0].provenance["audio_sha256"] == pack.provenance["audio_sha256"]
    assert pack.structural_regions
    assert len(pack.structural_regions) == len(pack.sections)
    assert len(pack.section_hypotheses) == len(pack.structural_regions)
    assert {region.region_id for region in pack.structural_regions} == {
        hypothesis.region_id for hypothesis in pack.section_hypotheses
    }
    assert all(
        "semantic label is a hypothesis" in limitation.lower()
        for hypothesis in pack.section_hypotheses
        for limitation in hypothesis.limitations
    )
    assert pack.windows[0].timbre.spectral_centroid_hz is not None
    assert pack.windows[0].harmony.key_candidate is not None
    assert any(item.startswith("32-bar windows aggregate evidence") for item in pack.limitations)


def test_real_music_analyzer_reports_lowend_relationship_when_stems_exist(tmp_path: Path):
    sample_rate = 16000
    length = sample_rate * 8
    kick = np.zeros(length, dtype=np.float32)
    for position in range(0, length, sample_rate):
        kick[position:position + 160] = np.hanning(160).astype(np.float32)
    bass_time = np.arange(length) / sample_rate
    bass = (0.25 * np.sin(2 * np.pi * 55.0 * bass_time)).astype(np.float32)
    master = (kick + bass).astype(np.float32)
    master_path = tmp_path / "master.wav"
    kick_path = tmp_path / "kick.wav"
    bass_path = tmp_path / "bass.wav"
    sf.write(master_path, master, sample_rate)
    sf.write(kick_path, kick, sample_rate)
    sf.write(bass_path, bass, sample_rate)

    pack = analyze_reference_music(
        master_path,
        reference_state_token="reference:lowend",
        target_state_token="target:lowend",
        tempo_bpm=120,
        kick_path=kick_path,
        bass_path=bass_path,
        use_cache=False,
    )

    window = pack.windows[0]
    assert window.lowend_measurement_status == "STEMS_ENERGY_TIMING"
    assert window.kick_energy is not None
    assert window.bass_energy is not None
    assert window.kick_bass_overlap_duration_s is not None


def test_audio_analysis_input_is_the_shared_project_and_reference_boundary(tmp_path: Path):
    path = tmp_path / "boundary.wav"
    sf.write(path, np.zeros(16000, dtype=np.float32), 16000)
    audio_input = AudioAnalysisInput(
        main_path=path,
        reference_state_token="reference:boundary",
        target_state_token="target:boundary",
        tempo_bpm=120,
        project_identity="project-boundary",
        capture_id="capture-boundary",
    )

    pack = analyze_audio_input(audio_input, use_cache=False)

    assert pack.tokens.reference_state_token == "reference:boundary"
    assert pack.tokens.target_state_token == "target:boundary"
    assert pack.provenance["ingest_boundary"] == "AudioAnalysisInput"
    assert pack.provenance["project_identity"] == "project-boundary"
    assert pack.provenance["capture_id"] == "capture-boundary"
    assert pack.no_write is True
