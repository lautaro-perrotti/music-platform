from __future__ import annotations

import hashlib
import math
import os
import wave
from pathlib import Path

from copilot.music_generation.schemas import (
    GeneratedAsset,
    GenerationBatch,
    GeneratorCapability,
    GeneratorDescriptor,
    GeneratorHealth,
    GeneratorRequest,
    ModelManifest,
    PerformanceManifest,
    RightsClassification,
    RightsManifest,
)


class SimulatedMusicProvider:
    """Deterministic, playable demo provider behind the real generator port."""

    provider_id = "simulated-music"

    def __init__(self, seed_namespace: str = "music-studio") -> None:
        self.seed_namespace = seed_namespace

    def _model(self) -> ModelManifest:
        return ModelManifest(
            provider=self.provider_id,
            model_id="studio-demo-synth-v1",
            revision="deterministic",
            license="PROJECT_DEMO_ONLY",
            quality_tier="SIMULATION",
            compute_tier="LOCAL_CPU",
            benchmark_role="NOT_PRODUCTION",
        )

    def describe(self) -> GeneratorDescriptor:
        return GeneratorDescriptor(
            provider_id=self.provider_id,
            model=self._model(),
            capabilities=[GeneratorCapability.TEXT_TO_MUSIC, GeneratorCapability.INSTRUMENTAL,
                          GeneratorCapability.VOCALS, GeneratorCapability.SECTION_CONTROL,
                          GeneratorCapability.EXTEND, GeneratorCapability.REPAINT],
            health=GeneratorHealth.HEALTHY,
            local_or_remote="SIMULATION",
            rights_classification=RightsClassification.UNKNOWN,
            runtime="studio-simulation",
        )

    def health(self) -> GeneratorHealth:
        return GeneratorHealth.HEALTHY

    def _write_demo_wav(self, path: Path, duration_s: float, seed: int) -> None:
        sample_rate = 16000
        frames = max(1, int(sample_rate * min(max(duration_s, 1.0), 90.0)))
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            block = bytearray()
            for index in range(frames):
                t = index / sample_rate
                beat = (t * 2.0) % 1.0
                kick = math.exp(-beat * 18.0) * math.sin(2 * math.pi * (58 + 20 * math.exp(-beat * 10)) * t)
                bass = math.sin(2 * math.pi * (82 + (seed % 5) * 7) * t) * .18
                hook = math.sin(2 * math.pi * (330 + (seed % 4) * 45) * t) * .08 * (0.3 + 0.7 * math.sin(t * .7) ** 2)
                value = max(-.75, min(.75, kick * .38 + bass + hook))
                sample = int(value * 32767)
                block.extend(sample.to_bytes(2, "little", signed=True))
                block.extend(int(value * .92 * 32767).to_bytes(2, "little", signed=True))
                if len(block) >= 65536:
                    output.writeframes(block)
                    block.clear()
            if block:
                output.writeframes(block)

    def generate(self, request: GeneratorRequest) -> GenerationBatch:
        namespace = os.environ.get("SIMULATION_SEED", self.seed_namespace)
        digest = hashlib.sha256(f"{namespace}:{request.brief.brief_id}:{request.seed}".encode()).hexdigest()
        path = request.output_dir / f"{request.request_id}.wav"
        self._write_demo_wav(path, request.brief.target_duration_s, int(digest[:8], 16))
        model = self._model()
        return GenerationBatch(
            batch_id=request.request_id,
            status="SIMULATED",
            provider=self.provider_id,
            model=model,
            brief_id=request.brief.brief_id,
            assets=[GeneratedAsset(
                asset_id=f"sim:{digest[:20]}", path=path, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bytes=path.stat().st_size, duration_s=min(max(request.brief.target_duration_s, 1.0), 90.0),
                sample_rate=16000, non_silent=True, model=model, seed=request.seed,
                prompt=request.brief.user_intent,
                performance=PerformanceManifest(device="simulation", actual_duration_s=request.brief.target_duration_s, sample_rate=16000),
                rights_manifest=RightsManifest(output_use=RightsClassification.UNKNOWN, provenance=["SIMULATED"]),
                provider_request=request.model_dump(mode="json"),
                provider_metadata={"provenance_mode": "SIMULATED", "simulation_seed": namespace},
            )],
        )

    def cancel(self, request_id: str) -> None:
        return None
