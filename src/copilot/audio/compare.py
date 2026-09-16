from __future__ import annotations

from copilot.schemas.observation import MusicObservation


def compare_observations(
    before: MusicObservation, after: MusicObservation
) -> dict[str, float | None]:
    def delta(getter) -> float | None:
        left = getter(before)
        right = getter(after)
        if left is None or right is None:
            return None
        return float(right - left)

    return {
        "rms_delta": delta(lambda obs: obs.signal.rms),
        "peak_delta": delta(lambda obs: obs.signal.peak),
        "lufs_delta": delta(lambda obs: obs.signal.lufs),
        "centroid_delta_hz": delta(lambda obs: obs.signal.spectral_centroid_hz),
        "bass_ratio_delta": delta(lambda obs: obs.signal.bass_energy_ratio),
    }
