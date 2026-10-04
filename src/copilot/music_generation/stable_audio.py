"""Stable Audio 3 Medium through an explicitly configured GPU worker.

Core never imports the model runtime and this provider has no DAW access.
The worker must attest its pinned official repository revision on every result.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from time import perf_counter

import numpy as np
import soundfile as sf

from copilot.music_generation.benchmark import validate_generated_audio
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

MODEL_ID = "medium"
REPOSITORY_REVISION = "3a82c807b69cf4b7c5c05270011a5d5e47abac18"
LICENSE_SOURCE = "https://github.com/Stability-AI/stable-audio-3#license"
MAX_RESPONSE_BYTES = 128 * 1024 * 1024


def build_stable_audio_prompt(request: GeneratorRequest) -> str:
    brief = request.brief
    lines = [brief.user_intent.strip()]
    if not lines[0]:
        raise ValueError("PROMPT_REQUIRED")
    if brief.tempo_bpm:
        lines.append(f"Target tempo: {brief.tempo_bpm:g} BPM.")
    if brief.meter:
        lines.append(f"Meter: {brief.meter}.")
    if brief.key_context:
        lines.append(f"Tonal context: {brief.key_context}.")
    if brief.instrumental:
        lines.append("Instrumental; no vocals.")
    for label, value in (
        ("Groove", brief.groove_intent),
        ("Energy", brief.energy_intent),
        ("Density", brief.density_intent),
    ):
        if value:
            lines.append(f"{label}: {value}.")
    if brief.structural_intent:
        lines.append("Structure: " + "; ".join(brief.structural_intent) + ".")
    if brief.negative_constraints:
        lines.append("Avoid: " + "; ".join(brief.negative_constraints) + ".")
    return "\n".join(lines)


class StableAudio3Provider:
    provider_id = "stable-audio-3"

    def __init__(
        self, *, api_url: str | None = None, api_key: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.api_url = (api_url if api_url is not None else os.environ.get("STABLE_AUDIO_API_URL", "")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("STABLE_AUDIO_API_KEY")
        self.timeout_s = float(timeout_s if timeout_s is not None else os.environ.get("STABLE_AUDIO_TIMEOUT_S", "900"))

    def _manifest(self) -> ModelManifest:
        return ModelManifest(
            provider=self.provider_id, model_id=MODEL_ID, revision=REPOSITORY_REVISION,
            license="Stability AI Community License", license_source=LICENSE_SOURCE,
            quality_tier="UNBENCHMARKED", compute_tier="REMOTE_GPU",
            benchmark_role="NOT_BENCHMARKED",
        )

    def describe(self) -> GeneratorDescriptor:
        return GeneratorDescriptor(
            provider_id=self.provider_id, model=self._manifest(),
            capabilities=[
                GeneratorCapability.TEXT_TO_MUSIC,
                GeneratorCapability.REMOTE_INFERENCE,
                GeneratorCapability.INSTRUMENTAL,
            ],
            health=self.health(), local_or_remote="REMOTE_WORKER",
            hardware_requirements={"worker": "CUDA with Flash Attention; exact fit measured on worker"},
            rights_classification=RightsClassification.UNKNOWN,
            runtime="official-stable-audio-3-worker",
        )

    def _endpoint(self, path: str) -> str:
        if not self.api_url:
            raise ValueError("STABLE_AUDIO_API_URL_REQUIRED")
        parsed = urllib.parse.urlsplit(self.api_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("STABLE_AUDIO_API_URL_INVALID")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("STABLE_AUDIO_API_URL_INVALID")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("STABLE_AUDIO_REMOTE_TLS_REQUIRED")
        return f"{self.api_url}{path}"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, audio/wav"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def health(self) -> GeneratorHealth:
        if not self.api_url:
            return GeneratorHealth.UNAVAILABLE
        try:
            req = urllib.request.Request(self._endpoint("/health"), headers=self._headers())
            with urllib.request.urlopen(req, timeout=min(self.timeout_s, 5.0)) as response:
                payload = json.loads(response.read(8192).decode("utf-8"))
            if (payload.get("ready") is True and payload.get("model") == MODEL_ID
                    and payload.get("revision") == REPOSITORY_REVISION):
                return GeneratorHealth.HEALTHY
        except (OSError, ValueError, TimeoutError, urllib.error.URLError):
            pass
        return GeneratorHealth.CONFIGURED

    def _failure(self, request: GeneratorRequest, code: GeneratorFailureCode, reason: str) -> GenerationBatch:
        return GenerationBatch(
            batch_id=request.request_id,
            status="GENERATOR_UNAVAILABLE" if code == GeneratorFailureCode.RUNTIME_UNAVAILABLE else "INFERENCE_FAILED",
            provider=self.provider_id, model=self._manifest(), brief_id=request.brief.brief_id,
            failures=[{"code": code.value, "reason": reason}],
        )

    def generate(self, request: GeneratorRequest) -> GenerationBatch:
        if not self.api_url:
            return self._failure(request, GeneratorFailureCode.RUNTIME_UNAVAILABLE, "WORKER_NOT_CONFIGURED")
        if request.no_ableton_access is not True or request.brief.no_write is not True:
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, "GENERATION_BOUNDARY_REQUIRED")
        if request.brief.generation_mode != "text_to_music" or request.source_audio_path is not None:
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, "TEXT_TO_MUSIC_ONLY")
        if request.brief.instrumental is not True:
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, "INSTRUMENTAL_ONLY")
        if request.model_manifest is not None and (
            request.model_manifest.provider != self.provider_id
            or
            request.model_manifest.model_id != MODEL_ID
            or request.model_manifest.revision not in {None, REPOSITORY_REVISION}
        ):
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, "MODEL_MISMATCH")
        if set(request.settings) - {"inference_steps"}:
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, "UNSUPPORTED_CONTROL")
        steps = request.settings.get("inference_steps", 8)
        if type(steps) is not int or not 1 <= steps <= 100:
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, "INVALID_STEPS")
        if not 1 <= request.brief.target_duration_s <= 380:
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, "INVALID_DURATION")
        if request.seed < 0:
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, "EXPLICIT_SEED_REQUIRED")
        try:
            url = self._endpoint("/generate")
            prompt = build_stable_audio_prompt(request)
            output = Path(request.output_dir)
            path = output / f"stable_audio_3_{hashlib.sha256(request.request_id.encode()).hexdigest()[:20]}.wav"
            if path.exists():
                return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, "OUTPUT_ALREADY_EXISTS")
            payload = {
                "prompt": prompt, "duration_s": request.brief.target_duration_s,
                "seed": request.seed, "inference_steps": steps, "model": MODEL_ID,
            }
            headers = {**self._headers(), "Content-Type": "application/json"}
            started = perf_counter()
            http_request = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
            )
            with urllib.request.urlopen(http_request, timeout=self.timeout_s) as response:
                if response.headers.get("Content-Type", "").split(";", 1)[0].lower() != "audio/wav":
                    return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, "WORKER_RESPONSE_NOT_WAV")
                metadata = {
                    "model": response.headers.get("X-Stable-Audio-Model"),
                    "revision": response.headers.get("X-Stable-Audio-Revision"),
                    "seed": response.headers.get("X-Stable-Audio-Seed"),
                    "steps": response.headers.get("X-Stable-Audio-Steps"),
                    "sample_rate": response.headers.get("X-Stable-Audio-Sample-Rate"),
                    "worker_id": response.headers.get("X-Stable-Audio-Worker-Id"),
                    "worker_elapsed_s": response.headers.get("X-Stable-Audio-Elapsed-S"),
                }
                if (metadata["model"] != MODEL_ID or metadata["revision"] != REPOSITORY_REVISION
                        or metadata["seed"] != str(request.seed) or metadata["steps"] != str(steps)
                        or metadata["sample_rate"] != "44100" or not metadata["worker_id"]):
                    return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, "WORKER_ATTESTATION_MISMATCH")
                if int(response.headers.get("Content-Length", "0")) > MAX_RESPONSE_BYTES:
                    return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, "WAV_TOO_LARGE")
                wav = response.read(MAX_RESPONSE_BYTES + 1)
            if len(wav) > MAX_RESPONSE_BYTES or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
                return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, "INVALID_WAV_BYTES")
            output.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(wav)
            info = sf.info(path)
            if int(info.samplerate) != 44100 or int(info.channels) != 2:
                return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, "WAV_FORMAT_MISMATCH")
            data, _ = sf.read(path, always_2d=True, dtype="float32")
            non_silent = bool(np.isfinite(data).all() and np.any(np.abs(data) > 1e-6))
            rights = request.brief.rights_manifest.model_copy(update={
                "output_use": RightsClassification.UNKNOWN,
                "provenance": [*request.brief.rights_manifest.provenance, LICENSE_SOURCE],
            })
            asset = GeneratedAsset(
                asset_id=f"{self.provider_id}:{request.request_id}",
                path=path, sha256=hashlib.sha256(wav).hexdigest(), bytes=len(wav),
                duration_s=float(info.duration), sample_rate=int(info.samplerate),
                non_silent=non_silent, model=self._manifest(), seed=request.seed, prompt=prompt,
                performance=PerformanceManifest(
                    device="stable-audio-3-gpu-worker", latency_s=perf_counter() - started,
                    actual_duration_s=float(info.duration), sample_rate=int(info.samplerate),
                ),
                rights_manifest=rights, lineage=list(request.lineage),
                provider_request=payload, provider_metadata=metadata,
            )
            validation = validate_generated_audio(asset, expected_duration_s=request.brief.target_duration_s)
            if validation.status != "VALID":
                return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, ",".join(validation.reasons))
            return GenerationBatch(
                batch_id=request.request_id, status="GENERATED", provider=self.provider_id,
                model=asset.model, assets=[asset], brief_id=request.brief.brief_id,
            )
        except FileExistsError:
            return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, "OUTPUT_ALREADY_EXISTS")
        except urllib.error.HTTPError as exc:
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, f"WORKER_HTTP_{exc.code}")
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            return self._failure(request, GeneratorFailureCode.RUNTIME_UNAVAILABLE, type(exc).__name__)
        except (ValueError, RuntimeError) as exc:
            return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, type(exc).__name__)

    def cancel(self, request_id: str) -> None:
        # Synchronous worker API has no safe cancellation endpoint.
        del request_id
