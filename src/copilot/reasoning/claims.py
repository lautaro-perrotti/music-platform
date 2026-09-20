"""Claim classification and causal/command discipline. Outside the model."""

from __future__ import annotations

import re

from copilot.reasoning.schema import (
    ClaimClass,
    ReasoningCandidate,
    ReasoningOutput,
)

COMMAND_RE = re.compile(
    r"(python\s+-m\s+copilot|copilot\.cli|run\s+capture(?:\s+command)?|"
    r"call\s+cli\b|execute\s+python|pytest\b|"
    r"producer-analyze|analyze-project\s+|abletonmcp)",
    re.IGNORECASE,
)
BROAD_EVIDENCE_RE = re.compile(
    r"analyze\s+everything|whole\s+project|all\s+tracks(?:\s+and\s+devices)?|"
    r"full\s+re-?analysis|dump\s+the\s+session",
    re.IGNORECASE,
)
PERCENT_CONF_RE = re.compile(
    r"\b\d{1,3}\s*%\s*(?:confident|confidence|sure|certain)\b|"
    r"\bconfidence\s*(?:of|=|:)?\s*\d{1,3}\s*%",
    re.IGNORECASE,
)
MUDDY_AS_FACT_RE = re.compile(
    r"\b(?:is|are|the\s+mix\s+is)\s+(?:too\s+)?muddy\b|"
    r"\bmuddy(?:ness)?\s+(?:problem|measurement)\b|"
    r"\bmasking\s+problem\b",
    re.IGNORECASE,
)
POTENTIAL_MASKING_OK_RE = re.compile(
    r"potential\s+masking\s+is\s+plausible|masking\s+is\s+plausible|"
    r"compatible\s+with\s+(?:potential\s+)?masking",
    re.IGNORECASE,
)
CAPTURE_SILENCE_RE = re.compile(
    r"measured\s+silence|\bis\s+silent\b|silence\s+was\s+measured|"
    r"captured\s+silence",
    re.IGNORECASE,
)
CERTAIN_KEY_RE = re.compile(
    r"\bthe\s+key\s+is\s+[A-G](?:#|b)?\b|"
    r"\bin\s+[A-G](?:#|b)?\s+(?:major|minor)\b|"
    r"\bkey\s+of\s+[A-G](?:#|b)?\s+(?:major|minor)?\b",
    re.IGNORECASE,
)
UNGROUNDED_DEVICE_CAUSE_RE = re.compile(
    r"the\s+compressor\s+is\s+suppressing|"
    r"suppressing\s+the\s+\w+\s+by\s+-?\d|"
    r"apply\s+-?\d+(?:\.\d+)?\s*dB\s+at\s+\d+",
    re.IGNORECASE,
)
INTERPRETATION_CUES = re.compile(
    r"\b(?:compatible\s+with|coincides\s+with|suggests|plausible|"
    r"weakly\s+supports|may\s+indicate|consistent\s+with)\b",
    re.IGNORECASE,
)
FACT_CUES = re.compile(
    r"\b(?:measured|rms|dB|hz|qn|median|count|energy)\b",
    re.IGNORECASE,
)


def classify_output(output: ReasoningOutput) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if output.summary:
        rows.append(
            {
                "text": output.summary,
                "claim_class": _classify_sentence(output.summary, default=ClaimClass.INTERPRETATION).value,
                "source": "summary",
            }
        )
    for hypo in output.hypotheses:
        rows.append(
            {
                "text": hypo.claim,
                "claim_class": ClaimClass.HYPOTHESIS.value,
                "source": "hypothesis.claim",
            }
        )
        if hypo.reasoning_summary:
            kind = (
                ClaimClass.INTERPRETATION
                if INTERPRETATION_CUES.search(hypo.reasoning_summary)
                else _classify_sentence(hypo.reasoning_summary, default=ClaimClass.INTERPRETATION)
            )
            rows.append(
                {
                    "text": hypo.reasoning_summary,
                    "claim_class": kind.value,
                    "source": "hypothesis.reasoning_summary",
                }
            )
    for action in output.candidate_actions:
        blob = f"{action.reason} {action.expected_effect}"
        rows.append(
            {
                "text": blob,
                "claim_class": ClaimClass.RECOMMENDATION.value,
                "source": "candidate_action",
            }
        )
    for strategy in output.candidate_strategies:
        rows.append(
            {
                "text": f"{strategy.strategy} {strategy.reason}",
                "claim_class": ClaimClass.RECOMMENDATION.value,
                "source": "candidate_strategy",
            }
        )
    return rows


def _classify_sentence(text: str, *, default: ClaimClass) -> ClaimClass:
    if COMMAND_RE.search(text):
        return ClaimClass.RECOMMENDATION
    if INTERPRETATION_CUES.search(text) and not FACT_CUES.search(text):
        return ClaimClass.INTERPRETATION
    if FACT_CUES.search(text):
        return ClaimClass.FACTUAL_REFERENCE
    return default


def command_hits(text: str) -> list[str]:
    return [match.group(0) for match in COMMAND_RE.finditer(text)]


def is_overbroad_request(text: str) -> bool:
    return bool(BROAD_EVIDENCE_RE.search(text or ""))


def invented_percentage(text: str) -> bool:
    return bool(PERCENT_CONF_RE.search(text or ""))


def muddy_as_measurement(text: str) -> bool:
    if POTENTIAL_MASKING_OK_RE.search(text or ""):
        return False
    return bool(MUDDY_AS_FACT_RE.search(text or ""))


def capture_failed_as_silence(text: str) -> bool:
    return bool(CAPTURE_SILENCE_RE.search(text or ""))


def certain_key_claim(text: str) -> bool:
    return bool(CERTAIN_KEY_RE.search(text or ""))


def ungrounded_device_cause(text: str) -> bool:
    return bool(UNGROUNDED_DEVICE_CAUSE_RE.search(text or ""))


def recommendation_masquerading_as_fact(action: ReasoningCandidate) -> bool:
    blob = f"{action.reason} {action.expected_effect} {action.action_type.value}"
    return bool(re.search(r"\b(?:is\s+measured|measurement\s+shows\s+we\s+must)\b", blob, re.I))
