from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from copilot.audio.musical_understanding_v1 import (
    _grid_point,
    _midi_notes,
    _periodicity,
    analyze_musical_understanding,
)
from copilot.audio.midi_read_only_v1 import identity_for_als_path
from copilot.schemas.musical_understanding import MusicalUnderstanding


def _write_impulses(path: Path, *, sr: int = 8_000, seconds: float = 4.0) -> None:
    audio = np.zeros(int(sr * seconds), dtype=np.float32)
    for time_s in np.arange(0.1, seconds, 0.5):
        index = int(time_s * sr)
        audio[index : index + 12] = 0.8
    sf.write(path, audio, sr)


def test_grid_preserves_raw_timing_and_nearest_subdivision() -> None:
    point = _grid_point(0.51, 120.0, evidence="fixture")
    assert point.onset_qn == 1.02
    assert point.nearest_grid_qn == 1.0
    assert point.subdivision == "quarter"
    assert point.deviation_ms == pytest.approx(10.0)


def test_periodicity_is_ranked_from_event_positions() -> None:
    candidates = _periodicity([0, 4, 8, 12, 16, 20, 24, 28], evidence="fixture")
    assert candidates
    assert candidates[0].period_bars in {1, 2, 4}
    assert 0.0 <= candidates[0].strength <= 1.0


def test_real_contract_is_read_only_and_keeps_insufficient_tonality(tmp_path: Path, monkeypatch) -> None:
    bass = tmp_path / "bass.wav"
    drums = tmp_path / "drums.wav"
    _write_impulses(bass)
    _write_impulses(drums)
    import hashlib
    import json

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    artifact = lambda role, path: {
        "role": role,
        "source_reference_id": "fixture-reference",
        "provider": "fixture",
        "provider_model": "fixture",
        "path": str(path),
        "artifact_id": f"fixture-{role}",
        "sha256": digest(path),
        "bytes": path.stat().st_size,
        "duration_s": 4.0,
        "sample_rate": 8000,
        "channels": 1,
        "non_silent": True,
        "provenance": ["TEST"],
    }
    payload = {
        "reference_id": "fixture-reference",
        "source_analysis_id": "fixture-analysis",
        "tempo_bpm": 120,
        "timeline": {"windows_reused": [{"start_qn": 0, "end_qn": 8}]},
        "stems": {
            role: {"role": role, "artifact": artifact(role, path), "observations": []}
            for role, path in (("BASS", bass), ("DRUMS", drums))
        },
        "global_limitations": [],
        "provenance": {},
        "no_write": True,
        "raw_audio_included": False,
    }
    for role in ("VOCALS", "OTHER"):
        payload["stems"][role] = {"role": role, "artifact": None, "observations": []}
    reference = tmp_path / "stem-analysis.json"
    reference.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "copilot.audio.musical_understanding_v1._pitch_events",
        lambda *args, **kwargs: ([], ["TEST_NO_PITCH"], {}),
    )
    result = analyze_musical_understanding(reference)
    assert isinstance(result, MusicalUnderstanding)
    assert result.bass.tonality_status == "INSUFFICIENT_EVIDENCE"
    assert result.provenance["model_api_calls"] == 0
    assert result.provenance["musical_writes"] == 0
    assert result.no_write is True


def test_midi_reconciliation_reads_events_from_the_reconciled_als_tree(tmp_path: Path) -> None:
    als = tmp_path / "rose-bass.als"
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton><LiveSet><Tracks>
  <MidiTrack>
    <Name><EffectiveName Value="Decoy" /></Name>
    <DeviceChain />
  </MidiTrack>
  <MidiTrack>
    <Name><EffectiveName Value="Rose Bass" /></Name>
    <DeviceChain><InstrumentGroupDevice><Name Value="Current Device" /></InstrumentGroupDevice></DeviceChain>
    <ArrangerAutomation><Events>
      <MidiClip Id="1" Time="160">
        <CurrentStart Value="160" /><CurrentEnd Value="192" />
        <Loop><LoopStart Value="0" /><LoopEnd Value="32" /><StartRelative Value="0" /><LoopOn Value="false" /></Loop>
        <Name Value="Rose Bass" /><Disabled Value="false" />
        <Notes><KeyTracks><KeyTrack><Notes>
          <MidiNoteEvent Time="0" Duration="2" Velocity="100" OffVelocity="64" IsEnabled="true" />
        </Notes><MidiKey Value="36" /></KeyTrack></KeyTracks></Notes>
      </MidiClip>
    </Events></ArrangerAutomation>
  </MidiTrack>
</Tracks></LiveSet></Ableton>"""
    als.write_bytes(gzip.compress(xml.encode("utf-8")))
    identity = identity_for_als_path(als)
    pack = {
        "source_ref": {
            "project_path": str(als),
            "project_identity": identity,
            "track_name": "Rose Bass",
            "track_index": 1,
            "persistent_track_ref": {
                "object_type": "track",
                "project_identity": identity,
                "role": "midi",
                "name": "Rose Bass",
                "device_names": ["Old Device"],
                "device_classes": ["InstrumentGroupDevice", "AudioEffectGroupDevice"],
                "clip_slots": [5],
                "clip_names": ["Rose Bass"],
                "note_counts": [0],
                "grouped": False,
                "content_fingerprint": "stale-fingerprint",
                "target_state_token": "stale-token",
            },
            "arrangement_clips": [
                {"name": "Rose Bass", "start_time": 160.0, "end_time": 192.0}
            ],
        },
        "timeline": {"start_qn": 160.0, "end_qn": 192.0},
    }
    pack_path = tmp_path / "midi-pack.json"
    pack_path.write_text(json.dumps(pack), encoding="utf-8")

    events, limits, diagnostics = _midi_notes(
        pack_path,
        tempo_bpm=120.0,
        evidence_prefix="fixture",
    )

    assert limits == []
    assert len(events) == 1
    assert events[0].midi_note == 36
    assert diagnostics["status"] == "READ"
    assert diagnostics["clips"] == 1
    assert diagnostics["notes"] == 1
    assert diagnostics["reconciliation"]["status"] == "RESOLVED"
