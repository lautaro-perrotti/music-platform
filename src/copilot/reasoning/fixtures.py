from copilot.reasoning.schema import (
    ANALYSIS_VERSION,
    SCHEMA_VERSION,
    GroundedHypothesis,
    ReasoningCandidate,
    ReasoningOutput,
)
from copilot.schemas.diagnosis import CandidateActionType, Confidence, DiagnosisStatus, FindingType
from copilot.schemas.evidence import (
    CaptureQuality,
    EntityKind,
    EntityRef,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    EvidenceRequest,
    EvidenceRequestKind,
    ObservationLimitation,
)

REGION = "0->8qn"
PROJECT_TOKEN = "proj_reason_fixture_1"
AUDIBLE_TOKEN = "aud_reason_fixture_1"
TARGET_TOKEN = "tgt_kick_bass_1"


def _item(
    evidence_id: str,
    name: str,
    value,
    *,
    unit: str | None = None,
    view: str | None = "TRACK_ISOLATED",
    signal_point: str = "TRACK_POST_MIXER",
    quality: CaptureQuality = CaptureQuality.LIMITED,
    kind: EvidenceKind = EvidenceKind.MEASUREMENT,
    source_ref: str = "asset:frozen",
    limitations: list[str] | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        source_ref=source_ref,
        region=REGION,
        view=view,
        signal_point=signal_point,
        analysis_version=ANALYSIS_VERSION,
        name=name,
        value=value,
        unit=unit,
        quality=quality,
        limitations=limitations or ["ALIGNMENT_LIMITED"],
        project_token=PROJECT_TOKEN,
        audible_token=AUDIBLE_TOKEN,
        target_token=TARGET_TOKEN,
    )


def _kick_bass_entities(*extra: EntityRef) -> list[EntityRef]:
    return [
        EntityRef(entity_id="track:kick", kind=EntityKind.TRACK, name="Kick", role="kick"),
        EntityRef(entity_id="track:bass", kind=EntityKind.TRACK, name="Bass", role="bass"),
        *extra,
    ]


def _pack(
    pack_id: str,
    items: list[EvidenceItem],
    *,
    entities: list[EntityRef] | None = None,
    extra_limitations: list[ObservationLimitation] | None = None,
) -> EvidencePack:
    limitations = [
        ObservationLimitation(
            code="ALIGNMENT_LIMITED",
            limitation_id="ALIGNMENT_LIMITED_52MS",
            detail="Capture alignment LIMITED. Microtiming 5-10 ms is not supported.",
            precision_ms=52.0,
            capability_ms=[5.0, 10.0, 52.0],
        ),
        *(extra_limitations or []),
    ]
    return EvidencePack(
        pack_id=pack_id,
        analysis_version=ANALYSIS_VERSION,
        prompt_schema_version=SCHEMA_VERSION,
        region=REGION,
        project_token=PROJECT_TOKEN,
        audible_token=AUDIBLE_TOKEN,
        target_token=TARGET_TOKEN,
        alignment_claim="LIMITED",
        alignment_envelope_ms=52.0,
        items=items,
        entities=entities or _kick_bass_entities(),
        limitations=limitations,
        domain="lowend",
    )


def pack_clear_no_action() -> EvidencePack:
    return _pack(
        "CLEAR_NO_ACTION",
        [
            _item("ev.kick.count", "kick_attack_count", 8, unit="count", view="TRACK_ISOLATED"),
            _item("ev.overlap.count", "overlap_events", 2, unit="count"),
            _item("ev.overlap.median", "median_overlap_ms", 180.0, unit="ms"),
            _item("ev.kick.transient", "kick_transient_rms", 0.42),
            _item("ev.bass.band", "bass_energy_40_80_hz", 0.11),
            _item(
                "ev.intent",
                "section_intent",
                "dense_low_end_ok",
                kind=EvidenceKind.FACT,
                unit=None,
            ),
        ],
    )


