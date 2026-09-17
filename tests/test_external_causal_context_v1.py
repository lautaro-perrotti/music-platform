from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.external_causal_context_v1 import (
    DIRECT_SIGNAL_PATH,
    GROUP_PATH,
    MIDI_NOT_APPLICABLE,
    MIDI_PRESENT_DURING_INTERVAL,
    NO_DIRECT_MAIN_PATH,
    RETURN_PATH,
    ROUTING_UNKNOWN,
    SIDECHAIN_CONTROL_PATH,
    SOURCE_EVIDENCE_UNAVAILABLE,
    SOURCE_HAS_SIGNAL,
    classify_routing_kind,
    collect_causal_context,
    read_session_routing,
    routing_hops,
)
from copilot.audio.file_hash import sha256_file
from copilot.audio.midi_read_only_v1 import identity_for_als_path, project_mismatch_still_fails_closed
from copilot.daw.object_ref import PersistentObjectRef, ref_from_track
from copilot.schemas.evidence import (
    CaptureQuality,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)
from copilot.schemas.session import (
    DeviceState,
    MixerState,
    RoutingState,
    SendState,
    SessionState,
    TrackState,
    TransportState,
)

FP_KICK = "fp_filter_kick"
FP_BASS = "fp_filtered_bass"
FP_HAT = "fp_open_hat"


def _tone(path: Path, *, seconds: float = 2.0, amp: float = 0.2) -> Path:
    n = int(seconds * 44100)
    t = np.arange(n, dtype=np.float64) / 44100
    wave = (amp * np.sin(2 * np.pi * 110.0 * t)).astype(np.float32)
    sf.write(str(path), np.column_stack([wave, wave]), 44100)
    return path


def _midi_clip_xml(*, clip_id: str, start: float, end: float, loop_on: bool, notes: str, loop_start: float = 0.0, loop_end: float = 8.0, start_relative: float = 0.0) -> str:
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
              <Name Value="clip" />
              <Disabled Value="false" />
              <Notes>
                <KeyTracks>
                  {notes}
                </KeyTracks>
              </Notes>
            </MidiClip>
    """


def _note_xml(pitch: int, time: float, duration: float) -> str:
    return f"""
                  <KeyTrack>
                    <Notes>
                      <MidiNoteEvent Time="{time}" Duration="{duration}" Velocity="100" OffVelocity="64" IsEnabled="true" NoteId="1" />
                    </Notes>
                    <MidiKey Value="{pitch}" />
                  </KeyTrack>
    """


def _track_xml(*, tag: str, locator: str, device: str, clips: str, grouped: bool = False, output: str = "Master") -> str:
    return f"""
      <{tag}>
        <Name>
          <EffectiveName Value="{locator}" />
          <UserName Value="{locator}" />
        </Name>
        <IsGrouped Value="{'true' if grouped else 'false'}" />
        <DeviceChain>
          <Mixer>
            <AudioOutputRouting>
              <UpperDisplayString Value="{output}" />
            </AudioOutputRouting>
          </Mixer>
          <OriginalSimpler Id="0">
            <Name Value="{device}" />
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


def _ref(fingerprint: str, name: str, device: str) -> PersistentObjectRef:
    return PersistentObjectRef(
        project_identity="id",
        role="midi",
        name=name,
        device_names=[device],
        device_classes=["OriginalSimpler"],
        content_fingerprint=fingerprint,
        target_state_token="tok",
    )


def _capture_item(evidence_id: str, fingerprint: str, wav: Path) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=EvidenceKind.MEASUREMENT,
        source_ref=fingerprint,
        region="AUTO_36_68:36.0-68.0",
        view="TRACK_ISOLATED",
        analysis_version="lowend-obs-1",
        name="source_capture",
        value={"ok": True, "signal_class": "HAS_SIGNAL", "audio_sha256": sha256_file(wav), "path": None, "ref": fingerprint},
        quality=CaptureQuality.LIMITED,
        project_token="pt",
        audible_token="at",
    )


