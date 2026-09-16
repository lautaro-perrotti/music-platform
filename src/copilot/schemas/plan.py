"""Backward-compatible re-exports. Production MusicPlan V1 lives in musicplan.py. """

from __future__ import annotations

from copilot.schemas.musicplan import LegacyMusicPlan, MusicPlan, PlannedAction

__all__ = ["LegacyMusicPlan", "MusicPlan", "PlannedAction"]