def pack_clear_temporal() -> EvidencePack:
    return _pack(
        "CLEAR_TEMPORAL",
        [
            _item("ev.kick.count", "kick_attack_count", 8, unit="count"),
            _item("ev.overlap.count", "overlap_events", 8, unit="count"),
            _item("ev.persist", "median_persist_ms", 220.0, unit="ms"),
            _item("ev.bass.on", "bass_energy_on_attack", 0.38),
            _item("ev.bass.off", "bass_energy_off_kick", 0.36),
            _item("ev.kick.transient", "kick_transient_rms", 0.21),
        ],
    )


def pack_clear_spectral() -> EvidencePack:
    return _pack(
        "CLEAR_SPECTRAL",
        [
            _item("ev.kick.count", "kick_attack_count", 8, unit="count"),
            _item("ev.overlap.count", "overlap_events", 7, unit="count"),
            _item("ev.persist", "median_persist_ms", 80.0, unit="ms"),
            _item("ev.band", "dominant_attack_band_hz", [40.0, 60.0], unit="Hz"),
            _item("ev.centroid", "centroid_hz", 52.0, unit="Hz"),
            _item("ev.shared", "simultaneous_low_band", True, kind=EvidenceKind.FACT),
        ],
    )


def pack_ambiguous() -> EvidencePack:
    return _pack(
        "AMBIGUOUS",
        [
            _item("ev.kick.count", "kick_attack_count", 6, unit="count"),
            _item("ev.overlap.count", "overlap_events", 3, unit="count"),
            _item("ev.persist", "median_persist_ms", 90.0, unit="ms"),
            _item("ev.band", "dominant_attack_band_hz", [40.0, 80.0], unit="Hz"),
            _item("ev.drop", "context_rms_drop", 0.04),
        ],
        extra_limitations=[
            ObservationLimitation(code="MIDI_UNREAD", detail="Clip notes were not read."),
            ObservationLimitation(code="DEVICE_PARAMS_UNREAD", detail="Envelope state unknown."),
            ObservationLimitation(code="ROUTING_UNKNOWN", detail="Sidechain/routing unread."),
            ObservationLimitation(code="MISSING_ISOLATE", detail="No additional isolate beyond current views."),
        ],
    )


def pack_contradictory() -> EvidencePack:
    return _pack(
        "CONTRADICTORY",
        [
            _item("ev.kick.count", "kick_attack_count", 8, unit="count"),
            _item("ev.isolate.persist", "median_persist_ms", 240.0, unit="ms", view="TRACK_ISOLATED"),
            _item(
                "ev.context.drop",
                "context_rms_drop",
                0.01,
                view="TRACK_CONTEXT_REMOVAL",
                signal_point="MAIN_FINAL",
            ),
            _item("ev.kick.transient", "kick_transient_rms", 0.55, view="TRACK_ISOLATED"),
            _item("ev.overlap.count", "overlap_events", 8, unit="count"),
        ],
        extra_limitations=[
            ObservationLimitation(
                code="CONFLICTING_OBSERVATIONS",
                detail="Isolate persist high; context-removal drop negligible; kick transient remains strong.",
            )
        ],
    )


def pack_hallucination_trap() -> EvidencePack:
    return _pack(
        "HALLUCINATION_TRAP",
        [
            _item("ev.kick.count", "kick_attack_count", 4, unit="count"),
            _item("ev.overlap.count", "overlap_events", 1, unit="count"),
            _item("ev.persist", "median_persist_ms", 160.0, unit="ms"),
            _item("ev.centroid", "centroid_hz", 48.0, unit="Hz"),
        ],
        extra_limitations=[
            ObservationLimitation(code="ROUTING_UNKNOWN", detail="Routing was not read."),
        ],
    )


def pack_ambiguous_entity() -> EvidencePack:
    return _pack(
        "AMBIGUOUS_ENTITY",
        [
            _item("ev.kick.count", "kick_attack_count", 4, unit="count"),
            _item("ev.overlap.count", "overlap_events", 2, unit="count"),
        ],
        entities=_kick_bass_entities(
            EntityRef(
                entity_id="device:bass/eq8#0",
                kind=EntityKind.DEVICE,
                name="EQ Eight",
                role="eq",
                parent_id="track:bass",
                class_name="Eq8",
            ),
            EntityRef(
                entity_id="device:kick/eq8#0",
                kind=EntityKind.DEVICE,
                name="EQ Eight",
                role="eq",
                parent_id="track:kick",
                class_name="Eq8",
            ),
        ),
    )


