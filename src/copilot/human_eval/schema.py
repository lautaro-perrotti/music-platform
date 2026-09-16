from __future__ import annotations

from enum import StrEnum
from typing import Any
from pydantic import BaseModel, Field

SCHEMA_VERSION = "human-eval-1"
HUMAN_LABEL_MODE_DEFAULT = "PRE_ASTRA_DSP_EXPOSED"


class CaseStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    LOCKED = "LOCKED"
    SKIPPED = "SKIPPED"


class StimulusKind(StrEnum):
    SINGLE = "SINGLE"
    BEFORE_AFTER = "BEFORE_AFTER"
    ABC = "ABC"


class EvalMode(StrEnum):
    QUICK = "QUICK"
    DEEP = "DEEP"


class OverallFeel(StrEnum):
    VERY_GOOD = "VERY_GOOD"
    GOOD = "GOOD"
    UNSURE = "UNSURE"
    BAD = "BAD"
    VERY_BAD = "VERY_BAD"


class Ternary(StrEnum):
    YES = "YES"
    NO = "NO"
    UNSURE = "UNSURE"


class ProblemType(StrEnum):
    LOW_END = "LOW_END"
    KICK_BASS_RELATIONSHIP = "KICK_BASS_RELATIONSHIP"
    GROOVE = "GROOVE"
    TIMING = "TIMING"
    LEVEL_BALANCE = "LEVEL_BALANCE"
    SPECTRAL_BALANCE = "SPECTRAL_BALANCE"
    DYNAMICS = "DYNAMICS"
    SOUND_SELECTION = "SOUND_SELECTION"
    ARRANGEMENT = "ARRANGEMENT"
    ENERGY = "ENERGY"
    TRANSITION = "TRANSITION"
    STEREO_IMAGE = "STEREO_IMAGE"
    VOCALS = "VOCALS"
    HARMONY = "HARMONY"
    MELODY = "MELODY"
    RHYTHM = "RHYTHM"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class LikelyCause(StrEnum):
    TEMPORAL = "TEMPORAL"
    SPECTRAL = "SPECTRAL"
    LEVEL = "LEVEL"
    ARRANGEMENT = "ARRANGEMENT"
    SOUND_SELECTION = "SOUND_SELECTION"
    GROOVE = "GROOVE"
    DYNAMICS = "DYNAMICS"
    HARMONIC = "HARMONIC"
    PERFORMANCE = "PERFORMANCE"
    UNKNOWN = "UNKNOWN"
    OTHER = "OTHER"


class DesiredAction(StrEnum):
    NO_CHANGE = "NO_CHANGE"
    CHANGE_LEVEL = "CHANGE_LEVEL"
    CHANGE_EQ_OR_SPECTRUM = "CHANGE_EQ_OR_SPECTRUM"
    CHANGE_DYNAMICS = "CHANGE_DYNAMICS"
    CHANGE_SOUND = "CHANGE_SOUND"
    CHANGE_MIDI = "CHANGE_MIDI"
    CHANGE_NOTE_LENGTH = "CHANGE_NOTE_LENGTH"
    CHANGE_GROOVE = "CHANGE_GROOVE"
    CHANGE_ARRANGEMENT = "CHANGE_ARRANGEMENT"
    CHANGE_AUTOMATION = "CHANGE_AUTOMATION"
    CHANGE_EFFECTS = "CHANGE_EFFECTS"
    CHANGE_STEREO = "CHANGE_STEREO"
    REPLACE_ELEMENT = "REPLACE_ELEMENT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class Severity(StrEnum):
    MINOR = "MINOR"
    MODERATE = "MODERATE"
    MAJOR = "MAJOR"


class Confidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class SkipReason(StrEnum):
    CANT_JUDGE = "CANT_JUDGE"
    BAD_AUDIO = "BAD_AUDIO"
    TOO_SHORT = "TOO_SHORT"
    WRONG_REGION = "WRONG_REGION"
    FATIGUE = "FATIGUE"
    OTHER = "OTHER"


class PreferenceChoice(StrEnum):
    PREFER_A = "PREFER_A"
    PREFER_B = "PREFER_B"
    NO_DIFFERENCE = "NO_DIFFERENCE"
    NEITHER = "NEITHER"


class KeepChoice(StrEnum):
    KEEP = "KEEP"
    ADJUST = "ADJUST"
    ROLLBACK = "ROLLBACK"


class AudioRef(BaseModel):
    audio_asset_id: str
    path: str
    audio_sha256: str
    duration_s: float | None = None
    role: str = "main"


class EvaluationStimulus(BaseModel):
    kind: StimulusKind = StimulusKind.SINGLE
    assets: list[AudioRef] = Field(default_factory=list)


class HumanResponse(BaseModel):
    mode: EvalMode = EvalMode.QUICK
    overall_feel: OverallFeel | None = None
    would_change: Ternary | None = None
    groove_feels_right: Ternary | None = None
    low_end_feels_right: Ternary | None = None
    problem_types: list[ProblemType] = Field(default_factory=list)
    likely_causes: list[LikelyCause] = Field(default_factory=list)
    desired_actions: list[DesiredAction] = Field(default_factory=list)
    severity: Severity | None = None
    confidence: Confidence | None = None
    notes: str = ""
    preference: PreferenceChoice | None = None
    keep_choice: KeepChoice | None = None
    replays: int = 0
    listen_ms: int = 0
    updated_at: str | None = None


class LabelRevision(BaseModel):
    revision: int
    human_label_hash: str
    audio_sha256: str
    schema_version: str = SCHEMA_VERSION
    created_at: str
    locked_at: str
    response: HumanResponse


class BlindMetadata(BaseModel):
    """Never sent to the labeling frontend."""

    region: str | None = None
    song_id: str | None = None
    project_id: str | None = None
    source_run: str | None = None
    genre: str | None = None
    section: str | None = None
    source_configuration: str | None = None
    diagnostic_family: str | None = None
    expected_labels: dict[str, Any] = Field(default_factory=dict)
    dsp: dict[str, Any] = Field(default_factory=dict)
    source_activity: dict[str, Any] = Field(default_factory=dict)
    astra: dict[str, Any] = Field(default_factory=dict)
    candidate_actions: list[Any] = Field(default_factory=list)
    fixture_metadata: dict[str, Any] = Field(default_factory=dict)


class EvalCase(BaseModel):
    case_id: str
    evaluation_run_id: str
    project_id: str | None = None
    audio_asset_id: str
    audio_sha256: str
    region: str | None = None
    song_id: str | None = None
    duration_s: float | None = None
    stimulus: EvaluationStimulus
    blind_metadata: BlindMetadata = Field(default_factory=BlindMetadata)
    status: CaseStatus = CaseStatus.PENDING
    presentation_order: int = 0
    draft: HumanResponse | None = None
    response: HumanResponse | None = None
    revisions: list[LabelRevision] = Field(default_factory=list)
    skip_reason: SkipReason | None = None
    skip_notes: str = ""
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    locked_at: str | None = None
    human_label_hash: str | None = None


class EvalRun(BaseModel):
    evaluation_run_id: str
    schema_version: str = SCHEMA_VERSION
    source_run: str | None = None
    project_id: str | None = None
    project_token: str | None = None
    seed: int = 0
    default_mode: EvalMode = EvalMode.QUICK
    human_label_mode: str = HUMAN_LABEL_MODE_DEFAULT
    comparison_requires_human_gt: bool = True
    autoplay: bool = False
    case_ids: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    locked_at: str | None = None
    musical_writes: int = 0
    astra_calls: int = 0
