"""Tech house style template + plan builder (genre knowledge, no reference analyzer).

TRACK_BUILDER_V1: turn genre conventions + sample library into a MusicPlan
(CREATE_TRACK + SAMPLE_LOAD per groove role). No reference-track analysis here.
"""

from __future__ import annotations

from copilot.human_eval.store import now_iso
from copilot.musicplan import build_create_track_action, build_sample_load_action
from copilot.sample_library.retrieval import SampleRetriever
from copilot.sample_library.schemas import LibraryIndex, SampleRole, SampleType
from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass
from copilot.schemas.session import MixerState, RoutingState, SessionState, TrackState

TECH_HOUSE_BPM = 124.0

# groove skeleton: (role, track_name, sample_type, bpm_filter_or_None)
TECH_HOUSE_GROOVE: list[tuple[SampleRole, str, SampleType, float | None]] = [
    (SampleRole.KICK, "Kick", SampleType.ONE_SHOT, None),
    (SampleRole.CLAP, "Clap", SampleType.ONE_SHOT, None),
    (SampleRole.CLOSED_HAT, "Closed Hat", SampleType.ONE_SHOT, None),
    (SampleRole.OPEN_HAT, "Open Hat", SampleType.ONE_SHOT, None),
    (SampleRole.BASS, "Bass", SampleType.LOOP, TECH_HOUSE_BPM),
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
    plan_id: str = "tech_house_groove",
    top_k: int = 1,
) -> MusicPlan:
    """Retrieve a sample per groove role from the library and build a MusicPlan.

    Each role -> CREATE_TRACK (audio) + SAMPLE_LOAD (retrieved sample into slot 0).
    """
    retriever = SampleRetriever(index)
    actions = []
    evidence_refs: list[str] = []
    for role, track_name, sample_type, bpm in TECH_HOUSE_GROOVE:
        results = retriever.search_samples(
            role=role, one_shot_or_loop=sample_type, bpm=bpm, top_k=top_k
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
                reason=f"tech house {role.value} track",
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
            diagnosis_id="style_tech_house",
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
        notes=[f"build tech house groove at {TECH_HOUSE_BPM} BPM from sample library"],
        created_at=now_iso(),
    )
