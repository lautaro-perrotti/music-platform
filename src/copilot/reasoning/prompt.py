from __future__ import annotations

import json
from typing import Any

from copilot.reasoning.evidence_input import scoped_evidence, serialize_for_prompt
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

CAUSAL_STANCES = (
    "COINCIDES_WITH",
    "COMPATIBLE_WITH",
    "SUGGESTS",
    "WEAKLY_SUPPORTS",
    "STRONGLY_SUPPORTS",
    "CAUSE_UNRESOLVED",
)


def build_prompt(pack: EvidencePack, view: Any | None = None) -> str:
    scoped = scoped_evidence(pack, view)
    evidence_view = serialize_for_prompt(scoped)
    entities = [item.model_dump() for item in pack.entities]
    limitations = [
        {
            "code": item.code,
            "detail": item.detail,
            "precision_ms": item.precision_ms,
        }
        for item in pack.limitations
    ]
    for row in evidence_view.get("limitations") or []:
        code = row.get("code") if isinstance(row, dict) else None
        if code and not any(item["code"] == code for item in limitations):
            limitations.append(
                {
                    "code": code,
                    "detail": row.get("detail") or "",
                    "precision_ms": row.get("precision_ms"),
                }
            )
    schema = {
        "question": "string",
        "scope": "string",
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
                "missing_evidence": ["string"],
                "limitations": ["string"],
                "status": "SUPPORTED|WEAKLY_SUPPORTED|INSUFFICIENT_EVIDENCE|NO_ACTION_REQUIRED|DIAGNOSIS_UNSTABLE|null",
                "causal_stance": "COINCIDES_WITH|COMPATIBLE_WITH|SUGGESTS|WEAKLY_SUPPORTS|STRONGLY_SUPPORTS|CAUSE_UNRESOLVED|null",
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
        "candidate_strategies": [
            {
                "strategy": "high-level musical strategy, not an executable write",
                "reason": "string",
                "evidence_refs": ["evidence_id"],
                "entity_refs": ["entity_id"],
            }
        ],
        "requested_evidence": [
            {
                "request_kind": "CAPTURE_VIEW|READ_MIDI|READ_DEVICE_PARAMETERS|ANALYZE_REGION|READ_ROUTING",
                "why_needed": "string",
                "target": "subject identity",
                "region": "string",
                "expected_information_gain": "string",
                "goal": "string",
                "required_evidence_kinds": ["MEASUREMENT|FACT|RELATIONSHIP|..."],
                "priority": "HIGH|NORMAL|LOW",
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
            f"PROJECT_IDENTITY:{evidence_view.get('project_identity') or pack.project_token}",
            "",
            "Hard boundary:",
            "- You receive a scoped EvidenceView. Do not invent repository history, parent/child packs, or unrelated logs.",
            "- Interpret observations. Do not invent measurements, devices, MIDI, routing, Hz, counts, or plugin settings.",
            "- You are never measurement authority. pdsp.* and DspObservation facts are MEASUREMENT (relational = RELATIONSHIP).",
            "- Do not claim an action succeeded. Do not call Ableton/LOM. Do not execute shell or Python.",
            "- Never output: run capture command, call CLI X, execute Python Y, python -m copilot.",
            "- All quantitative factual statements must quote evidence_id values that contain those numbers and units.",
            "- Missing evidence cannot be invented. Uncertainty must be explicit.",
            "- NO_ACTION_REQUIRED is valid. INSUFFICIENT_EVIDENCE is valid. Do not force a diagnosis.",
            "- INSUFFICIENT_EVIDENCE is not NO_ACTION_REQUIRED. DIAGNOSIS_UNSTABLE is not INSUFFICIENT_EVIDENCE.",
            "- Candidate actions and candidate_strategies are strategies, not executable MusicPlans or Ableton commands.",
            "- Do not emit parameter mutation values unless that number exists in evidence.",
            "- Durations in ms/s and levels in dB must match evidence values (including fullmix_* fields).",
            "- Confidence is HIGH/MEDIUM/LOW. Do not invent a combined percentage. combined remains null.",
            "",
            "Allowed interpretation example:",
            "- Measured source energy decreases during the event while MIDI remains present, which is compatible with attenuation somewhere after note generation.",
            "Not allowed without evidence:",
            "- The compressor is suppressing the synth by 6 dB.",
            "",
            "DSP facts:",
            "- LEVEL/DYNAMICS, SPECTRAL, TRANSIENT, STEREO, RHYTHM FACTS, TONAL FACTS, TIMBRE FACTS, RELATIONAL DSP are facts.",
            "- POTENTIAL_OVERLAP is a measured relationship, not 'muddy' and not a 'masking problem'.",
            "- High spectral overlap MAY be reasoned as 'potential masking is plausible'. Do not conclude 'apply -3 dB at 300 Hz' unless evidence and later action reasoning support it.",
            "- CAPTURE_FAILED is not measured silence.",
            "- KEY_IS_CANDIDATE_SET_NOT_CERTAIN means no single key is certain.",
            "- Non-canonical DSP limitation codes (ITU_LRA_NOT_IMPLEMENTED, TRUE_PEAK_4X_POLYPHASE_APPROXIMATION, etc.) must be copied into limitations, never dropped.",
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
            "- Physical DSP V2 (pdsp.* MEASUREMENT / RELATIONSHIP)",
            "",
            "Contradictions:",
            "- Surface support, counterevidence, and unresolved contradiction.",
            "- Fusion status CONTRADICT: do not pick one side as fact. PARTIALLY_AGREE and NOT_COMPARABLE stay visible.",
            "- HIGH confidence cannot ignore strong contradictory evidence.",
            "",
            "Causal language (not deep causal diagnosis):",
            " ".join(CAUSAL_STANCES),
            "- Temporal coincidence is not causation. Prefer COMPATIBLE_WITH / COINCIDES_WITH / CAUSE_UNRESOLVED.",
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
            "Each hypothesis needs claim, evidence_refs, reasoning_summary, confidence, alternatives_considered,",
            "contradicting_evidence_refs, missing_evidence, limitations, status.",
            "Empty evidence_refs is invalid. Unknown evidence_id is invalid. Stale evidence is not a current fact.",
            "If causes cannot be distinguished, status=INSUFFICIENT_EVIDENCE and request the smallest useful next evidence.",
            "Good next evidence: READ_DEVICE_PARAMETERS for one named subject in a named region, with why.",
            "Bad next evidence: analyze everything.",
            "If a measurable overlap or repeating dip is musically acceptable, status=NO_ACTION_REQUIRED is allowed.",
            "Candidate strategies: inspect attenuation; rebalance kick/bass relationship; reduce competing high-frequency activity; increase section contrast. No ungrounded exact parameter values.",
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
            "EVIDENCE_VIEW:",
            json.dumps(evidence_view, indent=2),
        ]
    )
