"""Measure PHYSICAL_DSP_V2 CPU/wall/cache on synthetic audio. No Live numbers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from copilot.audio.physical_dsp_v2.audio import buffer_from_samples
from copilot.audio.physical_dsp_v2.contract import ANALYZER_VERSION, CAPABILITY_ID
from copilot.audio.physical_dsp_v2.fixtures import impulse_train, mixture
from copilot.audio.physical_dsp_v2.pipeline import analyze_buffer, analyze_pair
from copilot.audio.physical_dsp_v2.providers import provider_inventory
from copilot.schemas.dsp import DspGranularity


def measure_baseline(*, cache_dir: Path, repeats: int = 2) -> dict[str, Any]:
    sr = 44100
    mix = mixture(sr, 2.0, [(110.0, 0.2), (1000.0, 0.1), (8000.0, 0.05)])
    pulses = impulse_train(sr, 2.0, 0.5)
    buf = buffer_from_samples(mix, sr)
    other = buffer_from_samples(pulses, sr)
    first = analyze_buffer(buf, use_cache=True, cache_dir=cache_dir, tempo_bpm=120.0)
    second = analyze_buffer(buf, use_cache=True, cache_dir=cache_dir, tempo_bpm=120.0)
    gran = analyze_buffer(
        buf,
        use_cache=False,
        cache_dir=cache_dir,
        tempo_bpm=120.0,
        granularities=(DspGranularity.REGION, DspGranularity.BEAT),
    )
    pair = analyze_pair(buf, other, use_cache=True, cache_dir=cache_dir)
    extra = None
    if repeats > 2:
        extra = analyze_buffer(buf, use_cache=True, cache_dir=cache_dir, tempo_bpm=120.0)
    return {
        "artifact": "physical_dsp_v2",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "capability_id": CAPABILITY_ID,
        "analyzer_version": ANALYZER_VERSION,
        "environment": {
            "note": "Synthetic 2 s stereo mixture + impulse train. Not a Live capture measurement.",
            "sample_rate": sr,
            "duration_s": 2.0,
            "providers": [
                {
                    "provider_id": row.provider_id,
                    "available": row.available,
                    "version": row.version,
                    "limitation": None if row.limitation is None else row.limitation.code,
                }
                for row in provider_inventory()
            ],
        },
        "measurements": {
            "first_pass_cache_miss": {
                "wall_s": first.wall_s,
                "cpu_s": first.cpu_s,
                "cache_hits": first.cache_hits,
                "cache_misses": first.cache_misses,
                "observation_count": len(first.observations),
            },
            "second_pass_cache_hit": {
                "wall_s": second.wall_s,
                "cpu_s": second.cpu_s,
                "cache_hits": second.cache_hits,
                "cache_misses": second.cache_misses,
                "observation_count": len(second.observations),
            },
            "region_plus_beat_granularity": {
                "wall_s": gran.wall_s,
                "cpu_s": gran.cpu_s,
                "observation_count": len(gran.observations),
            },
            "relational_pair": {
                "wall_s": pair.wall_s,
                "cpu_s": pair.cpu_s,
                "observation_count": len(pair.observations),
            },
        },
        "repeats": repeats,
        "third_pass": None
        if extra is None
        else {"wall_s": extra.wall_s, "cpu_s": extra.cpu_s, "cache_hits": extra.cache_hits},
    }


def write_baseline(path: Path, *, cache_dir: Path) -> dict[str, Any]:
    payload = measure_baseline(cache_dir=cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    payload["artifact_path"] = str(path)
    return payload
