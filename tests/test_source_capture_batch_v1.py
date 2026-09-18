"""SOURCE_CAPTURE_BATCH_V1 semantics.

Batching changes sequencing only. These tests pin the safety contract that must
survive it: bounded group width, per-source failure isolation, fail-closed on a
restore failure, no slot or staging collisions, and cancellation cleanup for
every prepared host.
"""

from __future__ import annotations

import pytest

from copilot.audio import source_capture_batch_v1 as batch
from copilot.audio.capture_journal_recovery import TERMINAL_STATUSES
from copilot.audio.tap_trust import FAILED
from copilot.schemas.session import SessionState, TrackState


def _Track(index: int, name: str) -> TrackState:
    return TrackState(stable_id=f"trk_{index}", index=index, name=name, role="audio")


def _Session(names: list[str]) -> SessionState:
    """Real schema objects: identity/token code reads more than name+index."""
    return SessionState(
        tracks=[_Track(i, n) for i, n in enumerate(names)],
        project_path="C:/p/x.als",
        project_name="x",
        project_identity="pid",
    )


BOTH_HOSTS = ["Kick", "Bass", "Pad", "Copilot Capture", "Copilot Capture Bass"]


def test_device_slot_ceiling_is_two_sources() -> None:
    """Slot 0 is Main; the device selector has three outlets."""
    assert batch.MAX_SOURCES_PER_PASS == 2
    slots = sorted(int(h["slot"]) for h in batch.BATCH_HOSTS)
    assert slots == [1, 2], "slot 0 is reserved for the Main sidecar"
    staging = {str(h["staging"]) for h in batch.BATCH_HOSTS}
    assert len(staging) == len(batch.BATCH_HOSTS), "each host needs its own staging file"


def test_available_hosts_reports_only_present_tracks() -> None:
    assert [h["name"] for h in batch.available_hosts(_Session(BOTH_HOSTS))] == [
        "Copilot Capture",
        "Copilot Capture Bass",
    ]
    one = _Session(["Kick", "Copilot Capture Bass"])
    assert [h["name"] for h in batch.available_hosts(one)] == ["Copilot Capture Bass"]
    assert batch.available_hosts(_Session(["Kick"])) == []


def test_plan_batches_groups_by_available_hosts() -> None:
    session = _Session(BOTH_HOSTS)
    assert batch.plan_batches([1, 2, 3, 4], session) == [[1, 2], [3, 4]]
    assert batch.plan_batches([1, 2, 3], session) == [[1, 2], [3]]
    assert batch.plan_batches([1], session) == [[1]]
    assert batch.plan_batches([], session) == []


def test_single_host_project_degrades_to_one_source_per_pass() -> None:
    """A project with one capture host must not batch."""
    one = _Session(["Kick", "Copilot Capture Bass"])
    assert batch.plan_batches([1, 2, 3], one) == [[1], [2], [3]]


def test_no_host_project_still_plans_singletons() -> None:
    """plan_batches must never return an empty group or a zero width."""
    none = _Session(["Kick", "Bass"])
    assert batch.plan_batches([1, 2], none) == [[1], [2]]


def test_no_capture_host_fails_every_row_not_just_one() -> None:
    session = _Session(["Kick", "Bass"])
    rows = batch.capture_sources_post_mixer_batch(
        None,
        session=session,
        preflight={},
        tracks=[_Track(0, "Kick"), _Track(1, "Bass")],
        start_qn=0.0,
        end_qn=32.0,
        region_id="R",
        tempo=120.0,
        dest_root=None,
    )
    assert len(rows) == 2
    assert all(row["ok"] is False for row in rows)
    assert all(row["error"] == "no_capture_host" for row in rows)


