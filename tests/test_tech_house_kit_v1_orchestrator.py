"""Offline checks for the TECH_HOUSE_PRODUCTION_KIT_V1 orchestrator helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from copilot.integration import tech_house_kit_v1 as orch
from copilot.producer import tech_house_kit_v1 as kit


def _definition(tmp_path: Path) -> dict:
    rows = []
    for item in kit.KIT:
        if not item.is_one_shot:
            continue
        source = tmp_path / "src" / item.browser_name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(item.role.encode() * 16)
        rows.append({"role": item.role, "source_type": item.source_type, "browser_name": item.browser_name,
                     "path": str(source), "sha256": kit.sha256_file(source)})
    return {"sounds": rows}


def test_staging_copies_with_digest_and_safe_names(tmp_path: Path) -> None:
    staged = orch.stage_one_shots(_definition(tmp_path), tmp_path / "proj")
    assert set(staged) == {"Kick", "Clap", "Closed Hat", "Open Hat", "Perc"}
    for role, row in staged.items():
        assert row["uri"].startswith("Samples/KIT_V1/kit_v1_")
        assert " " not in row["uri"]
        assert kit.sha256_file(Path(row["path"])) == row["sha256"]


def test_staging_never_overwrites(tmp_path: Path) -> None:
    definition = _definition(tmp_path)
    orch.stage_one_shots(definition, tmp_path / "proj")
    with pytest.raises(FileExistsError, match="KIT_SAMPLE_ALREADY_STAGED"):
        orch.stage_one_shots(definition, tmp_path / "proj")


def test_staging_rejects_digest_mismatch(tmp_path: Path) -> None:
    definition = _definition(tmp_path)
    definition["sounds"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="KIT_SAMPLE_DIGEST_MISMATCH"):
        orch.stage_one_shots(definition, tmp_path / "proj")


def _wav(path: Path, data: np.ndarray) -> Path:
    sf.write(path, data, 44100, subtype="FLOAT")
    return path


def test_technical_gates_are_channels_signal_and_clipping_only(tmp_path: Path) -> None:
    t = np.arange(44100) / 44100
    left, right = 0.4 * np.sin(2 * np.pi * 110 * t), 0.4 * np.sin(2 * np.pi * 111 * t)
    good = orch.technical_audio_ok(orch.channel_metrics(_wav(tmp_path / "ok.wav", np.column_stack((left, right)))))
    assert all(good.values())
    silent_right = orch.channel_metrics(_wav(tmp_path / "r.wav", np.column_stack((left, np.zeros_like(left)))))
    assert orch.technical_audio_ok(silent_right)["right_signal"] is False
    clipped = orch.channel_metrics(_wav(tmp_path / "c.wav", np.column_stack((left * 3, right * 3))))
    assert orch.technical_audio_ok(clipped)["no_clipping"] is False
    mono = orch.channel_metrics(_wav(tmp_path / "m.wav", np.column_stack((left, left))))
    assert orch.technical_audio_ok(mono)["not_bit_identical"] is False


def test_gain_stage_covers_every_role_and_leaves_headroom() -> None:
    assert set(orch.GAIN_STAGE) == {item.role for item in kit.KIT}
    assert all(0.0 < value < 0.85 for value in orch.GAIN_STAGE.values())  # below unity fader
    assert all(value - orch.HEADROOM_STEP > 0 for value in orch.GAIN_STAGE.values())


def test_markdown_report_states_facts_and_keeps_audition_pending() -> None:
    report = {"technical": "VERIFIED", "human_audition": "PENDING", "technical_gates": {"stereo": True},
              "artifacts": {"wav": "x.wav", "working_als": "y.als"}, "sidechain": "BLOCKED_BY_CONTROL_SURFACE",
              "automation": "DEFERRED", "semantic_controls": "DEFERRED", "processing": [], "capture_attempts": [{}]}
    text = orch.render_markdown(report, {"delivered": {"peak_dbfs": -3.2, "lufs_integrated": -14.0}})
    assert "TECHNICAL: **VERIFIED**" in text and "HUMAN_AUDITION: **PENDING**" in text
    assert "-3.2 dBFS" in text and "BLOCKED_BY_CONTROL_SURFACE" in text
    assert all(item.browser_name in text for item in kit.KIT)
    assert "sounds good" not in text.lower()
