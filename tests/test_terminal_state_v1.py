from pathlib import Path

from copilot.agent.journal import DurableJournal
from copilot.audio.terminal_state_v1 import verify_terminal_state
from copilot.schemas.transaction import TransactionStatus


def test_terminal_ok_when_idle(tmp_path: Path) -> None:
    out = verify_terminal_state(
        transport_playing=False,
        taps=[{"rec": 0.0, "track_name": "MASTER"}],
        host_infos={"Copilot Capture": {"output_routing_type": "Sends Only", "sends": []}},
        capture_journal_dir=tmp_path / "c",
        bootstrap_journal_dir=tmp_path / "b",
        agent_journal_roots=[tmp_path / "j"],
    )
    assert out["ok"] is True
    assert out["transport_stopped"] is True
    assert out["taps_idle"] is True


def test_terminal_reports_in_doubt(tmp_path: Path) -> None:
    journal = DurableJournal(tmp_path / "j" / "agent.jsonl")
    journal.append(
        {
            "transaction_id": "txn_1",
            "status": TransactionStatus.IN_DOUBT.value,
            "kind": "write",
        }
    )
    out = verify_terminal_state(
        transport_playing=False,
        agent_journal_roots=[tmp_path / "j"],
        capture_journal_dir=tmp_path / "missing_c",
        bootstrap_journal_dir=tmp_path / "missing_b",
    )
    assert out["ok"] is False
    assert "unresolved_IN_DOUBT" in out["failures"]