def test_oversized_batch_is_a_programming_error() -> None:
    """Callers must size groups with plan_batches, not guess."""
    session = _Session(BOTH_HOSTS)
    with pytest.raises(ValueError, match="exceeds"):
        batch.capture_sources_post_mixer_batch(
            None,
            session=session,
            preflight={},
            tracks=[_Track(0, "Kick"), _Track(1, "Bass"), _Track(2, "Pad")],
            start_qn=0.0,
            end_qn=32.0,
            region_id="R",
            tempo=120.0,
            dest_root=None,
        )


class _Journal:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []
        self.path = "memory"

    def record(self, status, **fields):
        self.records.append((status, fields))


class _Daw:
    def __init__(self) -> None:
        self.stopped = 0

    def stop_playback(self):
        self.stopped += 1
        return {"ok": True}


def test_restore_all_touches_every_host_even_after_a_failure(monkeypatch) -> None:
    seen: list[int] = []

    def _restore(_daw, index, _before):
        seen.append(index)
        return {"ok": index != 9, "errors": [] if index != 9 else ["nope"]}

    monkeypatch.setattr(batch, "_restore_host_full", _restore)
    prepared = [
        {"host": {"index": 9, "name": "A"}, "before": {}},
        {"host": {"index": 10, "name": "B"}, "before": {}},
    ]
    out = batch._restore_all(None, prepared)
    assert seen == [9, 10], "a failing restore must not skip the remaining hosts"
    assert out["ok"] is False
    assert out["hosts"]["A"]["ok"] is False
    assert out["hosts"]["B"]["ok"] is True


def test_restore_all_survives_a_raising_restore(monkeypatch) -> None:
    def _boom(_daw, index, _before):
        if index == 1:
            raise RuntimeError("socket gone")
        return {"ok": True}

    monkeypatch.setattr(batch, "_restore_host_full", _boom)
    out = batch._restore_all(
        None,
        [
            {"host": {"index": 1, "name": "A"}, "before": {}},
            {"host": {"index": 2, "name": "B"}, "before": {}},
        ],
    )
    assert out["ok"] is False
    assert "restore raised" in out["hosts"]["A"]["errors"][0]
    assert out["hosts"]["B"]["ok"] is True


def test_cancel_cleanup_stops_transport_and_settles_every_journal(monkeypatch) -> None:
    daw = _Daw()
    restored: list[int] = []
    monkeypatch.setattr(
        batch,
        "_restore_host_full",
        lambda d, i, b: restored.append(i) or {"ok": True},
    )
    journals = [_Journal(), _Journal()]
    prepared = [
        {"host": {"index": 5, "name": "A"}, "before": {}, "journal": journals[0]},
        {"host": {"index": 6, "name": "B"}, "before": {}, "journal": journals[1]},
    ]
    batch._cancel_cleanup_batch(daw, prepared)
    assert daw.stopped == 1, "transport stops exactly once for the shared pass"
    assert restored == [5, 6], "every prepared host is restored"
    for journal in journals:
        status, fields = journal.records[0]
        assert status in TERMINAL_STATUSES
        assert fields["error"] == "CANCELLED_BY_USER"


