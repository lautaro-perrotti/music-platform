"""Doctor is read-only and never exposes secrets."""

from __future__ import annotations

from pathlib import Path

from copilot.audio.doctor_v1 import ACTION_VOCABULARY, doctor


def test_doctor_offline_no_secrets(tmp_path: Path) -> None:
    report = doctor(evidence=tmp_path, daw=None)
    blob = str(report)
    assert "sk-" not in blob
    assert report["checks"]["secrets_exposed"] is False
    assert report["checks"]["canonical_action_vocabulary"] == list(ACTION_VOCABULARY)
    assert "sha256" in report["checks"]["frozen_analyzers"]["lowend"]
    assert "sha256" in report["checks"]["frozen_analyzers"]["fullmix"]
    assert report["NO WRITE"] is True
    assert report["checks"]["ableton_reachable"] is False
    assert report["checks"]["session_status"] == "SESSION_NOT_PROBED"
    assert report["checks"]["writes_permitted"] is False
    assert report["LIVE_SESSION_READINESS_V1"] == "VERIFIED / FROZEN"
    assert report["capabilities"]["NO MOCK SUCCESS"] is True
