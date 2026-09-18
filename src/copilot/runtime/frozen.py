"""Runtime artifacts must never overwrite committed frozen fixtures."""

from __future__ import annotations

from pathlib import Path

FROZEN_MARKER = "fixtures/frozen"


class FrozenFixtureError(RuntimeError):
    pass


def is_frozen_path(path: Path | str) -> bool:
    posix = Path(path).resolve().as_posix().replace("\\", "/")
    return f"/{FROZEN_MARKER}/" in f"/{posix}/" or posix.endswith(f"/{FROZEN_MARKER}")


def assert_mutable_output(path: Path | str) -> None:
    if is_frozen_path(path):
        raise FrozenFixtureError(
            f"refusing to write runtime output into frozen fixtures: {path}"
        )