ALL_PACKS = {
    "CLEAR_NO_ACTION": pack_clear_no_action,
    "CLEAR_TEMPORAL": pack_clear_temporal,
    "CLEAR_SPECTRAL": pack_clear_spectral,
    "AMBIGUOUS": pack_ambiguous,
    "CONTRADICTORY": pack_contradictory,
    "HALLUCINATION_TRAP": pack_hallucination_trap,
    "AMBIGUOUS_ENTITY": pack_ambiguous_entity,
}


def output_clear_no_action() -> ReasoningOutput:
    return ReasoningOutput(
        category=FindingType.NO_ACTION_REQUIRED,
        summary="Overlap exists (2 of 8 kick attacks, median 180 ms) but kick transient rms 0.42 stays distinct and section_intent is dense_low_end_ok.",
        status=DiagnosisStatus.NO_ACTION_REQUIRED,
        confidence=Confidence.MEDIUM,
        hypotheses=[
            GroundedHypothesis(
                claim="Low-end overlap is present but does not require a mix change in this region.",
                evidence_refs=["ev.overlap.count", "ev.kick.transient", "ev.intent"],
                reasoning_summary="2 overlap events of 8 attacks with a clear kick transient supports leaving the dense low end.",
                confidence=Confidence.MEDIUM,
                alternatives_considered=["TEMPORAL_MASKING", "SPECTRAL_MASKING"],
                contradicting_evidence_refs=["ev.overlap.median"],
                entity_refs=["track:kick", "track:bass"],
            )
        ],
        evidence_refs=["ev.kick.count", "ev.overlap.count", "ev.kick.transient", "ev.intent"],
        contradicting_evidence_refs=["ev.overlap.median", "ev.bass.band"],
        limitations=["ALIGNMENT_LIMITED"],
        candidate_actions=[
            ReasoningCandidate(
                action_type=CandidateActionType.NO_CHANGE,
                target="mix",
                entity_refs=["track:kick", "track:bass"],
                reason="Measurable overlap is compatible with section intent.",
                expected_effect="Preserve the dense low end.",
                risk="Leaving a later-section masking problem unaddressed.",
                evidence_refs=["ev.intent", "ev.kick.transient"],
            )
        ],
        requested_evidence=[],
        entity_refs=["track:kick", "track:bass"],
    )


def output_clear_temporal() -> ReasoningOutput:
    return ReasoningOutput(
        category=FindingType.TEMPORAL_MASKING,
        summary="Bass energy stays high through all 8 kick attacks (on-attack 0.38 vs off-kick 0.36) with median persist 220 ms.",
        status=DiagnosisStatus.SUPPORTED,
        confidence=Confidence.MEDIUM,
        hypotheses=[
            GroundedHypothesis(
                claim="Sustained bass envelope is the probable cause, not a missing EQ notch.",
                evidence_refs=["ev.persist", "ev.overlap.count", "ev.bass.on", "ev.bass.off"],
                reasoning_summary="Overlap on 8 of 8 attacks plus nearly equal on/off bass energy points to duration/envelope before EQ.",
                confidence=Confidence.MEDIUM,
                alternatives_considered=["SPECTRAL_MASKING", "ARRANGEMENT_COLLISION", "LEVEL_IMBALANCE"],
                contradicting_evidence_refs=["ev.kick.transient"],
                entity_refs=["track:bass"],
            )
        ],
        evidence_refs=["ev.kick.count", "ev.overlap.count", "ev.persist", "ev.bass.on", "ev.bass.off"],
        contradicting_evidence_refs=["ev.kick.transient"],
        limitations=["ALIGNMENT_LIMITED", "MIDI_UNREAD"],
        candidate_actions=[
            ReasoningCandidate(
                action_type=CandidateActionType.SHORTEN_BASS_RELEASE,
                target="track:bass",
                entity_refs=["track:bass"],
                reason="Persist 220 ms through kick attacks is an envelope/duration symptom.",
                expected_effect="More space at kick attacks without a permanent EQ.",
                risk="Bass may feel shorter than the section wants.",
                evidence_refs=["ev.persist", "ev.overlap.count"],
            ),
            ReasoningCandidate(
                action_type=CandidateActionType.CHANGE_BASS_NOTE_LENGTH,
                target="track:bass",
                entity_refs=["track:bass"],
                reason="Arrangement/MIDI length is earlier in the causal hierarchy than EQ.",
                expected_effect="Shorter notes reduce overlap at attacks.",
                risk="Groove change.",
                evidence_refs=["ev.persist"],
            ),
        ],
        requested_evidence=[
            EvidenceRequest(
                request_kind=EvidenceRequestKind.READ_MIDI,
                why_needed="Distinguish envelope vs note length.",
                target="track:bass",
                region=REGION,
                expected_information_gain="Whether bass notes already end before kick attacks.",
            )
        ],
        entity_refs=["track:kick", "track:bass"],
    )


