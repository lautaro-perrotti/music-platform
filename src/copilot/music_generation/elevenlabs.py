"""Eleven Music v2.5 provider using the official simple-prompt API.

This boundary creates and validates local artifacts only. It has no Ableton
access and never assigns musical quality or winner status.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any

import numpy as np
import soundfile as sf

from copilot.music_generation.schemas import (
    GeneratedAsset,
    GenerationBatch,
    GenerationBrief,
    GeneratorCapability,
    GeneratorDescriptor,
    GeneratorFailureCode,
    GeneratorHealth,
    GeneratorRequest,
    ModelManifest,
    PerformanceManifest,
    RightsClassification,
    RightsManifest,
)


ELEVENLABS_MUSIC_MODEL = "music_v2_5"
ELEVENLABS_MUSIC_URL = "https://api.elevenlabs.io"
MUSIC_API_DOC = "https://elevenlabs.io/docs/api-reference/music/compose"
STEM_API_DOC = "https://elevenlabs.io/docs/api-reference/music/separate-stems"
MAX_PROMPT_CHARS = 4100
MAX_HTTP_ATTEMPTS = 2
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
MAX_STEM_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_STEM_MEMBER_BYTES = 200 * 1024 * 1024
MAX_STEM_FILES = 12
_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aiff", ".aif"}


def build_elevenlabs_prompt(brief: GenerationBrief) -> str:
    """Map a brief to one conservative plain-text prompt without changing intent."""
    parts = [brief.user_intent.strip()]
    constraints: list[str] = []
    if brief.tempo_bpm:
        constraints.append(f"Target tempo: {brief.tempo_bpm:g} BPM.")
    if brief.meter:
        constraints.append(f"Meter: {brief.meter}.")
    if brief.key_context:
        constraints.append(f"Tonal context: {brief.key_context}.")
    if brief.groove_intent:
        constraints.append(f"Groove: {brief.groove_intent}.")
    if brief.energy_intent:
        constraints.append(f"Energy: {brief.energy_intent}.")
    if brief.density_intent:
        constraints.append(f"Density: {brief.density_intent}.")
    if brief.preserve_constraints:
        constraints.append("Preserve: " + "; ".join(brief.preserve_constraints) + ".")
    if brief.change_constraints:
        constraints.append("Change: " + "; ".join(brief.change_constraints) + ".")
    if brief.negative_constraints:
        constraints.append("Avoid: " + "; ".join(brief.negative_constraints) + ".")
    if brief.lyrics and not brief.instrumental:
        constraints.append(f"Lyrics: {brief.lyrics}")
    if brief.language and not brief.instrumental:
        constraints.append(f"Vocal language: {brief.language}.")
    prompt = "\n".join([*parts, *constraints]).strip()
    if not prompt:
        raise ValueError("PROMPT_REQUIRED")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError("PROMPT_TOO_LONG")
    duration_ms = round(brief.target_duration_s * 1000)
    if not 3000 <= duration_ms <= 600000:
        raise ValueError("DURATION_OUT_OF_RANGE")
    return prompt


def _safe_error_text(value: str, secret: str | None) -> str:
    text = value[:2000]
    return text.replace(secret, "[REDACTED]") if secret else text


class ElevenLabsMusicProvider:
    """Official remote music generator; one GenerationRequest produces one asset."""

    provider_id = "elevenlabs-music"

    def __init__(self, *, api_key: str | None = None, api_url: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("ELEVENLABS_API_KEY")
        self.api_url = (api_url or os.environ.get("ELEVENLABS_API_URL", ELEVENLABS_MUSIC_URL)).rstrip("/")
        self.timeout_s = float(os.environ.get("ELEVENLABS_TIMEOUT_S", "1800"))

    def _model_manifest(self) -> ModelManifest:
        return ModelManifest(
            provider=self.provider_id,
            model_id=ELEVENLABS_MUSIC_MODEL,
            license="ELEVENLABS_MUSIC_TERMS",
            license_source="https://elevenlabs.io/docs/overview/capabilities/music",
            quality_tier="PREMIUM_REMOTE",
            compute_tier="REMOTE_API",
            benchmark_role="PRIMARY_V1_GENERATOR",
        )

    def describe(self) -> GeneratorDescriptor:
        return GeneratorDescriptor(
            provider_id=self.provider_id,
            model=self._model_manifest(),
            capabilities=[
                GeneratorCapability.TEXT_TO_MUSIC,
                GeneratorCapability.REMOTE_INFERENCE,
                GeneratorCapability.INSTRUMENTAL,
                GeneratorCapability.VOCALS,
                GeneratorCapability.LONG_FORM,
                GeneratorCapability.STEM_OUTPUT,
            ],
            health=self.health(),
            local_or_remote="REMOTE_API",
            hardware_requirements={"api_access": "authorized ElevenLabs Music API"},
            rights_classification=RightsClassification.UNKNOWN,
            runtime="elevenlabs-music-api",
        )

    def health(self) -> GeneratorHealth:
        return GeneratorHealth.CONFIGURED if self.api_key else GeneratorHealth.UNAVAILABLE

    def _failure(self, request: GeneratorRequest, code: GeneratorFailureCode, **details: Any) -> GenerationBatch:
        unavailable = {
            GeneratorFailureCode.CREDENTIAL_REQUIRED,
            GeneratorFailureCode.RUNTIME_UNAVAILABLE,
            GeneratorFailureCode.DEPENDENCY_MISSING,
        }
        return GenerationBatch(
            batch_id=request.request_id,
            status="GENERATOR_UNAVAILABLE" if code in unavailable else "INFERENCE_FAILED",
            provider=self.provider_id,
            model=request.model_manifest or self._model_manifest(),
            brief_id=request.brief.brief_id,
            failures=[{"code": code.value, **details}],
        )

    def _post_audio(self, prompt: str, request: GeneratorRequest) -> tuple[bytes, dict[str, Any]]:
        duration_ms = round(request.brief.target_duration_s * 1000)
        query = urllib.parse.urlencode({"output_format": "auto"})
        body = {
            "prompt": prompt,
            "music_length_ms": duration_ms,
            "model_id": ELEVENLABS_MUSIC_MODEL,
            "force_instrumental": bool(request.brief.instrumental),
            "store_for_inpainting": bool(request.settings.get("store_for_inpainting", False)),
            "sign_with_c2pa": False,
        }
        attempts = 0
        while attempts < MAX_HTTP_ATTEMPTS:
            attempts += 1
            http_request = urllib.request.Request(
                f"{self.api_url}/v1/music?{query}",
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Accept": "audio/*",
                    "Content-Type": "application/json",
                    "xi-api-key": self.api_key or "",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(http_request, timeout=self.timeout_s) as response:
                    audio = response.read()
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    headers = {
                        "content_type": content_type,
                        "song_id": response.headers.get("song-id", ""),
                        "request_id": response.headers.get("x-request-id", ""),
                    }
                if not content_type.startswith("audio/") or not audio:
                    raise ValueError("ELEVEN_RESPONSE_NOT_AUDIO")
                return audio, {"attempts": attempts, **headers}
            except urllib.error.HTTPError as exc:
                if exc.code in RETRYABLE_HTTP_STATUS and attempts < MAX_HTTP_ATTEMPTS:
                    time.sleep(min(0.25 * attempts, 0.5))
                    continue
                mapping = {
                    401: "AUTHENTICATION_FAILED",
                    402: "PAYMENT_REQUIRED",
                    403: "PROVIDER_PERMISSION_DENIED",
                    422: "REQUEST_REJECTED",
                    429: "RATE_LIMITED",
                }
                detail = _safe_error_text(exc.read().decode("utf-8", errors="replace"), self.api_key)
                raise ElevenLabsRequestError(mapping.get(exc.code, "HTTP_ERROR"), exc.code, attempts, detail) from exc
            except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
                if attempts < MAX_HTTP_ATTEMPTS:
                    time.sleep(min(0.25 * attempts, 0.5))
                    continue
                raise ElevenLabsRequestError("NETWORK_ERROR", None, attempts, type(exc).__name__) from exc
        raise AssertionError("unreachable retry state")

    @staticmethod
    def _decode_mp3_to_wav(source: Path, target: Path) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise FileNotFoundError("FFMPEG_MISSING")
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-ar", "48000", "-ac", "2", str(target)],
            capture_output=True, timeout=180, check=False,
        )
        if result.returncode:
            raise RuntimeError("AUDIO_DECODE_FAILED: " + result.stderr.decode("utf-8", errors="replace")[-1000:])

    def generate(self, request: GeneratorRequest) -> GenerationBatch:
        if not self.api_key:
            return self._failure(request, GeneratorFailureCode.CREDENTIAL_REQUIRED, reason="ELEVENLABS_API_KEY_MISSING")
        try:
            prompt = build_elevenlabs_prompt(request.brief)
        except ValueError as exc:
            return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, reason=str(exc))
        if request.settings.get("sign_with_c2pa"):
            return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, reason="C2PA_SIGNED_MP3_PRESERVATION_NOT_SUPPORTED_BY_WAV_NORMALIZATION")
        if shutil.which("ffmpeg") is None:
            return self._failure(request, GeneratorFailureCode.DEPENDENCY_MISSING, reason="FFMPEG_MISSING_BEFORE_PROVIDER_REQUEST")
        request.output_dir.mkdir(parents=True, exist_ok=True)
        output_format = "auto"
        exact_request = {
            "method": "POST",
            "base_url": self.api_url,
            "endpoint": "/v1/music",
            "query": {"output_format": output_format},
            "headers": {"Accept": "audio/*", "Content-Type": "application/json", "xi-api-key": "[REDACTED]"},
            "body": {
                "prompt": prompt,
                "music_length_ms": round(request.brief.target_duration_s * 1000),
                "model_id": ELEVENLABS_MUSIC_MODEL,
                "force_instrumental": bool(request.brief.instrumental),
                "store_for_inpainting": bool(request.settings.get("store_for_inpainting", False)),
                "sign_with_c2pa": False,
            },
            "local_request_id": request.request_id,
            "internal_candidate_seed": request.seed,
            "provider_seed_applied": False,
            "seed_omitted_reason": "Eleven Music simple prompt mode does not accept seed.",
        }
        started = perf_counter()
        source_path = request.output_dir / f"{request.request_id}.provider-audio"
        wav_path = request.output_dir / f"{request.request_id}.wav"
        try:
            audio, response = self._post_audio(prompt, request)
            source_path.write_bytes(audio)
            self._decode_mp3_to_wav(source_path, wav_path)
            info = sf.info(wav_path)
            samples, sample_rate = sf.read(wav_path, always_2d=True, dtype="float32")
            duration_ok = abs(float(info.duration) - request.brief.target_duration_s) <= max(0.5, request.brief.target_duration_s * 0.05)
            if (
                not info.frames or not samples.size or not np.isfinite(samples).all()
                or not 1 <= int(info.channels) <= 8 or int(sample_rate) <= 0 or not duration_ok
            ):
                return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, reason="DECODED_AUDIO_INVALID")
            non_silent = bool(np.max(np.abs(samples)) > 1e-6)
            latency_s = perf_counter() - started
            sha = hashlib.sha256(wav_path.read_bytes()).hexdigest()
            metadata = {
                "song_id": response.get("song_id") or None,
                "request_id": response.get("request_id") or None,
                "returned_content_type": response.get("content_type"),
                "generation_attempts": response.get("attempts"),
                "returned_metadata": {"duration_s": float(info.duration), "channels": int(info.channels), "sample_rate": int(sample_rate)},
                "provider_seed_applied": False,
                "source_audio_sha256": hashlib.sha256(audio).hexdigest(),
                "source_audio_bytes": len(audio),
                "cost_usd": None,
                "cost_status": "NOT_EXPOSED_BY_MUSIC_RESPONSE",
                "documentation": [MUSIC_API_DOC],
            }
            asset = GeneratedAsset(
                asset_id=f"{self.provider_id}:{request.request_id}",
                path=wav_path,
                sha256=sha,
                bytes=wav_path.stat().st_size,
                duration_s=float(info.duration),
                sample_rate=int(sample_rate),
                non_silent=non_silent,
                model=request.model_manifest or self._model_manifest(),
                seed=request.seed,
                prompt=prompt,
                performance=PerformanceManifest(device="elevenlabs-music-api", latency_s=latency_s, actual_duration_s=float(info.duration), sample_rate=int(sample_rate)),
                rights_manifest=RightsManifest(
                    source_audio_ownership="NOT_APPLICABLE",
                    output_use=request.brief.rights_manifest.output_use,
                    provenance=["https://elevenlabs.io/docs/overview/capabilities/music"],
                ),
                provider_request=exact_request,
                provider_metadata=metadata,
                no_ableton_access=True,
            )
            if not non_silent:
                return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, reason="SILENT_AUDIO", asset=asset.model_dump(mode="json"))
            return GenerationBatch(batch_id=request.request_id, status="GENERATED", provider=self.provider_id, model=asset.model, brief_id=request.brief.brief_id, assets=[asset])
        except ElevenLabsRequestError as exc:
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, provider_error=exc.code, http_status=exc.status, attempts=exc.attempts, response=exc.detail)
        except FileNotFoundError as exc:
            return self._failure(request, GeneratorFailureCode.DEPENDENCY_MISSING, reason=str(exc))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, reason=type(exc).__name__, detail=_safe_error_text(str(exc), self.api_key))
        finally:
            source_path.unlink(missing_ok=True)

    def separate_stems(self, source_asset: GeneratedAsset, *, output_dir: Path, variation_id: str = "six_stems_v1") -> list[dict[str, Any]]:
        """Separate one human-selected Eleven asset and return validated derived WAVs.

        Opaque archive member names are retained only as provider metadata; no
        semantic stem labels are invented.
        """
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY_MISSING")
        if source_asset.model.provider != self.provider_id or not source_asset.path.is_file():
            raise ValueError("SOURCE_ASSET_NOT_ELEVENLABS_OR_MISSING")
        if variation_id not in {"six_stems_v1", "two_stems_v1"}:
            raise ValueError("STEM_VARIATION_INVALID")
        if shutil.which("ffmpeg") is None:
            raise FileNotFoundError("FFMPEG_MISSING")
        raw = source_asset.path.read_bytes()
        boundary = "----MusicPlatform" + uuid.uuid4().hex
        filename = source_asset.path.name.replace('"', "")
        multipart = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode() + raw + f"\r\n--{boundary}--\r\n".encode()
        url = f"{self.api_url}/v1/music/stem-separation?{urllib.parse.urlencode({'stem_variation_id': variation_id})}"
        req = urllib.request.Request(url, data=multipart, headers={"Accept": "application/zip", "Content-Type": f"multipart/form-data; boundary={boundary}", "xi-api-key": self.api_key}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as response:
                archive_bytes = response.read(MAX_STEM_ARCHIVE_BYTES + 1)
                content_type = response.headers.get("Content-Type", "").lower()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"STEM_SEPARATION_REQUEST_FAILED:{type(exc).__name__}") from exc
        if len(archive_bytes) > MAX_STEM_ARCHIVE_BYTES or not archive_bytes or not zipfile.is_zipfile(io.BytesIO(archive_bytes)):
            raise ValueError("STEM_RESPONSE_NOT_VALID_ZIP")
        if "zip" not in content_type and not content_type.startswith("application/octet-stream"):
            raise ValueError("STEM_RESPONSE_CONTENT_TYPE_INVALID")
        output_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = [m for m in archive.infolist() if not m.is_dir() and PurePosixPath(m.filename).suffix.lower() in _AUDIO_SUFFIXES]
            if not 1 <= len(members) <= MAX_STEM_FILES or sum(m.file_size for m in members) > MAX_STEM_ARCHIVE_BYTES or any(m.file_size > MAX_STEM_MEMBER_BYTES for m in members):
                raise ValueError("STEM_ARCHIVE_FILE_COUNT_OR_SIZE_INVALID")
            for index, member in enumerate(members, 1):
                # The archive path is never used as a filesystem destination.
                data = archive.read(member)
                source_path = output_dir / f".stem-{index:02d}-source{PurePosixPath(member.filename).suffix.lower()}"
                target_path = output_dir / f"stem-{index:02d}.wav"
                source_path.write_bytes(data)
                try:
                    self._decode_mp3_to_wav(source_path, target_path)
                finally:
                    source_path.unlink(missing_ok=True)
                info = sf.info(target_path)
                audio, _sr = sf.read(target_path, always_2d=True, dtype="float32")
                if not np.isfinite(audio).all() or not audio.size or not np.any(np.abs(audio) > 1e-6):
                    raise ValueError("STEM_AUDIO_INVALID")
                duration_delta = abs(float(info.duration) - float(source_asset.duration_s or info.duration))
                if duration_delta > max(1.0, float(source_asset.duration_s or 0) * 0.05):
                    raise ValueError("STEM_DURATION_MISMATCH")
                records.append({
                    "path": target_path,
                    "sha256": hashlib.sha256(target_path.read_bytes()).hexdigest(),
                    "bytes": target_path.stat().st_size,
                    "duration_s": float(info.duration),
                    "sample_rate": int(info.samplerate),
                    "provider_member_name": PurePosixPath(member.filename).name,
                    "provider": self.provider_id,
                    "model_id": ELEVENLABS_MUSIC_MODEL,
                    "stem_variation_id": variation_id,
                    "source_asset_id": source_asset.asset_id,
                    "source_asset_sha256": source_asset.sha256,
                    "source_provider": self.provider_id,
                    "provider_request": {
                        "method": "POST",
                        "endpoint": "/v1/music/stem-separation",
                        "stem_variation_id": variation_id,
                        "output_format": "provider_default",
                        "source_asset_id": source_asset.asset_id,
                        "source_sha256": source_asset.sha256,
                    },
                    "documentation": STEM_API_DOC,
                })
        return records

    def cancel(self, request_id: str) -> None:
        del request_id


class ElevenLabsRequestError(RuntimeError):
    def __init__(self, code: str, status: int | None, attempts: int, detail: str) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.attempts = attempts
        self.detail = detail
