from __future__ import annotations

import pytest

from copilot.audio.arrangement_activity import (
    clips_overlap_region,
    inspect_arrangement_activity,
    map_lowend_candidates,
)
from copilot.audio.arrangement_seek import (
    CONTINUE_AFTER_STOPPED_SEEK_SUPPORTED,
    PLAYBACK_START_TOLERANCE_QN,
    QN_UNIT,
    STOPPED_SEEK_PLUS_PLAY_SUPPORTED,
    TRANSPORT_PRIMITIVE_VERSION,
    ArrangementPlaybackStartError,
    ArrangementSeekError,
    implied_start_qn,
    seek_arrangement_qn,
    start_arrangement_at_qn,
    transport_target_qn,
    verify_playback_start_qn,
    verify_song_time_qn,
)
from copilot.audio.live_capture import beats_to_seconds


class _CursorDaw:
    def __init__(self, cursor: float = 1.45, apply_seek: bool = True) -> None:
        self.cursor = cursor
        self.apply_seek = apply_seek
        self.playing = False
        self.sets: list[float] = []
        self.calls: list[str] = []

    def get_playback_position(self) -> dict:
        return {
            "current_song_time": self.cursor,
            "is_playing": self.playing,
            "tempo": 167.0,
            "loop": True,
            "loop_start": 0.0,
            "loop_length": 432.0,
            "signature_numerator": 4,
            "signature_denominator": 4,
        }

    def stop_playback(self) -> dict:
        self.calls.append("stop_playback")
        self.playing = False
        return {"playing": False}

    def start_playback(self) -> dict:
        self.calls.append("start_playback")
        self.playing = True
        self.cursor = 1.16
        return {"playing": True}

    def continue_playing(self) -> dict:
        self.calls.append("continue_playing")
        self.playing = True
        return {"playing": True}

    def set_current_song_time(self, time: float) -> dict:
        self.calls.append("set_current_song_time")
        self.sets.append(float(time))
        if self.apply_seek:
            self.cursor = float(time)
        return {"success": True, "current_song_time": self.cursor}

    def jump_to_time(self, time: float) -> dict:
        self.calls.append("jump_to_time")
        return self.set_current_song_time(time)

    def scrub_by(self, delta: float) -> dict:
        self.calls.append("scrub_by")
        if self.apply_seek:
            self.cursor = self.cursor + float(delta)
        return {"success": True}


class _AtomicDaw(_CursorDaw):
    def start_playback_at_qn(self, time: float) -> dict:
        self.calls.append("start_playback_at_qn")
        self.playing = True
        elapsed = 0.10
        observed = float(time) + elapsed * 167.0 / 60.0
        self.cursor = observed
        return {
            "ok": True,
            "target_qn": float(time),
            "observed_qn": observed,
            "is_playing": True,
            "tempo": 167.0,
            "t_command_mono": 1.0,
            "t_tick_mono": 1.10,
            "t_command_wall": 10.0,
            "t_tick_wall": 10.10,
            "elapsed_s": elapsed,
            "same_callback_qn": 0.0,
            "same_callback_playing": True,
            "unit": QN_UNIT,
            "transport_primitive_version": TRANSPORT_PRIMITIVE_VERSION,
            "verification": "next_tick",
        }


def test_region_32_64_is_quarter_note_position_not_seconds_or_bars() -> None:
    assert QN_UNIT == "quarter_note_position"
    duration_s = beats_to_seconds(32.0, 167.0)
    assert abs(duration_s - (32.0 * 60.0 / 167.0)) < 1e-12
    assert duration_s != 32.0
    assert 32.0 != 32.0 * 4


def test_stopped_seek_plus_start_playing_is_not_supported() -> None:
    assert STOPPED_SEEK_PLUS_PLAY_SUPPORTED is False
    daw = _AtomicDaw()
    seek_arrangement_qn(daw, 32.0)
    started = start_arrangement_at_qn(daw, 32.0)
    assert "start_playback" not in daw.calls
    assert daw.calls.count("start_playback_at_qn") == 1
    assert started["methods"] == ["start_playback_at_qn"]


