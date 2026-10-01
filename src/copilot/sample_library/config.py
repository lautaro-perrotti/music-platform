"""Explicit, user-scoped configuration for local sample-library roots."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


CONFIG_FILENAME = "sample-library.json"
INDEX_FILENAME = "sample-library-index.json"


class SampleLibraryConfig(BaseModel):
    schema_version: str = "sample-library-config-v1"
    roots: list[str] = Field(default_factory=list)

    @field_validator("roots")
    @classmethod
    def roots_are_unique_absolute_paths(cls, roots: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in roots:
            path = Path(value)
            if not path.is_absolute():
                raise ValueError("sample-library roots must be absolute paths")
            canonical = str(path.resolve(strict=False))
            key = os.path.normcase(canonical)
            if key not in seen:
                normalized.append(canonical)
                seen.add(key)
        return normalized


def default_config_dir() -> Path:
    """Return a platform-native per-user config directory without repo paths."""
    explicit = os.environ.get("MUSIC_PLATFORM_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser()

    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "MusicPlatform"

    if sys_platform_is_macos():
        return Path.home() / "Library" / "Application Support" / "MusicPlatform"

    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg).expanduser() if xdg else Path.home() / ".config") / "music-platform"


def sys_platform_is_macos() -> bool:
    # Keep platform detection local and dependency-free.
    import sys

    return sys.platform == "darwin"


def config_path(config_dir: Path | None = None) -> Path:
    return (config_dir or default_config_dir()) / CONFIG_FILENAME


def index_path(config_dir: Path | None = None) -> Path:
    return (config_dir or default_config_dir()) / INDEX_FILENAME


def load_config(path: Path | None = None) -> SampleLibraryConfig:
    target = path or config_path()
    if not target.exists():
        return SampleLibraryConfig()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return SampleLibraryConfig.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"SAMPLE_LIBRARY_CONFIG_INVALID: {target}: {exc}") from exc


def save_config(config: SampleLibraryConfig, path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (config.model_dump_json(indent=2) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target


def add_root(root: Path, *, path: Path | None = None) -> tuple[SampleLibraryConfig, bool]:
    candidate = Path(root).expanduser().resolve(strict=True)
    if not candidate.is_dir():
        raise ValueError("SAMPLE_LIBRARY_ROOT_NOT_DIRECTORY")
    config = load_config(path)
    key = os.path.normcase(str(candidate))
    if any(os.path.normcase(existing) == key for existing in config.roots):
        return config, False
    config.roots.append(str(candidate))
    config = SampleLibraryConfig.model_validate(config.model_dump())
    save_config(config, path)
    return config, True


def remove_root(root: Path, *, path: Path | None = None) -> tuple[SampleLibraryConfig, bool]:
    candidate = Path(root).expanduser().resolve(strict=False)
    key = os.path.normcase(str(candidate))
    config = load_config(path)
    roots = [item for item in config.roots if os.path.normcase(item) != key]
    changed = len(roots) != len(config.roots)
    if changed:
        config.roots = roots
        save_config(config, path)
    return config, changed


__all__ = [
    "CONFIG_FILENAME",
    "INDEX_FILENAME",
    "SampleLibraryConfig",
    "add_root",
    "config_path",
    "default_config_dir",
    "index_path",
    "load_config",
    "remove_root",
    "save_config",
]
