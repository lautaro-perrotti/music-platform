"""Groovy / Latin Tech House style template + plan builder.

Producer context: underground, groovy, percussive Tech House with Latin/tribal
influences (references: Nacho Scoppa, Jay de Lys, Mar BR, Mau P — used as sonic
reference only, never copied).

Philosophy: GROOVE > SOUND SELECTION > RHYTHM > ARRANGEMENT > MIXING.
Fewer elements, more identity. Percussion is the track's identity.

No reference-track analysis here: this is pure genre knowledge + sample-library
retrieval (the "build a track from 0" path of TRACK_BUILDER_V1).
"""

from __future__ import annotations

from copilot.human_eval.store import now_iso
from copilot.musicplan import (
    build_create_track_action,
    build_sample_load_action,
)
from copilot.sample_library.retrieval import SampleRetriever
from copilot.sample_library.schemas import LibraryIndex, SampleRole, SampleType
from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass
from copilot.schemas.session import MixerState, RoutingState, SessionState, TrackState

# 126-128 BPM; default 127.
TECH_HOUSE_BPM = 127.0

# Groove skeleton: (role, track_name, sample_type, bpm_filter, text_query_hint)
# Percussion-first: the conversation between kick/clap/hats/shaker/conga/clave/loop
# is the identity. Bass + vocal + one rhythmic stab complete a minimal, groove-led set.
GROOVY_LATIN_GROOVE: list[tuple[SampleRole, str, SampleType, float | None, str | None]] = [
    (SampleRole.KICK, "Kick", SampleType.ONE_SHOT, None, None),
    (SampleRole.CLAP, "Clap", SampleType.ONE_SHOT, None, None),
    (SampleRole.CLOSED_HAT, "Closed Hat", SampleType.ONE_SHOT, None, None),
    (SampleRole.SHAKER, "Shaker", SampleType.ONE_SHOT, None, "shaker"),
    (SampleRole.PERCUSSION, "Conga", SampleType.ONE_SHOT, None, "conga"),
    (SampleRole.PERCUSSION, "Clave", SampleType.ONE_SHOT, None, "clave"),
    (SampleRole.TOP_LOOP, "Perc Loop", SampleType.LOOP, TECH_HOUSE_BPM, None),
    (SampleRole.BASS, "Bass", SampleType.LOOP, TECH_HOUSE_BPM, None),
    (SampleRole.VOCAL, "Vocal", SampleType.ONE_SHOT, None, None),
    (SampleRole.SYNTH, "Stab", SampleType.ONE_SHOT, None, "stab"),
    (SampleRole.UNKNOWN, "Guitar", SampleType.ONE_SHOT, None, "guitar"),
    (SampleRole.UNKNOWN, "Sax", SampleType.ONE_SHOT, None, "sax"),
    (SampleRole.FX, "FX", SampleType.ONE_SHOT, None, None),
    (SampleRole.IMPACT, "Impact", SampleType.ONE_SHOT, None, None),
    (SampleRole.DOWNLIFTER, "Downlifter", SampleType.LOOP, None, None),
    (SampleRole.TEXTURE, "Texture", SampleType.LOOP, None, None),
]

# Mixing + mastering chains live in copilot.musicplan.mixing (native Ableton devices).

# Latin percussion humanized as MIDI patterns (Simpler + groove swing), not static samples.
# Kick/Clap/Closed Hat stay audio samples (their transients are the identity).
MIDI_PERCUSSION: dict[str, str] = {
    "Shaker": "shaker_pattern",
    "Conga": "conga_pattern",
    "Clave": "clave_pattern",
}

_PATTERN_FNS = None


def _pattern_notes(name: str) -> list:
    global _PATTERN_FNS
    if _PATTERN_FNS is None:
        from copilot.musicplan.groove import shaker_pattern, conga_pattern, clave_pattern

        _PATTERN_FNS = {
            "shaker_pattern": shaker_pattern,
            "conga_pattern": conga_pattern,
            "clave_pattern": clave_pattern,
        }
    return _PATTERN_FNS[name]()


def _virtual_track(name: str, role: str = "audio") -> TrackState:
    return TrackState(
        stable_id="",
        index=-1,
        name=name,
        role=role,
        mixer=MixerState(),
        routing=RoutingState(output_type="Main", monitoring="in"),
        devices=[],
        clips=[],
    )


def _virtual_audio_track(name: str) -> TrackState:
    return _virtual_track(name, role="audio")


