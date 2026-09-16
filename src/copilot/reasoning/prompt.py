from __future__ import annotations

import json

from copilot.reasoning.schema import PROMPT_VERSION, SCHEMA_VERSION
from copilot.schemas.evidence import EvidencePack

CAUSAL_HIERARCHY = (
    "arrangement/MIDI",
    "envelope",
    "sound selection",
    "level",
    "dynamic interaction",
    "EQ last",
)


def build_prompt(pack: EvidencePack) -> str:
    evidence = [
        {
            "evidence_id": item.evidence_id,
            "kind": item.kind.value,
            "name": item.name,
            "value": item.value,
            "unit": item.unit,
            "source_ref": item.source_ref,
            "region": item.region,
            "view": item.view,
            "signal_point": item.signal_point,
            "analysis_version": item.analysis_version,
            "quality": item.quality.value,
            "limitations": item.limitations,
            "project_token": item.project_token,
            "audible_token": item.audible_token,
            "target_token": item.target_token,
        }
        for item in pack.items
    ]
    entities = [item.model_dump() for item in pack.entities]
    limitations = [
        {
            "code": item.code,
            "detail": item.detail,
            "precision_ms": item.precision_ms,
        }
        for item in pack.limitations
    ]
    schema = {
        "category": "FindingType",
        "summary": "string",
        "status": "SUPPORTED|WEAKLY_SUPPORTED|INSUFFICIENT_EVIDENCE|NO_ACTION_REQUIRED|DIAGNOSIS_UNSTABLE",
        "confidence": "HIGH|MEDIUM|LOW",
        "hypotheses": [
            {
                "claim": "string",
                "evidence_refs": ["evidence_id"],
                "reasoning_summary": "string",
                "confidence": "HIGH|MEDIUM|LOW",
                "alternatives_considered": ["string"],
                "contradicting_evidence_refs": ["evidence_id"],
                "entity_refs": ["entity_id"],
            }
        ],
        "evidence_refs": ["evidence_id"],
        "contradicting_evidence_refs": ["evidence_id"],
        "limitations": ["string"],
        "candidate_actions": [
            {
                "action_type": "SHORTEN_BASS_RELEASE|CHANGE_BASS_NOTE_LENGTH|CHANGE_OCTAVE|REDUCE_LOW_BAND_ENERGY|CHANGE_SOUND_SELECTION|SIDECHAIN|NO_CHANGE",
                "target": "role or entity_id or null",
                "entity_refs": ["entity_id"],
                "reason": "string",
                "expected_effect": "string",
                "risk": "string",
                "evidence_refs": ["evidence_id"],
            }
        ],
        "requested_evidence": [
            {
                "request_kind": "CAPTURE_VIEW|READ_MIDI|READ_DEVICE_PARAMETERS|ANALYZE_REGION|READ_ROUTING",
                "why_needed": "string",
                "target": "string",
                "region": "string",
                "expected_information_gain": "string",
            }
        ],
        "entity_refs": ["entity_id"],
    }
    return "\n".join(
        [
            "LLM REASONS. CORE OWNS REALITY.",
            f"PROMPT_VERSION:{PROMPT_VERSION}",
            f"SCHEMA_VERSION:{SCHEMA_VERSION}",
            f"PACK_ID:{pack.pack_id}",
            f"DOMAIN:{pack.domain}",
            f"REGION:{pack.region}",
            f"ALIGNMENT:{pack.alignment_claim} ±{pack.alignment_envelope_ms:g} ms",
            f"PROJECT_TOKEN:{pack.project_token}",
            f"AUDIBLE_TOKEN:{pack.audible_token}",
            f"TARGET_TOKEN:{pack.target_token or ''}",
            "",
            "Hard boundary:",
            "- Interpret observations. Do not invent measurements, devices, MIDI, routing, Hz, counts, or plugin settings.",
            "- Do not claim an action succeeded. Do not call Ableton/LOM. Do not execute shell or Python.",
            "- All factual claims must quote evidence_id values supplied below.",
            "- Missing evidence cannot be invented. Uncertainty must be explicit.",
            "- NO_ACTION_REQUIRED is valid. INSUFFICIENT_EVIDENCE is valid. Do not force a diagnosis.",
            "- Candidate actions are strategies, not executable MusicPlans or Ableton commands.",
            "- Do not emit parameter mutation values unless that number exists in evidence.",
            "- Durations in ms/s and levels in dB must match evidence values (including fullmix_* fields).",
            "",
            "Measure vs diagnose:",
            "- Evidence items are facts. 'kick is weak' / 'bass is muddy' / 'dropout problem' / 'groove breathing' are interpretations, not observations.",
            "- Full-mix energy events are MEASURE facts (SILENCE / NEAR_SILENCE / STRONG_ENERGY_DIP + repetition stats).",
            "- Saying 'this energy dip occurs repeatedly' is allowed only when similarity/period evidence_ids support it.",
            "- Saying 'this is groove breathing' is DIAGNOSIS and needs category GROOVE_PATTERN with evidence.",
            "",
            "Evidence families present may include:",
            "- LowEndObservation (kick/bass temporal/spectral measures; ids without fm. prefix)",
            "- FullMixObservation (Main energy/spectral/transient/stereo/dynamics; ids with fm. prefix)",
            "",
            "Category guidance (hypothesis labels, not automatic truth):",
            "- TEMPORAL_MASKING / SPECTRAL_MASKING / EXCESSIVE_BASS_DECAY / KICK_DECAY_COLLISION: low-end family",
            "- LEVEL_IMBALANCE / ENERGY_STRUCTURE: Main level or silence/dip structure",
            "- ARRANGEMENT_COLLISION: arrangement/MIDI presence gaps when supported",
            "- GROOVE_PATTERN: repeating energy structure when repetition evidence supports it",
            "- POSSIBLE_DYNAMIC_INTERACTION: dynamics/pumping measures",
            "- NO_ACTION_REQUIRED / INSUFFICIENT_EVIDENCE: always valid when earned",
            "",
            "Alignment:",
            f"- Current capture alignment is LIMITED ±{pack.alignment_envelope_ms:g} ms.",
            "- Broad masking over hundreds of ms may be assessable.",
            "- Microtiming claims of 5–10 ms are NOT SUPPORTED.",
            "- Main-internal fullmix window/hop/transient precision values may be cited when present as evidence.",
            "",
            "Causal intervention hierarchy (prefer this order):",
            " → ".join(CAUSAL_HIERARCHY),
            "Low-frequency overlap does not imply bad EQ.",
            "A Main energy dip with high repetition_strength is not automatically a problem.",
            "",
            "Each hypothesis needs claim, evidence_refs, reasoning_summary, confidence, alternatives_considered.",
            "Empty evidence_refs is invalid. Unknown evidence_id is invalid.",
            "Include contradicting_evidence_refs when a phenomenon exists but another reading is possible.",
            "If causes cannot be distinguished, status=INSUFFICIENT_EVIDENCE and request typed evidence.",
            "If a measurable overlap or repeating dip is musically acceptable, status=NO_ACTION_REQUIRED is allowed.",
            "",
            "Return ONLY JSON matching this schema:",
            json.dumps(schema, indent=2),
            "",
            "ENTITIES:",
            json.dumps(entities, indent=2),
            "",
            "LIMITATIONS:",
            json.dumps(limitations, indent=2),
            "",
            "EVIDENCE:",
            json.dumps(evidence, indent=2),
        ]
    )
