from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from copilot.audio.analyze_region_v1 import (
    ARRANGEMENT_GAP,
    ROLE_UNKNOWN,
    TARGET_SOURCE_EXPECTED,
    TARGET_SOURCE_NOT_EXPECTED,
    collect_region,
    project_mismatch_still_fails_closed,
    segment_region,
)
from copilot.audio.file_hash import sha256_file
from copilot.audio.midi_read_only_v1 import identity_for_als_path
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
OTHER_DEVICE = "other_sample_pad"


def _note_xml() -> str:
    return """
                  <KeyTrack>
                    <Notes>
                      <MidiNoteEvent Time="0" Duration="8" Velocity="100" IsEnabled="true" NoteId="9" />
                    </Notes>
                    <MidiKey Value="77" />
                  </KeyTrack>
    """


def _clip_xml(
    *,
    clip_id: str,
    name: str,
    start: float,
    end: float,
    disabled: bool = False,
    notes: str = "",
) -> str:
    return f"""
            <MidiClip Id="{clip_id}" Time="{start}">
              <CurrentStart Value="{start}" />
              <CurrentEnd Value="{end}" />
              <Loop>
                <LoopStart Value="0" />
                <LoopEnd Value="4" />
                <StartRelative Value="0" />
                <LoopOn Value="false" />
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


def _track_xml(
    *,
    locator_name: str,
    device_name: str,
    clips: str,
    speaker: bool = True,
    tag: str = "MidiTrack",
) -> str:
    return f"""
      <{tag}>
        <Name>
          <EffectiveName Value="{locator_name}" />
          <UserName Value="{locator_name}" />
        </Name>
        <DeviceChain>
          <Mixer>
            <Speaker>
              <Manual Value="{'true' if speaker else 'false'}" />
            </Speaker>
          </Mixer>
          <OriginalSimpler Id="0">
            <Name Value="{device_name}" />
          </OriginalSimpler>
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
        "project_identity": "wiped",
        "role": "midi",
        "name": "High String",
        "device_names": [DEVICE_NAME],
        "device_classes": ["OriginalSimpler"],
        "content_fingerprint": FINGERPRINT,
        "target_state_token": "tok",
    }
    payload.update(kwargs)
    return PersistentObjectRef.model_validate(payload)