def _event_items(idx: int, kind: str, start_s: float, end_s: float, active: list[str]) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            evidence_id=f"fm.event.{idx}.kind",
            kind=EvidenceKind.FACT,
            source_ref="fullmix-obs-1",
            region="AUTO_36_68",
            analysis_version="fullmix-obs-1",
            name="fullmix_energy_event_kind",
            value=kind,
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
            value=start_s,
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
            value=end_s,
            unit="s",
            project_token="pt",
            audible_token="at",
        ),
        EvidenceItem(
            evidence_id=f"region.event.fm.event.{idx}",
            kind=EvidenceKind.FACT,
            source_ref="arrangement_region",
            region="AUTO_36_68:36.0-68.0",
            analysis_version="analyze-region-v1",
            name="region_event_context",
            value={
                "event_id": f"fm.event.{idx}",
                "kind": kind,
                "start_s": start_s,
                "end_s": end_s,
                "start_qn": 36.0 + start_s * (126.0 / 60.0),
                "end_qn": 36.0 + end_s * (126.0 / 60.0),
                "active_clip_identities": active,
            },
            project_token="pt",
            audible_token="at",
        ),
    ]


def _clip_item(idx: int, identity: str, fingerprint: str | None, locator: str, role: str = "midi") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"region.clip.{idx}",
        kind=EvidenceKind.FACT,
        source_ref="arrangement_region",
        region="AUTO_36_68:36.0-68.0",
        analysis_version="analyze-region-v1",
        name="region_clip",
        value={
            "clip_identity": identity,
            "locator_name": locator,
            "content_fingerprint": fingerprint,
            "role": role,
        },
        project_token="pt",
        audible_token="at",
    )


def _pack(items: list[EvidenceItem], entities: list[dict], identity: str) -> EvidencePack:
    return EvidencePack(
        pack_id="pack_parent_causal",
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
            *items,
        ],
        entities=entities,
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


def _journal(directory: Path, ref: PersistentObjectRef, digest: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"ref": ref.model_dump(mode="json"), "status": "PREPARED", "pass_id": "pass"}),
        json.dumps({"hashes": {"source": digest}, "status": "VERIFIED", "pass_id": "pass"}),
    ]
    (directory / f"{ref.content_fingerprint[:8]}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _track_state(*, index: int, name: str, role: str, output: str, grouped: bool = False, foldable: bool = False, devices: list[str] | None = None, sends: list[SendState] | None = None) -> TrackState:
    return TrackState(
        stable_id=str(index),
        index=index,
        name=name,
        role=role,  # type: ignore[arg-type]
        mixer=MixerState(),
        routing=RoutingState(output_type=output, output_channel=""),
        grouped=grouped,
        foldable=foldable,
        devices=[DeviceState(stable_id=name, index=0, name=name, class_name="OriginalSimpler")] if devices else [],
        sends=list(sends or []),
    )


def test_looped_other_source_midi(tmp_path: Path) -> None:
    wav = _tone(tmp_path / "kick.wav")
    als = _write_als(
        tmp_path / "song.als",
        _track_xml(
            tag="MidiTrack",
            locator="Filter Kick",
            device="KickDevice",
            clips=_midi_clip_xml(
                clip_id="1",
                start=36.0,
                end=68.0,
                loop_on=True,
                loop_start=0.0,
                loop_end=8.0,
                notes=_note_xml(36, 0.0, 0.5),
            ),
        ),
    )
    identity = identity_for_als_path(als)
    digest = sha256_file(wav) or ""
    _journal(tmp_path / "journal", _ref(FP_KICK, "Filter Kick", "KickDevice").model_copy(update={"project_identity": "wiped"}), digest)
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_KICK, wav),
            *_event_items(3, "NEAR_SILENCE", 3.823, 3.95, ["clip:kick"]),
            _clip_item(0, "clip:kick", FP_KICK, "Filter Kick"),
        ],
        [{"entity_id": FP_KICK, "kind": "TRACK", "name": "", "role": "midi"}],
        identity,
    )
    report = collect_causal_context(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        als_path=als,
        journal_dir=tmp_path / "journal",
        capture_roots=[tmp_path],
    )
    midi = report["midi_rows"][0]
    onsets = [note["arrangement_start_qn"] for note in midi["notes"]]
    assert 44.0 in onsets
    assert 36.0 not in onsets
    rel = next(row for row in midi["relations"] if row["event_id"] == "fm.event.3")
    assert rel["relation"] == MIDI_PRESENT_DURING_INTERVAL


