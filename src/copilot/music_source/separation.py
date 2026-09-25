"""Provider-neutral specialist source-separation boundary.

The implementation targets the maintained ``bs-roformer-infer`` runtime.  It
is intentionally subprocess-isolated: model weights and its ML dependency
tree stay outside this repository, while Core owns source provenance,
technical validation, and the resulting immutable stem manifest.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any

import soundfile as sf
from pydantic import BaseModel, Field

from copilot.music_source.ingestion import ExternalSourceManifest


BS_ROFORMER_PROVIDER_ID = "bs-roformer-infer"
BS_ROFORMER_MODEL_ID = "roformer-model-bs-roformer-sw-by-jarredou"
BS_ROFORMER_MODEL_SHA256 = "24e7d35ee9c64415673d3fd33e06a67cac2c103c5df6267ba1576459c775916e"
BS_ROFORMER_SOURCE = "https://github.com/openmirlab/bs-roformer-infer"
BS_ROFORMER_MODEL_SOURCE = "https://huggingface.co/enerjazzer/BS-ROFO-SW-Fixed"


class SeparationStatus(StrEnum):
    SEPARATED = "SEPARATED"
    SOURCE_ASSET_REQUIRED = "SOURCE_ASSET_REQUIRED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INFERENCE_FAILED = "INFERENCE_FAILED"


class SeparatorDescriptor(BaseModel):
    provider_id: str
    model_id: str
    runtime: str
    source: str
    model_source: str
    model_sha256: str | None = None
    output_stems: list[str] = Field(default_factory=list)
    quality_tier: str = "SPECIALIST_UNBENCHMARKED"
    license: str = "MIT_RUNTIME_MODEL_TERMS_TO_VERIFY"


class SeparationRequest(BaseModel):
    request_id: str
    source: ExternalSourceManifest
    output_dir: Path
    model_id: str = BS_ROFORMER_MODEL_ID
    model_dir: Path | None = None
    command: str | None = None
    timeout_s: float = Field(default=3600.0, gt=0)
    no_ableton_access: bool = True


class SeparatedStem(BaseModel):
    stem_id: str
    role: str
    path: Path
    sha256: str
    bytes: int = Field(ge=0)
    duration_s: float = Field(gt=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    non_silent: bool


class SeparationBatch(BaseModel):
    batch_id: str
    status: SeparationStatus
    provider: str
    model_id: str
    source_asset_id: str
    stems: list[SeparatedStem] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    latency_s: float | None = None
    command: list[str] | None = None
    provenance: list[str] = Field(default_factory=list)
    no_ableton_access: bool = True


class BSRoFormerInferProvider:
    """Run the official provider CLI only when it is actually installed."""

    provider_id = BS_ROFORMER_PROVIDER_ID

    def __init__(self, *, executable: str | None = None) -> None:
        if executable is not None:
            self.executable = executable or None
        else:
            self.executable = os.environ.get("BS_ROFORMER_INFER_BIN") or shutil.which("bs-roformer-infer")

    def describe(self) -> SeparatorDescriptor:
        return SeparatorDescriptor(
            provider_id=self.provider_id,
            model_id=BS_ROFORMER_MODEL_ID,
            runtime="bs-roformer-infer-cli",
            source=BS_ROFORMER_SOURCE,
            model_source=BS_ROFORMER_MODEL_SOURCE,
            model_sha256=BS_ROFORMER_MODEL_SHA256,
            output_stems=["vocals", "drums", "bass", "guitar", "piano", "other", "instrumental"],
        )

    def health(self) -> str:
        return "CONFIGURED" if self.executable else "UNAVAILABLE"

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _stem_role(path: Path) -> str:
        name = path.stem.casefold()
        for role in ("vocals", "drums", "bass", "guitar", "piano", "other", "instrumental"):
            if name.endswith(f"_{role}") or f"_{role}_" in name:
                return role
        return "unknown"

    def separate(self, request: SeparationRequest) -> SeparationBatch:
        if not request.source.immutable_path.is_file():
            return SeparationBatch(
                batch_id=request.request_id,
                status=SeparationStatus.SOURCE_ASSET_REQUIRED,
                provider=self.provider_id,
                model_id=request.model_id,
                source_asset_id=request.source.source_asset_id,
                failures=[{"code": "IMMUTABLE_SOURCE_MISSING"}],
            )
        if not self.executable:
            return SeparationBatch(
                batch_id=request.request_id,
                status=SeparationStatus.PROVIDER_UNAVAILABLE,
                provider=self.provider_id,
                model_id=request.model_id,
                source_asset_id=request.source.source_asset_id,
                failures=[{"code": "BS_ROFORMER_INFER_RUNTIME_MISSING"}],
                provenance=[BS_ROFORMER_SOURCE, BS_ROFORMER_MODEL_SOURCE],
            )

        request.output_dir.mkdir(parents=True, exist_ok=True)
        run_output_dir = request.output_dir / request.request_id
        run_output_dir.mkdir(parents=True, exist_ok=True)
        started = perf_counter()
        with tempfile.TemporaryDirectory(prefix="copilot-separation-") as input_dir_name:
            input_dir = Path(input_dir_name)
            input_copy = input_dir / request.source.immutable_path.name
            shutil.copy2(request.source.immutable_path, input_copy)
            if request.model_id != BS_ROFORMER_MODEL_ID:
                return SeparationBatch(
                    batch_id=request.request_id,
                    status=SeparationStatus.INFERENCE_FAILED,
                    provider=self.provider_id,
                    model_id=request.model_id,
                    source_asset_id=request.source.source_asset_id,
                    failures=[
                        {
                            "code": "MODEL_SELECTION_NOT_SUPPORTED_BY_CURRENT_CLI_ADAPTER",
                            "requested_model": request.model_id,
                            "supported_model": BS_ROFORMER_MODEL_ID,
                        }
                    ],
                    latency_s=perf_counter() - started,
                )
            # These are the documented bs-roformer-infer CLI arguments.  The
            # default model is the provider's documented recommended model;
            # do not invent a --model flag that the CLI does not advertise.
            command = [
                self.executable,
                "--input_folder",
                str(input_dir),
                "--store_dir",
                str(run_output_dir),
            ]
            if request.model_dir:
                command.extend(["--models_dir", str(request.model_dir)])
            # The provider CLI defaults to auto device selection, but making
            # the selected device observable/configurable is important on
            # workers where CPU checkpoint loading can exhaust host memory
            # before CUDA is selected.  No machine-specific path is involved.
            device = os.environ.get("BS_ROFORMER_DEVICE")
            if device:
                command.extend(["--device", device])
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=request.timeout_s,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return SeparationBatch(
                    batch_id=request.request_id,
                    status=SeparationStatus.INFERENCE_FAILED,
                    provider=self.provider_id,
                    model_id=request.model_id,
                    source_asset_id=request.source.source_asset_id,
                    failures=[{"code": type(exc).__name__, "error": str(exc)}],
                    latency_s=perf_counter() - started,
                    command=command,
                )
        if completed.returncode != 0:
            return SeparationBatch(
                batch_id=request.request_id,
                status=SeparationStatus.INFERENCE_FAILED,
                provider=self.provider_id,
                model_id=request.model_id,
                source_asset_id=request.source.source_asset_id,
                failures=[
                    {
                        "code": "SEPARATOR_PROCESS_FAILED",
                        "returncode": completed.returncode,
                        "stderr": completed.stderr[-4000:],
                    }
                ],
                latency_s=perf_counter() - started,
                command=command,
            )

        stems: list[SeparatedStem] = []
        for path in sorted(run_output_dir.glob("*")):
            if path.suffix.lower() not in {".wav", ".flac", ".aiff", ".aif"} or not path.is_file():
                continue
            try:
                info = sf.info(path)
                samples, _ = sf.read(path, always_2d=True, dtype="float32")
            except (OSError, RuntimeError, ValueError):
                continue
            stems.append(
                SeparatedStem(
                    stem_id=f"{request.request_id}:{path.stem}",
                    role=self._stem_role(path),
                    path=path,
                    sha256=self._sha256(path),
                    bytes=path.stat().st_size,
                    duration_s=float(info.duration),
                    sample_rate=int(info.samplerate),
                    channels=int(info.channels),
                    non_silent=bool(samples.size and (samples * samples).mean() > 1e-10),
                )
            )
        if not stems:
            return SeparationBatch(
                batch_id=request.request_id,
                status=SeparationStatus.INFERENCE_FAILED,
                provider=self.provider_id,
                model_id=request.model_id,
                source_asset_id=request.source.source_asset_id,
                failures=[{"code": "NO_VALID_STEMS_RETURNED"}],
                latency_s=perf_counter() - started,
                command=command,
            )
        return SeparationBatch(
            batch_id=request.request_id,
            status=SeparationStatus.SEPARATED,
            provider=self.provider_id,
            model_id=request.model_id,
            source_asset_id=request.source.source_asset_id,
            stems=stems,
            latency_s=perf_counter() - started,
            command=command,
            provenance=[BS_ROFORMER_SOURCE, BS_ROFORMER_MODEL_SOURCE],
        )


DEMUCS_PROVIDER_ID = "demucs-infer"
DEMUCS_MODEL_ID = "htdemucs_ft"


class DemucsInferProvider:
    """Run an already-installed local Demucs inference runtime."""

    provider_id = DEMUCS_PROVIDER_ID

    def __init__(self, *, executable: str | None = None, repo_dir: Path | None = None) -> None:
        self.executable = executable or os.environ.get("DEMUCS_INFER_BIN") or shutil.which("demucs-infer")
        self.repo_dir = repo_dir or (
            Path(os.environ["DEMUCS_MODEL_REPO"]) if os.environ.get("DEMUCS_MODEL_REPO") else None
        )

    def describe(self) -> SeparatorDescriptor:
        return SeparatorDescriptor(
            provider_id=self.provider_id,
            model_id=DEMUCS_MODEL_ID,
            runtime="demucs-infer-cli",
            source="https://github.com/openmirlab/demucs-infer",
            model_source="local-runtime-repository",
            output_stems=["vocals", "drums", "bass", "other"],
            quality_tier="SPECIALIST_UNBENCHMARKED",
            license="RUNTIME_AND_CHECKPOINT_TERMS_FROM_UPSTREAM",
        )

    def health(self) -> str:
        return "CONFIGURED" if self.executable and self.repo_dir and self.repo_dir.is_dir() else "UNAVAILABLE"

    def separate(self, request: SeparationRequest) -> SeparationBatch:
        model_id = request.model_id or DEMUCS_MODEL_ID
        if not request.source.immutable_path.is_file():
            return SeparationBatch(
                batch_id=request.request_id,
                status=SeparationStatus.SOURCE_ASSET_REQUIRED,
                provider=self.provider_id,
                model_id=model_id,
                source_asset_id=request.source.source_asset_id,
                failures=[{"code": "IMMUTABLE_SOURCE_MISSING"}],
            )
        if not self.executable or not self.repo_dir or not self.repo_dir.is_dir():
            return SeparationBatch(
                batch_id=request.request_id,
                status=SeparationStatus.PROVIDER_UNAVAILABLE,
                provider=self.provider_id,
                model_id=model_id,
                source_asset_id=request.source.source_asset_id,
                failures=[{"code": "DEMUCS_INFER_RUNTIME_OR_REPOSITORY_MISSING"}],
                provenance=["https://github.com/openmirlab/demucs-infer"],
            )

        request.output_dir.mkdir(parents=True, exist_ok=True)
        run_output_dir = request.output_dir / request.request_id
        run_output_dir.mkdir(parents=True, exist_ok=True)
        started = perf_counter()
        with tempfile.TemporaryDirectory(prefix="copilot-demucs-") as input_dir_name:
            input_dir = Path(input_dir_name)
            input_copy = input_dir / request.source.immutable_path.name
            shutil.copy2(request.source.immutable_path, input_copy)
            command = [
                self.executable,
                "-n", model_id,
                "--repo", str(self.repo_dir),
                "-o", str(run_output_dir),
                "--float32",
                "-j", "1",
            ]
            device = os.environ.get("DEMUCS_DEVICE")
            if device:
                command.extend(["-d", device])
            command.append(str(input_copy))
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=request.timeout_s,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return SeparationBatch(
                    batch_id=request.request_id,
                    status=SeparationStatus.INFERENCE_FAILED,
                    provider=self.provider_id,
                    model_id=model_id,
                    source_asset_id=request.source.source_asset_id,
                    failures=[{"code": type(exc).__name__, "error": str(exc)}],
                    latency_s=perf_counter() - started,
                    command=command,
                )

        if completed.returncode != 0:
            return SeparationBatch(
                batch_id=request.request_id,
                status=SeparationStatus.INFERENCE_FAILED,
                provider=self.provider_id,
                model_id=model_id,
                source_asset_id=request.source.source_asset_id,
                failures=[
                    {
                        "code": "SEPARATOR_PROCESS_FAILED",
                        "returncode": completed.returncode,
                        "stderr": completed.stderr[-4000:],
                    }
                ],
                latency_s=perf_counter() - started,
                command=command,
            )

        output_root = run_output_dir / model_id / input_copy.stem
        stems: list[SeparatedStem] = []
        for path in sorted(output_root.glob("*.wav")):
            try:
                info = sf.info(path)
                samples, _ = sf.read(path, always_2d=True, dtype="float32")
            except (OSError, RuntimeError, ValueError):
                continue
            stems.append(
                SeparatedStem(
                    stem_id=f"{request.request_id}:{path.stem}",
                    role=path.stem.casefold(),
                    path=path,
                    sha256=BSRoFormerInferProvider._sha256(path),
                    bytes=path.stat().st_size,
                    duration_s=float(info.duration),
                    sample_rate=int(info.samplerate),
                    channels=int(info.channels),
                    non_silent=bool(samples.size and (samples * samples).mean() > 1e-10),
                )
            )
        if not stems:
            return SeparationBatch(
                batch_id=request.request_id,
                status=SeparationStatus.INFERENCE_FAILED,
                provider=self.provider_id,
                model_id=model_id,
                source_asset_id=request.source.source_asset_id,
                failures=[{"code": "NO_VALID_STEMS_RETURNED", "output_root": str(output_root)}],
                latency_s=perf_counter() - started,
                command=command,
            )
        return SeparationBatch(
            batch_id=request.request_id,
            status=SeparationStatus.SEPARATED,
            provider=self.provider_id,
            model_id=model_id,
            source_asset_id=request.source.source_asset_id,
            stems=stems,
            latency_s=perf_counter() - started,
            command=command,
            provenance=["https://github.com/openmirlab/demucs-infer", "LOCAL_MODEL_REPOSITORY"],
        )
