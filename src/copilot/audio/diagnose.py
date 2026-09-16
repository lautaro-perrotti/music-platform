from __future__ import annotations

import time
from uuid import uuid4

import soundfile as sf

from copilot.audio.live_capture import AudioAsset
from copilot.audio.lowend import (
    band_energy_over_time,
    context_change,
    context_around_kicks,
    detect_transients,
    estimate_fundamental,
    low_frequency_envelope,
    overlap_at_attacks,
    prune_kick_attacks,
    spectral_overlap_over_time,
)
from copilot.schemas.diagnosis import (
    CandidateAction,
    Confidence,
    EvidenceStatus,
    Finding,
    FindingType,
    Hypothesis,
    MusicDiagnosis,
    ObservationRef,
)
from copilot.schemas.observation import SignalPoint

REGION_EPS = 1e-3


def _load_mono_path(path: str):
    data, sr = sf.read(path, always_2d=True)
    return data, int(sr)


def _limitations_from_asset(asset: AudioAsset) -> list[str]:
    limits: list[str] = []
    if asset.signal_point is SignalPoint.TRACK_POST_MIXER_THROUGH_MASTER_CHAIN:
        limits.append("THROUGH_MASTER_CHAIN")
    if asset.signal_point is SignalPoint.MAIN_NOT_FINAL:
        limits.append("MAIN_NOT_FINAL")
    if asset.capture_quality and asset.capture_quality != "OK":
        if "QUALITY_WARNING" in asset.capture_quality or asset.capture_quality == "CAPTURE_QUALITY_WARNING":
            limits.append("REGION_ALIGNMENT_PARTIAL")
        limits.append(asset.capture_quality)
    if asset.signal_point_label:
        limits.append(f"signal_point_label={asset.signal_point_label}")
    return limits


def _ref(asset: AudioAsset, role: str) -> ObservationRef:
    start = float(asset.analysis_start_beat if asset.analysis_start_beat is not None else asset.start_beat)
    end = float(asset.analysis_end_beat if asset.analysis_end_beat is not None else asset.end_beat)
    return ObservationRef(
        capture_id=asset.capture_id,
        role=role,
        view=asset.capture_view.value if asset.capture_view else None,
        signal_point=asset.signal_point.value if asset.signal_point else None,
        signal_point_label=asset.signal_point_label,
        region=f"{start:g}->{end:g}qn",
        start_quarter=start,
        end_quarter=end,
        capture_quality=asset.capture_quality,
        session_revision=getattr(asset, "session_revision", None)
        or getattr(asset, "session_revision_at_end", None),
        limitations=_limitations_from_asset(asset),
    )


def assert_same_region(assets: list[AudioAsset]) -> tuple[float, float]:
    starts = [
        float(a.analysis_start_beat if a.analysis_start_beat is not None else a.start_beat)
        for a in assets
    ]
    ends = [
        float(a.analysis_end_beat if a.analysis_end_beat is not None else a.end_beat)
        for a in assets
    ]
    if max(starts) - min(starts) > REGION_EPS or max(ends) - min(ends) > REGION_EPS:
        raise ValueError(
            f"views do not share a region: starts={starts} ends={ends}"
        )
    return starts[0], ends[0]


def _confidence(
    limitations: list[str],
    attacks: int,
    *,
    contradictory: bool,
    unresolved_f0: bool,
) -> Confidence:
    severe = {
        "THROUGH_MASTER_CHAIN",
        "MAIN_NOT_FINAL",
        "REGION_ALIGNMENT_PARTIAL",
        "CAPTURE_QUALITY_WARNING",
        "CAPTURE_MUTATED",
        "SIDECHAIN_UNVERIFIED",
        "GROUP_CONTEXT_PARTIAL",
        "LATENCY_PARTIAL",
    }
    hits = sum(1 for item in limitations if any(tag in item for tag in severe))
    if attacks < 2 or contradictory:
        return Confidence.LOW
    if unresolved_f0:
        hits += 1
    if hits >= 2:
        return Confidence.LOW
    if hits == 1:
        return Confidence.MEDIUM
    return Confidence.HIGH