def test_continue_playing_after_stopped_seek_is_not_supported() -> None:
    assert CONTINUE_AFTER_STOPPED_SEEK_SUPPORTED is False
    daw = _AtomicDaw()
    seek_arrangement_qn(daw, 32.0)
    start_arrangement_at_qn(daw, 32.0)
    assert "continue_playing" not in daw.calls


def test_atomic_start_seek_passes_implied_start() -> None:
    daw = _AtomicDaw()
    started = start_arrangement_at_qn(daw, 32.0)
    assert started["TRANSPORT_START_PROVENANCE"] == "VERIFIED"
    assert started["verification"] == "next_tick"
    assert abs(started["implied_start_qn"] - 32.0) < 0.05
    assert started["transport_primitive_version"] == TRANSPORT_PRIMITIVE_VERSION


def test_first_poll_is_not_accepted_as_start_truth() -> None:
    with pytest.raises(ArrangementPlaybackStartError, match="ARRANGEMENT_PLAYBACK_START_MISMATCH"):
        verify_playback_start_qn(33.17, 32.0)
    assert PLAYBACK_START_TOLERANCE_QN <= 0.75


def test_verify_playback_start_rejects_previous_cursor() -> None:
    with pytest.raises(ArrangementPlaybackStartError, match="ARRANGEMENT_PLAYBACK_START_MISMATCH"):
        verify_playback_start_qn(1.45, 32.0)


def test_verify_playback_start_accepts_requested_start() -> None:
    verify_playback_start_qn(32.12, 32.0)
    verify_song_time_qn(32.0, 32.0)


def test_implied_start_from_delayed_first_poll_is_32_not_33() -> None:
    tempo = 167.0
    t_play_command = 1000.0
    t_first_poll = 1000.42
    first_qn = 33.17
    implied = implied_start_qn(
        first_qn,
        t_first_poll=t_first_poll,
        t_play_command=t_play_command,
        tempo=tempo,
    )
    assert abs(implied - 32.0) < 0.05


def test_implied_start_near_zero_is_mismatch() -> None:
    implied = implied_start_qn(
        1.16,
        t_first_poll=1000.40,
        t_play_command=1000.0,
        tempo=167.0,
    )
    assert implied < 1.0
    with pytest.raises(ArrangementPlaybackStartError, match="ARRANGEMENT_PLAYBACK_START_MISMATCH"):
        verify_playback_start_qn(implied, 32.0)


def test_seek_to_qn_fails_closed_when_live_keeps_old_cursor() -> None:
    daw = _CursorDaw(cursor=1.45, apply_seek=False)
    with pytest.raises(ArrangementSeekError, match="ARRANGEMENT_SEEK_FAILED"):
        seek_arrangement_qn(daw, 32.0)
    assert daw.cursor == 1.45


def test_seek_to_qn_moves_to_requested_quarter_note() -> None:
    daw = _CursorDaw(cursor=1.45, apply_seek=True)
    result = seek_arrangement_qn(daw, 32.0)
    assert result["ok"] is True
    assert result["observed_qn"] == 32.0


def test_pre_roll_target_is_16_qn_before_requested() -> None:
    assert transport_target_qn(32.0) == 16.0
    assert transport_target_qn(8.0) == 0.0


