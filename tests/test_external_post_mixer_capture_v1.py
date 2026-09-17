from __future__ import annotations

from pathlib import Path

import pytest

from copilot.audio.live_capture import (
    AudioCaptureError,
    wait_until_wav_shared_readable,
)
from copilot.audio.source_capture_pool_v1 import capture_source_post_mixer_ref
from copilot.audio.tap_trust import (
    PREPARED,
    RECORDING,
    VERIFIED,
    CaptureJournal,
    incomplete_assets_are_invalid,
)
from copilot.daw.object_ref import ResolveStatus, ref_from_track, resolve_track
from copilot.daw.state_tokens import attach_tokens
from copilot.schemas.session import (
    DeviceState,
    MixerState,
    RoutingState,
    SessionState,
    TrackState,
    TransportState,
)


def _track(index: int, name: str) -> TrackState:
    return TrackState(
        stable_id=f"id_{index}",
        index=index,
        name=name,
        role="audio",
        mixer=MixerState(),
        routing=RoutingState(),
        devices=[DeviceState(stable_id=f"d{index}", index=0, name="Simpler")],
    )


def _session(*, path: str, name: str) -> SessionState:
    session = SessionState(
        project_path=path,
        project_name=name,
        transport=TransportState(),
        tracks=[
            _track(0, "Filter Kick"),
            _track(1, "Copilot Capture Bass"),
        ],
    )
    return attach_tokens(session, path=path, name=name)


def test_locked_after_rec_zero_is_not_verified(tmp_path: Path) -> None:
    journal = CaptureJournal("lockprobe", directory=tmp_path)
    journal.record(PREPARED)
    journal.record(RECORDING, rec=0.0, device_on=0.0, exclusive=False)
    assert incomplete_assets_are_invalid(journal.last_status() or "")
    assert journal.claim_verified() != VERIFIED


def test_finalization_timeout_when_never_readable(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "never.wav"
    monkeypatch.setattr(
        "copilot.audio.live_capture.wav_shared_read_ok",
        lambda p: {"exists": True, "readable": False, "error": "winerror=32", "size": 0},
    )
    monkeypatch.setattr(
        "copilot.audio.live_capture._exclusive_open_ok",
        lambda p: {"exists": True, "exclusive": False, "error": "winerror=32", "size": 0},
    )
    with pytest.raises(AudioCaptureError, match="CAPTURE_FINALIZATION_TIMEOUT"):
        wait_until_wav_shared_readable([missing], timeout_s=0.08, interval_s=0.02)


def test_finalization_succeeds_when_shared_readable_even_if_exclusive_held(
    monkeypatch, tmp_path: Path
) -> None:
    wav = tmp_path / "ready.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr(
        "copilot.audio.live_capture.wav_shared_read_ok",
        lambda p: {"exists": True, "readable": True, "error": None, "size": 4},
    )
    monkeypatch.setattr(
        "copilot.audio.live_capture._exclusive_open_ok",
        lambda p: {"exists": True, "exclusive": False, "error": "winerror=32", "size": 4},
    )
    out = wait_until_wav_shared_readable([wav], timeout_s=0.2, interval_s=0.01)
    assert out["ok"] is True
    assert out["exclusive_required"] is False
    assert out["rows"][0]["exclusive"] is False


def test_pool_restores_path_identity_after_isolation_attach(monkeypatch, tmp_path: Path) -> None:
    path = r"C:\Users\lsper\CopilotProjects\Abletunes - Groove Rider Project\Abletunes - Groove Rider.als"
    session = _session(path=path, name="Abletunes - Groove Rider")
    original = session.project_identity
    ref = ref_from_track(session.tracks[0], project_identity=original)

    def fake_capture(daw, **kwargs):
        attach_tokens(kwargs["session"])
        assert kwargs["session"].project_path is None
        assert kwargs["session"].project_identity != original
        return {"ok": True, "signal_status": "HAS_SIGNAL"}

    monkeypatch.setattr(
        "copilot.audio.source_capture_pool_v1.capture_source_post_mixer",
        fake_capture,
    )
    out = capture_source_post_mixer_ref(
        None,  # type: ignore[arg-type]
        session=session,
        preflight={"pass": True},
        target_ref=ref,
        start_qn=36.0,
        end_qn=68.0,
        region_id="AUTO_36_68",
        tempo=120.0,
        dest_root=tmp_path,
    )
    assert out["ok"] is True
    assert out["identity_audit"]["identity_restored"] is True
    assert session.project_path == path
    assert session.project_identity == original
    second = resolve_track(session, ref)
    assert second.status is ResolveStatus.RESOLVED


def test_actual_project_mismatch_still_fails_closed() -> None:
    groove = _session(
        path=r"C:\Users\lsper\CopilotProjects\Abletunes - Groove Rider Project\a.als",
        name="Abletunes - Groove Rider",
    )
    other = _session(path=r"D:\sets\other.als", name="other")
    ref = ref_from_track(groove.tracks[0], project_identity=groove.project_identity)
    attach_tokens(other, path=other.project_path, name=other.project_name)
    resolved = resolve_track(other, ref)
    assert resolved.status is ResolveStatus.PROJECT_MISMATCH
    assert groove.project_identity != other.project_identity
