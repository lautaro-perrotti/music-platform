"""Lucas reasoning handoff: natural language to an evidence-bounded brief.

This module never runs a music generator or accesses Ableton. The configured
reasoning provider supplies a JSON proposal; Core validates it and owns the
defaults and the typed GenerationBrief.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from copilot.music_generation.schemas import BriefFieldOrigin, GenerationBrief
from copilot.music_generation.registry import GeneratorRoute, MusicGeneratorRegistry
from copilot.reasoning.errors import ProviderError


DEFAULT_PREVIEW_DURATION_S = 30.0  # Existing Studio generation policy.


class _ReasoningProvider(Protocol):
    identity: str

    def reason_json_object(self, prompt: str, *, timeout_s: float = 30.0) -> str: ...


class QuotedInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    source_quote: str = Field(min_length=1)


class LucasGenerationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tempo_bpm: float | None = None
    meter: str | None = None
    target_duration_s: float | None = None
    instrumental: bool | None = None
    groove_intent: QuotedInterpretation | None = None
    energy_intent: QuotedInterpretation | None = None
    key_context: QuotedInterpretation | None = None
    structural_intent: list[QuotedInterpretation] = Field(default_factory=list)
    negative_constraints: list[QuotedInterpretation] = Field(default_factory=list)
    clarification_needed: bool = False


class LucasBriefHandoff(BaseModel):
    status: Literal["READY", "CLARIFICATION_NEEDED", "MODEL_OUTPUT_REJECTED", "PROVIDER_UNAVAILABLE"]
    brief: GenerationBrief | None = None
    reason: str = ""
    reasoning_provider: str = ""


@dataclass(frozen=True)
class LucasGenerationRequestPreparation:
    handoff: LucasBriefHandoff
    route: GeneratorRoute | None = None


def prepare_lucas_generation_request(
    user_text: str, *, reasoning_provider: _ReasoningProvider,
    registry: MusicGeneratorRegistry, seed: int, output_dir: Path,
    default_duration_s: float = DEFAULT_PREVIEW_DURATION_S,
) -> LucasGenerationRequestPreparation:
    """One read-only NL → brief → capability-fit request entrypoint."""
    handoff = lucas_nl_to_generation_brief(
        user_text, reasoning_provider=reasoning_provider,
        default_duration_s=default_duration_s,
    )
    if handoff.status != "READY" or handoff.brief is None:
        return LucasGenerationRequestPreparation(handoff=handoff)
    return LucasGenerationRequestPreparation(
        handoff=handoff,
        route=registry.prepare_request(handoff.brief, seed=seed, output_dir=output_dir),
    )


def _explicit_number(text: str, pattern: str) -> float | None:
    matches = {float(value) for value in re.findall(pattern, text, flags=re.IGNORECASE)}
    if len(matches) > 1:
        raise ValueError("CONFLICTING_EXPLICIT_VALUES")
    return next(iter(matches), None)


def _explicit_duration(text: str) -> float | None:
    matches = re.findall(
        r"\b(\d+(?:[.,]\d+)?)\s*(segundos?|seconds?|secs?|minutos?|minutes?|mins?)\b",
        text, flags=re.IGNORECASE,
    )
    values = {
        float(number.replace(",", ".")) * (60 if unit.lower().startswith(("min",)) else 1)
        for number, unit in matches
    }
    if len(values) > 1:
        raise ValueError("CONFLICTING_EXPLICIT_DURATIONS")
    return next(iter(values), None)


def _vocal_intent(text: str) -> bool | None | Literal["AMBIGUOUS"]:
    lower = text.casefold()
    no_vocals = bool(re.search(r"\b(instrumental|sin voz|sin voces|no vocals?|no voice)\b", lower))
    vocals = bool(re.search(r"\b(con voz|con voces|with vocals?|vocales|vocal texture|textura vocal)\b", lower))
    uncertain = bool(re.search(r"\b(no s[eé]|not sure|quiz[aá]s|maybe)\b", lower))
    if (no_vocals and vocals) or (vocals and uncertain) or ("voz o textura vocal" in lower):
        return "AMBIGUOUS"
    if no_vocals:
        return True
    if vocals:
        return False
    return None


def _explicit_generation_mode(text: str) -> tuple[str, list[str]]:
    lower = text.casefold()
    if re.search(r"\b(separate|separar|extract|extraer)\b.*\bstems?\b", lower):
        return "stem_separation", ["stems"]
    if re.search(r"\brepaint\b", lower):
        return "repaint", ["stereo"]
    if re.search(r"\baudio[- ]to[- ]audio\b", lower):
        return "audio_to_audio", ["stereo"]
    if re.search(r"\b(only|solo|únicamente)\s+midi\b", lower):
        return "midi_output", ["midi"]
    return "text_to_music", ["stems"] if re.search(r"\bstems?\b", lower) else ["stereo"]


def lucas_nl_to_generation_brief(
    user_text: str, *, reasoning_provider: _ReasoningProvider,
    default_duration_s: float = DEFAULT_PREVIEW_DURATION_S,
) -> LucasBriefHandoff:
    """Call Lucas's configured JSON reasoning boundary; accept only grounded fields."""
    intent = user_text.strip()
    identity = str(getattr(reasoning_provider, "identity", "configured-reasoning-provider"))
    if not intent:
        return LucasBriefHandoff(status="CLARIFICATION_NEEDED", reason="EMPTY_USER_INTENT", reasoning_provider=identity)
    if default_duration_s <= 0:
        raise ValueError("PRODUCT_DEFAULT_DURATION_INVALID")
    voice = _vocal_intent(intent)
    generation_mode, requested_outputs = _explicit_generation_mode(intent)
    if voice == "AMBIGUOUS":
        return LucasBriefHandoff(status="CLARIFICATION_NEEDED", reason="VOCAL_INTENT_AMBIGUOUS", reasoning_provider=identity)
    schema = LucasGenerationProposal.model_json_schema()
    prompt = (
        "Translate the user's music request into one JSON object matching this schema. "
        "Use null for unstated tempo, meter, duration, or vocals. Do not invent measurements. "
        "Each interpreted musical phrase needs an exact source_quote from the user text. "
        "No DAW actions, no file paths, no provider selection.\n"
        f"Schema: {json.dumps(schema, ensure_ascii=False)}\nUser request: {intent}"
    )
    try:
        proposal = LucasGenerationProposal.model_validate_json(
            reasoning_provider.reason_json_object(prompt, timeout_s=30.0)
        )
        tempo = _explicit_number(intent, r"\b(\d+(?:\.\d+)?)\s*BPM\b")
        meters = set(re.findall(r"\b\d{1,2}\s*/\s*\d{1,2}\b", intent))
        if len(meters) > 1:
            raise ValueError("CONFLICTING_EXPLICIT_METERS")
        meter = next(iter(meters), None)
        duration = _explicit_duration(intent)
        if proposal.tempo_bpm is not None and (tempo is None or abs(proposal.tempo_bpm - tempo) > 1e-6):
            raise ValueError("UNSUPPORTED_TEMPO_PROPOSAL")
        if proposal.meter is not None and proposal.meter.replace(" ", "") != (meter or "").replace(" ", ""):
            raise ValueError("UNSUPPORTED_METER_PROPOSAL")
        if proposal.target_duration_s is not None and (
            duration is None or abs(proposal.target_duration_s - duration) > 1e-6
        ):
            raise ValueError("UNSUPPORTED_DURATION_PROPOSAL")
        if proposal.instrumental is not None and (
            voice is None and proposal.instrumental is False
            or voice is not None and proposal.instrumental is not voice
        ):
            raise ValueError("UNSUPPORTED_VOCAL_PROPOSAL")
        if proposal.clarification_needed:
            return LucasBriefHandoff(status="CLARIFICATION_NEEDED", reason="LUCAS_REQUESTS_CLARIFICATION", reasoning_provider=identity)
        quotes: dict[str, str] = {}
        origins: dict[str, BriefFieldOrigin] = {
            "user_intent": BriefFieldOrigin.USER_EXPLICIT,
            "candidate_count": BriefFieldOrigin.PRODUCT_DEFAULT,
            "generation_mode": BriefFieldOrigin.USER_EXPLICIT if generation_mode != "text_to_music" else BriefFieldOrigin.PRODUCT_DEFAULT,
            "requested_outputs": BriefFieldOrigin.USER_EXPLICIT if requested_outputs != ["stereo"] else BriefFieldOrigin.PRODUCT_DEFAULT,
        }

        def grounded(item: QuotedInterpretation | None, field: str) -> str | None:
            if item is None:
                return None
            if item.source_quote.casefold() not in intent.casefold():
                raise ValueError(f"UNSUPPORTED_INTERPRETATION_QUOTE:{field}")
            quotes[field] = item.source_quote
            origins[field] = BriefFieldOrigin.MODEL_INTERPRETATION
            return item.value.strip()

        structural = [grounded(item, f"structural_intent[{index}]") for index, item in enumerate(proposal.structural_intent)]
        negative = [grounded(item, f"negative_constraints[{index}]") for index, item in enumerate(proposal.negative_constraints)]
        origins["target_duration_s"] = BriefFieldOrigin.USER_EXPLICIT if duration is not None else BriefFieldOrigin.PRODUCT_DEFAULT
        origins["instrumental"] = BriefFieldOrigin.USER_EXPLICIT if voice is not None else BriefFieldOrigin.PRODUCT_DEFAULT
        if tempo is not None:
            origins["tempo_bpm"] = BriefFieldOrigin.USER_EXPLICIT
        if meter is not None:
            origins["meter"] = BriefFieldOrigin.USER_EXPLICIT
        brief = GenerationBrief(
            brief_id=f"brief_{uuid4().hex[:16]}", user_intent=intent,
            target_duration_s=duration if duration is not None else default_duration_s,
            tempo_bpm=tempo, meter=meter, instrumental=voice if voice is not None else True,
            groove_intent=grounded(proposal.groove_intent, "groove_intent"),
            energy_intent=grounded(proposal.energy_intent, "energy_intent"),
            key_context=grounded(proposal.key_context, "key_context"),
            structural_intent=[value for value in structural if value],
            negative_constraints=[value for value in negative if value],
            field_origins=origins, field_evidence_quotes=quotes,
            generation_mode=generation_mode, requested_outputs=requested_outputs,
            no_write=True,
        )
        return LucasBriefHandoff(status="READY", brief=brief, reasoning_provider=identity)
    except ProviderError:
        return LucasBriefHandoff(status="PROVIDER_UNAVAILABLE", reason="LUCAS_REASONING_PROVIDER_FAILED", reasoning_provider=identity)
    except (OSError, TimeoutError):
        return LucasBriefHandoff(status="PROVIDER_UNAVAILABLE", reason="LUCAS_REASONING_PROVIDER_FAILED", reasoning_provider=identity)
    except (ValueError, ValidationError) as exc:
        return LucasBriefHandoff(status="MODEL_OUTPUT_REJECTED", reason=str(exc), reasoning_provider=identity)