def output_clear_spectral() -> ReasoningOutput:
    return ReasoningOutput(
        category=FindingType.SPECTRAL_MASKING,
        summary="7 of 8 attacks share 40 Hz to 60 Hz concentration (centroid 52 Hz) without long persist (80 ms).",
        status=DiagnosisStatus.SUPPORTED,
        confidence=Confidence.MEDIUM,
        hypotheses=[
            GroundedHypothesis(
                claim="Concurrent low-band concentration, not long bass decay, is the main overlap mechanism.",
                evidence_refs=["ev.overlap.count", "ev.band", "ev.centroid", "ev.persist"],
                reasoning_summary="Short persist with simultaneous 40 Hz to 60 Hz energy favors sound selection or octave over release shortening.",
                confidence=Confidence.MEDIUM,
                alternatives_considered=["TEMPORAL_MASKING", "LEVEL_IMBALANCE"],
                contradicting_evidence_refs=["ev.persist"],
                entity_refs=["track:kick", "track:bass"],
            )
        ],
        evidence_refs=["ev.kick.count", "ev.overlap.count", "ev.band", "ev.centroid", "ev.persist"],
        contradicting_evidence_refs=["ev.persist"],
        limitations=["ALIGNMENT_LIMITED"],
        candidate_actions=[
            ReasoningCandidate(
                action_type=CandidateActionType.CHANGE_OCTAVE,
                target="track:bass",
                entity_refs=["track:bass"],
                reason="Shared 40-60 Hz band can be moved by register before EQ.",
                expected_effect="Less concurrent low-band energy at attacks.",
                risk="Bass register change.",
                evidence_refs=["ev.band", "ev.centroid"],
            ),
            ReasoningCandidate(
                action_type=CandidateActionType.CHANGE_SOUND_SELECTION,
                target="track:bass",
                entity_refs=["track:bass"],
                reason="Sound selection sits above EQ in the causal hierarchy.",
                expected_effect="Different spectrum at the same notes.",
                risk="Timbre change.",
                evidence_refs=["ev.band"],
            ),
        ],
        requested_evidence=[],
        entity_refs=["track:kick", "track:bass"],
    )


