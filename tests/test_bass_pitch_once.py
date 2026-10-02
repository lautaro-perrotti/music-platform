"""Focused safeguards for the bounded FAST_LAB bass pitch path."""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
import soundfile as sf

from copilot.audio.bass_pitch_once import run


def _facts(tmp_path):
    sr = 11025
    time = np.arange(int(7.68 * sr)) / sr
    audio = np.zeros_like(time)
    for onset in (1.0, 2.0, 3.0):
        active = (time >= onset) & (time < onset + 0.36)
        audio[active] = .2 * np.sin(2 * np.pi * 49 * (time[active] - onset))
    source = tmp_path / "source.wav"
    region = tmp_path / "region.wav"
    sf.write(source, audio, sr, subtype="FLOAT")
    sf.write(region, audio, sr, subtype="FLOAT")
    facts = {
        "source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "wav": str(region), "start_s": 138.24, "end_s": 145.92, "tempo_bpm": 125,
        "transients": {"attacks": [
            {"time_s": 1.0, "strength": .04},
            {"time_s": 2.0, "strength": .04},
            {"time_s": 3.0, "strength": .001},
        ]},
    }
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(facts), encoding="utf-8")
    return path


def test_inferred_pitch_rejects_weak_attack_and_preserves_source_time(tmp_path):
    report_path = tmp_path / "notes.json"
    summary = run(_facts(tmp_path), report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert summary["attacks"] == 3
    assert summary["accepted_count"] == 2
    assert summary["rejected_count"] == 1
    assert summary["midi_range"] == [31, 31]
    assert report["accepted_notes"][0]["source_onset_s"] == 139.24
    assert all(note["authority"] == "INFERRED" for note in report["accepted_notes"])


def test_hash_mismatch_fails_before_output(tmp_path):
    facts_path = _facts(tmp_path)
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    facts["source_sha256"] = "0" * 64
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    output = tmp_path / "notes.json"
    with pytest.raises(RuntimeError, match="SOURCE_HASH_MISMATCH"):
        run(facts_path, output)
    assert not output.exists()
