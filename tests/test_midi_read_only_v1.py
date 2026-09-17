from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from copilot.audio.astra_external_reasoning_v1 import pack_payload_hash
from copilot.audio.file_hash import sha256_file
from copilot.audio.midi_read_only_v1 import (
    MIDI_HAS_MATERIAL,
    MIDI_NO_MATERIAL,
    MIDI_NOT_APPLICABLE,
    MIDI_PRESENT_DURING_INTERVAL,
    NO_MIDI_EXPECTED_DURING_INTERVAL,
    RELATION_UNCERTAIN,
    bind_operational_ref,
    collect_midi,
    expand_note_occurrences,
    identity_for_als_path,
    load_persisted_ref,
    match_als_track,
    project_mismatch_still_fails_closed,
)
from copilot.daw.object_ref import PersistentObjectRef, ResolveStatus, ref_from_track, resolve_track
from copilot.daw.state_tokens import attach_tokens
from copilot.schemas.evidence import (
    CaptureQuality,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)
from copilot.schemas.session import DeviceState, MixerState, RoutingState, SessionState, TrackState, TransportState


DEVICE_NAME = "illements_LAB_High_String_125_D#"
FINGERPRINT = "fp_high_string_test"


def _midi_clip_xml(
    *,
    clip_id: str,
    name: str,
    start: float,
    end: float,
    loop_on: bool,
    loop_start: float,
    loop_end: float,
    start_relative: float,
    notes: str,
    disabled: bool = False,
) -> str:
    return f"""
            <MidiClip Id="{clip_id}" Time="{start}">
              <CurrentStart Value="{start}" />
              <CurrentEnd Value="{end}" />
              <Loop>
                <LoopStart Value="{loop_start}" />
                <LoopEnd Value="{loop_end}" />
                <StartRelative Value="{start_relative}" />
                <LoopOn Value="{'true' if loop_on else 'false'}" />
              </Loop>
              <Name Value="{name}" />
              <Disabled Value="{'true' if disabled else 'false'}" />
              <Notes>
                <KeyTracks>
                  {notes}
                </KeyTracks>
              </Notes>
            </MidiClip>
    """


def _note_xml(pitch: int, time: float, duration: float, velocity: int = 100, enabled: bool = True) -> str:
    return f"""
                  <KeyTrack>
                    <Notes>
                      <MidiNoteEvent Time="{time}" Duration="{duration}" Velocity="{velocity}" OffVelocity="64" IsEnabled="{'true' if enabled else 'false'}" NoteId="1" />
                    </Notes>
                    <MidiKey Value="{pitch}" />
                  </KeyTrack>
    """


def _track_xml(
    *,
    tag: str,
    locator_name: str,
    device_name: str,
    clips: str,
    device_class: str = "OriginalSimpler",
) -> str:
    return f"""
      <{tag}>
        <Name>
          <EffectiveName Value="{locator_name}" />
          <UserName Value="{locator_name}" />
        </Name>
        <DeviceChain>
          <{device_class} Id="0">
            <SampleRef>
              <FileRef>
                <Name Value="{device_name}.wav" />
              </FileRef>
            </SampleRef>
            <Name Value="{device_name}" />
          </{device_class}>
        </DeviceChain>
        <ArrangerAutomation>
          <Events>
            {clips}
          </Events>
        </ArrangerAutomation>
      </{tag}>
    """


def _write_als(path: Path, tracks: str, *, tempo: float = 126.0) -> Path:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton>
  <LiveSet>
    <MasterTrack>
      <DeviceChain>
        <Mixer>
          <Tempo>
            <Manual Value="{tempo}" />
          </Tempo>
        </Mixer>
      </DeviceChain>
    </MasterTrack>
    <Tracks>
      {tracks}
    </Tracks>
  </LiveSet>
