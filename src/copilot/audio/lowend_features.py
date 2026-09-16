from __future__ import annotations

from pathlib import Path
from typing import Any

import soundfile as sf

from copilot.audio.live_capture import AudioAsset
from copilot.audio.lowend import (
    band_energy_over_time,
    context_change,
    context_around_kicks,
    detect_transients,
    estimate_fundamental,
    low_frequency_envelope,
    overlap_at_attacks,
    prune_kick_attacks,
    spectral_overlap_over_time,
)

ANALYZER_ID = "lowend-obs-1"
ANALYZER_FILES = (
    "src/copilot/audio/lowend.py",
    "src/copilot/audio/lowend_features.py",
    "src/copilot/reasoning/from_dsp.py",
)


def analyzer_fingerprint(root: Path | None = None) -> str:
    import hashlib

    base = root or Path(__file__).resolve().parents[3]
    digest = hashlib.sha256()
    digest.update(ANALYZER_ID.encode("utf-8"))
    for rel in ANALYZER_FILES:
        path = base / rel
        digest.update(path.read_bytes())
    return digest.hexdigest()


def compute_lowend_features(views: dict[str, AudioAsset]) -> dict[str, Any]:
    """Frozen DSP measurements. Same functions/params as diagnose_lowend. No findings."""
    required = ("master", "kick", "bass")
    missing = [key for key in required if key not in views]
    if missing:
        return {"ok": False, "missing": missing, "analyzer_id": ANALYZER_ID}

    kick_wav, kick_sr = _load(views["kick"])
    bass_wav, bass_sr = _load(views["bass"])
    master_wav, master_sr = _load(views["master"])
    duration_s = len(kick_wav) / float(kick_sr) if kick_sr else 0.0
    isolated = detect_transients(kick_wav, kick_sr, role="kick")
    pruned = prune_kick_attacks(isolated["attacks"], duration_s=duration_s)
    kick_event_src = "TRACK_ISOLATED"
    detected = isolated
    if "master_without_bass" in views:
        without_b, without_b_sr = _load(views["master_without_bass"])
        wb = detect_transients(without_b, without_b_sr, role="kick")
        wb_pruned = prune_kick_attacks(wb["attacks"], duration_s=len(without_b) / float(without_b_sr))
        isolated_late = not pruned or float(pruned[0]["time_s"]) > 0.12
        if isolated_late and wb_pruned:
            detected = wb
            pruned = wb_pruned
            duration_s = len(without_b) / float(without_b_sr)
            kick_event_src = "MASTER_CONTEXT_REMOVAL(BASS)"
    kick_bands = band_energy_over_time(kick_wav, kick_sr)
    bass_bands = band_energy_over_time(bass_wav, bass_sr)
    attacks = {
        **detected,
        "attacks": pruned,
        "count": len(pruned),
        "raw_count": detected["count"],
        "source": kick_event_src,
    }
    bass_env = low_frequency_envelope(bass_wav, bass_sr)
    temporal = overlap_at_attacks(attacks["attacks"], bass_env)
    spectral = spectral_overlap_over_time(
        kick_bands, bass_bands, kick_attacks=attacks["attacks"]
    )
    context: dict[str, Any] = {}
    attack_context = None
    if "master_without_kick" in views and "master_without_bass" in views:
        without_k, _sr_k = _load(views["master_without_kick"])
        without_b, _sr_b = _load(views["master_without_bass"])
        context = {
            "without_kick": context_change(master_wav, without_k, master_sr),
            "without_bass": context_change(master_wav, without_b, master_sr),
        }
        kick_times = [float(a["time_s"]) for a in attacks["attacks"]]
        attack_context = context_around_kicks(master_wav, without_b, master_sr, kick_times)
    return {
        "ok": True,
        "analyzer_id": ANALYZER_ID,
        "kick_event_source": kick_event_src,
        "duration_s": duration_s,
        "attacks": attacks,
        "temporal": temporal,
        "spectral": spectral,
        "kick_f0": estimate_fundamental(kick_wav, kick_sr),
        "bass_f0": estimate_fundamental(bass_wav, bass_sr),
        "context": context,
        "attack_context": attack_context,
        "kick_rms": _rms(kick_wav),
        "bass_rms": _rms(bass_wav),
        "master_rms": _rms(master_wav),
    }


def _load(asset: AudioAsset) -> tuple[Any, int]:
    data, sr = sf.read(asset.file_path, always_2d=True)
    return data, int(sr)


def _rms(samples) -> float:
    import numpy as np

    mono = np.asarray(samples, dtype=np.float64)
    if mono.ndim > 1:
        mono = np.mean(mono, axis=1)
    if mono.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(mono**2)))
