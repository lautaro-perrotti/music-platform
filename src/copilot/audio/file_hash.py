"""Daw-free file hashing helpers for analyzers and evidence digests."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path | str) -> str | None:
    target = Path(path)
    if not target.is_file():
        return None
    digest = hashlib.sha256()
    digest.update(target.read_bytes())
    return digest.hexdigest()
