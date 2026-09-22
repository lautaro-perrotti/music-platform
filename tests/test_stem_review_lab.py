from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.review.stem_review_lab import ReviewLab, compute_audio_metrics, constant_gain_db


def _write(path: Path, tone: float) -> None:
    rate = 8000
    t = np.arange(rate, dtype=np.float32) / rate
    sf.write(path, (tone * np.sin(2 * np.pi * 220 * t)).astype(np.float32), rate)


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "benchmark"
    (root / "manifests").mkdir(parents=True)
    raw = root / "raw"
    blind = root / "blind"
    private = {}
    runs = []
    for index, public in enumerate(("candidate_A", "candidate_B")):
        private_id = f"private_{index}"
        private[public] = private_id
        roles = []
        for role in ("drums", "bass", "other", "vocals"):
            p = raw / private_id / f"{role}.wav"
            p.parent.mkdir(parents=True, exist_ok=True)
            _write(p, 0.15 + index * 0.1)
            q = blind / role / f"{public}.wav"
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_bytes(p.read_bytes())
            for n in range(1, 5):
                (blind / role / f"{public}_excerpt_{n:02d}.wav").write_bytes(p.read_bytes())
            roles.append({"role": role, "path": str(p), "latency_s": 1.0})
        runs.append({"candidate_id": private_id, "latency_s": 1.0, "separator": {"model": f"secret-model-{index}", "runtime_revision": "secret-runtime"}, "roles": roles})
    manifest = {"benchmark_id": "fixture", "runs": runs}
    (root / "manifests" / "benchmark_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "manifests" / "private_candidate_mapping.json").write_text(json.dumps({"benchmark_id": "fixture", "public_to_private": private, "gain_db_by_public_role": {}}), encoding="utf-8")
    return root


def test_metrics_and_gain_are_factual(tmp_path: Path) -> None:
    path = tmp_path / "tone.wav"
    _write(path, 0.2)
    metrics = compute_audio_metrics(path)
    assert metrics["sample_rate"] == 8000
    assert metrics["lufs"] is not None
    assert metrics["clipping"] is False
    assert constant_gain_db(metrics, float(metrics["lufs"])) == 0.0


def test_review_is_blind_until_explicit_reveal_and_persists(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    lab = ReviewLab(root)
    payload = lab.public_state()
    raw = json.dumps(payload)
    assert "secret-model" not in raw
    assert "private_" not in raw
    for role in ("drums", "bass", "other"):
        from copilot.review.stem_review_lab import DIMENSIONS
        dims = {key: 5 for key in DIMENSIONS[role]}
        for candidate in lab.candidates:
            lab.vote(role, candidate, dims, "USABLE", "clean")
    assert lab.complete()
    lab.finalize()
    assert not lab.public_state()["revealed"]
    revealed = lab.reveal()
    assert revealed["revealed"]
    assert "secret-model" in json.dumps(revealed)
    assert (root / "reviews" / "final_selection.json").is_file()


def test_quick_pairwise_and_keep_are_separate_from_detailed_votes(tmp_path: Path) -> None:
    lab = ReviewLab(_fixture(tmp_path))
    state = lab.pairwise("drums", "candidate_A", "candidate_B", "TOO_CLOSE")
    state = lab.keep("drums", "candidate_A")
    assert state["pairwise"]["drums:candidate_A:candidate_B"]["choice"] == "TOO_CLOSE"
    assert state["kept_by_role"]["drums"] == "candidate_A"
    assert state["quick_reviewed"]["drums"] == 2
    assert state["votes"] == {}
