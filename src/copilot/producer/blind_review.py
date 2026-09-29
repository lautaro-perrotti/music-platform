"""Optional post-production blind listening pack; no automatic quality verdict."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.human_eval.store import atomic_write


REVIEW_DIMENSIONS = (
    "groove", "sample_selection", "hook", "evolution", "transitions", "mix",
)


def prepare_blind_review(
    before: dict, after: dict, *, destination: Path,
) -> dict:
    """Level-match equal regions and keep the answer key out of review.json."""
    if (
        before.get("capture_id") == after.get("capture_id")
        or before.get("sha256") == after.get("sha256")
    ):
        raise ValueError("BLIND_REVIEW_IDENTICAL_CAPTURES")
    files = []
    for row in (before, after):
        path = Path(row["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("BLIND_REVIEW_CAPTURE_CHANGED")
        data, sr = sf.read(path, always_2d=True, dtype="float32")
        if not len(data) or not np.isfinite(data).all():
            raise ValueError("BLIND_REVIEW_AUDIO_INVALID")
        files.append((data, sr))
    if files[0][1] != files[1][1] or files[0][0].shape != files[1][0].shape:
        raise ValueError("BLIND_REVIEW_REGION_MISMATCH")
    target_rms = 10 ** (-18 / 20)
    normalized = []
    for data, _ in files:
        rms = float(np.sqrt(np.mean(data ** 2)))
        peak = float(np.max(np.abs(data)))
        if rms <= 1e-6 or peak <= 0:
            raise ValueError("BLIND_REVIEW_SILENT")
        if peak * (target_rms / rms) >= 1:
            raise ValueError("BLIND_REVIEW_GAIN_WOULD_CLIP")
        normalized.append(data * (target_rms / rms))
    if np.allclose(normalized[0], normalized[1], atol=1e-5):
        raise ValueError("BLIND_REVIEW_INDISTINGUISHABLE")
    destination.mkdir(parents=True, exist_ok=False)
    reversed_order = bool(secrets.randbelow(2))
    ordered = [1, 0] if reversed_order else [0, 1]
    entries = []
    for label, index in zip(("A", "B"), ordered):
        output = destination / f"{label}.wav"
        sf.write(output, normalized[index], files[index][1], subtype="PCM_24")
        entries.append({"label": label, "path": str(output),
                        "sha256": hashlib.sha256(output.read_bytes()).hexdigest()})
    review = {
        "status": "AWAITING_HUMAN_REVIEW",
        "artistic_quality_human_verified": False,
        "candidates": entries,
        "questions": [
            {"dimension": dimension, "preference": None, "observation": None}
            for dimension in REVIEW_DIMENSIONS
        ],
        "instructions": "Listen without version labels. For each dimension choose A, B or tie and note why. This is optional and does not alter technical COMPLETE.",
    }
    atomic_write(destination / "review.json", review)
    atomic_write(destination / "answer_key.json", {
        label: {"source_capture_id": (before, after)[index]["capture_id"]}
        for label, index in zip(("A", "B"), ordered)
    })
    return review
