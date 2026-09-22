"""ACE-Step 1.5 provider boundary.

The official ACE-Step repository exposes a local HTTP worker
(``/release_task`` and ``/query_result``), not the Diffusers pipeline that
older examples used.  This provider therefore talks to an explicitly
configured worker and never downloads weights, opens Ableton, or silently
falls back to an LLM.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
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
    """Describe an official profile without presenting it as the quality tier."""
    if vram_gb is None:
        return {"tier": "CPU_OR_UNKNOWN", "dit": "acestep-v15-turbo", "lm": None, "offload": True}
    if vram_gb <= 6:
        return {
            "tier": "DIT_ONLY",
            "dit": "acestep-v15-turbo",
            "lm": None,
            "offload": True,
            "quantization": "INT8",
        }
    if vram_gb <= 8:
        return {
            "tier": "LOW_VRAM",
            "dit": "acestep-v15-turbo",
            "lm": "acestep-5Hz-lm-0.6B",
            "offload": True,
            "quantization": "INT8",
        }
    if vram_gb < 12:
        return {
            "tier": "MID_VRAM",
            "dit": "acestep-v15-turbo",
            "lm": "acestep-5Hz-lm-0.6B",
            "offload": True,
            "quantization": "INT8",
        }
    return {"tier": "HIGHER_VRAM", "dit": "acestep-v15-turbo", "lm": "acestep-5Hz-lm-1.7B", "offload": False}


class AceStepProvider:
    provider_id = "ace-step"

    def __init__(self, *, model_path: str | Path | None = None, api_url: str | None = None) -> None:
        # model_path remains in the manifest for provenance, but the supported
        # runtime boundary is the official API worker.  Direct model imports
        # would duplicate ACE-Step's lifecycle and make health unverifiable.
        configured_model_path = model_path or os.environ.get("ACESTEP_MODEL_PATH")
        self.model_path = Path(configured_model_path) if configured_model_path else None
        self.api_url = (api_url or os.environ.get("ACESTEP_API_URL", "")).rstrip("/")
        self.api_key = os.environ.get("ACESTEP_API_KEY")
        self.timeout_s = float(os.environ.get("ACESTEP_TIMEOUT_S", "1800"))
        self.profile = choose_acestep_profile(detect_gpu_vram_gb())

    def _model_manifest(self) -> ModelManifest:
        return ModelManifest(
            provider=self.provider_id,
            model_id="ACE-Step/Ace-Step1.5",
            revision=os.environ.get("ACESTEP_MODEL_REVISION"),
            checkpoint_path=str(self.model_path) if self.model_path is not None else None,
            license="MIT",
            license_source="https://huggingface.co/ACE-Step/Ace-Step1.5",
            quantization=self.profile.get("quantization"),
            quality_tier=os.environ.get("ACESTEP_QUALITY_TIER", "LOCAL_COST_TIER"),
            compute_tier=os.environ.get("ACESTEP_COMPUTE_TIER", "LOCAL_WINDOWS"),
            benchmark_role=os.environ.get("ACESTEP_BENCHMARK_ROLE", "DEV_ONLY"),
        )

    def describe(self) -> GeneratorDescriptor:
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
            health=self.health(),
            local_or_remote="LOCAL_WORKER",
            hardware_requirements={
                "profile": self.profile,
                "detected_vram_gb": detect_gpu_vram_gb(),
                "checkpoint_required": True,
                "quality_tier_is_not_local_hardware_ceiling": True,
            },
            rights_classification=RightsClassification.COMMERCIAL_ALLOWED,
            runtime="official-acestep-api-worker",
        )

    def health(self) -> GeneratorHealth:
        if not self.api_url:
            return GeneratorHealth.UNAVAILABLE
        try:
            payload = self._request_json("GET", "/health", None, timeout=5.0)
            return GeneratorHealth.HEALTHY if payload is not None else GeneratorHealth.CONFIGURED
        except (OSError, ValueError, urllib.error.URLError):
            return GeneratorHealth.CONFIGURED

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None, *, timeout: float) -> Any:
        if not self.api_url:
            raise OSError("ACESTEP_API_URL is not configured")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(f"{self.api_url}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _request_multipart_audio(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        field_name: str,
        file_path: Path,
        timeout: float,
    ) -> Any:
        """Submit an audio-conditioned request through the official upload field."""
        boundary = f"----copilot-{hashlib.sha256(file_path.read_bytes()[:4096]).hexdigest()[:20]}"
        chunks: list[bytes] = []
        for key, value in payload.items():
            if value is None:
                continue
            if isinstance(value, bool):
                value = "true" if value else "false"
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ])
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"\r\n'.encode(),
            b"Content-Type: audio/wav\r\n\r\n",
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        request = urllib.request.Request(
            f"{self.api_url}{path}",
            data=b"".join(chunks),
            headers={"Accept": "application/json", "Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _failure(self, request: GeneratorRequest, code: GeneratorFailureCode, **details: Any) -> GenerationBatch:
        unavailable = {
            GeneratorFailureCode.MODEL_MISSING,
            GeneratorFailureCode.RUNTIME_UNAVAILABLE,
            GeneratorFailureCode.DEPENDENCY_MISSING,
            GeneratorFailureCode.CREDENTIAL_REQUIRED,
        }
        return GenerationBatch(
            batch_id=request.request_id,
            status="GENERATOR_UNAVAILABLE" if code in unavailable else "INFERENCE_FAILED",
            provider=self.provider_id,
            model=request.model_manifest or self._model_manifest(),
            brief_id=request.brief.brief_id,
            failures=[{"code": code.value, **details}],
        )

    @staticmethod
    def _unwrap_response(payload: Any) -> Any:
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    def _submit(self, request: GeneratorRequest) -> str:
        brief = request.brief
        payload: dict[str, Any] = {
            "prompt": brief.user_intent,
            "lyrics": brief.lyrics or "[Instrumental]",
            "thinking": False,
            "model": self.profile.get("dit", "acestep-v15-turbo"),
            "bpm": int(brief.tempo_bpm) if brief.tempo_bpm else None,
            "key_scale": brief.key_context or "",
            "time_signature": brief.meter or "",
            "audio_duration": brief.target_duration_s,
            "inference_steps": int(request.settings.get("inference_steps", 8)),
            "use_random_seed": False,
            "seed": request.seed,
            "audio_format": "wav",
            "task_type": brief.generation_mode,
            "use_cot_caption": False,
            "use_cot_language": False,
            "is_format_caption": False,
        }
        if request.source_audio_path is not None:
            payload.update({
                "src_audio_path": str(request.source_audio_path),
                "repainting_start": request.edit_region_start_s or 0.0,
                "repainting_end": request.edit_region_end_s,
                "repaint_mode": request.settings.get("repaint_mode", "balanced"),
                "repaint_strength": float(request.settings.get("repaint_strength", 0.5)),
                "repaint_latent_crossfade_frames": int(request.settings.get("repaint_latent_crossfade_frames", 10)),
                "repaint_wav_crossfade_sec": float(request.settings.get("repaint_wav_crossfade_sec", 0.0)),
            })
        if self.api_key:
            payload["ai_token"] = self.api_key
        if request.source_audio_path is not None:
            upload_payload = dict(payload)
            upload_payload.pop("src_audio_path", None)
            response = self._request_multipart_audio(
                "/release_task", upload_payload, field_name="src_audio",
                file_path=Path(request.source_audio_path), timeout=30.0,
            )
        else:
            response = self._request_json("POST", "/release_task", payload, timeout=30.0)
        value = self._unwrap_response(response)
        task_id = value.get("task_id") if isinstance(value, dict) else None
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("ACE-Step worker did not return task_id")
        return task_id

    def _wait_for_result(self, task_id: str) -> Any:
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            payload: dict[str, Any] = {"task_id_list": [task_id]}
            if self.api_key:
                payload["ai_token"] = self.api_key
            response = self._request_json("POST", "/query_result", payload, timeout=30.0)
            value = self._unwrap_response(response)
            item = value[0] if isinstance(value, list) and value else value
            if isinstance(item, dict):
                status = item.get("status")
                if status in (1, "1", "succeeded", "SUCCESS"):
                    return item
                if status in (2, "2", "failed", "FAILED"):
                    raise RuntimeError(str(item.get("error") or item))
            time.sleep(1.0)
        raise TimeoutError(f"ACE-Step task timed out after {self.timeout_s:.0f}s")

    @classmethod
    def _result_audio_source(cls, payload: Any) -> str | None:
        value = cls._unwrap_response(payload)
        if isinstance(value, dict):
            for key in ("file", "audio_path", "path", "url", "audio_url"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate:
                    return candidate
            return cls._result_audio_source(value.get("result"))
        if isinstance(value, list):
            for item in value:
                source = cls._result_audio_source(item)
                if source:
                    return source
        if isinstance(value, str):
            try:
                return cls._result_audio_source(json.loads(value))
            except json.JSONDecodeError:
                return value if value.lower().endswith((".wav", ".flac", ".mp3", ".opus")) else None
        return None

    def _download_or_copy_audio(self, source: str, destination: Path) -> None:
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme in {"http", "https"}:
            request = urllib.request.Request(source, headers={"Accept": "audio/*"})
            with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output)
            return
        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(source)
        shutil.copyfile(source_path, destination)

    def generate(self, request: GeneratorRequest) -> GenerationBatch:
        model = request.model_manifest or self._model_manifest()
        if self.health() is not GeneratorHealth.HEALTHY:
            code = GeneratorFailureCode.RUNTIME_UNAVAILABLE if self.api_url else GeneratorFailureCode.MODEL_MISSING
            return self._failure(
                request,
                code,
                health=self.health().value,
                profile=self.profile,
                api_url_configured=bool(self.api_url),
            )
        started = perf_counter()
        try:
            import soundfile as sf

            request.output_dir.mkdir(parents=True, exist_ok=True)
            task_id = self._submit(request)
            result = self._wait_for_result(task_id)
            source = self._result_audio_source(result)
            if not source:
                raise ValueError("ACE-Step worker returned no audio path")
            if source.startswith("/") and self.api_url:
                # The official worker may return either a relative download
                # URL or a filesystem path.  Preserve an already formed
                # ``/v1/audio`` URL; wrapping it a second time causes a 403.
                if source.startswith("/v1/"):
                    source = f"{self.api_url}{source}"
                else:
                    source = f"{self.api_url}/v1/audio?path={urllib.parse.quote(source, safe='')}"
            path = request.output_dir / f"{request.request_id}.wav"
            self._download_or_copy_audio(source, path)
            info = sf.info(path)
            array, sample_rate = sf.read(path, always_2d=True)
            non_silent = bool(float((array * array).mean()) > 1e-10)
            asset = GeneratedAsset(
                asset_id=f"{self.provider_id}:{request.request_id}",
                path=path,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bytes=path.stat().st_size,
                duration_s=float(info.duration),
                sample_rate=int(sample_rate),
                non_silent=non_silent,
                model=model,
                seed=request.seed,
                prompt=request.brief.user_intent,
                performance=PerformanceManifest(
                    device="official-ace-step-worker",
                    precision=self.profile.get("quantization"),
                    latency_s=perf_counter() - started,
                    actual_duration_s=float(info.duration),
                    sample_rate=int(sample_rate),
                ),
                rights_manifest=request.brief.rights_manifest,
                lineage=list(request.lineage),
            )
            if not non_silent:
                return GenerationBatch(
                    batch_id=request.request_id,
                    status="OUTPUT_INVALID",
                    provider=self.provider_id,
                    model=model,
                    brief_id=request.brief.brief_id,
                    failures=[{"code": GeneratorFailureCode.OUTPUT_INVALID.value}],
                    assets=[asset],
                )
            return GenerationBatch(
                batch_id=request.request_id,
                status="GENERATED",
                provider=self.provider_id,
                model=model,
                brief_id=request.brief.brief_id,
                assets=[asset],
            )
        except Exception as exc:
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, type=type(exc).__name__, error=str(exc))

    def cancel(self, request_id: str) -> None:
        # ACE-Step's public API has no cancellation endpoint.  Do not kill the
        # shared worker: that could corrupt unrelated jobs.
        del request_id

    def repaint_asset(
        self,
        parent: GeneratedAsset,
        *,
        brief: Any,
        output_dir: Path,
        seed: int,
        start_s: float,
        end_s: float,
        settings: dict[str, Any] | None = None,
    ) -> GenerationBatch:
        """Run ACE-Step's official repaint task against a local parent asset."""
        source = Path(parent.path)
        if not source.is_file():
            return self._failure(
                GeneratorRequest(request_id="missing-parent", brief=brief, seed=seed, output_dir=output_dir),
                GeneratorFailureCode.OUTPUT_INVALID,
                reason="PARENT_ASSET_MISSING",
            )
        if start_s < 0 or end_s <= start_s:
            raise ValueError("invalid repaint region")
        request = GeneratorRequest(
            request_id=f"{parent.asset_id.replace(':', '-')}-repaint-{int(start_s)}-{int(end_s)}",
            brief=brief.model_copy(update={"generation_mode": "repaint"}),
            seed=seed,
            output_dir=output_dir,
            source_audio_path=source,
            edit_region_start_s=start_s,
            edit_region_end_s=end_s,
            settings=settings or {},
            lineage=[parent.asset_id, parent.sha256, f"repaint:{start_s}-{end_s}s"],
        )
        return self.generate(request)
