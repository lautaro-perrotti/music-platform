"""Versioned PHYSICAL_DSP_V2 constants. Changing these bumps parameters_hash."""

from __future__ import annotations

from typing import Any

ANALYZER_VERSION = "2.0.0"
CAPABILITY_ID = "PHYSICAL_DSP_V2"
PROVIDER_ID = "PhysicalDsp"

# Versioned spectral bands. Not genre labels.
SPECTRAL_BANDS_V1: dict[str, tuple[float, float]] = {
    "low": (20.0, 80.0),
    "low_mid": (80.0, 250.0),
    "mid": (250.0, 2000.0),
    "high_mid": (2000.0, 6000.0),
    "high": (6000.0, 12000.0),
    "air": (12000.0, 20000.0),
}
SPECTRAL_BAND_DEFINITION_ID = "spectral-bands-v1"

WINDOW_S = 0.050
HOP_S = 0.025
SHORT_TERM_S = 0.400
MACRO_S = 3.0
STFT_NPERSEG = 2048
ROLLOFF_PERCENT = 0.85
SILENCE_RMS = 1.0e-4
NEAR_SILENCE_RMS = 1.0e-3
MIN_LUFS_S = 0.400
TRUE_PEAK_OVERSAMPLE = 4
TRANSIENT_MIN_DISTANCE_S = 0.08
ONSET_COINCIDENCE_S = 0.030
EVENT_WINDOW_S = 0.120
EVENT_SLICE_CAP = 16
BEATS_PER_BAR = 4
CHROMA_BINS = 12
KEY_CANDIDATE_COUNT = 5
RELATIONAL_JOINT_SHARE_MIN = 0.15
RELATIONAL_TEMPORAL_MIN = 0.20
RELATIONAL_ONSET_MIN = 0.30
FREQ_WIDTH_MIN_ENERGY = 1.0e-10

DEFAULT_PARAMS: dict[str, Any] = {
    "analyzer_version": ANALYZER_VERSION,
    "window_s": WINDOW_S,
    "hop_s": HOP_S,
    "short_term_s": SHORT_TERM_S,
    "macro_s": MACRO_S,
    "stft_nperseg": STFT_NPERSEG,
    "rolloff_percent": ROLLOFF_PERCENT,
    "silence_rms": SILENCE_RMS,
    "near_silence_rms": NEAR_SILENCE_RMS,
    "min_lufs_s": MIN_LUFS_S,
    "true_peak_oversample": TRUE_PEAK_OVERSAMPLE,
    "transient_min_distance_s": TRANSIENT_MIN_DISTANCE_S,
    "spectral_band_definition_id": SPECTRAL_BAND_DEFINITION_ID,
    "spectral_bands": SPECTRAL_BANDS_V1,
    "beats_per_bar": BEATS_PER_BAR,
    "key_candidate_count": KEY_CANDIDATE_COUNT,
}
