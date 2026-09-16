from __future__ import annotations

from copilot.schemas.observation import SignalPoint, TailPolicy

DEFAULT_PREROLL_BEATS = 0.5


def classify_signal_point(channel_name: str | None) -> SignalPoint:
    label = (channel_name or "").lower().replace("-", " ").replace("_", " ")
    if "post mixer" in label or "postmixer" in label:
        return SignalPoint.TRACK_POST_MIXER
    if "post fx" in label or "postfx" in label:
        return SignalPoint.POST_FX
    if "pre fx" in label or "prefx" in label:
        return SignalPoint.PRE_FX
    if label in {"master", "main"}:
        return SignalPoint.MAIN_FINAL
    return SignalPoint.UNKNOWN


def region_windows(
    request_start: float,
    request_end: float,
    *,
    preroll_beats: float = DEFAULT_PREROLL_BEATS,
    tail_policy: TailPolicy = TailPolicy.STRICT_REGION,
) -> dict[str, float | str]:
    if request_end <= request_start:
        raise ValueError("request_end must be after request_start")
    if preroll_beats < 0:
        raise ValueError("preroll_beats must be >= 0")
    capture_start = max(0.0, request_start - preroll_beats)
    if tail_policy is TailPolicy.STRICT_REGION:
        analysis_start = request_start
        analysis_end = request_end
        capture_end = request_end
    else:
        analysis_start = request_start
        analysis_end = request_end
        capture_end = request_end
    return {
        "request_start_beat": float(request_start),
        "request_end_beat": float(request_end),
        "capture_start_beat": float(capture_start),
        "capture_end_beat": float(capture_end),
        "analysis_start_beat": float(analysis_start),
        "analysis_end_beat": float(analysis_end),
        "preroll_beats": float(preroll_beats),
        "tail_policy": tail_policy.value,
    }


def expected_analysis_seconds(start_beat: float, end_beat: float, tempo: float) -> float:
    if tempo <= 0:
        raise ValueError(f"invalid tempo {tempo}")
    return (end_beat - start_beat) * 60.0 / tempo
