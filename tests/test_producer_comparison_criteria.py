from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.producer.criteria import ProducerCriteria
from copilot.producer.track_spec import TrackSpec
from copilot.sample_library.context_comparison import compare_shortlist
from copilot.sample_library.schemas import LibraryIndex, SampleAsset, SampleType


def _sample(path: Path, hz: float, *, sr: int = 8000) -> SampleAsset:
    time = np.arange(sr) / sr
    sf.write(str(path), 0.25 * np.sin(2 * np.pi * hz * time), sr)
    return SampleAsset(
        id=path.stem, path=str(path), filename=path.name,
        library_root=str(path.parent), relative_path=path.name,
        extension=".wav", size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        sample_type=SampleType.ONE_SHOT,
    )


def test_comparison_previews_share_gain_timeline_context_and_digest(tmp_path):
    kick = _sample(tmp_path / "kick.wav", 70)
    bass_a = _sample(tmp_path / "bass_a.wav", 110)
    bass_b = _sample(tmp_path / "bass_b.wav", 220)
    index = LibraryIndex(assets={row.sha256: row for row in (kick, bass_a, bass_b)})
    shortlist = {
        "Kick": [{"sha256": kick.sha256, "filename": kick.filename}],
        "Bass": [{"sha256": row.sha256, "filename": row.filename} for row in (bass_a, bass_b)],
    }
    destination = tmp_path / "ab"
    one = compare_shortlist(
        index, shortlist, authorized_root=tmp_path,
        preview_root=destination, bpm=120,
    )
    two = compare_shortlist(
        index, shortlist, authorized_root=tmp_path,
        preview_root=destination, bpm=120,
    )
    for candidate, repeated in zip(one["Bass"], two["Bass"]):
        assert candidate["preview"]["sha256"] == repeated["preview"]["sha256"]
        assert candidate["context_preview"]["sha256"] == repeated["context_preview"]["sha256"]
        assert candidate["context_preview"]["reference_sha256"] == kick.sha256
        assert candidate["preview"]["pattern"] == "bar_start_once"
        assert candidate["preview"]["semantic_listening"] is False
        assert candidate["ab_limitations"]
        assert 0 <= candidate["ab_facts"]["context_low_band_overlap_proxy"] <= 1
    assert one["Bass"][0]["context_preview"]["sha256"] != one["Bass"][1]["context_preview"]["sha256"]
    bass_a.path = str(tmp_path / "other.wav")
    with pytest.raises(FileNotFoundError):
        compare_shortlist(index, shortlist, authorized_root=tmp_path,
                          preview_root=destination, bpm=120)


def test_criteria_rejects_ungrounded_roles_hook_and_refs():
    spec = TrackSpec(
        bpm=120, primary_hook="Bass riff", hook_role="Bass",
        sections=[{"name": "Drop", "bars": 8, "energy": .8,
                   "active_roles": ["Kick", "Bass"]}],
    )
    payload = {
        "primary_hook": "Bass riff", "hook_role": "Bass",
        "uncertainty": "No human audition of arrangement",
        "sections": [{
            "section_name": "Drop", "perceptual_goal": "Drive the dancefloor",
            "lead_role": "Bass", "low_end_owner": "Kick",
            "space_roles": ["Clap"], "hook_usage": "foreground",
            "energy_rationale": "More pulse and fewer layers",
            "variation_hypothesis": "A one-bar fill should cue the next entry",
            "claim_kind": "ARTISTIC_PREFERENCE", "evidence_refs": ["sample-sha"],
        }],
    }
    criteria = ProducerCriteria.model_validate(payload)
    criteria.validate_against(spec, selected_digests={"sample-sha"})
    payload["sections"][0]["space_roles"] = ["Bass"]
    with pytest.raises(ValueError, match="UNGROUNDED"):
        ProducerCriteria.model_validate(payload).validate_against(
            spec, selected_digests={"sample-sha"},
        )


def test_adapter_preserves_physical_zero_ceiling_and_negative_range(monkeypatch):
    adapter = AbletonTcpAdapter()
    monkeypatch.setattr(adapter, "_ensure_project", lambda: {"path": "C:\\copy.als", "name": "copy"})
    observed = adapter._session_from_infos(
        {"tempo": 120}, {}, {
            0: {"name": "Test", "devices": [{
                "index": 0, "name": "Limiter", "class_name": "Limiter",
                "parameters": [{"index": 0, "name": "Ceiling",
                                "value": -0.5, "min": -36.0, "max": 0.0}],
            }]},
        }, include_notes=False,
    )
    parameter = observed.tracks[0].devices[0].parameters[0]
    assert (parameter.value, parameter.min, parameter.max) == (-0.5, -36.0, 0.0)
