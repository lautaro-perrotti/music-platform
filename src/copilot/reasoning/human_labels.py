from __future__ import annotations

from pydantic import BaseModel, Field


class HumanLabeledCase(BaseModel):
    """Future producer labels. Musical diagnosis is not unique ground truth."""

    case_id: str
    region: str
    observation_ids: list[str]
    human_diagnosis: str
    acceptable_alternatives: list[str] = Field(default_factory=list)
    unacceptable_claims: list[str] = Field(default_factory=list)
    acceptable_actions: list[str] = Field(default_factory=list)
    no_action_acceptable: bool = False
    notes: str = ""


def seed_cases() -> list[HumanLabeledCase]:
    return [
        HumanLabeledCase(
            case_id="human.lowend.dense_ok",
            region="0->8qn",
            observation_ids=["ev.overlap.count", "ev.kick.transient", "ev.intent"],
            human_diagnosis="NO_ACTION_REQUIRED",
            acceptable_alternatives=["WEAKLY_SUPPORTED temporal, still NO_CHANGE"],
            unacceptable_claims=["Serum", "63 Hz peak", "kick is 8 ms late"],
            acceptable_actions=["NO_CHANGE"],
            no_action_acceptable=True,
            notes="Benign overlap can be left alone.",
        ),
        HumanLabeledCase(
            case_id="human.lowend.ambiguous",
            region="0->8qn",
            observation_ids=["ev.overlap.count", "ev.persist", "ev.band"],
            human_diagnosis="INSUFFICIENT_EVIDENCE",
            acceptable_alternatives=["WEAKLY_SUPPORTED with evidence requests, no EQ default"],
            unacceptable_claims=["must EQ", "unique cause is release"],
            acceptable_actions=["NO_CHANGE", "request MIDI", "request envelope"],
            no_action_acceptable=True,
            notes="Do not pretend one musical ground truth.",
        ),
    ]
