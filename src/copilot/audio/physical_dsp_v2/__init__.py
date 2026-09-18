"""PHYSICAL_DSP_V2 public surface. Factual DSP only."""

from __future__ import annotations

from copilot.audio.physical_dsp_v2.capability import register_physical_dsp_v2
from copilot.audio.physical_dsp_v2.contract import ANALYZER_VERSION, CAPABILITY_ID
from copilot.audio.physical_dsp_v2.pipeline import analyze_buffer, analyze_pair, analyze_path
from copilot.schemas.dsp import DspBundle, DspObservation

__all__ = [
    "ANALYZER_VERSION",
    "CAPABILITY_ID",
    "DspBundle",
    "DspObservation",
    "analyze_buffer",
    "analyze_pair",
    "analyze_path",
    "register_physical_dsp_v2",
]