def output_ambiguous() -> ReasoningOutput:
    return ReasoningOutput(
        category=FindingType.INSUFFICIENT_EVIDENCE,
        summary="Overlap is 3 of 6 attacks with mixed persist 90 ms and 40 Hz to 80 Hz concentration; MIDI, envelope, and routing are unread so causes cannot be ranked.",
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        confidence=Confidence.LOW,
        hypotheses=[
            GroundedHypothesis(
                claim="Temporal, spectral, arrangement, and level causes are not distinguishable from current evidence.",
                evidence_refs=["ev.overlap.count", "ev.persist", "ev.band", "ev.drop"],
                reasoning_summary="Partial overlap plus unread MIDI/envelope/routing should not be collapsed into EQ.",
                confidence=Confidence.LOW,
                alternatives_considered=[
                    "TEMPORAL_MASKING",
                    "SPECTRAL_MASKING",
                    "ARRANGEMENT_COLLISION",
                    "LEVEL_IMBALANCE",
                ],
                contradicting_evidence_refs=[],
                entity_refs=["track:kick", "track:bass"],
            )
        ],
        evidence_refs=["ev.kick.count", "ev.overlap.count", "ev.persist", "ev.band", "ev.drop"],
        contradicting_evidence_refs=[],
        limitations=["MIDI_UNREAD", "DEVICE_PARAMS_UNREAD", "ROUTING_UNKNOWN", "MISSING_ISOLATE", "ALIGNMENT_LIMITED"],
        candidate_actions=[
            ReasoningCandidate(
                action_type=CandidateActionType.NO_CHANGE,
                target="mix",
                reason="Do not guess among timing, sound, arrangement, and balance.",
                expected_effect="Wait for requested evidence.",
                risk="Delaying a needed change.",
                evidence_refs=["ev.overlap.count"],
            )
        ],
        requested_evidence=[
            EvidenceRequest(
                request_kind=EvidenceRequestKind.READ_MIDI,
                why_needed="Separate arrangement/note length from envelope.",
                target="track:bass",
                region=REGION,
                expected_information_gain="Bass note end times vs kick attacks.",
            ),
            EvidenceRequest(
                request_kind=EvidenceRequestKind.READ_DEVICE_PARAMETERS,
                why_needed="See whether a long release is even present.",
                target="track:bass",
                region=REGION,
                expected_information_gain="Envelope/release state if any.",
            ),
            EvidenceRequest(
                request_kind=EvidenceRequestKind.READ_ROUTING,
                why_needed="Dynamic interaction vs static overlap.",
                target="track:bass",
                region=REGION,
                expected_information_gain="Whether sidechain/routing exists.",
            ),
        ],
        entity_refs=["track:kick", "track:bass"],
    )


def output_contradictory() -> ReasoningOutput:
    return ReasoningOutput(
        category=FindingType.TEMPORAL_MASKING,
        summary="Isolate persist is 240 ms on 8 overlaps, but context-removal drop is 0.01 and kick transient rms 0.55 remains strong.",
        status=DiagnosisStatus.DIAGNOSIS_UNSTABLE,
        confidence=Confidence.LOW,
        hypotheses=[
            GroundedHypothesis(
                claim="Isolate and context-removal disagree on whether bass decay is audible in the mix.",
                evidence_refs=["ev.isolate.persist", "ev.context.drop", "ev.kick.transient"],
                reasoning_summary="Conflicting observations should not be collapsed into a single supported cause.",
                confidence=Confidence.LOW,
                alternatives_considered=["NO_ACTION_REQUIRED", "TEMPORAL_MASKING"],
                contradicting_evidence_refs=["ev.kick.transient", "ev.context.drop"],
                entity_refs=["track:kick", "track:bass"],
            )
        ],
        evidence_refs=["ev.kick.count", "ev.isolate.persist", "ev.context.drop", "ev.kick.transient"],
        contradicting_evidence_refs=["ev.context.drop", "ev.kick.transient"],
        limitations=["CONFLICTING_OBSERVATIONS", "ALIGNMENT_LIMITED"],
        candidate_actions=[
            ReasoningCandidate(
                action_type=CandidateActionType.NO_CHANGE,
                target="mix",
                reason="Unstable evidence; do not pick a side.",
                expected_effect="Request another region or isolated view rather than write.",
                risk="Leaving a real masking problem.",
                evidence_refs=["ev.context.drop", "ev.isolate.persist"],
            )
        ],
        requested_evidence=[
            EvidenceRequest(
                request_kind=EvidenceRequestKind.ANALYZE_REGION,
                why_needed="See whether the conflict is local to 0->8qn.",
                target="track:bass",
                region="8->16qn",
                expected_information_gain="Whether isolate/context disagreement repeats.",
            )
        ],
        entity_refs=["track:kick", "track:bass"],
    )


