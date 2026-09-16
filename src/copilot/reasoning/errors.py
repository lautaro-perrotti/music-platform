from __future__ import annotations

from enum import StrEnum


class ReasoningFailure(StrEnum):
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    LLM_GROUNDING_VIOLATION = "LLM_GROUNDING_VIOLATION"
    UNKNOWN_EVIDENCE_REF = "UNKNOWN_EVIDENCE_REF"
    UNKNOWN_ENTITY_REF = "UNKNOWN_ENTITY_REF"
    AMBIGUOUS_ENTITY_REFERENCE = "AMBIGUOUS_ENTITY_REFERENCE"
    UNSUPPORTED_PRECISION = "UNSUPPORTED_PRECISION"
    DIAGNOSIS_UNSTABLE = "DIAGNOSIS_UNSTABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ProviderError(Exception):
    def __init__(self, kind: ReasoningFailure, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