def _mini_pack(*, identity: str) -> EvidencePack:
    return EvidencePack(
        pack_id="pack_parent_region",
        analysis_version="lowend-obs-1",
        prompt_schema_version="music-diagnosis-reason-1",
        region="AUTO_36_68:36.0-68.0",
        project_token="pt",
        audible_token="at",
        alignment_claim="LIMITED",
        alignment_envelope_ms=52.0,
        items=[
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
                value={"id": "AUTO_36_68", "start_qn": 36.0, "end_qn": 68.0},
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="midi.source.entity_id",
                kind=EvidenceKind.FACT,
                source_ref=FINGERPRINT,
                region="AUTO_36_68:36.0-68.0",
                analysis_version="midi-read-only-v1",
                name="midi_source_entity_id",
                value=FINGERPRINT,
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="midi.tempo_bpm",
                kind=EvidenceKind.FACT,
                source_ref=FINGERPRINT,
                region="AUTO_36_68:36.0-68.0",
                analysis_version="midi-read-only-v1",
                name="midi_tempo_bpm",
                value=126.0,
                unit="bpm",
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="midi.clip.0",
                kind=EvidenceKind.FACT,
                source_ref=FINGERPRINT,
                region="AUTO_36_68:36.0-68.0",
                analysis_version="midi-read-only-v1",
                name="midi_clip",
                value={
                    "clip_identity": "clip:0:target:56.0",
                    "arrangement_start_qn": 56.0,
                    "arrangement_end_qn": 168.0,
                    "muted": False,
                },
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="midi.note.0",
                kind=EvidenceKind.FACT,
                source_ref=FINGERPRINT,
                region="AUTO_36_68:36.0-68.0",
                analysis_version="midi-read-only-v1",
                name="midi_note",
                value={"pitch": 65, "arrangement_start_qn": 56.0, "duration_qn": 20.5, "muted": False},
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="midi.clip_note_starts",
                kind=EvidenceKind.FACT,
                source_ref=FINGERPRINT,
                region="AUTO_36_68:36.0-68.0",
                analysis_version="midi-read-only-v1",
                name="clip_note_starts",
                value=[56.0],
                unit="qn",
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="fm.event.0.kind",
                kind=EvidenceKind.FACT,
                source_ref="fullmix-obs-1",
                region="AUTO_36_68",
                analysis_version="fullmix-obs-1",
                name="fullmix_energy_event_kind",
                value="SILENCE",
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="fm.event.0.start_s",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="fullmix-obs-1",
                region="AUTO_36_68",
                analysis_version="fullmix-obs-1",
                name="fullmix_energy_event_start_s",
                value=0.0,
                unit="s",
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="fm.event.0.end_s",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="fullmix-obs-1",
                region="AUTO_36_68",
                analysis_version="fullmix-obs-1",
                name="fullmix_energy_event_end_s",
                value=1.0,
                unit="s",
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="fm.event.5.kind",
                kind=EvidenceKind.FACT,
                source_ref="fullmix-obs-1",
                region="AUTO_36_68",
                analysis_version="fullmix-obs-1",
                name="fullmix_energy_event_kind",
                value="STRONG_ENERGY_DIP",
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="fm.event.5.start_s",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="fullmix-obs-1",
                region="AUTO_36_68",
                analysis_version="fullmix-obs-1",
                name="fullmix_energy_event_start_s",
                value=11.4448,
                unit="s",
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="fm.event.5.end_s",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="fullmix-obs-1",
                region="AUTO_36_68",
                analysis_version="fullmix-obs-1",
                name="fullmix_energy_event_end_s",
                value=11.5448,
                unit="s",
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="fm.event.3.approx_period_s",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="fullmix-obs-1",
                region="AUTO_36_68",
                analysis_version="fullmix-obs-1",
                name="fullmix_energy_event_approx_period_s",
                value=3.823265306122449,
                unit="s",
                project_token="pt",
                audible_token="at",
            ),
        ],
        entities=[{"entity_id": FINGERPRINT, "kind": "TRACK", "name": "", "role": "midi"}],
        limitations=[
            ObservationLimitation(
                code="ALIGNMENT_LIMITED",
                detail="musical alignment remains LIMITED ±52 ms",
                precision_ms=52.0,
                capability_ms=[52.0],
            )
        ],
    )


def _write_parent(path: Path, pack: EvidencePack) -> Path:
    path.write_text(json.dumps({"pack": pack.model_dump(mode="json")}, indent=2), encoding="utf-8")
    return path


def _write_journal(directory: Path, ref: PersistentObjectRef) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pass.jsonl").write_text(
        json.dumps({"ref": ref.model_dump(mode="json"), "status": "PREPARED"}) + "\n",
        encoding="utf-8",
    )


def _session(*, path: str, name: str) -> SessionState:
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
    return attach_tokens(session, path=path, name=name)


def _collect(tmp_path: Path, tracks: str) -> dict:
    als = _write_als(tmp_path / "song.als", tracks)
    identity = identity_for_als_path(als)
    parent = _write_parent(tmp_path / "parent.json", _mini_pack(identity=identity))
    _write_journal(tmp_path / "journal", _ref())
    return collect_region(
        parent_path=parent,
        als_path=als,
        journal_dir=tmp_path / "journal",
    )


def test_clip_start_and_end_inside_region_become_segment_bounds() -> None:
    clips = [
        {
            "arrangement_start_qn": 40.0,
            "arrangement_end_qn": 48.0,
            "muted": False,
            "track_muted": False,
            "clip_identity": "a",
            "locator_name": "A",
            "content_fingerprint": None,
        },
        {
            "arrangement_start_qn": 56.0,
            "arrangement_end_qn": 80.0,
            "muted": False,
            "track_muted": False,
            "clip_identity": "b",
            "locator_name": "B",
            "content_fingerprint": None,
        },
    ]
    segs = segment_region(region_start=36.0, region_end=68.0, clips=clips)
    bounds = [(row["start_qn"], row["end_qn"]) for row in segs]
    assert bounds == [(36.0, 40.0), (40.0, 48.0), (48.0, 56.0), (56.0, 68.0)]
    assert segs[0]["arrangement_gap"] is True
    assert segs[1]["active_clip_count"] == 1
    assert segs[2]["arrangement_gap"] is True
    assert segs[3]["active_clip_count"] == 1