def build_tech_house_plan(
    *,
    index: LibraryIndex,
    session: SessionState,
    plan_id: str = "groovy_latin_tech_house",
    top_k: int = 1,
    sample_map: dict[str, str] | None = None,
) -> MusicPlan:
    """Retrieve a sample per groove role from the library and build a MusicPlan.

    Each role -> CREATE_TRACK (audio) + SAMPLE_LOAD (retrieved sample into slot 0).
    If `sample_map` (track_name -> asset sha256) is given, use those samples
    instead of top-1 (Astra-selected).
    """
    retriever = SampleRetriever(index)
    actions = []
    evidence_refs: list[str] = []
    for role, track_name, sample_type, bpm, text_query in GROOVY_LATIN_GROOVE:
        if sample_map and track_name in sample_map:
            asset = index.assets.get(sample_map[track_name])
            if asset is None:
                continue
        else:
            results = retriever.search_samples(
                role=role,
                one_shot_or_loop=sample_type,
                bpm=bpm,
                text_query=text_query,
                top_k=top_k,
            )
            if not results:
                continue
            asset = results[0].asset
        sample_uri = asset.relative_path
        if track_name in MIDI_PERCUSSION:
            # Humanized latin percussion: MIDI track + Simpler(sample) + groove pattern
            # with velocity/micro-timing/swing (not a static audio one-shot).
            actions.append(
                build_create_track_action(
                    project_identity=session.project_identity,
                    track_name=track_name,
                    track_kind="midi",
                    reason=f"groovy latin tech house {role.value} MIDI track",
                    evidence_refs=[asset.id],
                )
            )
            actions.append(
                build_sample_load_action(
                    track=_virtual_track(track_name, role="midi"),
                    project_identity=session.project_identity,
                    clip_index=0,
                    sample_uri=sample_uri,
                    reason=f"load {role.value} sample {asset.filename} into Simpler",
                    evidence_refs=[asset.id],
                )
            )
            from copilot.musicplan import build_pattern_action

            notes = _pattern_notes(MIDI_PERCUSSION[track_name])
            actions.append(
                build_pattern_action(
                    track=_virtual_track(track_name, role="midi"),
                    project_identity=session.project_identity,
                    clip_index=0,
                    length_beats=1.0,
                    notes=notes,
                    reason=f"humanize {track_name} groove (velocity/micro-timing/swing)",
                    evidence_refs=[asset.id],
                )
            )
        else:
            actions.append(
                build_create_track_action(
                    project_identity=session.project_identity,
                    track_name=track_name,
                    track_kind="audio",
                    reason=f"groovy latin tech house {role.value} track",
                    evidence_refs=[asset.id],
                )
            )
            actions.append(
                build_sample_load_action(
                    track=_virtual_audio_track(track_name),
                    project_identity=session.project_identity,
                    clip_index=0,
                    sample_uri=sample_uri,
                    reason=f"load {role.value} sample {asset.filename}",
                    evidence_refs=[asset.id],
                )
            )
        evidence_refs.append(asset.id)

    # MIXING_V1: native per-track chains (EQ/saturation/sidechain) from mixing.py.
    # Each drum element stays on its own track (no Drum Rack).
    from copilot.musicplan.mixing import build_mixing_actions

    actions.extend(build_mixing_actions(project_identity=session.project_identity))

    # SIDECHAIN_V1: native Compressor sidechain on the Bass (source = Kick).
    from copilot.musicplan import build_set_device_routing_action

    actions.append(
        build_set_device_routing_action(
            track=_virtual_audio_track("Bass"),
            project_identity=session.project_identity,
            device_index=1,  # Compressor (after EQ Eight) in the Bass chain
            routing_type="Kick",  # sidechain source track (not a literal 'Track' type)
            routing_channel="Post FX",  # kick post-FX signal
            reason="sidechain: Bass Compressor ducks against Kick (groove > loudness)",
            evidence_refs=[],
        )
    )

    # NO BUSES: every element goes DIRECT to Main on its own channel (user
    # decided group/bus routing was not being applied correctly). The leftover
    # build_group_actions is intentionally not invoked here.

    return MusicPlan(
        plan_id=plan_id,
        diagnosis=DiagnosisBinding(
            diagnosis_id="style_groovy_latin_tech_house",
            diagnosis_status="SUPPORTED",
            diagnosis_accepted=True,
            cause_status="CAUSE_SUPPORTED",
        ),
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        project_state_token=session.project_token or session.project_identity,
        audible_state_token=session.audible_token or "",
        target_state_tokens={},
        evidence_refs=evidence_refs,
        actions=actions,
        notes=[
            f"groovy latin tech house groove at {TECH_HOUSE_BPM} BPM from sample library",
            "philosophy: GROOVE > SOUND SELECTION > RHYTHM > ARRANGEMENT > MIXING",
            "percussion-first; fewer elements, more identity",
        ],
        created_at=now_iso(),
    )