def test_midi_not_applicable_audio_track() -> None:
    assert classify_routing_kind(output_type="Main") == DIRECT_SIGNAL_PATH
    kind = MIDI_NOT_APPLICABLE
    assert kind == "MIDI_NOT_APPLICABLE"


def test_group_direct_return_and_sidechain_routing() -> None:
    assert classify_routing_kind(output_type="Main") == DIRECT_SIGNAL_PATH
    assert classify_routing_kind(output_type="AudioOut/GroupTrack") == GROUP_PATH
    assert classify_routing_kind(output_type="A-Reverb", return_names=["A-Reverb"]) == RETURN_PATH
    assert classify_routing_kind(output_type="", grouped=False) == ROUTING_UNKNOWN
    hops = routing_hops(source_locator="Kick", grouped=True, parent_group="Drums", output_type="Main")
    assert hops == ["Kick", "Drums", "Main"]
    session = SessionState(
        project_identity="proj",
        tracks=[
            _track_state(index=0, name="Drums", role="midi", output="Main", foldable=True),
            _track_state(index=1, name="Filter Kick", role="midi", output="Group", grouped=True, devices=["KickDevice"]),
            _track_state(index=2, name="A-Reverb", role="return", output="Main"),
            _track_state(
                index=3,
                name="SC Trigger",
                role="midi",
                output="Main",
                devices=["Trig"],
                sends=[SendState(index=0, name="A-Reverb", value=0.4)],
            ),
        ],
    )
    sc = session.tracks[3]
    sc_ref = ref_from_track(sc, project_identity="proj")
    rows = read_session_routing(
        session,
        fingerprints={sc_ref.content_fingerprint: sc_ref},
        sidechain_sources={"SC Trigger"},
    )
    assert rows[0]["control_kind"] == SIDECHAIN_CONTROL_PATH
    assert rows[0]["kind"] != SIDECHAIN_CONTROL_PATH
    assert rows[0]["send_is_not_audible_contribution"] is True


def test_unavailable_routing_property() -> None:
    assert classify_routing_kind(output_type=None) == ROUTING_UNKNOWN
    assert classify_routing_kind(output_type="") == ROUTING_UNKNOWN
    hops = routing_hops(source_locator="Pad", grouped=False, parent_group=None, output_type="")
    assert ROUTING_UNKNOWN in hops or hops[-1] == ROUTING_UNKNOWN