</Ableton>
"""
    path.write_bytes(gzip.compress(xml.encode("utf-8")))
    return path


def _ref(**kwargs: object) -> PersistentObjectRef:
    payload = {
        "project_identity": "pack_identity",
        "role": "midi",
        "name": "High String",
        "device_names": [DEVICE_NAME],
        "device_classes": ["OriginalSimpler"],
        "content_fingerprint": FINGERPRINT,
        "target_state_token": "tok",
    }
    payload.update(kwargs)
    return PersistentObjectRef.model_validate(payload)


def _mini_pack(
    *,
    identity: str,
    wav_dummy: str = "main.wav",
    start_s: float = 0.0,
    end_s: float = 0.2,
    kind: str = "NEAR_SILENCE",
    extra_events: list[tuple[str, float, float]] | None = None,
) -> EvidencePack:
    events = [(kind, start_s, end_s)] + list(extra_events or [])
    items: list[EvidenceItem] = [
        EvidenceItem(
            evidence_id="ev.project.identity",
            kind=EvidenceKind.STATE_TOKEN,
            source_ref="session.project_identity",
            region="AUTO_36_68:36.0-68.0",
            analysis_version="lowend-obs-1",
            name="project_identity",
            value=identity,
            project_token="pt",
            audible_token="at",
        ),
        EvidenceItem(
            evidence_id="ev.region",
            kind=EvidenceKind.FACT,
            source_ref="region",
            region="AUTO_36_68:36.0-68.0",
            analysis_version="lowend-obs-1",
            name="region",
            value={"id": "AUTO_36_68", "start_qn": 36.0, "end_qn": 68.0, "why": "explicit"},
            project_token="pt",
            audible_token="at",
        ),
        EvidenceItem(
            evidence_id="ev.source.2.capture",
            kind=EvidenceKind.MEASUREMENT,
            source_ref=FINGERPRINT,
            region="AUTO_36_68:36.0-68.0",
            view="TRACK_ISOLATED",
            analysis_version="lowend-obs-1",
            name="source_capture",
            value={"ok": True, "signal_class": "NEAR_SILENCE", "ref": FINGERPRINT},
            quality=CaptureQuality.LIMITED,
            project_token="pt",
            audible_token="at",
        ),
        EvidenceItem(
            evidence_id="ev.main.capture",
            kind=EvidenceKind.MEASUREMENT,
            source_ref="main_sidecar",
            region="AUTO_36_68:36.0-68.0",
            analysis_version="lowend-obs-1",
            name="main_capture",
            value={"ok": True, "path": wav_dummy, "audio_sha256": "x"},
            project_token="pt",
            audible_token="at",
        ),
    ]
    for idx, (event_kind, event_start, event_end) in enumerate(events):
        items.extend(
            [
                EvidenceItem(
                    evidence_id=f"fm.event.{idx}.kind",
                    kind=EvidenceKind.FACT,
                    source_ref="fullmix-obs-1",
                    region="AUTO_36_68",
                    analysis_version="fullmix-obs-1",
                    name="fullmix_energy_event_kind",
                    value=event_kind,
                    project_token="pt",
                    audible_token="at",
                ),
                EvidenceItem(
                    evidence_id=f"fm.event.{idx}.start_s",
                    kind=EvidenceKind.MEASUREMENT,
                    source_ref="fullmix-obs-1",
                    region="AUTO_36_68",
                    analysis_version="fullmix-obs-1",
                    name="fullmix_energy_event_start_s",
                    value=event_start,
                    unit="s",
                    project_token="pt",
                    audible_token="at",
                ),
                EvidenceItem(
                    evidence_id=f"fm.event.{idx}.end_s",
                    kind=EvidenceKind.MEASUREMENT,
                    source_ref="fullmix-obs-1",
                    region="AUTO_36_68",
                    analysis_version="fullmix-obs-1",
                    name="fullmix_energy_event_end_s",
                    value=event_end,
                    unit="s",
                    project_token="pt",
                    audible_token="at",
                ),
            ]
        )
    return EvidencePack(
        pack_id="pack_parent_midi",
        analysis_version="lowend-obs-1",
        prompt_schema_version="music-diagnosis-reason-1",
        region="AUTO_36_68:36.0-68.0",
        project_token="pt",
        audible_token="at",
        alignment_claim="LIMITED",
        alignment_envelope_ms=52.0,
        items=items,
        entities=[
            {
                "entity_id": FINGERPRINT,
                "kind": "TRACK",
                "name": "",
                "role": "midi",
            }
        ],
        limitations=[
            ObservationLimitation(
                code="ALIGNMENT_LIMITED",
                detail="musical alignment remains LIMITED ±52 ms",
                precision_ms=52.0,
                capability_ms=[52.0],
            ),
            ObservationLimitation(
                code="MIDI_UNREAD",
                detail="MIDI notes were not requested for this pack",
            ),
        ],
    )


def _write_parent(path: Path, pack: EvidencePack) -> Path:
    path.write_text(json.dumps({"pack": pack.model_dump(mode="json")}, indent=2), encoding="utf-8")
    return path


def _write_journal(directory: Path, ref: PersistentObjectRef) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "pass.jsonl"
    path.write_text(
        json.dumps({"ref": ref.model_dump(mode="json"), "status": "PREPARED"}) + "\n",
        encoding="utf-8",
    )
    return path


def _session(*, path: str, name: str, identity: str | None = None) -> SessionState:
    session = SessionState(
        project_path=path,
        project_name=name,
        transport=TransportState(),
        tracks=[
            TrackState(
                stable_id="t0",
                index=0,
                name="High String",
                role="midi",
                mixer=MixerState(),
                routing=RoutingState(),
                devices=[
                    DeviceState(
                        stable_id="d0",
                        index=0,
                        name=DEVICE_NAME,
                        class_name="OriginalSimpler",
                    )
                ],
            )
        ],
    )
    attached = attach_tokens(session, path=path, name=name)
    if identity:
        attached.project_identity = identity
    return attached


def _decoy_and_target_tracks(*, clips: str, target_name: str = "Pad") -> str:
    decoy = _track_xml(
        tag="MidiTrack",
        locator_name="High String",
        device_name="other_sample",
        clips=_midi_clip_xml(
            clip_id="9",
            name="decoy",
            start=36,
            end=68,
            loop_on=False,
            loop_start=0,
            loop_end=4,
            start_relative=0,
            notes=_note_xml(72, 0, 8),
        ),
    )
    target = _track_xml(
        tag="MidiTrack",
        locator_name=target_name,
        device_name=DEVICE_NAME,
        clips=clips,
    )
    return decoy + target


def test_non_looped_midi_maps_clip_local_to_arrangement(tmp_path: Path) -> None:
    occ = expand_note_occurrences(
        clip_local_start=2.0,
        duration=1.5,
        current_start=40.0,
        current_end=48.0,
        start_relative=0.0,
        loop_on=False,
        loop_start=0.0,
        loop_end=8.0,
    )
    assert len(occ) == 1
    assert occ[0]["arrangement_start_qn"] == 42.0
    assert occ[0]["duration_qn"] == 1.5
    assert occ[0]["loop_iteration"] == 0


def test_looped_midi_repeats_onsets() -> None:
    occ = expand_note_occurrences(
        clip_local_start=0.0,
        duration=1.0,
        current_start=0.0,
        current_end=12.0,
        start_relative=0.0,
        loop_on=True,
        loop_start=0.0,
        loop_end=4.0,
    )
    onsets = [row["arrangement_start_qn"] for row in occ]
    assert onsets == [0.0, 4.0, 8.0]


def test_clip_offset_is_not_treated_as_arrangement_time() -> None:
    occ = expand_note_occurrences(
        clip_local_start=6.0,
        duration=2.0,
        current_start=10.0,
        current_end=20.0,
        start_relative=4.0,
        loop_on=False,
        loop_start=0.0,
        loop_end=8.0,
    )
    assert len(occ) == 1
    assert occ[0]["arrangement_start_qn"] == 12.0
    skipped = expand_note_occurrences(
        clip_local_start=2.0,
        duration=1.0,
        current_start=10.0,
        current_end=20.0,
        start_relative=4.0,
        loop_on=False,
        loop_start=0.0,
        loop_end=8.0,
    )
    assert skipped == []


def test_region_boundary_crossing_keeps_intersecting_note(tmp_path: Path) -> None:
    als = _write_als(
        tmp_path / "song.als",
        _decoy_and_target_tracks(
            clips=_midi_clip_xml(
                clip_id="1",
                name="pad",
                start=32.0,
                end=80.0,
                loop_on=False,
                loop_start=0.0,
                loop_end=16.0,
                start_relative=0.0,
                notes=_note_xml(65, 34.0, 4.0),
            )
        ),
    )
    identity = identity_for_als_path(als)
    parent = _write_parent(tmp_path / "parent.json", _mini_pack(identity=identity))
    ref = _ref(project_identity="wiped")
    _write_journal(tmp_path / "journal", ref)
    report = collect_midi(
        parent_path=parent,
        als_path=als,
        journal_dir=tmp_path / "journal",
        search_roots=[tmp_path],
    )
    assert report["status"] == "VERIFIED"
    notes = [item for item in report["pack"]["items"] if item["name"] == "midi_note"]
    assert len(notes) == 1
    note = notes[0]["value"]
    assert note["arrangement_start_qn"] == 66.0
    assert note["region_overlap_start_qn"] == 66.0
    assert note["region_overlap_end_qn"] == 68.0
    assert note["time_basis"] == "arrangement_qn"
    typed = {
        item["evidence_id"]: item
        for item in report["pack"]["items"]
        if item["unit"] == "qn"
    }
    assert typed["midi.region.start_qn"]["value"] == 36.0
    assert typed["midi.note.0.arrangement_start_qn"]["value"] == 66.0
    assert typed["midi.note.0.region_overlap_end_qn"]["value"] == 68.0
    assert any(item["name"] == "clip_note_starts" for item in report["pack"]["items"])


def test_muted_note_is_not_active_material(tmp_path: Path) -> None:
    als = _write_als(
        tmp_path / "song.als",
        _decoy_and_target_tracks(
            clips=_midi_clip_xml(
                clip_id="1",
                name="pad",
                start=36.0,
                end=68.0,
                loop_on=False,
                loop_start=0.0,
                loop_end=32.0,
                start_relative=0.0,
                notes=_note_xml(60, 0.0, 8.0, enabled=False),
            )
        ),
    )
    identity = identity_for_als_path(als)
    parent = _write_parent(tmp_path / "parent.json", _mini_pack(identity=identity))
    _write_journal(tmp_path / "journal", _ref(project_identity="wiped"))
    report = collect_midi(
        parent_path=parent,
        als_path=als,
        journal_dir=tmp_path / "journal",
    )
    assert report["midi_status"] == MIDI_NO_MATERIAL
    note = next(item["value"] for item in report["pack"]["items"] if item["name"] == "midi_note")
    assert note["muted"] is True
    active = next(
        item["value"] for item in report["pack"]["items"] if item["name"] == "midi_active_note_intervals"
    )
    assert active == []


def test_empty_midi_region_is_no_material(tmp_path: Path) -> None:
    als = _write_als(
        tmp_path / "song.als",
        _decoy_and_target_tracks(
            clips=_midi_clip_xml(
                clip_id="1",
                name="pad",
                start=36.0,
                end=68.0,
                loop_on=False,
                loop_start=0.0,
                loop_end=32.0,
                start_relative=0.0,
                notes="",
            )
        ),
    )
    identity = identity_for_als_path(als)
    parent = _write_parent(tmp_path / "parent.json", _mini_pack(identity=identity))
    _write_journal(tmp_path / "journal", _ref(project_identity="wiped"))
    report = collect_midi(
        parent_path=parent,
        als_path=als,
        journal_dir=tmp_path / "journal",
    )
    assert report["midi_status"] == MIDI_NO_MATERIAL
    assert report["notes_in_region"] == 0
    gaps = next(
        item["value"]
        for item in report["pack"]["items"]
        if item["name"] == "midi_gaps_without_note_activity"
    )
    assert gaps == [{"start_qn": 36.0, "end_qn": 68.0}]


def test_midi_not_applicable_for_audio_track(tmp_path: Path) -> None:
    als = _write_als(
        tmp_path / "song.als",
        _track_xml(
            tag="AudioTrack",
            locator_name="High String",
            device_name=DEVICE_NAME,
            clips="",
        ),
    )
    identity = identity_for_als_path(als)
    parent = _write_parent(tmp_path / "parent.json", _mini_pack(identity=identity))
    _write_journal(tmp_path / "journal", _ref(project_identity="wiped"))
    report = collect_midi(
        parent_path=parent,
        als_path=als,
        journal_dir=tmp_path / "journal",
    )
    assert report["midi_status"] == MIDI_NOT_APPLICABLE
    assert report["MIDI_EVIDENCE_OUTCOME"]["C"] is True
    status = next(item["value"] for item in report["pack"]["items"] if item["name"] == "midi_material_status")
    assert status == MIDI_NOT_APPLICABLE


def test_persistent_object_ref_ignores_display_name_decoy(tmp_path: Path) -> None:
    als = _write_als(
        tmp_path / "song.als",
        _decoy_and_target_tracks(
            clips=_midi_clip_xml(
                clip_id="1",
                name="pad",
                start=36.0,
                end=52.0,
                loop_on=False,
                loop_start=0.0,
                loop_end=16.0,
                start_relative=0.0,
                notes=_note_xml(65, 0.0, 2.0),
            ),
            target_name="Not The Historical Label",
        ),
    )
    identity = identity_for_als_path(als)
    parent = _write_parent(tmp_path / "parent.json", _mini_pack(identity=identity))
    journal_ref = _ref(project_identity="wiped_structural")
    _write_journal(tmp_path / "journal", journal_ref)
    loaded = load_persisted_ref(FINGERPRINT, journal_dir=tmp_path / "journal")
    assert loaded is not None
    bound = bind_operational_ref(loaded, pack_identity=identity, entity_id=FINGERPRINT)
    assert bound["ok"] is True
    assert bound["ref"].project_identity == identity
    assert bound["ref"].name == "High String"
    report = collect_midi(
        parent_path=parent,
        als_path=als,
        journal_dir=tmp_path / "journal",
    )
    assert report["source_resolve"]["locator_name"] == "Not The Historical Label"
    assert report["source_resolve"]["used_display_name"] is False
    assert report["midi_status"] == MIDI_HAS_MATERIAL
    notes = [item["value"] for item in report["pack"]["items"] if item["name"] == "midi_note"]
    assert notes[0]["pitch"] == 65
    assert notes[0]["arrangement_start_qn"] == 36.0


def test_actual_project_mismatch_still_fails_closed(tmp_path: Path) -> None:
    als = _write_als(tmp_path / "song.als", _decoy_and_target_tracks(clips=""))
    other = tmp_path / "other.als"
    other.write_bytes(als.read_bytes())
    pack_identity = identity_for_als_path(als)
    parent = _write_parent(tmp_path / "parent.json", _mini_pack(identity=pack_identity))
    _write_journal(tmp_path / "journal", _ref(project_identity=pack_identity))
    mismatched = collect_midi(
        parent_path=parent,
        als_path=other,
        journal_dir=tmp_path / "journal",
    )
    assert mismatched["BLOCKER"] == "PROJECT_MISMATCH"
    groove = _session(path=str(als), name=als.stem)
    foreign = _session(path=str(other), name=other.stem)
    ref = ref_from_track(groove.tracks[0], project_identity=groove.project_identity)
    assert resolve_track(foreign, ref).status is ResolveStatus.PROJECT_MISMATCH
    assert project_mismatch_still_fails_closed(foreign, ref) is True


def test_zero_midi_mutation_and_parent_pack_preserved(tmp_path: Path) -> None:
    als = _write_als(
        tmp_path / "song.als",
        _decoy_and_target_tracks(
            clips=_midi_clip_xml(
                clip_id="1",
                name="pad",
                start=50.0,
                end=70.0,
                loop_on=True,
                loop_start=0.0,
                loop_end=8.0,
                start_relative=0.0,
                notes=_note_xml(64, 0.0, 1.0),
            )
        ),
    )
    identity = identity_for_als_path(als)
    parent_path = _write_parent(tmp_path / "parent.json", _mini_pack(identity=identity))
    _write_journal(tmp_path / "journal", _ref(project_identity="wiped"))
    before_als = sha256_file(als)
    before_parent = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    report = collect_midi(
        parent_path=parent_path,
        als_path=als,
        journal_dir=tmp_path / "journal",
    )
    assert sha256_file(als) == before_als
    assert hashlib.sha256(parent_path.read_bytes()).hexdigest() == before_parent
    assert report["ALS_UNCHANGED"] is True
    assert report["parent_file_unchanged"] is True
    assert report["pack"]["pack_id"] != "pack_parent_midi"
    assert report["pack"]["alignment_claim"] == "LIMITED"
    assert report["pack"]["alignment_envelope_ms"] == 52.0
    assert report["NO MIDI WRITE"] is True
    codes = {row["code"] for row in report["pack"]["limitations"]}
    assert "MIDI_UNREAD" not in codes
    assert "MIDI_READ_ONLY_V1" in codes
    assert pack_payload_hash(EvidencePack.model_validate(report["pack"]))


def test_audio_midi_relation_uses_limited_alignment(tmp_path: Path) -> None:
    als = _write_als(
        tmp_path / "song.als",
        _decoy_and_target_tracks(
            clips=_midi_clip_xml(
                clip_id="1",
                name="pad",
                start=56.0,
                end=80.0,
                loop_on=False,
                loop_start=0.0,
                loop_end=16.0,
                start_relative=0.0,
                notes=_note_xml(65, 0.0, 12.0),
            )
        ),
        tempo=126.0,
    )
    identity = identity_for_als_path(als)
    pack = _mini_pack(
        identity=identity,
        start_s=0.0,
        end_s=1.0,
        kind="SILENCE",
        extra_events=[("STRONG_ENERGY_DIP", 11.4448, 11.5448)],
    )
    parent = _write_parent(tmp_path / "parent.json", pack)
    _write_journal(tmp_path / "journal", _ref(project_identity="wiped"))
    report = collect_midi(
        parent_path=parent,
        als_path=als,
        journal_dir=tmp_path / "journal",
    )
    rels = {row["event_id"]: row["relation"] for row in report["relations"]}
    assert rels["fm.event.0"] == NO_MIDI_EXPECTED_DURING_INTERVAL
    assert rels["fm.event.1"] == MIDI_PRESENT_DURING_INTERVAL
    assert report["MIDI_EVIDENCE_OUTCOME"]["A"] is True
    assert report["MIDI_EVIDENCE_OUTCOME"]["B"] is True
    assert report["pack"]["alignment_envelope_ms"] == 52.0


def test_collar_within_alignment_envelope_is_uncertain() -> None:
    # 52 ms at 120 bpm = 0.104 qn. MIDI ends at 40.0; event starts at 40.05.
    relation_core = expand_note_occurrences(
        clip_local_start=0.0,
        duration=4.0,
        current_start=36.0,
        current_end=40.0,
        start_relative=0.0,
        loop_on=False,
        loop_start=0.0,
        loop_end=4.0,
    )
    assert relation_core[0]["sounding_end_qn"] == 40.0
    from copilot.audio.midi_read_only_v1 import classify_audio_midi_relation

    active = [(36.0, 40.0)]
    envelope = 0.052 * (120.0 / 60.0)
    assert (
        classify_audio_midi_relation(
            event_start_qn=40.05,
            event_end_qn=40.15,
            active_intervals=active,
            envelope_qn=envelope,
        )
        == RELATION_UNCERTAIN
    )
    assert (
        classify_audio_midi_relation(
            event_start_qn=41.0,
            event_end_qn=41.2,
            active_intervals=active,
            envelope_qn=envelope,
        )
        == NO_MIDI_EXPECTED_DURING_INTERVAL
    )