def test_cancel_cleanup_never_raises(monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(batch, "_restore_host_full", _boom)

    class _BadJournal(_Journal):
        def record(self, status, **fields):
            raise OSError("unwritable")

    class _BadDaw:
        def stop_playback(self):
            raise RuntimeError("dead socket")

    batch._cancel_cleanup_batch(
        _BadDaw(),
        [{"host": {"index": 1, "name": "A"}, "before": {}, "journal": _BadJournal()}],
    )  # must not raise


def test_cancel_status_uses_an_existing_terminal_status() -> None:
    """No new journal status: recovery semantics stay as verified."""
    assert FAILED in TERMINAL_STATUSES


def test_accepted_claims_exclude_anything_through_main() -> None:
    """The isolation contract must not be widened by batching."""
    assert batch.ACCEPTED_CLAIMS == frozenset({"OFF_MIX_GRAPH", "OFF_DIRECT_MAIN"})
    assert "THROUGH_MASTER_CHAIN" not in batch.ACCEPTED_CLAIMS
    assert "MAIN_FINAL" not in batch.ACCEPTED_CLAIMS


def test_producer_analyze_uses_batching() -> None:
    import inspect

    from copilot.audio import producer_analyze_v1 as pa

    assert "capture_bounded_sources(" in inspect.getsource(pa.producer_analyze)
    body = inspect.getsource(pa.capture_bounded_sources)
    assert "plan_batches(" in body
    assert "capture_sources_post_mixer_batch_refs(" in body
    # the single-source path must remain for odd counts and one-host projects
    assert "capture_source_post_mixer_ref(" in body
    assert "if len(group) == 1:" in body


class _Asset:
    def __init__(self, path: str) -> None:
        self.file_path = path
        self.analysis_file_path = path


def _prepared_batch(
    monkeypatch, tmp_path, *, assets, restore_ok=True, claim=None
):
    """Drive a batch with Live replaced, so only our orchestration is tested."""
    wavs = {}
    for key in ("master", "kick", "bass"):
        p = tmp_path / f"{key}.wav"
        p.write_bytes(b"RIFF")
        wavs[key] = _Asset(str(p))

    monkeypatch.setattr(batch, "inventory_taps", lambda daw: [])
    monkeypatch.setattr(batch, "assert_unique_slots", lambda inv: None)
    monkeypatch.setattr(batch, "find_tap", lambda daw, idx: {"index": 1})
    monkeypatch.setattr(batch, "set_tap_enabled", lambda *a, **k: None)
    monkeypatch.setattr(batch, "set_tap_recording", lambda *a, **k: None)
    monkeypatch.setattr(
        batch, "route_host_post_mixer", lambda d, i, n: {"input_channel": "Post Mixer"}
    )
    monkeypatch.setattr(batch, "_silence_sends", lambda d, i: [])
    monkeypatch.setattr(batch, "_snapshot_host", lambda d, i, **k: {"input": {}, "output": {}})
    monkeypatch.setattr(
        batch,
        "_snapshot_hosts",
        lambda d, indices, **k: {int(i): {"input": {}, "output": {}} for i in indices},
    )
    from copilot.audio.capture_host_baseline_v1 import parked_restore_baseline

    monkeypatch.setattr(
        batch,
        "_require_parked_baselines",
        lambda d, hosts: {int(h["index"]): parked_restore_baseline() for h in hosts},
    )
    verdict = claim or {"claim": "OFF_MIX_GRAPH", "through_main": False}
    monkeypatch.setattr(batch, "_verify_off_mix_graph", lambda d, i, n: verdict)
    monkeypatch.setattr(
        batch,
        "_restore_host_full",
        lambda d, i, b: {"ok": restore_ok, "errors": [] if restore_ok else ["stuck"]},
    )
    monkeypatch.setattr(
        batch,
        "capture_parallel_pass",
        lambda daw, **kw: {
            "assets": {k: wavs[k] for k in assets},
            "timings": {"total_s": 1.0, "region_s": 15.2},
        },
    )
    from copilot.audio import arrangement_active_source_isolation as aasi

    monkeypatch.setattr(
        aasi,
        "_wav_stats",
        lambda p: {
            "audio_sha256": "deadbeef",
            "signal_class": "HAS_SIGNAL",
            "rms": 0.1,
            "peak": 0.5,
            "sample_rate": 44100,
            "duration_s": 15.2,
        },
    )
    return batch.capture_sources_post_mixer_batch(
        object(),
        session=_Session(BOTH_HOSTS),
        preflight={"revision": 1},
        tracks=[_Track(0, "Kick"), _Track(1, "Bass")],
        start_qn=0.0,
        end_qn=32.0,
        region_id="R",
        tempo=126.0,
        dest_root=tmp_path / "out",
    )


def test_shared_pass_verifies_every_source(monkeypatch, tmp_path) -> None:
    rows = _prepared_batch(
        monkeypatch, tmp_path, assets=("master", "kick", "bass")
    )
    assert len(rows) == 2
    assert all(row["ok"] for row in rows)
    # one shared pass, one shared batch id, distinct hosts and slots
    assert len({row["batch_pass_id"] for row in rows}) == 1
    assert {row["host_slot"] for row in rows} == {1, 2}
    assert len({row["wav_path"] for row in rows}) == 2
    # every row carries the same Main sidecar: it was recorded once
    assert len({row["main_wav_path"] for row in rows}) == 2  # per-source named copy
    assert all(row["routing_claim"] == "OFF_MIX_GRAPH" for row in rows)
    assert all(row["through_main"] is False for row in rows)


def test_one_missing_recorder_does_not_fake_success_for_the_other(
    monkeypatch, tmp_path
) -> None:
    """Per-source failure isolation: the bass asset never arrives."""
    rows = _prepared_batch(monkeypatch, tmp_path, assets=("master", "kick"))
    by_name = {row["display_name"]: row for row in rows}
    assert by_name["Kick"]["ok"] is True
    assert by_name["Bass"]["ok"] is False
    assert by_name["Bass"]["error"] == "CAPTURE_MISSING_ASSET"
    assert by_name["Bass"]["signal_status"] == "CAPTURE_FAILED"


def test_restore_failure_fails_the_whole_batch_closed(monkeypatch, tmp_path) -> None:
    """A host left mis-routed means the session is not known-good."""
    rows = _prepared_batch(
        monkeypatch, tmp_path, assets=("master", "kick", "bass"), restore_ok=False
    )
    assert len(rows) == 2
    assert all(row["ok"] is False for row in rows)
    assert all(row["error"] == "ROUTING_RESTORE_FAILED" for row in rows)
    assert all(row["restore_all"]["ok"] is False for row in rows)


def test_missing_main_sidecar_fails_the_batch_closed(monkeypatch, tmp_path) -> None:
    rows = _prepared_batch(monkeypatch, tmp_path, assets=("kick", "bass"))
    assert all(row["ok"] is False for row in rows)
    assert all(row["batch_failed_closed"] for row in rows)
    assert all("CAPTURE_MISSING_ASSET" in row["error"] for row in rows)


def test_through_main_is_a_hard_failure(monkeypatch, tmp_path) -> None:
    """No source may leak into Main, batched or not."""
    rows = _prepared_batch(
        monkeypatch,
        tmp_path,
        assets=("master", "kick", "bass"),
        claim={"claim": "OFF_MIX_GRAPH", "through_main": True},
    )
    assert all(row["ok"] is False for row in rows)
    assert all("AUDIBLE_MIX_RISK" in row["error"] for row in rows)
    assert all(row["batch_failed_closed"] for row in rows)


def test_unsupported_claim_is_a_hard_failure(monkeypatch, tmp_path) -> None:
    rows = _prepared_batch(
        monkeypatch,
        tmp_path,
        assets=("master", "kick", "bass"),
        claim={"claim": "THROUGH_MASTER_CHAIN", "through_main": False},
    )
    assert all(row["ok"] is False for row in rows)
    assert all("CAPTURE_ROUTING_UNSUPPORTED" in row["error"] for row in rows)


def test_batch_performs_no_musical_writes(monkeypatch, tmp_path) -> None:
    """Only infra hosts are touched: routing, sends, monitoring, tap Rec."""
    import inspect

    body = inspect.getsource(batch.capture_sources_post_mixer_batch)
    for forbidden in (
        "set_mixer_volume",
        "replace_clip_notes",
        "create_midi_clip",
        "delete_clip",
        "set_track_mute",
        "set_track_solo",
        "set_tempo",
        "create_midi_track",
    ):
        assert forbidden not in body, f"{forbidden} is a musical write"
    rows = _prepared_batch(monkeypatch, tmp_path, assets=("master", "kick", "bass"))
    assert all(row["ok"] for row in rows)
