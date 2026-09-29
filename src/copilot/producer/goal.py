"""Validated user intent for a new, isolated Ableton production."""

from __future__ import annotations

import math
import re

from pydantic import BaseModel, ConfigDict, Field

from copilot.producer.track_spec import TrackSpec


class ProducerGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "producer-goal-v1"
    prompt: str = Field(min_length=1)
    bpm: float = Field(ge=40, le=240)
    duration_bars: int = Field(ge=1, le=4096)
    requested_seconds: float | None = None
    quantization_seconds: float = Field(gt=0)

    @classmethod
    def from_prompt(cls, prompt: str) -> "ProducerGoal":
        bpm_values = re.findall(r"\bBPM\s*:\s*(\d+(?:[.,]\d+)?)\b", prompt, re.I)
        duration_values = re.findall(
            r"\b(?:Duraci[oó]n|Duration)\s*:\s*([^\r\n]+)", prompt, re.I
        )
        if len(bpm_values) != 1 or len(duration_values) != 1:
            raise ValueError("GOAL_REQUIRES_ONE_EXPLICIT_BPM_AND_DURATION")
        bpm = float(bpm_values[0].replace(",", "."))
        value = duration_values[0].strip()
        bars_match = re.fullmatch(r"(\d+)\s*(?:compases|bars?)", value, re.I)
        clock_match = re.fullmatch(r"(\d{1,3}):([0-5]\d)\s*(?:min(?:utos?)?)?", value, re.I)
        seconds_match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(?:segundos?|seconds?|s)", value, re.I)
        minutes_match = re.fullmatch(
            r"(\d+)\s*(?:minutos?|minutes?)\s*(?:(\d+)\s*(?:segundos?|seconds?))?",
            value, re.I,
        )
        if bars_match:
            bars = int(bars_match[1])
            seconds = None
        elif clock_match or seconds_match or minutes_match:
            seconds = (
                int(clock_match[1]) * 60 + int(clock_match[2])
                if clock_match else
                float(seconds_match[1].replace(",", ".")) if seconds_match else
                int(minutes_match[1]) * 60 + int(minutes_match[2] or 0)
            )
            bars = math.floor(seconds * bpm / 240 + 0.5)
        else:
            raise ValueError("GOAL_DURATION_UNIT_OR_FORMAT_INVALID")
        return cls(
            prompt=prompt.strip(),
            bpm=bpm,
            duration_bars=bars,
            requested_seconds=seconds,
            quantization_seconds=120 / bpm,
        )

    def validate_track_spec(self, spec: TrackSpec) -> None:
        if not math.isclose(spec.bpm, self.bpm, abs_tol=1e-6):
            raise ValueError("GOAL_TRACK_SPEC_BPM_MISMATCH")
        if spec.meter_numerator != 4 or spec.meter_denominator != 4:
            raise ValueError("GOAL_TRACK_SPEC_METER_UNSUPPORTED")
        if spec.duration_bars != self.duration_bars:
            raise ValueError("GOAL_TRACK_SPEC_DURATION_MISMATCH")
        if not spec.primary_hook or not spec.primary_hook.strip():
            raise ValueError("GOAL_TRACK_SPEC_HOOK_REQUIRED")
        if not spec.hook_role or spec.hook_role not in {
            role for section in spec.sections for role in section.active_roles
        }:
            raise ValueError("GOAL_TRACK_SPEC_HOOK_ROLE_REQUIRED")
