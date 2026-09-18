"""Slice a buffer into track/region/bar/beat/event spans. Live optional."""

from __future__ import annotations

from copilot.audio.physical_dsp_v2.audio import AudioBuffer, qn_to_seconds, whole_span
from copilot.audio.physical_dsp_v2.contract import BEATS_PER_BAR, EVENT_SLICE_CAP, EVENT_WINDOW_S
from copilot.schemas.dsp import DspGranularity, DspLimitation, TimeSpan


def region_span(
    buffer: AudioBuffer,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
    start_qn: float | None = None,
    end_qn: float | None = None,
    tempo_bpm: float | None = None,
    granularity: DspGranularity = DspGranularity.REGION,
) -> TimeSpan:
    if start_qn is not None and end_qn is not None and tempo_bpm and tempo_bpm > 0:
        start_s = qn_to_seconds(start_qn, tempo_bpm)
        end_s = qn_to_seconds(end_qn, tempo_bpm)
    start = 0.0 if start_s is None else max(0.0, float(start_s))
    end = buffer.duration_s if end_s is None else min(buffer.duration_s, float(end_s))
    if end < start:
        end = start
    end_qn_out = end_qn
    if tempo_bpm and tempo_bpm > 0 and end_qn_out is None and start_qn is not None:
        end_qn_out = start_qn + (end - start) * float(tempo_bpm) / 60.0
    return TimeSpan(
        start_s=start,
        end_s=end,
        start_qn=start_qn,
        end_qn=end_qn_out,
        tempo_bpm=tempo_bpm,
        granularity=granularity,
    )


def iter_spans(
    buffer: AudioBuffer,
    *,
    granularities: tuple[DspGranularity, ...] | list[DspGranularity],
    start_s: float | None = None,
    end_s: float | None = None,
    start_qn: float | None = None,
    end_qn: float | None = None,
    tempo_bpm: float | None = None,
    beats_per_bar: int = BEATS_PER_BAR,
    onset_times_s: list[float] | None = None,
) -> tuple[list[TimeSpan], list[DspLimitation]]:
    limitations: list[DspLimitation] = []
    wanted = list(granularities)
    spans: list[TimeSpan] = []
    base = region_span(
        buffer,
        start_s=start_s,
        end_s=end_s,
        start_qn=start_qn,
        end_qn=end_qn,
        tempo_bpm=tempo_bpm,
        granularity=DspGranularity.REGION,
    )
    for gran in wanted:
        if gran in {DspGranularity.TRACK, DspGranularity.SOURCE}:
            track = whole_span(
                buffer,
                granularity=gran,
                tempo_bpm=tempo_bpm,
                start_qn=0.0 if start_qn is None else start_qn,
            )
            spans.append(track)
        elif gran == DspGranularity.REGION:
            spans.append(base)
        elif gran in {DspGranularity.BAR, DspGranularity.BEAT}:
            if not tempo_bpm or tempo_bpm <= 0:
                limitations.append(
                    DspLimitation(
                        "ABLETON_TIMING_NOT_PROVIDED",
                        f"{gran.value} slices require tempo_bpm. Live is not required if tempo is supplied.",
                    )
                )
                continue
            beat_s = 60.0 / float(tempo_bpm)
            step = beat_s * float(beats_per_bar) if gran is DspGranularity.BAR else beat_s
            t0 = base.start_s
            qn0 = base.start_qn if base.start_qn is not None else 0.0
            idx = 0
            while t0 < base.end_s - 1e-9:
                t1 = min(base.end_s, t0 + step)
                qn_step = step * float(tempo_bpm) / 60.0
                spans.append(
                    TimeSpan(
                        start_s=t0,
                        end_s=t1,
                        start_qn=qn0 + idx * qn_step,
                        end_qn=qn0 + (idx + 1) * qn_step,
                        tempo_bpm=tempo_bpm,
                        granularity=gran,
                    )
                )
                t0 = t1
                idx += 1
        elif gran == DspGranularity.EVENT:
            times = list(onset_times_s or [])
            if not times:
                limitations.append(DspLimitation("EVENT_GRANULARITY_NEEDS_ONSETS", ""))
                continue
            if len(times) > EVENT_SLICE_CAP:
                times = times[:EVENT_SLICE_CAP]
                limitations.append(
                    DspLimitation("EVENT_SLICE_CAPPED", f"first {EVENT_SLICE_CAP} onsets")
                )
            half = EVENT_WINDOW_S / 2.0
            for t in times:
                spans.append(
                    TimeSpan(
                        start_s=max(base.start_s, t - half),
                        end_s=min(base.end_s, t + half),
                        start_qn=None,
                        end_qn=None,
                        tempo_bpm=tempo_bpm,
                        granularity=DspGranularity.EVENT,
                    )
                )
        elif gran == DspGranularity.SOURCE_PAIR:
            pair = base.model_copy(update={"granularity": DspGranularity.SOURCE_PAIR})
            spans.append(pair)
    return spans, limitations
