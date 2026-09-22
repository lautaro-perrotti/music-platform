"""Core-side Executive Producer boundary.

This adapter does not compose music.  It accepts an already-grounded brief and
candidate evidence, exposes only provider capabilities, and returns typed
intent for downstream validation.  Lucas remains the executive producer when
available; Core never fabricates a replacement plan.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from copilot.music_generation.schemas import GenerationBrief, GeneratorCapability


class ExecutiveProducerContext(BaseModel):
    user_intent: str
    reference_context: dict[str, Any] = Field(default_factory=dict)
    project_context: dict[str, Any] = Field(default_factory=dict)
    preference_context: dict[str, Any] = Field(default_factory=dict)
    active_capabilities: list[GeneratorCapability] = Field(default_factory=list)
    candidate_evidence: list[dict[str, Any]] = Field(default_factory=list)


class GeneratorEditIntent(BaseModel):
    operation: str
    parent_candidate_id: str
    target_region: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    required_capability: GeneratorCapability
    evidence_refs: list[str] = Field(default_factory=list)


class ProductionRefinementIntent(BaseModel):
    operation: str
    target: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    write_authority: str = "ProductionCompiler->SafeWrite"


class ExecutiveProducerDecision(BaseModel):
    generation_brief: GenerationBrief
    generator_edit_intents: list[GeneratorEditIntent] = Field(default_factory=list)
    production_refinement_intents: list[ProductionRefinementIntent] = Field(default_factory=list)
    rejected_or_unselected_choices: list[dict[str, Any]] = Field(default_factory=list)
    provider_status: str = "REAL_PROVIDER_REQUIRED"
    no_musical_invention: bool = True
    no_ableton_access: bool = True


class ExecutiveProducerAdapter:
    """Normalize a provider-produced brief without inventing musical facts."""

    def build_decision(
        self,
        context: ExecutiveProducerContext,
        *,
        generation_brief: GenerationBrief,
        provider_status: str = "REAL_PROVIDER",
        generator_edit_intents: list[GeneratorEditIntent] | None = None,
        production_refinement_intents: list[ProductionRefinementIntent] | None = None,
        rejected_or_unselected_choices: list[dict[str, Any]] | None = None,
    ) -> ExecutiveProducerDecision:
        if not context.user_intent.strip():
            raise ValueError("EXECUTIVE_PRODUCER_USER_INTENT_REQUIRED")
        if generation_brief.no_write is not True:
            raise ValueError("GENERATION_BRIEF_MUST_BE_NO_WRITE")
        available = set(context.active_capabilities)
        for intent in generator_edit_intents or []:
            if intent.required_capability not in available:
                raise ValueError(f"GENERATOR_CAPABILITY_UNAVAILABLE:{intent.required_capability}")
        return ExecutiveProducerDecision(
            generation_brief=generation_brief,
            generator_edit_intents=generator_edit_intents or [],
            production_refinement_intents=production_refinement_intents or [],
            rejected_or_unselected_choices=rejected_or_unselected_choices or [],
            provider_status=provider_status,
        )