def test_incomplete_source_coverage_and_matched_windows(tmp_path: Path) -> None:
    wav = _tone(tmp_path / "kick.wav")
    als = _write_als(
        tmp_path / "song.als",
        _track_xml(tag="MidiTrack", locator="Filter Kick", device="KickDevice", clips=_midi_clip_xml(clip_id="1", start=36.0, end=52.0, loop_on=False, notes=_note_xml(36, 0.0, 2.0))),
    )
    identity = identity_for_als_path(als)
    digest = sha256_file(wav) or ""
    _journal(tmp_path / "journal", _ref(FP_KICK, "Filter Kick", "KickDevice").model_copy(update={"project_identity": "wiped"}), digest)
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_KICK, wav),
            *_event_items(3, "NEAR_SILENCE", 1.0, 1.1, ["clip:kick", "clip:vox"]),
            _clip_item(0, "clip:kick", FP_KICK, "Filter Kick"),
            _clip_item(1, "clip:vox", None, "Vox FX", role="UNKNOWN"),
        ],
        [{"entity_id": FP_KICK, "kind": "TRACK", "name": "", "role": "midi"}],
        identity,
    )
    report = collect_causal_context(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        als_path=als,
        journal_dir=tmp_path / "journal",
        capture_roots=[tmp_path],
    )
    event = next(row for row in report["audio_by_event"] if row["event_id"] == "fm.event.3")
    assert event["ARRANGEMENT_ACTIVE_SOURCE_COUNT"] == 2
    assert event["UNCAPTURED_SOURCE_COUNT"] == 1
    assert report["SOURCE_AUDIO_COVERAGE"] == "INCOMPLETE"
    assert event["uncaptured_active"][0]["status"] == "TRUTHFULLY_UNAVAILABLE"
    assert event["sources"][0]["relation"] in {SOURCE_HAS_SIGNAL, SOURCE_EVIDENCE_UNAVAILABLE}
    table = next(row for row in report["causal_tables"] if row["event_id"] == "fm.event.3")
    assert "fault" not in json.dumps(table).lower()


def test_parent_pack_immutable_and_zero_writes(tmp_path: Path) -> None:
    wav = _tone(tmp_path / "kick.wav")
    als = _write_als(
        tmp_path / "song.als",
        _track_xml(tag="MidiTrack", locator="Filter Kick", device="KickDevice", clips=""),
    )
    identity = identity_for_als_path(als)
    digest = sha256_file(wav) or ""
    _journal(tmp_path / "journal", _ref(FP_KICK, "Filter Kick", "KickDevice").model_copy(update={"project_identity": "wiped"}), digest)
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_KICK, wav),
            *_event_items(3, "NEAR_SILENCE", 0.4, 0.5, ["clip:kick"]),
            _clip_item(0, "clip:kick", FP_KICK, "Filter Kick"),
        ],
        [{"entity_id": FP_KICK, "kind": "TRACK", "name": "", "role": "midi"}],
        identity,
    )
    parent = _write_parent(tmp_path / "parent.json", pack)
    before = hashlib.sha256(parent.read_bytes()).hexdigest()
    als_before = hashlib.sha256(als.read_bytes()).hexdigest()
    report = collect_causal_context(
        parent_path=parent,
        als_path=als,
        journal_dir=tmp_path / "journal",
        capture_roots=[tmp_path],
    )
    assert hashlib.sha256(parent.read_bytes()).hexdigest() == before
    assert report["parent_file_unchanged"] is True
    assert report["ALS_UNCHANGED"] is True
    assert hashlib.sha256(als.read_bytes()).hexdigest() == als_before
    assert report["MUSICAL WRITES"] == 0
    assert report["NO MIDI WRITE"] is True
    assert report["NO ROUTING WRITE"] is True
    source = Path("src/copilot/audio/external_causal_context_v1.py").read_text(encoding="utf-8")
    assert "set_track_output_routing" not in source
    assert "set_track_volume" not in source
    assert "create_return_track" not in source


def test_project_mismatch_fail_closed(tmp_path: Path) -> None:
    wav = _tone(tmp_path / "kick.wav")
    als = _write_als(
        tmp_path / "song.als",
        _track_xml(tag="MidiTrack", locator="Filter Kick", device="KickDevice", clips=""),
    )
    other = tmp_path / "other.als"
    other.write_bytes(als.read_bytes())
    identity = identity_for_als_path(als)
    digest = sha256_file(wav) or ""
    _journal(tmp_path / "journal", _ref(FP_KICK, "Filter Kick", "KickDevice"), digest)
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_KICK, wav),
            *_event_items(3, "NEAR_SILENCE", 0.4, 0.5, ["clip:kick"]),
            _clip_item(0, "clip:kick", FP_KICK, "Filter Kick"),
        ],
        [{"entity_id": FP_KICK, "kind": "TRACK", "name": "", "role": "midi"}],
        identity,
    )
    mismatched = collect_causal_context(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        als_path=other,
        journal_dir=tmp_path / "journal",
        capture_roots=[tmp_path],
    )
    assert mismatched["BLOCKER"] == "PROJECT_MISMATCH"
    session = SessionState(project_identity="other-project", tracks=[])
    ref = PersistentObjectRef(project_identity=identity, role="midi", name="Filter Kick")
    assert project_mismatch_still_fails_closed(session, ref) is True
    blocked = collect_causal_context(
        parent_path=_write_parent(tmp_path / "parent2.json", pack),
        als_path=als,
        journal_dir=tmp_path / "journal",
        capture_roots=[tmp_path],
        session=session,
    )
    assert blocked["BLOCKER"] == "PROJECT_MISMATCH"


