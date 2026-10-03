import json

import pytest

from copilot.music_generation.symbolic_bundle import (
    GenerationBundle, GeneratedLane, RepresentationType, compare_editability,
    from_external_symbolic_file, from_our_symbolic_candidate,
)
from copilot.music_generation.symbolic_comparison import run
from copilot.producer.prompt_groove_v1 import generate_prompt_groove_candidates
from copilot.producer.track_spec import SectionSpec, TrackSpec


PROMPT = "Dark hypnotic tech-house, 125 BPM, instrumental."


def _ours():
    spec = TrackSpec(
        bpm=125, key="D minor", duration_bars=16,
        sections=[SectionSpec(name="A", bars=16, energy=.6,
                              active_roles=["KICK", "HAT", "BASS", "HARMONY", "HOOK"])],
    )
    candidate = generate_prompt_groove_candidates(spec)[0]
    return from_our_symbolic_candidate(prompt=PROMPT, spec=spec, candidate=candidate)


def test_our_candidate_exposes_five_independent_roles_without_daw():
    bundle = _ours()
    row = compare_editability([bundle])[0]
    assert row["editable_role_count"] == 5
    assert row["arrangement_spans"] == 1
    assert bundle.musical_winner is None and bundle.no_ableton_access


def test_external_abc_and_midi_do_not_invent_roles(tmp_path):
    abc = tmp_path / "score.abc"
    abc.write_text("X:1\nM:4/4\nK:Dm\n|D E F G|", encoding="utf-8")
    midi = tmp_path / "music.mid"
    midi.write_bytes(b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0")
    yue = from_external_symbolic_file(
        prompt=PROMPT, provider="YUE2", model="YuE2-3B", output_id="y1",
        path=abc, representation_type=RepresentationType.ABC_SCORE,
        provenance="worker:y1:score.abc",
    )
    muse = from_external_symbolic_file(
        prompt=PROMPT, provider="MUSECOCO", model="MuseCoco", output_id="m1",
        path=midi, representation_type=RepresentationType.MIDI_FILE,
        provenance="worker:m1:music.mid",
    )
    rows = compare_editability([_ours(), yue, muse])
    assert [row["editable_role_count"] for row in rows] == [5, 0, 0]
    assert [row["symbolic_artifact_count"] for row in rows] == [5, 1, 1]
    abc.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="SYMBOLIC_ARTIFACT_MISSING_OR_CHANGED"):
        compare_editability([yue])


def test_missing_worker_outputs_are_pending_not_success(tmp_path):
    path = tmp_path / "our.json"
    path.write_text(_ours().model_dump_json(), encoding="utf-8")
    report = run([path])
    assert report["status"] == "PROVIDER_OUTPUTS_PENDING"
    assert report["providers_missing"] == ["MUSECOCO", "YUE2"]
    assert report["musical_winner"] is None
    assert report["model_calls"] == report["ableton_writes"] == 0


def test_same_prompt_required_and_unmapped_role_not_editable():
    with pytest.raises(ValueError, match="BENCHMARK_PROMPT_MISMATCH"):
        compare_editability([_ours(), _ours().model_copy(update={"prompt": "different", "output_id": "other"})])
    with pytest.raises(ValueError, match="UNMAPPED_ROLE_IS_NOT_INDEPENDENTLY_EDITABLE"):
        GeneratedLane(role="UNMAPPED", representation_type=RepresentationType.MIDI_EVENTS,
                      independently_editable=True,
                      notes=_ours().tracks[0].notes, provenance="test")
    with pytest.raises(ValueError, match="FILE_REPRESENTATION_REQUIRES_FILE_AND_HASH"):
        GenerationBundle.model_validate(json.loads(_ours().model_dump_json()) | {
            "tracks": [{"role": "UNMAPPED", "representation_type": "ABC_SCORE", "provenance": "test"}]
        })
