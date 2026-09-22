"""Safe discovery and immutable ingestion of a user-authorized audio source.

This module deliberately does not guess which audio file is the user's source.
It searches only explicit/user-facing import locations, rejects ambiguity, and
copies accepted bytes to a runtime-owned location before any analysis.  The
original is never edited and no Ableton API is involved.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Iterable
import soundfile as sf
from pydantic import BaseModel, Field

from copilot.music_generation.schemas import RightsClassification, RightsManifest


SUPPORTED_SOURCE_SUFFIXES = frozenset({".wav", ".flac", ".aiff", ".aif", ".mp3"})
LOSSLESS_SOURCE_SUFFIXES = frozenset({".wav", ".flac", ".aiff", ".aif"})


class SourceDiscoveryStatus(StrEnum):
    SOURCE_FOUND = "SOURCE_FOUND"
    SOURCE_ASSET_REQUIRED = "SOURCE_ASSET_REQUIRED"
    SOURCE_SELECTION_REQUIRED = "SOURCE_SELECTION_REQUIRED"


class ExternalSourceCandidate(BaseModel):
    path: Path
    suffix: str
    bytes: int = Field(ge=0)
    modified_at: str
    age_days: float = Field(ge=0)


class ExternalSourceManifest(BaseModel):
    """Durable provenance for the exact bytes imported from outside the repo."""

    source_asset_id: str
    original_path: Path
    immutable_path: Path
    source_origin: str = "USER_AUTHORIZED_EXTERNAL_ASSET"
    source_provider: str | None = None
    source_model: str | None = None
    original_sha256: str
    immutable_sha256: str
    bytes: int = Field(ge=0)
    media_format: str
    duration_s: float = Field(gt=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    discovered_at: str
    imported_at: str
    rights_manifest: RightsManifest = Field(default_factory=RightsManifest)
    provenance: list[str] = Field(default_factory=list)
    immutable_source: bool = True
    no_ableton_access: bool = True


class ExternalSourceDiscovery(BaseModel):
    status: SourceDiscoveryStatus
    candidates: list[ExternalSourceCandidate] = Field(default_factory=list)
    selected: ExternalSourceCandidate | None = None
    searched_directories: list[Path] = Field(default_factory=list)
    reason: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_days(path: Path, now: datetime) -> float:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return max(0.0, (now - modified).total_seconds() / 86400.0)


def _configured_directories(extra_directories: Iterable[Path] | None) -> list[Path]:
    # An explicit directory list is a closed discovery scope.  This keeps
    # tests and controlled imports from accidentally seeing an unrelated
    # user download, and preserves fail-closed ambiguity semantics.
    directories: list[Path] = [] if extra_directories is not None else [Path.home() / "Downloads", Path.home() / "Desktop"]
    configured = os.environ.get("MUSIC_PLATFORM_IMPORT_DIRS", "")
    if configured:
        directories.extend(Path(value) for value in configured.split(os.pathsep) if value)
    if extra_directories:
        directories.extend(Path(value) for value in extra_directories)
    unique: list[Path] = []
    seen: set[str] = set()
    for directory in directories:
        resolved = directory.expanduser()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def discover_external_source(
    *,
    directories: Iterable[Path] | None = None,
    max_age_days: float | None = 30.0,
) -> ExternalSourceDiscovery:
    """Find exactly one recent external source, or fail closed.

    The default deliberately excludes repository outputs and arbitrary drives.
    Additional runtime input directories must be explicitly configured through
    ``MUSIC_PLATFORM_IMPORT_DIRS`` or ``directories``.
    """

    now = _now()
    searched = _configured_directories(directories)
    candidates: list[ExternalSourceCandidate] = []
    for directory in searched:
        if not directory.is_dir():
            continue
        try:
            files = directory.iterdir()
        except OSError:
            continue
        for path in files:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
                continue
            try:
                stat = path.stat()
                age = _age_days(path, now)
            except OSError:
                continue
            if max_age_days is not None and age > max_age_days:
                continue
            candidates.append(
                ExternalSourceCandidate(
                    path=path,
                    suffix=path.suffix.lower(),
                    bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    age_days=age,
                )
            )
    candidates.sort(key=lambda candidate: (candidate.age_days, str(candidate.path).casefold()))
    if not candidates:
        return ExternalSourceDiscovery(
            status=SourceDiscoveryStatus.SOURCE_ASSET_REQUIRED,
            searched_directories=searched,
            reason="NO_RECENT_EXTERNAL_AUDIO_SOURCE_FOUND",
        )
    if len(candidates) != 1:
        return ExternalSourceDiscovery(
            status=SourceDiscoveryStatus.SOURCE_SELECTION_REQUIRED,
            candidates=candidates,
            searched_directories=searched,
            reason="MULTIPLE_RECENT_AUDIO_SOURCES_ARE_AMBIGUOUS",
        )
    return ExternalSourceDiscovery(
        status=SourceDiscoveryStatus.SOURCE_FOUND,
        candidates=candidates,
        selected=candidates[0],
        searched_directories=searched,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audio_info(path: Path) -> tuple[float, int, int, str]:
    if path.suffix.lower() == ".mp3":
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise ValueError("MP3_TECHNICAL_PROBE_REQUIRES_FFPROBE")
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate,channels:format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError("MP3_TECHNICAL_PROBE_FAILED")
        try:
            payload = json.loads(completed.stdout)
            stream = payload["streams"][0]
            duration_s = float(payload["format"]["duration"])
            sample_rate = int(stream["sample_rate"])
            channels = int(stream["channels"])
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("MP3_TECHNICAL_PROBE_INCOMPLETE") from exc
        if duration_s <= 0 or sample_rate <= 0 or channels <= 0:
            raise ValueError("SOURCE_AUDIO_INVALID_TECHNICAL_METADATA")
        return duration_s, sample_rate, channels, "mp3"
    info = sf.info(path)
    if info.duration <= 0 or info.samplerate <= 0 or info.channels <= 0:
        raise ValueError("SOURCE_AUDIO_INVALID_TECHNICAL_METADATA")
    return float(info.duration), int(info.samplerate), int(info.channels), path.suffix.lower()[1:]


def ingest_external_source(
    source_path: Path,
    *,
    runtime_input_dir: Path,
    source_provider: str | None = None,
    source_model: str | None = None,
    rights_manifest: RightsManifest | None = None,
    provenance: Iterable[str] = (),
) -> ExternalSourceManifest:
    """Validate and copy source bytes into an immutable runtime-owned location."""

    source = Path(source_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
        raise ValueError("UNSUPPORTED_EXTERNAL_SOURCE_FORMAT")
    duration_s, sample_rate, channels, media_format = _audio_info(source)
    original_hash = _sha256(source)
    imported_at = _now().isoformat()
    target_root = Path(runtime_input_dir).expanduser()
    target_root.mkdir(parents=True, exist_ok=True)
    source_asset_id = f"external-source-{original_hash[:16]}"
    immutable = target_root / f"{source_asset_id}{source.suffix.lower()}"
    if immutable.exists():
        if _sha256(immutable) != original_hash:
            raise ValueError("IMMUTABLE_SOURCE_PATH_HASH_COLLISION")
    else:
        temp = target_root / f".{source_asset_id}.partial"
        try:
            shutil.copy2(source, temp)
            if _sha256(temp) != original_hash:
                raise ValueError("EXTERNAL_SOURCE_COPY_HASH_MISMATCH")
            temp.replace(immutable)
        finally:
            temp.unlink(missing_ok=True)
    immutable_hash = _sha256(immutable)
    manifest = ExternalSourceManifest(
        source_asset_id=source_asset_id,
        original_path=source,
        immutable_path=immutable,
        source_provider=source_provider,
        source_model=source_model,
        original_sha256=original_hash,
        immutable_sha256=immutable_hash,
        bytes=immutable.stat().st_size,
        media_format=media_format,
        duration_s=duration_s,
        sample_rate=sample_rate,
        channels=channels,
        discovered_at=datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc).isoformat(),
        imported_at=imported_at,
        rights_manifest=rights_manifest or RightsManifest(
            source_audio_ownership="USER_AUTHORIZED_EXTERNAL_SOURCE",
            remote_upload_allowed=False,
            transformation_allowed=True,
            output_use=RightsClassification.UNKNOWN,
        ),
        provenance=list(provenance),
    )
    return manifest