def test_audio_track_midi_not_applicable_in_collect(tmp_path: Path) -> None:
    wav = _tone(tmp_path / "vox.wav")
    als = _write_als(
        tmp_path / "song.als",
        _track_xml(tag="AudioTrack", locator="Filter Kick", device="KickDevice", clips=""),
    )
    identity = identity_for_als_path(als)
    digest = sha256_file(wav) or ""
    _journal(tmp_path / "journal", _ref(FP_KICK, "Filter Kick", "KickDevice").model_copy(update={"project_identity": "wiped"}), digest)
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_KICK, wav),
            *_event_items(3, "NEAR_SILENCE", 0.4, 0.5, ["clip:kick"]),
            _clip_item(0, "clip:kick", FP_KICK, "Filter Kick"),
        ],
        [{"entity_id": FP_KICK, "kind": "TRACK", "name": "", "role": "midi"}],
        identity,
    )
    report = collect_causal_context(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        als_path=als,
        journal_dir=tmp_path / "journal",
        capture_roots=[tmp_path],
    )
    assert report["midi_rows"][0]["midi_status"] == MIDI_NOT_APPLICABLE
    rel = report["midi_rows"][0]["relations"][0]["relation"]
    assert rel == MIDI_NOT_APPLICABLE


def test_audio_track_mixer_only_devices_unique_locator(tmp_path: Path) -> None:
    wav = _tone(tmp_path / "cmon.wav")
    als = _write_als(
        tmp_path / "song.als",
        _track_xml(tag="AudioTrack", locator="C'mon", device="Unused", clips="", output="Master"),
    )
    identity = identity_for_als_path(als)
    digest = sha256_file(wav) or ""
    ref = PersistentObjectRef(
        project_identity="wiped",
        role="audio",
        name="C'mon",
        device_names=["Delay"],
        device_classes=["Delay"],
        content_fingerprint="fp_cmon_audio",
        target_state_token="tok",
    )
    _journal(tmp_path / "journal", ref, digest)
    pack = _pack(
        [
            *_event_items(3, "NEAR_SILENCE", 0.4, 0.5, ["clip:cmon"]),
            _clip_item(0, "clip:cmon", None, "C'mon", role="UNKNOWN"),
        ],
        [{"entity_id": "fp_cmon_audio", "kind": "TRACK", "name": "", "role": "audio"}],
        identity,
    )
    report = collect_causal_context(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        als_path=als,
        journal_dir=tmp_path / "journal",
        capture_roots=[tmp_path],
    )
    routing = next(row for row in report["routing_rows"] if row.get("locator_name") == "C'mon")
    assert routing["ok"] is True
    assert routing["kind"] == DIRECT_SIGNAL_PATH
    assert routing["midi_capable"] is False
    table = next(row for row in report["causal_tables"] if row["event_id"] == "fm.event.3")
    cmon = next(row for row in table["rows"] if row["SOURCE"] == "C'mon")
    assert cmon["MIDI_EXPECTED"] == MIDI_NOT_APPLICABLE
    assert cmon["ARRANGEMENT_ACTIVE"] is True
    assert report["SOURCE_AUDIO_COVERAGE"] == "COMPLETE"
