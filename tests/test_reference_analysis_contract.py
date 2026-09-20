import pytest

from copilot.schemas.reference_analysis import (
    ReferenceAnalysisPack,
    ReferenceStateTokens,
    ReferenceWindowEvidence,
)


def test_reference_and_target_tokens_are_separate():
    tokens = ReferenceStateTokens(
        reference_state_token="reference:abc",
        target_state_token="target:def",
    )
    assert tokens.reference_state_token != tokens.target_state_token


def test_reference_pack_is_no_write_and_has_no_audio():
    pack = ReferenceAnalysisPack(
        tokens={"reference_state_token": "r", "target_state_token": "t"},
        windows=[
            {
                "start_beat": 0,
                "end_beat": 128,
                "event_locations": [32, 64],
                "overlap_durations": {"kick_bass": 8},
            }
        ],
    )
    assert pack.no_write is True
    assert pack.raw_audio_included is False


def test_reference_pack_rejects_same_identity_and_raw_audio():
    with pytest.raises(ValueError):
        ReferenceStateTokens(reference_state_token="same", target_state_token="same")
    with pytest.raises(ValueError):
        ReferenceAnalysisPack(
            tokens={"reference_state_token": "r", "target_state_token": "t"},
            raw_audio_included=True,
        )


def test_window_rejects_events_outside_span():
    with pytest.raises(ValueError):
        ReferenceWindowEvidence(start_beat=0, end_beat=32, event_locations=[33])