def output_hallucination_honest() -> ReasoningOutput:
    return ReasoningOutput(
        category=FindingType.INSUFFICIENT_EVIDENCE,
        summary="Only 4 kick attacks and 1 overlap event are measured; routing is unknown; session entities do not include a named synth plugin.",
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        confidence=Confidence.LOW,
        hypotheses=[
            GroundedHypothesis(
                claim="Current facts cannot support a named-plugin or precise-peak-frequency diagnosis.",
                evidence_refs=["ev.kick.count", "ev.overlap.count", "ev.centroid"],
                reasoning_summary="Centroid is 48 Hz. Routing unread. Plugin identity and a precise spectral peak are not in evidence.",
                confidence=Confidence.LOW,
                alternatives_considered=["TEMPORAL_MASKING", "NO_ACTION_REQUIRED"],
                entity_refs=["track:kick", "track:bass"],
            )
        ],
        evidence_refs=["ev.kick.count", "ev.overlap.count", "ev.centroid", "ev.persist"],
        contradicting_evidence_refs=[],
        limitations=["ROUTING_UNKNOWN", "ALIGNMENT_LIMITED"],
        candidate_actions=[
            ReasoningCandidate(
                action_type=CandidateActionType.NO_CHANGE,
                target="mix",
                reason="Refuse to invent missing plugin/frequency facts.",
                expected_effect="Keep uncertainty explicit.",
                risk="Under-diagnosing a real issue.",
                evidence_refs=["ev.kick.count"],
            )
        ],
        requested_evidence=[
            EvidenceRequest(
                request_kind=EvidenceRequestKind.READ_ROUTING,
                why_needed="Routing is unknown.",
                target="track:bass",
                region=REGION,
                expected_information_gain="Whether dynamic interaction is even possible.",
            )
        ],
        entity_refs=["track:kick", "track:bass"],
    )


def output_hallucination_trap() -> ReasoningOutput:
    """Adversarial payload: invents Serum, 63 Hz, 7 collisions, 8 ms timing."""
    return ReasoningOutput(
        category=FindingType.TEMPORAL_MASKING,
        summary="Serum bass has 7 collisions and energy peaks at 63 Hz; kick is 8 ms late.",
        status=DiagnosisStatus.SUPPORTED,
        confidence=Confidence.HIGH,
        hypotheses=[
            GroundedHypothesis(
                claim="Reduce release on Serum because 7 collisions peak at 63 Hz.",
                evidence_refs=["ev.missing"],
                reasoning_summary="Invented plugin and frequency.",
                confidence=Confidence.HIGH,
                alternatives_considered=[],
                entity_refs=["device:serum"],
            )
        ],
        evidence_refs=["ev.missing"],
        contradicting_evidence_refs=[],
        limitations=[],
        candidate_actions=[
            ReasoningCandidate(
                action_type=CandidateActionType.REDUCE_LOW_BAND_ENERGY,
                target="Serum",
                entity_refs=["device:serum"],
                reason="set_device_parameter release=0.23 on Serum",
                expected_effect="Fix 63 Hz mud.",
                risk="none",
                evidence_refs=["ev.missing"],
            )
        ],
        requested_evidence=[],
        entity_refs=["device:serum"],
    )


def output_microtiming() -> ReasoningOutput:
    return ReasoningOutput(
        category=FindingType.TEMPORAL_MASKING,
        summary="Kick is 8 ms late relative to bass; this microtiming should be quantized.",
        status=DiagnosisStatus.SUPPORTED,
        confidence=Confidence.HIGH,
        hypotheses=[
            GroundedHypothesis(
                claim="An 8 ms kick delay is the masking cause.",
                evidence_refs=["ev.kick.count"],
                reasoning_summary="Claims 8 ms precision under LIMITED ±52 ms alignment.",
                confidence=Confidence.HIGH,
                alternatives_considered=["NO_ACTION_REQUIRED"],
                contradicting_evidence_refs=["ev.overlap.count"],
                entity_refs=["track:kick"],
            )
        ],
        evidence_refs=["ev.kick.count"],
        contradicting_evidence_refs=["ev.overlap.count"],
        limitations=["ALIGNMENT_LIMITED"],
        candidate_actions=[
            ReasoningCandidate(
                action_type=CandidateActionType.NO_CHANGE,
                target="track:kick",
                reason="Would require unsupported 8 ms correction.",
                expected_effect="None.",
                risk="False precision.",
                evidence_refs=["ev.kick.count"],
            )
        ],
        entity_refs=["track:kick"],
    )


