"""Register PHYSICAL_DSP_V2 on a CapabilityRegistry. Does not alter AnalyzeProject sequencing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from copilot.audio.physical_dsp_v2.contract import ANALYZER_VERSION, CAPABILITY_ID, PROVIDER_ID
from copilot.runtime.registry import Capability, CapabilityRegistry, FailureSemantics, LatencyCategory, Provider
from copilot.runtime.resources import ResourceKind, shared

PHYSICAL_DSP_PROVIDER = Provider(PROVIDER_ID, version=ANALYZER_VERSION)


def execute_physical_dsp_v2(ctx: Any, blackboard: dict[str, Any], node: Any) -> dict[str, Any]:
    del ctx, node
    path = blackboard.get("audio_path") or blackboard.get("PHYSICAL_DSP_PATH")
    if not path:
        return {
            "status": "SKIPPED",
            "reason": "NO_AUDIO_REF",
            "capability_id": CAPABILITY_ID,
            "note": "Producer may request PHYSICAL_DSP_V2 later. AnalyzeProject graph is unchanged.",
        }
    from copilot.audio.physical_dsp_v2.pipeline import analyze_path

    tempo = blackboard.get("tempo_bpm")
    start_qn = blackboard.get("start_qn")
    end_qn = blackboard.get("end_qn")
    bundle = analyze_path(
        Path(str(path)),
        tempo_bpm=tempo,
        start_qn=start_qn,
        end_qn=end_qn,
        use_cache=bool(blackboard.get("use_cache", True)),
    )
    blackboard["physical_dsp_v2"] = bundle
    return {
        "status": "OK",
        "capability_id": CAPABILITY_ID,
        "observation_count": len(bundle.observations),
        "wall_s": bundle.wall_s,
        "cpu_s": bundle.cpu_s,
        "cache_hits": bundle.cache_hits,
        "cache_misses": bundle.cache_misses,
        "quality": bundle.quality.value,
    }


def register_physical_dsp_v2(registry: CapabilityRegistry) -> None:
    registry.register(
        Capability(
            CAPABILITY_ID,
            "AudioRef+AnalysisParams",
            "DspBundle[DspObservation]",
            PHYSICAL_DSP_PROVIDER,
            version="2",
            resources=[shared(ResourceKind.CPU_DSP)],
            cache_semantics="audio_hash+analyzer_id+analyzer_version+params",
            failure_semantics=FailureSemantics.FAIL_CLOSED,
            latency_category=LatencyCategory.LOCAL,
            execute=execute_physical_dsp_v2,
        )
    )
