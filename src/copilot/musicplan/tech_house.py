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
from copilot.musicplan import build_create_track_action, build_sample_load_action
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
    (SampleRole.SYNTH, "Stab", SampleType.ONE_SHOT, None, None),
]

# Mixing hints (groove-first; applied as DEVICE_LOAD/DEVICE_TWEAK in a later step).
MIXING_HINTS = [
    "sidechain Bass to Kick (Duck/Compressor sidechain) so kick+bass feel like one machine",
    "moderate Saturator drive on mid-bass (harmonics around low-mid, keep sub controlled)",
    "EQ Eight on percussion to carve low-mid buildup; cut rather than boost",
]


def _virtual_audio_track(name: str) -> TrackState:
    return TrackState(
        stable_id="",
        index=-1,
        name=name,
        role="audio",
        mixer=MixerState(),
        routing=RoutingState(output_type="Main", monitoring="in"),
        devices=[],
        clips=[],
    )


def build_tech_house_plan(
    *,
    index: LibraryIndex,
    session: SessionState,
    plan_id: str = "groovy_latin_tech_house",
    top_k: int = 1,
) -> MusicPlan:
    """Retrieve a sample per groove role from the library and build a MusicPlan.

    Each role -> CREATE_TRACK (audio) + SAMPLE_LOAD (retrieved sample into slot 0).
    """
    retriever = SampleRetriever(index)
    actions = []
    evidence_refs: list[str] = []
    for role, track_name, sample_type, bpm, text_query in GROOVY_LATIN_GROOVE:
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
        sample_uri = asset.path
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