def test_muted_clip_is_not_active(tmp_path: Path) -> None:
    report = _collect(
        tmp_path,
        _track_xml(
            locator_name="Pad",
            device_name=DEVICE_NAME,
            clips=_clip_xml(clip_id="1", name="pad", start=40.0, end=60.0, disabled=True),
        ),
    )
    assert report["status"] == "VERIFIED"
    mid = next(row for row in report["segments"] if row["start_qn"] == 40.0)
    assert mid["overlapping_clip_count"] == 1
    assert mid["active_clip_count"] == 0
    assert mid["arrangement_gap"] is True


def test_track_mute_excludes_unmuted_clip(tmp_path: Path) -> None:
    report = _collect(
        tmp_path,
        _track_xml(
            locator_name="Pad",
            device_name=DEVICE_NAME,
            speaker=False,
            clips=_clip_xml(clip_id="1", name="pad", start=40.0, end=60.0),
        ),
    )
    mid = next(row for row in report["segments"] if row["start_qn"] == 40.0)
    assert mid["overlapping_clip_count"] == 1
    assert mid["active_clip_count"] == 0
    assert mid["active_track_count"] == 0


def test_overlapping_clips_count_separately(tmp_path: Path) -> None:
    tracks = _track_xml(
        locator_name="One",
        device_name=DEVICE_NAME,
        clips=_clip_xml(clip_id="1", name="a", start=36.0, end=60.0)
        + _clip_xml(clip_id="2", name="b", start=48.0, end=68.0),
    ) + _track_xml(
        locator_name="Kick",
        device_name=OTHER_DEVICE,
        clips=_clip_xml(clip_id="3", name="c", start=40.0, end=48.0, notes=_note_xml()),
    )
    report = _collect(tmp_path, tracks)
    overlap = next(row for row in report["segments"] if row["start_qn"] == 48.0)
    assert overlap["active_clip_count"] == 2
    assert overlap["active_track_count"] == 1


def test_no_clip_activity_is_single_gap_segment(tmp_path: Path) -> None:
    report = _collect(
        tmp_path,
        _track_xml(locator_name="Empty", device_name=DEVICE_NAME, clips=""),
    )
    assert report["segment_count"] == 1
    assert report["segments"][0]["start_qn"] == 36.0
    assert report["segments"][0]["end_qn"] == 68.0
    assert report["segments"][0]["arrangement_gap"] is True
    event0 = next(row for row in report["event_context"] if row["event_id"] == "fm.event.0")
    assert ARRANGEMENT_GAP in event0["labels"]
    assert event0["target_source"] == TARGET_SOURCE_NOT_EXPECTED


def test_unknown_source_roles_remain_unknown(tmp_path: Path) -> None:
    tracks = _track_xml(
        locator_name="High String",
        device_name=DEVICE_NAME,
        clips=_clip_xml(clip_id="1", name="t", start=56.0, end=80.0),
    ) + _track_xml(
        locator_name="Kick",
        device_name=OTHER_DEVICE,
        clips=_clip_xml(clip_id="2", name="k", start=40.0, end=48.0, notes=_note_xml()),
    )
    report = _collect(tmp_path, tracks)
    roles = {
        item["value"]["locator_name"]: item["value"]["role"]
        for item in report["pack"]["items"]
        if item["name"] == "region_clip"
    }
    assert roles["Kick"] == ROLE_UNKNOWN
    assert roles["High String"] == "midi"
    event5 = next(row for row in report["event_context"] if row["event_id"] == "fm.event.5")
    assert event5["target_source"] == TARGET_SOURCE_EXPECTED


