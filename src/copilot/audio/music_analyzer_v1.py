"""MUSIC_ANALYZER_V1: deterministic read-only evidence composition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from copilot.schemas.music_analysis import (
    GrooveEvidence,
    HarmonyEvidence,
    MusicAnalysisPack,
    MusicAnalysisWindow,
    ProminenceEvidence,
    SectionEvidence,
    SourceActivityEvidence,
    TextureEvidence,
    TimbreEvidence,
    TransitionEvidence,
)
from copilot.schemas.reference_analysis import ReferenceAnalysisPack


def build_music_analysis_pack(
    reference: ReferenceAnalysisPack,
    *,
    tempo_bpm: float,
    lowend_windows: Sequence[Mapping[str, Any]] = (),
    groove_windows: Sequence[Mapping[str, Any]] = (),
    harmony_windows: Sequence[Mapping[str, Any]] = (),
    timbre_windows: Sequence[Mapping[str, Any]] = (),
    texture_windows: Sequence[Mapping[str, Any]] = (),
    prominence_windows: Sequence[Mapping[str, Any]] = (),
    sections: Sequence[Mapping[str, Any] | SectionEvidence] = (),
    source_activity: Sequence[Mapping[str, Any] | SourceActivityEvidence] = (),
    transitions: Sequence[Mapping[str, Any] | TransitionEvidence] = (),
    analyzer_ids: Mapping[str, str] | None = None,
    limitations: Sequence[str] = (),
) -> MusicAnalysisPack:
    """Compose existing factual analyzer outputs without measuring or writing.

    Missing analyzer families remain explicitly unknown.  The function does
    not infer musical judgments and never receives an audio path or payload.
    """
    if tempo_bpm <= 0:
        raise ValueError("tempo_bpm must be positive")
    families = [lowend_windows, groove_windows, harmony_windows, timbre_windows, texture_windows, prominence_windows]
    if any(len(rows) > len(reference.windows) for rows in families):
        raise ValueError("analyzer rows cannot exceed reference window count")

    def row(rows: Sequence[Mapping[str, Any]], index: int) -> dict[str, Any]:
        return dict(rows[index]) if index < len(rows) else {}

    windows: list[MusicAnalysisWindow] = []
    for index, ref_window in enumerate(reference.windows):
        low = row(lowend_windows, index)
        groove = row(groove_windows, index)
        harmony = row(harmony_windows, index)
        timbre = row(timbre_windows, index)
        texture = row(texture_windows, index)
        prominence = row(prominence_windows, index)
        windows.append(MusicAnalysisWindow(
            index=index,
            start_beat=ref_window.start_beat,
            end_beat=ref_window.end_beat,
            section_label=ref_window.section_label,
            energy_db=ref_window.energy_db,
            lufs=ref_window.lufs,
            crest_factor_db=ref_window.crest_factor_db,
            low_band_energy=low.get("low_band_energy", ref_window.low_band_energy),
            kick_energy=low.get("kick_energy"),
            bass_energy=low.get("bass_energy"),
            decay_trajectory=list(low.get("decay_trajectory", ref_window.decay_trajectory) or []),
            groove=GrooveEvidence(**groove),
            harmony=HarmonyEvidence(**harmony),
            timbre=TimbreEvidence(**timbre),
            texture=TextureEvidence(**texture),
            prominence=ProminenceEvidence(**prominence),
            limitations=list(ref_window.model_dump().get("limitations", []) or []),
        ))

    parsed_sections = [item if isinstance(item, SectionEvidence) else SectionEvidence(**item) for item in sections]
    parsed_activity = [item if isinstance(item, SourceActivityEvidence) else SourceActivityEvidence(**item) for item in source_activity]
    parsed_transitions = [item if isinstance(item, TransitionEvidence) else TransitionEvidence(**item) for item in transitions]
    return MusicAnalysisPack(
        tokens=reference.tokens,
        tempo_bpm=tempo_bpm,
        window_bars=int(round(reference.window_beats / 4.0)),
        windows=windows,
        sections=parsed_sections,
        source_activity=parsed_activity,
        transitions=parsed_transitions,
        analyzer_ids=dict(analyzer_ids or {}),
        limitations=list(limitations),
        no_write=True,
        raw_audio_included=False,
    )
