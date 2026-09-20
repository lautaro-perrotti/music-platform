from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.music_analyzer import analyze_reference_file


def test_analyze_reference_file_is_windowed_and_no_write(tmp_path: Path):
    path = tmp_path / "reference.wav"
    samples = np.zeros(48000, dtype=np.float32)
    sf.write(path, samples, 48000)
    pack = analyze_reference_file(
        path,
        reference_state_token="reference:r1",
        target_state_token="target:t1",
        tempo_bpm=120,
        use_cache=False,
    )
    assert pack.no_write is True
    assert pack.raw_audio_included is False
    assert pack.tokens.reference_state_token != pack.tokens.target_state_token
    assert len(pack.windows) == 1
