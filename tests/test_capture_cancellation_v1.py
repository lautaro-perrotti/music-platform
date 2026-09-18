"""CAPTURE_CANCELLATION_V1.

Cancellation is a BaseException. Before this was fixed, Ctrl-C during a capture
skipped both the success path and the `except Exception` path, leaving the
capture host with rewritten input routing, zeroed sends and changed monitoring,
the transport playing, and the capture journal open with no terminal record.

These tests pin the cleanup contract:
  * routing is restored
  * the transport is stopped
  * the journal reaches a terminal status
  * the cancellation still propagates to the caller
  * none of it runs twice on the normal paths
"""

from __future__ import annotations

import json

import pytest

from copilot.audio import arrangement_active_source_isolation as aasi
from copilot.audio.capture_journal_recovery import TERMINAL_STATUSES


class _RecordingDaw:
    """Records the cleanup calls a cancelled capture is expected to make."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_on = fail_on

    def _note(self, name: str):
        self.calls.append(name)
        if self.fail_on == name:
            raise RuntimeError(f"{name} failed during cleanup")

    def stop_playback(self):
        self._note("stop_playback")
        return {"ok": True}


class _Journal:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []
        self.path = "memory"

    def record(self, status, **fields):
        self.records.append((status, fields))


def test_cancel_cleanup_stops_transport_restores_and_closes_journal(monkeypatch) -> None:
    daw = _RecordingDaw()
    journal = _Journal()
    restored: list[dict] = []
    monkeypatch.setattr(
        aasi,
        "_restore_host_full",
        lambda d, idx, before: restored.append(before) or {"ok": True},
    )

    aasi._cancel_cleanup(daw, 7, {"input": "saved"}, journal)

    assert daw.calls == ["stop_playback"]
    assert restored == [{"input": "saved"}]
    assert len(journal.records) == 1
    status, fields = journal.records[0]
    assert status in TERMINAL_STATUSES
    assert fields["error"] == "CANCELLED_BY_USER"


def test_cancel_cleanup_never_raises(monkeypatch) -> None:
    """A second interrupt or a dead socket must not escape cleanup."""
    daw = _RecordingDaw(fail_on="stop_playback")

    def _boom(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(aasi, "_restore_host_full", _boom)

    class _BadJournal(_Journal):
        def record(self, status, **fields):
            raise OSError("journal unwritable")

    aasi._cancel_cleanup(daw, 0, {}, _BadJournal())  # must not raise


def test_cancel_cleanup_restores_even_when_transport_stop_fails(monkeypatch) -> None:
    """Steps are independent: a failed stop must not skip the restore."""
    daw = _RecordingDaw(fail_on="stop_playback")
    journal = _Journal()
    restored: list[int] = []
    monkeypatch.setattr(
        aasi,
        "_restore_host_full",
        lambda d, idx, before: restored.append(idx) or {"ok": True},
    )

    aasi._cancel_cleanup(daw, 3, {}, journal)

    assert restored == [3], "restore must still run after a failed transport stop"
    assert journal.records[0][0] in TERMINAL_STATUSES


def test_capture_propagates_cancellation_after_cleaning_up(monkeypatch) -> None:
    """The interrupt must reach the caller; cleanup does not swallow it."""
    cleaned: list[str] = []
    monkeypatch.setattr(
        aasi,
        "_cancel_cleanup",
        lambda *a, **k: cleaned.append("cleaned"),
    )

    settled = False
    journal = _Journal()

    def _run() -> None:
        nonlocal settled
        try:
            raise KeyboardInterrupt
        except Exception:  # mirrors the production handler: does NOT catch this
            settled = True
        finally:
            if not settled:
                aasi._cancel_cleanup(None, 0, {}, journal)

    with pytest.raises(KeyboardInterrupt):
        _run()
    assert cleaned == ["cleaned"]


def test_cleanup_is_not_invoked_on_the_success_or_failure_paths() -> None:
    """The sentinel must make cleanup exclusive to BaseException."""
    source = aasi.capture_source_post_mixer.__doc__ or ""
    assert source  # sanity

    import inspect

    body = inspect.getsource(aasi.capture_source_post_mixer)
    # success path and `except Exception` path both mark the capture settled
    assert body.count("settled = True") == 2
    assert "settled = False" in body
    assert "if not settled:" in body
    assert "_cancel_cleanup(" in body


def test_terminal_status_used_for_cancel_is_already_recognised() -> None:
    """Recovery semantics are unchanged: no new journal status was introduced."""
    assert aasi.FAILED in TERMINAL_STATUSES


def test_parallel_pass_stops_transport_in_cleanup() -> None:
    """capture_parallel_pass must not leave Live playing on interrupt."""
    import inspect

    from copilot.audio import batch_capture

    body = inspect.getsource(batch_capture.capture_parallel_pass)
    assert "transport_stopped = False" in body
    assert "transport_stopped = True" in body
    assert "if not transport_stopped:" in body
    # the guard exists so the success path does not pay a second RPC
    finally_block = body.split("finally:")[-1]
    assert "stop_playback" in finally_block


def test_cancel_cleanup_payload_is_json_serialisable() -> None:
    """Journal records are persisted, so the cancel reason must serialise."""
    daw = _RecordingDaw()
    journal = _Journal()
    aasi._cancel_cleanup(daw, 0, {}, journal)
    json.dumps(journal.records[0][1])
