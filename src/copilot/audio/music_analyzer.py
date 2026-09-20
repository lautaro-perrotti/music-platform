"""High-level read-only reference music analyzer."""

from __future__ import annotations

from pathlib import Path

from copilot.audio.fullmix import compute_fullmix_observation
from copilot.audio.reference_analysis_v1 import pack_from_fullmix_observation
from copilot.schemas.reference_analysis import ReferenceAnalysisPack


def analyze_reference_file(
    path: Path | str,
    *,
    reference_state_token: str,
    target_state_token: str,
    tempo_bpm: float,
    use_cache: bool = True,
) -> ReferenceAnalysisPack:
    """Analyze one reference file and return factual, windowed evidence.

    This function is deliberately DAW-free. It cannot mutate the target
    project and does not expose raw audio to Astra.
    """
    observation = compute_fullmix_observation(
        path,
        region_id="REFERENCE_FULL_TRACK",
        region_label="reference",
        use_cache=use_cache,
    )
    return pack_from_fullmix_observation(
        observation,
        reference_state_token=reference_state_token,
        target_state_token=target_state_token,
        tempo_bpm=tempo_bpm,
    )
