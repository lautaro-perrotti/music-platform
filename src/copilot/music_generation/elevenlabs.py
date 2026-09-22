"""Official ElevenLabs Music API provider boundary.

This adapter uses the documented ``POST /v1/music/detailed`` endpoint and
the documented ``music_v2_5`` composition-plan contract.  It has no Ableton
access and never falls back to a fixture or another generator.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from email import policy
from email.parser import BytesParser
from pathlib import Path
from time import perf_counter
from typing import Any

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
COMPOSITION_PLAN_DOC = "https://elevenlabs.io/docs/eleven-api/guides/how-to/music/composition-plans"
MUSIC_API_DOC = "https://elevenlabs.io/docs/api-reference/music/compose-detailed"


def _section_label(value: str) -> str:
    cleaned = " ".join(value.replace("[", "").replace("]", "").split())
    return cleaned or "Development"


def _section_durations(total_ms: int, count: int) -> list[int]:
    count = max(1, min(count, 30))
    if total_ms < count * 3000:
        count = max(1, total_ms // 3000)
    base, remainder = divmod(total_ms, count)
    durations = [base + (1 if index < remainder else 0) for index in range(count)]
    if any(duration < 3000 or duration > 120000 for duration in durations):
        raise ValueError("ElevenLabs composition plan section duration out of range")
    return durations


def build_elevenlabs_composition_plan(brief: GenerationBrief) -> dict[str, Any]:
    """Translate a Core brief into ElevenLabs' documented native plan shape."""
    total_ms = int(round(brief.target_duration_s * 1000))
    if total_ms < 3000 or total_ms > 600000:
        raise ValueError("ElevenLabs Music requires a duration between 3 and 600 seconds")
    labels = list(brief.structural_intent) or ["Intro", "Development", "Peak", "Outro"]
    labels = labels[:30]
    durations = _section_durations(total_ms, len(labels))

    common_styles = ["high quality production"]
    if brief.tempo_bpm:
        common_styles.append(f"{brief.tempo_bpm:g} BPM")
    if brief.key_context:
        common_styles.append(brief.key_context)
    if brief.instrumental:
        common_styles.append("instrumental")
    for value in (brief.groove_intent, brief.energy_intent, brief.density_intent):
        if value:
            common_styles.append(value)
    # The user brief remains the direction.  It is not replaced by a generic
    # ElevenLabs prompt; it is carried as a style instruction in each chunk.
    common_styles.append(brief.user_intent)
    common_styles = common_styles[:50]
    negative_styles = list(brief.negative_constraints)
    if brief.instrumental:
        negative_styles.extend(["vocals", "lyrics"])
    negative_styles = negative_styles[:50]

    chunks: list[dict[str, Any]] = []
    for label, duration_ms in zip(labels, durations):
        text = f"[{_section_label(label)}]"
        if brief.lyrics and not brief.instrumental:
            text = f"{text}\n{brief.lyrics}"
        chunks.append({
            "text": text,
            "duration_ms": duration_ms,
            "positive_styles": list(common_styles),
            "negative_styles": list(negative_styles),
            "context_adherence": "high",
        })
    return {"chunks": chunks}