def diagnose_lowend(
    views: dict[str, AudioAsset],
    *,
    kick_name: str,
    bass_name: str,
) -> MusicDiagnosis:
    """Deterministic low-end diagnosis. Does not know fixture labels."""
    t0 = time.perf_counter()
    required = ("master", "kick", "bass")
    missing = [key for key in required if key not in views]
    used = [_ref(views[key], key) for key in views]
    limitations: list[str] = []
    for ref in used:
        limitations.extend(ref.limitations)
    limitations.append("PHASE_NOT_ASSESSED")
    limitations.append("PHASE_NOT_ASSESSED: isolated captures are not sample-coherent")
    limitations.append("SIDECHAIN_UNVERIFIED")

    if missing:
        return MusicDiagnosis(
            diagnosis_id=f"diag_{uuid4().hex[:12]}",
            region="unknown",
            targets={"kick": kick_name, "bass": bass_name},
            observations_used=used,
            findings=[
                Finding(
                    type=FindingType.INSUFFICIENT_EVIDENCE,
                    status=EvidenceStatus.UNRESOLVED,
                    summary=f"missing views: {missing}",
                )
            ],
            confidence=Confidence.LOW,
            no_change_is_valid=True,
            limitations=limitations + [f"missing:{','.join(missing)}"],
            user_facing=(
                "No hay vistas suficientes para un diagnóstico de low-end. "
                "No hice cambios."
            ),
            phase="PHASE_NOT_ASSESSED",
            timings={"diagnosis_s": time.perf_counter() - t0},
        )

    start_q, end_q = assert_same_region([views[key] for key in required])
    region = f"{start_q:g}->{end_q:g} quarter_note"
    t_dsp = time.perf_counter()
    kick_wav, kick_sr = _load_mono_path(views["kick"].file_path)
    bass_wav, bass_sr = _load_mono_path(views["bass"].file_path)
    master_wav, master_sr = _load_mono_path(views["master"].file_path)
    duration_s = len(kick_wav) / float(kick_sr) if kick_sr else 0.0
    isolated = detect_transients(kick_wav, kick_sr, role="kick")
    isolated_pruned = prune_kick_attacks(isolated["attacks"], duration_s=duration_s)
    kick_event_src = "TRACK_ISOLATED"
    detected = isolated
    pruned = isolated_pruned
    if "master_without_bass" in views:
        without_b_events, without_b_sr = _load_mono_path(views["master_without_bass"].file_path)
        wb = detect_transients(without_b_events, without_b_sr, role="kick")
        wb_pruned = prune_kick_attacks(wb["attacks"], duration_s=len(without_b_events) / float(without_b_sr))
        isolated_late = not isolated_pruned or float(isolated_pruned[0]["time_s"]) > 0.12
        if isolated_late and wb_pruned:
            detected = wb
            pruned = wb_pruned
            duration_s = len(without_b_events) / float(without_b_sr)
            kick_event_src = "MASTER_CONTEXT_REMOVAL(BASS)"
            limitations.append(
                "kick_event_source=MASTER_CONTEXT_REMOVAL(BASS); "
                "not KICK_ISOLATED — isolated onsets looked leaked"
            )
    kick_bands = band_energy_over_time(kick_wav, kick_sr)
    bass_bands = band_energy_over_time(bass_wav, bass_sr)
    master_bands = band_energy_over_time(master_wav, master_sr)
    attacks = {**detected, "attacks": pruned, "count": len(pruned), "raw_count": detected["count"], "source": kick_event_src}
    bass_env = low_frequency_envelope(bass_wav, bass_sr)
    temporal = overlap_at_attacks(attacks["attacks"], bass_env)
    spectral = spectral_overlap_over_time(
        kick_bands, bass_bands, kick_attacks=attacks["attacks"]
    )
    kick_f0 = estimate_fundamental(kick_wav, kick_sr)
    bass_f0 = estimate_fundamental(bass_wav, bass_sr)
    context: dict[str, object] = {}
    if "master_without_kick" in views and "master_without_bass" in views:
        without_k, _sr_k = _load_mono_path(views["master_without_kick"].file_path)
        without_b, _sr_b = _load_mono_path(views["master_without_bass"].file_path)
        context = {
            "without_kick": context_change(master_wav, without_k, master_sr),
            "without_bass": context_change(master_wav, without_b, master_sr),
        }
    dsp_s = time.perf_counter() - t_dsp

    def _src(asset: AudioAsset | None, view_name: str | None = None) -> dict[str, object]:
        if asset is None:
            return {"view": view_name, "signal_point": None, "capture_id": None}
        return {
            "view": view_name or (asset.capture_view.value if asset.capture_view else None),
            "signal_point": asset.signal_point.value if asset.signal_point else None,
            "signal_point_label": asset.signal_point_label,
            "capture_id": asset.capture_id,
        }

    kick_event_asset = (
        views.get("master_without_bass")
        if kick_event_src == "MASTER_CONTEXT_REMOVAL(BASS)"
        else views["kick"]
    )
    feature_sources = {
        "kick_events": {
            **_src(kick_event_asset, kick_event_src),
            "feature": "kick_events",
        },
        "bass_envelope": {
            **_src(views["bass"], "TRACK_ISOLATED"),
            "feature": "bass_envelope",
        },
        "spectral_overlap": {
            "feature": "spectral_overlap",
            "sources": [
                _src(views["kick"], "TRACK_ISOLATED"),
                _src(views["bass"], "TRACK_ISOLATED"),
            ],
        },
        "master_context": {
            **_src(views["master"], "MASTER_CONTEXT"),
            "feature": "master_context",
        },
        "context_removal_kick": {
            **_src(views.get("master_without_kick"), "MASTER_CONTEXT_REMOVAL(KICK)"),
            "feature": "context_removal_kick",
        },
        "context_removal_bass": {
            **_src(views.get("master_without_bass"), "MASTER_CONTEXT_REMOVAL(BASS)"),
            "feature": "context_removal_bass",
        },
    }

    n_kick = int(temporal["kick_events"])
    n_col = int(temporal["events_with_overlap"])
    temporal_ratio = n_col / n_kick if n_kick else 0.0
    co_n = int(spectral.get("events_with_co_concentration") or 0)
    spectral_ratio = co_n / n_kick if n_kick else 0.0
    shared = spectral.get("simultaneous") or []

    drop_b = drop_k = None
    context_weak = False
    kick_times = [float(a["time_s"]) for a in attacks["attacks"]]
    attack_context = None
    if context:
        drop_b = float(context["without_bass"]["rms_drop_ratio"])  # type: ignore[index]
        drop_k = float(context["without_kick"]["rms_drop_ratio"])  # type: ignore[index]
        without_b, _sr_b = _load_mono_path(views["master_without_bass"].file_path)
        attack_context = context_around_kicks(master_wav, without_b, master_sr, kick_times)
        drop_at = float(attack_context["median_drop_at_kicks"])
        drop_off = float(attack_context["drop_off_kick"])
        if 0.0 <= drop_at < 0.12:
            context_weak = True
            limitations.append(
                "muting bass barely changes Main low-end at kick attacks"
            )

    temporal_strong = n_kick >= 2 and temporal_ratio >= 0.5
    spectral_strong = n_kick >= 2 and spectral_ratio >= 0.5 and (
        bool(shared) or co_n >= max(3, n_kick // 2)
    )
    if context_weak:
        temporal_strong = False
        spectral_strong = False

    findings: list[Finding] = []
    actions: list[CandidateAction] = []
    hypotheses: list[Hypothesis] = []

    if temporal_strong:
        findings.append(
            Finding(
                type=FindingType.TEMPORAL_MASKING,
                status=EvidenceStatus.MEASURED,
                summary=(
                    f"{n_col} of {n_kick} kick events keep bass low-end energy "
                    f"near the attack similar to bass energy away from kicks "
                    f"(median on-attack {temporal['median_on_attack']:.2f}, "
                    f"off-kick {temporal['off_kick_mean']:.2f})."
                ),
                details={
                    "type": "temporal_overlap",
                    "kick_events": n_kick,
                    "events_with_overlap": n_col,
                    "band": temporal["band"],
                    "median_persist": temporal["median_persist"],
                    "median_on_attack": temporal["median_on_attack"],
                    "off_kick_mean": temporal["off_kick_mean"],
                    "raw_onsets_before_prune": attacks.get("raw_count"),
                    "feature_sources": {
                        "kick_events": feature_sources["kick_events"],
                        "bass_envelope": feature_sources["bass_envelope"],
                    },
                },
                evidence_refs=["kick_attacks", "bass_envelope", "overlap_at_attacks"],
            )
        )
        findings.append(
            Finding(
                type=FindingType.EXCESSIVE_BASS_DECAY,
                status=EvidenceStatus.INFERRED,
                summary="Bass sustain/decay does not yield at kick attacks in this region.",
                details={"median_on_attack": temporal["median_on_attack"]},
                evidence_refs=["overlap_at_attacks"],
            )
        )
        actions.append(
            CandidateAction(
                action_type="SHORTEN_BASS_RELEASE",
                target=bass_name,
                rationale="temporal overlap is the more consistent pattern",
                expected_effect="reduce bass energy during following kick attacks",
                risk="may reduce sustain/groove",
                confidence=Confidence.MEDIUM,
                evidence_refs=["overlap_at_attacks"],
            )
        )
        actions.append(
            CandidateAction(
                action_type="TIMING_ADJUSTMENT",
                target=bass_name,
                rationale="a later bass attack could leave the kick body clearer",
                expected_effect="leave a gap after kick attacks",
                risk="may loosen the pocket",
                confidence=Confidence.LOW,
                evidence_refs=["overlap_at_attacks"],
            )
        )

    if spectral_strong:
        top = max(shared, key=lambda item: item["mean_joint"]) if shared else None
        band = spectral.get("dominant_attack_band") or (top["band"] if top else None)
        attack_hits = [
            item["kick_time_s"]
            for item in (spectral.get("attack_events") or [])
            if item.get("co_concentrated")
        ]
        findings.append(
            Finding(
                type=FindingType.SPECTRAL_MASKING,
                status=EvidenceStatus.MEASURED,
                summary=(
                    f"During {co_n} of {n_kick} kick attacks, kick and bass "
                    f"both concentrate low-end share in {band or 'the same band'}."
                ),
                details={
                    "type": "spectral_overlap",
                    "kick_events": n_kick,
                    "events_with_overlap": co_n,
                    "dominant_band_hz": band,
                    "attack_times_s": attack_hits,
                    "simultaneous": shared,
                    "feature_sources": feature_sources["spectral_overlap"],
                },
                evidence_refs=["spectral_overlap"],
            )
        )
        if not temporal_strong:
            actions.append(
                CandidateAction(
                    action_type="OCTAVE_OR_NOTE_CHANGE",
                    target=bass_name,
                    rationale="repeated same-band concentration at kick attacks",
                    expected_effect=f"move bass energy away from {band}",
                    risk="may change the bass line identity",
                    confidence=Confidence.MEDIUM,
                    evidence_refs=["spectral_overlap"],
                )
            )
            actions.append(
                CandidateAction(
                    action_type="SOUND_SELECTION",
                    target=kick_name,
                    rationale="the shared body may be a sound choice, not a mix error",
                    expected_effect="choose a kick or bass with a different low-end center",
                    risk="may change the record's identity",
                    confidence=Confidence.LOW,
                    evidence_refs=["spectral_overlap"],
                )
            )

    if context and drop_b is not None and drop_k is not None:
        nonlinear = drop_b < -0.05 or drop_k < -0.05
        if nonlinear:
            findings.append(
                Finding(
                    type=FindingType.POSSIBLE_DYNAMIC_INTERACTION,
                    status=EvidenceStatus.INFERRED,
                    summary=(
                        f"Muting a target raised Main RMS "
                        f"(bass mute {drop_b:.2f}, kick mute {drop_k:.2f}). "
                        "Processors on Main may be moving. Not a stem."
                    ),
                    details={
                        "rms_drop_without_bass": drop_b,
                        "rms_drop_without_kick": drop_k,
                    },
                    evidence_refs=["context_removal"],
                )
            )

    if kick_f0["status"] == "UNRESOLVED":
        limitations.append("KICK_FUNDAMENTAL_UNRESOLVED")
    if bass_f0["status"] == "UNRESOLVED":
        limitations.append("BASS_FUNDAMENTAL_UNRESOLVED")
    if bass_f0["status"] == "MEASURED":
        hypotheses.append(
            Hypothesis(
                statement=f"Bass energy peak near {bass_f0['hz']:.1f} Hz.",
                confidence=Confidence.MEDIUM,
                status=EvidenceStatus.MEASURED,
                evidence_refs=["bass_fundamental"],
            )
        )

    if not temporal_strong and not spectral_strong:
        findings.append(
            Finding(
                type=FindingType.NO_ACTION_REQUIRED,
                status=EvidenceStatus.INFERRED,
                summary=(
                    "Kick and bass may share low-end, but attacks stay "
                    "differentiated enough in this region that a change is not required."
                ),
                details={
                    "type": "temporal_overlap",
                    "kick_events": n_kick,
                    "events_with_overlap": n_col,
                    "spectral_events_with_overlap": co_n,
                    "off_kick_mean": temporal.get("off_kick_mean"),
                    "median_on_attack": temporal.get("median_on_attack"),
                    "context_weak": context_weak,
                },
                evidence_refs=["overlap_at_attacks", "spectral_overlap"],
            )
        )
        actions.append(
            CandidateAction(
                action_type="NO_CHANGE",
                target="mix",
                rationale="overlap is present but not a consistent problem in this region",
                expected_effect="leave the pocket as it is",
                risk="none",
                confidence=Confidence.MEDIUM,
                evidence_refs=["overlap_at_attacks"],
            )
        )
    else:
        actions.append(
            CandidateAction(
                action_type="NO_CHANGE",
                target="mix",
                rationale="overlap can be intentional; no-change remains valid",
                expected_effect="do nothing if the groove depends on this overlap",
                risk="leaving a real mask in place if the evidence is under-read",
                confidence=Confidence.MEDIUM,
                evidence_refs=["overlap_at_attacks", "spectral_overlap"],
            )
        )

    primary = None
    secondaries: list[Hypothesis] = []
    if temporal_strong:
        primary = Hypothesis(
            statement=(
                "The more consistent conflict looks temporal "
                "(bass sustain/decay across kick attacks), not just excess bass."
            ),
            confidence=Confidence.MEDIUM,
            evidence_refs=["overlap_at_attacks"],
        )
        hypotheses.append(primary)
        if spectral_strong:
            secondaries.append(
                Hypothesis(
                    statement="Kick and bass also share a low band at some attacks.",
                    confidence=Confidence.LOW,
                    evidence_refs=["spectral_overlap"],
                )
            )
    elif spectral_strong:
        primary = Hypothesis(
            statement="Kick and bass repeatedly occupy the same low band at the same attacks.",
            confidence=Confidence.MEDIUM,
            evidence_refs=["spectral_overlap"],
        )
        hypotheses.append(primary)
    else:
        primary = Hypothesis(
            statement=(
                "Evidence does not show a consistent low-end problem that requires a change."
            ),
            confidence=Confidence.MEDIUM,
            evidence_refs=["overlap_at_attacks", "spectral_overlap"],
        )
        hypotheses.append(primary)
    hypotheses.extend(secondaries)

    conf = _confidence(
        limitations,
        n_kick,
        contradictory=context_weak,
        unresolved_f0=kick_f0["status"] == "UNRESOLVED",
    )
    evidence = {
        "temporal_overlap": {
            "type": "temporal_overlap",
            "kick_events": n_kick,
            "events_with_overlap": n_col,
            "band": temporal.get("band"),
            "median_on_attack": temporal.get("median_on_attack"),
            "off_kick_mean": temporal.get("off_kick_mean"),
            "median_persist": temporal.get("median_persist"),
        },
        "spectral_overlap": {
            "type": "spectral_overlap",
            "kick_events": n_kick,
            "events_with_overlap": co_n,
            "dominant_band": spectral.get("dominant_attack_band"),
            "simultaneous": shared,
        },
        "context_removal": {
            "rms_drop_without_bass": drop_b,
            "rms_drop_without_kick": drop_k,
            "at_kicks": attack_context,
            "note": "Contextual system change, not a stem.",
        },
        "fundamentals": {"kick": kick_f0, "bass": bass_f0},
        "master_bands": list((master_bands.get("bands") or {}).keys()),
        "phase": "PHASE_NOT_ASSESSED",
        "feature_sources": feature_sources,
    }
    user = _user_facing(
        findings=findings,
        temporal=temporal,
        spectral=spectral,
        confidence=conf,
        kick_name=kick_name,
        bass_name=bass_name,
        region=region,
        context_weak=context_weak,
    )
    return MusicDiagnosis(
        diagnosis_id=f"diag_{uuid4().hex[:12]}",
        region=region,
        targets={"kick": kick_name, "bass": bass_name},
        observations_used=used,
        findings=findings,
        hypotheses=hypotheses,
        primary_hypothesis=primary,
        secondary_hypotheses=secondaries,
        confidence=conf,
        candidate_actions=actions,
        no_change_is_valid=True,
        limitations=sorted(set(limitations)),
        structured_evidence=evidence,
        feature_sources=feature_sources,
        user_facing=user,
        phase="PHASE_NOT_ASSESSED",
        timings={"dsp_s": dsp_s, "diagnosis_s": time.perf_counter() - t0},
    )


def _user_facing(
    *,
    findings: list[Finding],
    temporal: dict,
    spectral: dict,
    confidence: Confidence,
    kick_name: str,
    bass_name: str,
    region: str,
    context_weak: bool,
) -> str:
    types = {item.type for item in findings}
    n_kick = int(temporal.get("kick_events") or 0)
    n_col = int(temporal.get("events_with_overlap") or 0)
    on_att = float(temporal.get("median_on_attack") or 0.0)
    off = float(temporal.get("off_kick_mean") or 0.0)
    band = temporal.get("band") or [40, 120]
    co_n = int(spectral.get("events_with_co_concentration") or 0)
    spec_band = spectral.get("dominant_attack_band")
    lines: list[str] = [
        f"Región {region}. Vistas: master, {kick_name} isolated, {bass_name} isolated."
    ]
    if FindingType.NO_ACTION_REQUIRED in types:
        lines.append(
            "Kick y bass comparten parte del low-end, "
            "pero los ataques se mantienen suficientemente diferenciados "
            "y el overlap no aparece de forma problemática en esta región."
        )
        if context_weak:
            lines.append(
                "El overlap aislado tampoco se refleja como un cambio grande "
                "de low-end en Main al silenciar pistas."
            )
        lines.append("NO_ACTION_REQUIRED.")
        lines.append("Yo no tocaría nada.")
    elif FindingType.TEMPORAL_MASKING in types:
        lines.append("El conflicto principal parece temporal, no sólo exceso de graves.")
        lines.append(
            f"Detecté {n_kick} ataques del kick. En {n_col} de ellos el bass "
            f"conserva energía low-end (~{band[0]}–{band[1]} Hz) cerca del ataque "
            f"(on-attack {on_att:.2f} vs off-kick {off:.2f} en esta región)."
        )
        if co_n:
            lines.append(
                f"Existe overlap espectral en {co_n} ataques"
                + (f" ({spec_band} Hz)" if spec_band else "")
                + ", pero es menos consistente que el temporal."
            )
        lines.append(
            "La evidencia sugiere probar primero una reducción moderada "
            "del decay/release del bass antes de usar EQ permanente."
        )
    elif FindingType.SPECTRAL_MASKING in types:
        lines.append(
            f"Durante {co_n} de {n_kick} ataques, kick y bass concentran "
            f"energía a la vez en {spec_band or 'la misma banda'} Hz."
        )
        lines.append(
            "Eso puede ser intencional. Si molesta, movería nota/octava o el sonido "
            "antes que un EQ permanente."
        )
    lines.append(f"Confidence: {confidence.value}.")
    lines.append("No hice cambios.")
    return "\n\n".join(lines)


def canonical_diagnosis(diagnosis: MusicDiagnosis) -> dict:
    """Strip run-ids and timings so two analyzer passes can be compared."""
    payload = diagnosis.model_dump()
    payload.pop("diagnosis_id", None)
    payload.pop("timings", None)
    return payload


def diagnoses_equivalent(left: MusicDiagnosis, right: MusicDiagnosis) -> bool:
    return canonical_diagnosis(left) == canonical_diagnosis(right)


def primary_finding_types(diagnosis: MusicDiagnosis) -> list[str]:
    return [item.type.value for item in diagnosis.findings]


def context_removal_needed(diagnosis: MusicDiagnosis) -> dict[str, object]:
    types = set(primary_finding_types(diagnosis))
    if FindingType.INSUFFICIENT_EVIDENCE.value in types and len(types) == 1:
        return {"needed": False, "reason": "already_insufficient"}
    if FindingType.NO_ACTION_REQUIRED.value in types and not (
        types & {FindingType.TEMPORAL_MASKING.value, FindingType.SPECTRAL_MASKING.value}
    ):
        return {"needed": False, "reason": "three_view_no_action"}
    if types & {FindingType.TEMPORAL_MASKING.value, FindingType.SPECTRAL_MASKING.value}:
        return {
            "needed": True,
            "reason": "distinguish_isolated_overlap_from_main_context",
        }
    if diagnosis.confidence is Confidence.LOW:
        return {"needed": True, "reason": "low_confidence_contextual"}
    return {"needed": False, "reason": "not_required"}


def unstable_insufficient(
    *,
    left: MusicDiagnosis,
    right: MusicDiagnosis,
    region: str,
    kick_name: str,
    bass_name: str,
) -> MusicDiagnosis:
    left_types = primary_finding_types(left)
    right_types = primary_finding_types(right)
    statement = (
        "Same musical region produced materially inconsistent diagnoses. "
        "Not enough stable evidence to recommend a change."
    )
    finding = Finding(
        type=FindingType.INSUFFICIENT_EVIDENCE,
        status=EvidenceStatus.UNRESOLVED,
        summary=statement,
        details={
            "reason": "DIAGNOSIS_UNSTABLE",
            "run_a": left_types,
            "run_b": right_types,
        },
        evidence_refs=["capture_repeatability"],
    )
    action = CandidateAction(
        action_type="NO_CHANGE",
        target="mix",
        rationale="DIAGNOSIS_UNSTABLE: do not pick one of the conflicting results",
        expected_effect="request more evidence instead of moving a knob",
        risk="none",
        confidence=Confidence.LOW,
        evidence_refs=["capture_repeatability"],
    )
    hypothesis = Hypothesis(
        statement=statement,
        confidence=Confidence.LOW,
        status=EvidenceStatus.UNRESOLVED,
        evidence_refs=["capture_repeatability"],
    )
    return MusicDiagnosis(
        diagnosis_id=f"diag_{uuid4().hex[:12]}",
        region=region,
        targets={"kick": kick_name, "bass": bass_name},
        observations_used=list(left.observations_used) + list(right.observations_used),
        findings=[finding],
        hypotheses=[hypothesis],
        primary_hypothesis=hypothesis,
        confidence=Confidence.LOW,
        candidate_actions=[action],
        no_change_is_valid=True,
        limitations=sorted(
            set(left.limitations + right.limitations + ["DIAGNOSIS_UNSTABLE"])
        ),
        structured_evidence={
            "reason": "DIAGNOSIS_UNSTABLE",
            "run_a": {"findings": left_types, "evidence": left.structured_evidence},
            "run_b": {"findings": right_types, "evidence": right.structured_evidence},
        },
        user_facing=(
            f"Región {region}. No tengo evidencia suficientemente estable "
            "para recomendar un cambio.\n\n"
            f"Una captura dijo {left_types}; otra dijo {right_types}. "
            "No elijo una.\n\n"
            "INSUFFICIENT_EVIDENCE (DIAGNOSIS_UNSTABLE).\n\n"
            "Confidence: LOW.\n\n"
            "No hice cambios."
        ),
        phase="PHASE_NOT_ASSESSED",
    )