def test_arrangement_activity_presence_only(tmp_path) -> None:
    xml = """<?xml version="1.0"?>
    <Ableton>
      <LiveSet>
        <GroupTrack>
          <Name><EffectiveName Value="Drums"/></Name>
          <IsFoldable Value="true"/>
        </GroupTrack>
        <MidiTrack>
          <Name><EffectiveName Value="Kick 808 Deep"/></Name>
          <IsGrouped Value="true"/>
          <DeviceChain>
            <MainSequencer>
              <ClipTimeable>
                <ArrangerAutomation>
                  <Events>
                    <MidiClip Time="0">
                      <CurrentStart Value="0"/>
                      <CurrentEnd Value="432"/>
                    </MidiClip>
                  </Events>
                </ArrangerAutomation>
              </ClipTimeable>
            </MainSequencer>
          </DeviceChain>
        </MidiTrack>
        <MidiTrack>
          <Name><EffectiveName Value="Sub Sub Bass"/></Name>
          <DeviceChain>
            <MainSequencer>
              <ClipTimeable>
                <ArrangerAutomation>
                  <Events>
                    <MidiClip Time="172">
                      <CurrentStart Value="172"/>
                      <CurrentEnd Value="324"/>
                    </MidiClip>
                  </Events>
                </ArrangerAutomation>
              </ClipTimeable>
            </MainSequencer>
          </DeviceChain>
        </MidiTrack>
      </LiveSet>
    </Ableton>
    """
    path = tmp_path / "pista_copilot_eval.als"
    path.write_text(xml, encoding="utf-8")
    regions = [
        {"id": "REGION_1", "start_qn": 32.0, "end_qn": 64.0},
        {"id": "REGION_2", "start_qn": 172.0, "end_qn": 204.0},
        {"id": "REGION_3", "start_qn": 292.0, "end_qn": 324.0},
    ]
    report = inspect_arrangement_activity(path, regions)
    by_id = {row["id"]: row for row in report["regions"]}
    assert by_id["REGION_1"]["drums"]["class"] == "HAS_MATERIAL"
    assert by_id["REGION_1"]["sub_sub_bass"]["class"] == "SOURCE_INACTIVE"
    assert by_id["REGION_2"]["drums"]["class"] == "HAS_MATERIAL"
    assert by_id["REGION_2"]["sub_sub_bass"]["class"] == "HAS_MATERIAL"
    assert clips_overlap_region(
        [{"start_qn": 32.0, "end_qn": 64.0}], 64.0, 96.0
    ) == []


def test_lowend_candidates_prefer_real_overlap(tmp_path) -> None:
    xml = """<?xml version="1.0"?>
    <Ableton>
      <LiveSet>
        <MidiTrack>
          <Name><EffectiveName Value="Drums"/></Name>
          <DeviceChain><MainSequencer><ClipTimeable><ArrangerAutomation><Events>
            <MidiClip Time="0"><CurrentStart Value="0"/><CurrentEnd Value="160"/></MidiClip>
            <MidiClip Time="224"><CurrentStart Value="224"/><CurrentEnd Value="352"/></MidiClip>
          </Events></ArrangerAutomation></ClipTimeable></MainSequencer></DeviceChain>
        </MidiTrack>
        <MidiTrack>
          <Name><EffectiveName Value="Rose Bass"/></Name>
          <DeviceChain><MainSequencer><ClipTimeable><ArrangerAutomation><Events>
            <MidiClip Time="32"><CurrentStart Value="32"/><CurrentEnd Value="96"/></MidiClip>
          </Events></ArrangerAutomation></ClipTimeable></MainSequencer></DeviceChain>
        </MidiTrack>
        <MidiTrack>
          <Name><EffectiveName Value="Sub Sub Bass"/></Name>
          <DeviceChain><MainSequencer><ClipTimeable><ArrangerAutomation><Events>
            <MidiClip Time="256"><CurrentStart Value="256"/><CurrentEnd Value="320"/></MidiClip>
          </Events></ArrangerAutomation></ClipTimeable></MainSequencer></DeviceChain>
        </MidiTrack>
      </LiveSet>
    </Ableton>
    """
    path = tmp_path / "set.als"
    path.write_text(xml, encoding="utf-8")
    report = map_lowend_candidates(path)
    assert report["candidates"]["REGION_A"]["kind"] == "drums_plus_rose_bass"
    assert report["candidates"]["REGION_B"]["kind"] == "drums_plus_sub_sub_bass"
    assert report["candidates"]["REGION_C"]["kind"] == "drums_bass_absent"
