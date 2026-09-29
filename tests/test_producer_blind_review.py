import hashlib
import json

import numpy as np
import pytest
import soundfile as sf

from copilot.producer.blind_review import prepare_blind_review


def test_blind_review_level_matched_anonymous_and_not_verified(tmp_path):
    t = np.arange(8000) / 8000
    original = tmp_path / "original.wav"
    revised = tmp_path / "revised.wav"
    sf.write(original, .10 * np.sin(2 * np.pi * 100 * t), 8000)
    sf.write(revised, .20 * np.sin(2 * np.pi * 100 * t)
             + .03 * np.sin(2 * np.pi * 220 * t), 8000)
    def capture(path, identity):
        return {"path": str(path), "capture_id": identity,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    review = prepare_blind_review(
        capture(original, "original"), capture(revised, "revised"),
        destination=tmp_path / "blind",
    )
    assert review["artistic_quality_human_verified"] is False
    assert [item["label"] for item in review["candidates"]] == ["A", "B"]
    assert len(review["questions"]) == 6
    assert all(row["preference"] is None for row in review["questions"])
    for item in review["candidates"]:
        data, _ = sf.read(item["path"])
        assert np.sqrt(np.mean(data ** 2)) == pytest.approx(10 ** (-18 / 20), rel=.001)
    public = json.loads((tmp_path / "blind" / "review.json").read_text())
    assert "original" not in json.dumps(public)
    assert set(json.loads((tmp_path / "blind" / "answer_key.json").read_text())) == {"A", "B"}


def test_blind_review_refuses_stale_and_mismatched_capture(tmp_path):
    one = tmp_path / "one.wav"
    two = tmp_path / "two.wav"
    sf.write(one, np.ones(8000) * .1, 8000)
    sf.write(two, np.ones(4000) * .2, 8000)
    before = {"path": str(one), "sha256": hashlib.sha256(one.read_bytes()).hexdigest(),
              "capture_id": "a"}
    after = {"path": str(two), "sha256": hashlib.sha256(two.read_bytes()).hexdigest(),
             "capture_id": "b"}
    with pytest.raises(ValueError, match="REGION_MISMATCH"):
        prepare_blind_review(before, after, destination=tmp_path / "blind")
    after["sha256"] = "stale"
    with pytest.raises(ValueError, match="CAPTURE_CHANGED"):
        prepare_blind_review(before, after, destination=tmp_path / "blind")