def output_ambiguous_eq_name() -> ReasoningOutput:
    return ReasoningOutput(
        category=FindingType.SPECTRAL_MASKING,
        summary="Reduce the low band on EQ Eight.",
        status=DiagnosisStatus.WEAKLY_SUPPORTED,
        confidence=Confidence.LOW,
        hypotheses=[
            GroundedHypothesis(
                claim="EQ Eight is masking the kick.",
                evidence_refs=["ev.overlap.count"],
                reasoning_summary="Name matches two devices.",
                confidence=Confidence.LOW,
                alternatives_considered=["NO_ACTION_REQUIRED"],
                contradicting_evidence_refs=["ev.kick.count"],
            )
        ],
        evidence_refs=["ev.overlap.count"],
        contradicting_evidence_refs=["ev.kick.count"],
        limitations=["ALIGNMENT_LIMITED"],
        candidate_actions=[
            ReasoningCandidate(
                action_type=CandidateActionType.REDUCE_LOW_BAND_ENERGY,
                target="EQ Eight",
                reason="Ambiguous EQ Eight reference.",
                expected_effect="Less low band.",
                risk="Wrong track.",
                evidence_refs=["ev.overlap.count"],
            )
        ],
    )


SCRIPTED_VALID = {
    "CLEAR_NO_ACTION": output_clear_no_action,
    "CLEAR_TEMPORAL": output_clear_temporal,
    "CLEAR_SPECTRAL": output_clear_spectral,
    "AMBIGUOUS": output_ambiguous,
    "CONTRADICTORY": output_contradictory,
    "HALLUCINATION_TRAP": output_hallucination_honest,
}


EXPECTED_STATUS = {
    "CLEAR_NO_ACTION": DiagnosisStatus.NO_ACTION_REQUIRED,
    "CLEAR_TEMPORAL": DiagnosisStatus.SUPPORTED,
    "CLEAR_SPECTRAL": DiagnosisStatus.SUPPORTED,
    "AMBIGUOUS": DiagnosisStatus.INSUFFICIENT_EVIDENCE,
    "CONTRADICTORY": DiagnosisStatus.DIAGNOSIS_UNSTABLE,
    "HALLUCINATION_TRAP": DiagnosisStatus.INSUFFICIENT_EVIDENCE,
}

EXPECTED_CATEGORY = {
    "CLEAR_NO_ACTION": FindingType.NO_ACTION_REQUIRED,
    "CLEAR_TEMPORAL": FindingType.TEMPORAL_MASKING,
    "CLEAR_SPECTRAL": FindingType.SPECTRAL_MASKING,
    "AMBIGUOUS": FindingType.INSUFFICIENT_EVIDENCE,
    "CONTRADICTORY": FindingType.TEMPORAL_MASKING,
    "HALLUCINATION_TRAP": FindingType.INSUFFICIENT_EVIDENCE,
}

EXPECTED_ACTION_FAMILY = {
    "CLEAR_NO_ACTION": {CandidateActionType.NO_CHANGE},
    "CLEAR_TEMPORAL": {
        CandidateActionType.SHORTEN_BASS_RELEASE,
        CandidateActionType.CHANGE_BASS_NOTE_LENGTH,
    },
    "CLEAR_SPECTRAL": {
        CandidateActionType.CHANGE_OCTAVE,
        CandidateActionType.CHANGE_SOUND_SELECTION,
    },
    "AMBIGUOUS": {CandidateActionType.NO_CHANGE},
    "CONTRADICTORY": {CandidateActionType.NO_CHANGE},
    "HALLUCINATION_TRAP": {CandidateActionType.NO_CHANGE},
}
