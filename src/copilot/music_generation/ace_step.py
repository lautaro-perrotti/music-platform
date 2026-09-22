"""ACE-Step 1.5 adapter with truthful lazy local inference.

The adapter never downloads weights, opens Ableton, or silently falls back to
an LLM.  It becomes healthy only when the isolated runtime, checkpoint and
hardware are all observable.  Generation uses the official Diffusers
``AceStepPipeline`` API when those prerequisites exist.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
from time import perf_counter
from typing import Any

from copilot.music_generation.schemas import (
    GeneratedAsset,
    GenerationBatch,
    GeneratorCapability,
    GeneratorDescriptor,
    GeneratorFailureCode,
    GeneratorHealth,
    GeneratorRequest,
    ModelManifest,
    PerformanceManifest,
    RightsClassification,
)


def detect_gpu_vram_gb() -> float | None:
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        return float(torch.cuda.get_device_properties(0).total_memory / (1024**3))
    except Exception:
        return None


def choose_acestep_profile(vram_gb: float | None) -> dict[str, Any]:
    """Select the official low-VRAM profile without pretending it is XL."""
    if vram_gb is None:
        return {"tier": "CPU_OR_UNKNOWN", "dit": "acestep-v15-turbo", "lm": None, "offload": True}
    if vram_gb <= 6:
        return {"tier": "DIT_ONLY", "dit": "acestep-v15-turbo", "lm": None, "offload": True, "quantization": "INT8"}
    if vram_gb <= 8:
        return {"tier": "LOW_VRAM", "dit": "acestep-v15-turbo", "lm": "acestep-5Hz-lm-0.6B", "offload": True, "quantization": "INT8"}
    if vram_gb < 12:
        return {"tier": "MID_VRAM", "dit": "acestep-v15-turbo", "lm": "acestep-5Hz-lm-0.6B", "offload": True, "quantization": "INT8"}
    return {"tier": "HIGHER_VRAM", "dit": "acestep-v15-turbo", "lm": "acestep-5Hz-lm-1.7B", "offload": False}


class AceStepProvider:
    provider_id = "ace-step"

    def __init__(self, *, model_path: str | Path | None = None) -> None:
        self.model_path = Path(model_path or os.environ.get("ACESTEP_MODEL_PATH", ""))
        self.profile = choose_acestep_profile(detect_gpu_vram_gb())
        self._pipeline: Any = None

    def _model_manifest(self) -> ModelManifest:
        return ModelManifest(
            provider=self.provider_id,
            model_id="ACE-Step/Ace-Step1.5",
            revision=os.environ.get("ACESTEP_MODEL_REVISION"),
            checkpoint_path=str(self.model_path) if self.model_path else None,
            license="MIT",
            license_source="https://huggingface.co/ACE-Step/Ace-Step1.5",
            quantization=self.profile.get("quantization"),
        )

    def describe(self) -> GeneratorDescriptor:
        health = self.health()
        return GeneratorDescriptor(
            provider_id=self.provider_id,
            model=self._model_manifest(),
            capabilities=[
                GeneratorCapability.TEXT_TO_MUSIC,
                GeneratorCapability.LOCAL_INFERENCE,
                GeneratorCapability.INSTRUMENTAL,
                GeneratorCapability.VOCALS,
                GeneratorCapability.LONG_FORM,
                GeneratorCapability.REPAINT,
                GeneratorCapability.COVER,
                GeneratorCapability.AUDIO_TO_AUDIO,
            ],
            health=health,
            local_or_remote="LOCAL",
            hardware_requirements={"profile": self.profile, "detected_vram_gb": detect_gpu_vram_gb(), "checkpoint_required": True},
            rights_classification=RightsClassification.COMMERCIAL_ALLOWED,
            runtime="lazy-diffusers-isolated-boundary",
        )

    def health(self) -> GeneratorHealth:
        if not self.model_path or not self.model_path.exists():
            return GeneratorHealth.UNAVAILABLE
        if importlib.util.find_spec("diffusers") is None:
            return GeneratorHealth.CONFIGURED
        if self.profile.get("tier") == "CPU_OR_UNKNOWN":
            return GeneratorHealth.CONFIGURED
        return GeneratorHealth.HEALTHY

    def generate(self, request: GeneratorRequest) -> GenerationBatch:
        model = request.model_manifest or self._model_manifest()
        health = self.health()
        if health is not GeneratorHealth.HEALTHY:
            code = GeneratorFailureCode.MODEL_MISSING if not self.model_path.exists() else GeneratorFailureCode.DEPENDENCY_MISSING
            return GenerationBatch(batch_id=request.request_id, status="GENERATOR_UNAVAILABLE", provider=self.provider_id, model=model, brief_id=request.brief.brief_id, failures=[{"code": code.value, "health": health.value, "profile": self.profile}])
        started = perf_counter()
        try:
            import torch
            import soundfile as sf
            from diffusers import AceStepPipeline

            if self._pipeline is None:
                self._pipeline = AceStepPipeline.from_pretrained(str(self.model_path), torch_dtype=torch.float16)
                if hasattr(self._pipeline, "enable_model_cpu_offload"):
                    self._pipeline.enable_model_cpu_offload()
                else:
                    self._pipeline = self._pipeline.to("cuda")
            request.output_dir.mkdir(parents=True, exist_ok=True)
            generator = torch.Generator(device="cpu").manual_seed(request.seed)
            result = self._pipeline(
                prompt=request.brief.user_intent,
                lyrics=request.brief.lyrics or "",
                audio_duration=request.brief.target_duration_s,
                num_inference_steps=int(request.settings.get("num_inference_steps", 8)),
                bpm=int(request.brief.tempo_bpm) if request.brief.tempo_bpm else None,
                keyscale=request.brief.key_context,
                timesignature=request.brief.meter,
                generator=generator,
            )
            audio = result.audios[0]
            array = audio.detach().cpu().float().numpy() if hasattr(audio, "detach") else audio
            if getattr(array, "ndim", 1) == 2 and array.shape[0] <= 2:
                array = array.T
            path = request.output_dir / f"{request.request_id}.wav"
            sf.write(path, array, 48_000)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            non_silent = bool(float((array * array).mean()) > 1e-10)
            asset = GeneratedAsset(
                asset_id=f"{self.provider_id}:{request.request_id}",
                path=path,
                sha256=digest,
                bytes=path.stat().st_size,
                duration_s=float(len(array) / 48_000),
                sample_rate=48_000,
                non_silent=non_silent,
                model=model,
                seed=request.seed,
                prompt=request.brief.user_intent,
                performance=PerformanceManifest(device="cuda", precision="float16", latency_s=perf_counter() - started, actual_duration_s=float(len(array) / 48_000), sample_rate=48_000),
                rights_manifest=request.brief.rights_manifest,
            )
            if not non_silent:
                return GenerationBatch(batch_id=request.request_id, status="OUTPUT_INVALID", provider=self.provider_id, model=model, brief_id=request.brief.brief_id, failures=[{"code": GeneratorFailureCode.OUTPUT_INVALID.value}], assets=[asset])
            return GenerationBatch(batch_id=request.request_id, status="GENERATED", provider=self.provider_id, model=model, brief_id=request.brief.brief_id, assets=[asset])
        except Exception as exc:
            return GenerationBatch(batch_id=request.request_id, status="INFERENCE_FAILED", provider=self.provider_id, model=model, brief_id=request.brief.brief_id, failures=[{"code": GeneratorFailureCode.INFERENCE_FAILED.value, "type": type(exc).__name__}])

    def cancel(self, request_id: str) -> None:
        del request_id
        self._pipeline = None
