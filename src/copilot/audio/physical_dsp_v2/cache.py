"""Deterministic cache: audio hash + analyzer id/version + parameters. Never path alone."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from copilot.schemas.dsp import DspObservation

CACHE_DIR = Path("logs") / "physical_dsp_v2_cache"


def parameters_hash(params: dict[str, Any]) -> str:
    raw = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_key(
    artifact_hash: str,
    analyzer_id: str,
    analyzer_version: str,
    params: dict[str, Any],
) -> str:
    payload = {
        "audio": artifact_hash,
        "analyzer_id": analyzer_id,
        "analyzer_version": analyzer_version,
        "params": params,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_path(key: str, directory: Path | None = None) -> Path:
    root = directory if directory is not None else CACHE_DIR
    return root / f"{key}.json"


def load_cached(key: str, directory: Path | None = None) -> DspObservation | None:
    path = cache_path(key, directory)
    if not path.is_file():
        return None
    obs = DspObservation.model_validate(json.loads(path.read_text(encoding="utf-8")))
    obs.provenance.cache_hit = True
    return obs


def store_cached(obs: DspObservation, directory: Path | None = None) -> None:
    root = directory if directory is not None else CACHE_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = cache_path(obs.provenance.cache_key, root)
    path.write_text(
        json.dumps(obs.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