class ElevenLabsMusicProvider:
    provider_id = "elevenlabs-music"

    def __init__(self, *, api_key: str | None = None, api_url: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
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
            benchmark_role="QUALITY_COMPARATOR",
        )

    def describe(self) -> GeneratorDescriptor:
        return GeneratorDescriptor(
            provider_id=self.provider_id,
            model=self._model_manifest(),
            capabilities=[
                GeneratorCapability.TEXT_TO_MUSIC,
                GeneratorCapability.COMPOSITION_PLAN,
                GeneratorCapability.REMOTE_INFERENCE,
                GeneratorCapability.INSTRUMENTAL,
                GeneratorCapability.VOCALS,
                GeneratorCapability.LONG_FORM,
            ],
            health=self.health(),
            local_or_remote="REMOTE_API",
            hardware_requirements={"api_access": "paid ElevenLabs Music API"},
            rights_classification=RightsClassification.COMMERCIAL_ALLOWED,
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

    @staticmethod
    def _parse_detailed_response(body: bytes, content_type: str) -> tuple[dict[str, Any], bytes]:
        if not content_type.lower().startswith("multipart/"):
            raise ValueError(f"ElevenLabs detailed response was not multipart: {content_type}")
        envelope = (
            b"MIME-Version: 1.0\r\nContent-Type: "
            + content_type.encode("utf-8")
            + b"\r\n\r\n"
            + body
        )
        message = BytesParser(policy=policy.default).parsebytes(envelope)
        metadata: dict[str, Any] | None = None
        audio: bytes | None = None
        for part in message.walk():
            if part.is_multipart():
                continue
            payload = part.get_payload(decode=True) or b""
            part_type = part.get_content_type().lower()
            if part_type == "application/json":
                metadata = json.loads(payload.decode("utf-8"))
            elif part_type.startswith("audio/") or part_type in {"application/octet-stream", "binary/octet-stream"}:
                audio = payload
        if metadata is None or not audio:
            raise ValueError("ElevenLabs detailed response omitted JSON metadata or audio")
        return metadata, audio

    def _request_detailed(self, body: dict[str, Any], *, output_format: str) -> tuple[dict[str, Any], bytes, dict[str, str]]:
        query = urllib.parse.urlencode({"output_format": output_format})
        request = urllib.request.Request(
            f"{self.api_url}/v1/music/detailed?{query}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "multipart/mixed",
                "Content-Type": "application/json",
                "xi-api-key": self.api_key or "",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            response_body = response.read()
            headers = {
                "content_type": response.headers.get("Content-Type", ""),
                "song_id": response.headers.get("song-id", ""),
                "request_id": response.headers.get("x-request-id", ""),
            }
        metadata, audio = self._parse_detailed_response(response_body, headers["content_type"])
        return metadata, audio, headers

    @staticmethod
    def _convert_to_wav(source_mp3: Path, target_wav: Path) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise FileNotFoundError("ffmpeg is required to decode ElevenLabs MP3 output")
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source_mp3), "-ar", "48000", "-ac", "2", str(target_wav)],
            capture_output=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.decode("utf-8", errors="replace")[-2000:])

    def generate(self, request: GeneratorRequest) -> GenerationBatch:
        if not self.api_key:
            return self._failure(request, GeneratorFailureCode.CREDENTIAL_REQUIRED, reason="ELEVENLABS_API_KEY_MISSING")
        try:
            plan = build_elevenlabs_composition_plan(request.brief)
        except ValueError as exc:
            return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, reason=str(exc))
        request.output_dir.mkdir(parents=True, exist_ok=True)
        exact_request: dict[str, Any] = {
            "endpoint": "/v1/music/detailed",
            "query": {"output_format": "mp3_48000_192"},
            "body": {
                "composition_plan": plan,
                "model_id": ELEVENLABS_MUSIC_MODEL,
                "store_for_inpainting": bool(request.settings.get("store_for_inpainting", False)),
                "with_timestamps": False,
                "with_waveform_visual": False,
            },
            "seed": request.seed,
            "seed_sent": False,
            "seed_omitted_reason": "ElevenLabs forbids seed with composition_plan",
        }
        started = perf_counter()
        try:
            metadata, audio, response_headers = self._request_detailed(
                exact_request["body"], output_format="mp3_48000_192"
            )
            mp3_path = request.output_dir / f"{request.request_id}.mp3"
            wav_path = request.output_dir / f"{request.request_id}.wav"
            mp3_path.write_bytes(audio)
            self._convert_to_wav(mp3_path, wav_path)
            info = sf.info(wav_path)
            samples, sample_rate = sf.read(wav_path, always_2d=True, dtype="float32")
            non_silent = bool(samples.size and (samples * samples).mean() > 1e-10)
            latency_s = perf_counter() - started
            provider_metadata = {
                "response_headers": response_headers,
                "returned_metadata": metadata,
                "audio_format": "mp3_48000_192",
                "source_audio_sha256": hashlib.sha256(audio).hexdigest(),
                "source_audio_bytes": len(audio),
                "cost_usd": None,
                "cost_status": "NOT_EXPOSED_BY_MUSIC_RESPONSE",
                "documentation": [MUSIC_API_DOC, COMPOSITION_PLAN_DOC],
            }
            asset = GeneratedAsset(
                asset_id=f"{self.provider_id}:{request.request_id}",
                path=wav_path,
                sha256=hashlib.sha256(wav_path.read_bytes()).hexdigest(),
                bytes=wav_path.stat().st_size,
                duration_s=float(info.duration),
                sample_rate=int(sample_rate),
                non_silent=non_silent,
                model=request.model_manifest or self._model_manifest(),
                seed=request.seed,
                prompt=request.brief.user_intent,
                performance=PerformanceManifest(
                    device="elevenlabs-music-api",
                    latency_s=latency_s,
                    actual_duration_s=float(info.duration),
                    sample_rate=int(sample_rate),
                ),
                rights_manifest=RightsManifest(
                    source_audio_ownership="NOT_APPLICABLE",
                    output_use=RightsClassification.COMMERCIAL_ALLOWED,
                    provenance=["https://elevenlabs.io/docs/overview/capabilities/music"],
                ),
                provider_request=exact_request,
                provider_metadata=provider_metadata,
            )
            if not non_silent:
                return self._failure(request, GeneratorFailureCode.OUTPUT_INVALID, reason="SILENT_AUDIO", asset=asset.model_dump(mode="json"))
            return GenerationBatch(
                batch_id=request.request_id,
                status="GENERATED",
                provider=self.provider_id,
                model=asset.model,
                brief_id=request.brief.brief_id,
                assets=[asset],
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:4000]
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, http_status=exc.code, response=detail)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            return self._failure(request, GeneratorFailureCode.INFERENCE_FAILED, type=type(exc).__name__, error=str(exc))

    def cancel(self, request_id: str) -> None:
        del request_id