def test_no_accidental_midi_note_collection_from_other_tracks(tmp_path: Path) -> None:
    source = Path("src/copilot/audio/analyze_region_v1.py").read_text(encoding="utf-8")
    assert "MidiNoteEvent" not in source
    assert "KeyTrack" not in source
    tracks = _track_xml(
        locator_name="High String",
        device_name=DEVICE_NAME,
        clips=_clip_xml(clip_id="1", name="t", start=56.0, end=80.0, notes=_note_xml()),
    ) + _track_xml(
        locator_name="Lead",
        device_name=OTHER_DEVICE,
        clips=_clip_xml(clip_id="2", name="lead", start=36.0, end=68.0, notes=_note_xml()),
    )
    report = _collect(tmp_path, tracks)
    note_ids = [item["evidence_id"] for item in report["pack"]["items"] if item["evidence_id"].startswith("midi.note.")]
    assert note_ids == ["midi.note.0"]
    assert report["extra_midi_note_ids"] == []
    assert report["NO EXTRA MIDI READ"] is True
    assert report["pack"]["items"]
    pitches = [item["value"].get("pitch") for item in report["pack"]["items"] if item["name"] == "midi_note"]
    assert pitches == [65]


def test_actual_project_mismatch_still_fails_closed(tmp_path: Path) -> None:
    als = _write_als(
        tmp_path / "song.als",
        _track_xml(
            locator_name="Pad",
            device_name=DEVICE_NAME,
            clips=_clip_xml(clip_id="1", name="t", start=40.0, end=48.0),
        ),
    )
    other = tmp_path / "other.als"
    other.write_bytes(als.read_bytes())
    identity = identity_for_als_path(als)
    parent = _write_parent(tmp_path / "parent.json", _mini_pack(identity=identity))
    _write_journal(tmp_path / "journal", _ref(project_identity=identity))
    mismatched = collect_region(
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


def test_parent_evidence_pack_unchanged(tmp_path: Path) -> None:
    als = _write_als(
        tmp_path / "song.als",
        _track_xml(
            locator_name="Pad",
            device_name=DEVICE_NAME,
            clips=_clip_xml(clip_id="1", name="t", start=40.0, end=48.0)
            + _clip_xml(clip_id="2", name="u", start=48.0, end=56.0),
        ),
    )
    identity = identity_for_als_path(als)
    parent_path = _write_parent(tmp_path / "parent.json", _mini_pack(identity=identity))
    _write_journal(tmp_path / "journal", _ref())
    before_parent = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    before_als = sha256_file(als)
    report = collect_region(
        parent_path=parent_path,
        als_path=als,
        journal_dir=tmp_path / "journal",
    )
    assert hashlib.sha256(parent_path.read_bytes()).hexdigest() == before_parent
    assert sha256_file(als) == before_als
    assert report["parent_file_unchanged"] is True
    assert report["ALS_UNCHANGED"] is True
    assert report["pack"]["pack_id"] != "pack_parent_region"
    assert report["pack"]["alignment_claim"] == "LIMITED"
    assert report["pack"]["alignment_envelope_ms"] == 52.0
    codes = {row["code"] for row in report["pack"]["limitations"]}
    assert "ANALYZE_REGION_V1" in codes
    starts = [row["start_qn"] for row in report["segments"]]
    assert starts == [36.0, 40.0, 48.0, 56.0]
    assert report["NO ROUTING READ"] is True
    assert report["NO CAPTURE_VIEW"] is True
    assert report["NO RECAPTURE"] is True
    assert report["MUSICAL WRITES"] == 0
    blob = json.dumps(report["pack"], ensure_ascii=False).lower()
    assert "the break is too empty" not in blob
    assert "tech house" not in blob
    event0 = next(row for row in report["event_context"] if row["event_id"] == "fm.event.0")
    assert event0["target_source"] == TARGET_SOURCE_NOT_EXPECTED
    event5 = next(row for row in report["event_context"] if row["event_id"] == "fm.event.5")
    assert event5["target_source"] == TARGET_SOURCE_EXPECTED
    assert event5["arrangement_gap"] is True
    typed = {
        item["evidence_id"]: item["value"]
        for item in report["pack"]["items"]
        if item["unit"] == "qn" and item["evidence_id"].startswith("region.segment.1")
    }
    assert typed["region.segment.1.start_qn"] == 40.0
    assert typed["region.segment.1.end_qn"] == 48.0
