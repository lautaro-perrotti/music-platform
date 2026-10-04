from __future__ import annotations

import inspect

import pytest

from copilot.core_v2.direct import AbletonBatch, DirectAbletonExecutor, is_pristine_default_set, resolve_timeout
from copilot.core_v2.smoke import batch
from copilot.schemas.session import SessionState, TrackState


def test_smoke_batch_is_typed_and_valid() -> None:
    candidate = batch()
    candidate.validate()
    assert candidate.tempo == 126
    assert [t.name for t in candidate.tracks] == ["FAST V2 - Kick", "FAST V2 - Hat", "FAST V2 - Bass"]
    assert [len(t.notes) for t in candidate.tracks] == [16, 16, 8]


def test_batch_rejects_out_of_bounds_notes() -> None:
    candidate = batch()
    bad = AbletonBatch(126, 4, candidate.tracks)
    with pytest.raises(ValueError, match="note out of clip bounds"):
        bad.validate()


def test_timeout_accepts_only_observed_postcondition() -> None:
    def timeout() -> None:
        raise RuntimeError("Timeout waiting for Ableton")
    assert resolve_timeout(timeout, lambda: object(), lambda _: True) is None
    with pytest.raises(RuntimeError, match="POSTCONDITION_MISSING"):
        resolve_timeout(timeout, lambda: object(), lambda _: False)


def test_direct_executor_does_not_use_safewrite() -> None:
    source = inspect.getsource(DirectAbletonExecutor)
    assert "SafeWrite" not in source
    assert "ProductionCompiler" not in source


def test_untitled_set_with_user_work_is_not_disposable() -> None:
    session = SessionState(tracks=[
        TrackState(stable_id=str(i), index=i, name=name, role=role)
        for i, (name, role) in enumerate((("1-MIDI", "midi"), ("2-MIDI", "midi"),
                                          ("3-Audio", "audio"), ("4-Audio", "audio")))
    ])
    assert is_pristine_default_set(session, [])
    assert not is_pristine_default_set(session, [{"track_index": 0}])
    session.tracks[0].name = "My idea"
    assert not is_pristine_default_set(session, [])
