from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import soundfile as sf

from copilot.human_eval.blind import assert_blind, public_case
from copilot.human_eval.gate import AstraPrelockBlocked, assert_astra_allowed_for_pack, assert_run_locked
from copilot.human_eval.queue import create_run_from_session, export_run
from copilot.human_eval.schema import CaseStatus, EvalMode, HumanResponse, OverallFeel, Ternary
from copilot.human_eval.store import EvalStore, label_hash
from copilot.reasoning.eval import scripted_provider
from copilot.reasoning.fixtures import pack_clear_no_action
from copilot.reasoning.pipeline import reason


def _wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.zeros(4410, dtype=np.float32), 44100)


def _session_report(tmp: Path) -> Path:
    a = tmp / "listen_REGION_A_main.wav"
    b = tmp / "listen_REGION_B_main.wav"
    c = tmp / "listen_REGION_C_main.wav"
    _wav(a)
    _wav(b)
    _wav(c)
    report = {
        "PRE-FLIGHT": {"live_set_path": str(tmp / "pista_copilot_eval.als")},
        "STATE TOKENS": {"project_token": "token-human-eval-test"},
        "HUMAN LISTEN MAIN": [
            {"region": "REGION_A", "path": str(a), "start_qn": 256, "end_qn": 288},
            {"region": "REGION_B", "path": str(b), "start_qn": 96, "end_qn": 128},
            {"region": "REGION_C", "path": str(c), "start_qn": 32, "end_qn": 64},
        ],
        "CAPTURE ASSETS": [
            {"region": {"id": "REGION_A"}, "source_class": {"drums": "HAS_SIGNAL"}},
            {"region": {"id": "REGION_B"}, "source_class": {"drums": "HAS_SIGNAL"}},
            {"region": {"id": "REGION_C"}, "source_class": {"sub_sub_bass": "SOURCE_INACTIVE"}},
        ],
    }
    evidence = tmp / "logs"
    evidence.mkdir()
    path = evidence / "session_run1.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return evidence


def test_queue_creates_three_shuffled_cases(tmp_path) -> None:
    evidence = _session_report(tmp_path)
    store = EvalStore(tmp_path / "eval")
    run = create_run_from_session("session_run1", evidence=evidence, store=store, seed=7)
    cases = store.load_cases(run.evaluation_run_id)
    assert len(cases) == 3
    assert {case.region for case in cases} == {"REGION_A", "REGION_B", "REGION_C"}
    assert [case.presentation_order for case in cases] == [0, 1, 2]
    orders = [case.region for case in cases]
    again = create_run_from_session("session_run1", evidence=evidence, store=store, seed=7)
    assert [c.region for c in store.load_cases(again.evaluation_run_id)] == orders


def test_public_case_is_blind(tmp_path) -> None:
    evidence = _session_report(tmp_path)
    store = EvalStore(tmp_path / "eval")
    run = create_run_from_session("session_run1", evidence=evidence, store=store, seed=1)
    case = store.load_cases(run.evaluation_run_id)[0]
    payload = public_case(case, index=1, total=3, remaining=3)
    assert_blind(payload)
    assert "region" not in payload
    assert "dsp" not in payload
    assert "source_activity" not in payload
    assert payload["audio_url"].endswith("/audio")


def test_autosave_resume_skip_lock_export(tmp_path) -> None:
    evidence = _session_report(tmp_path)
    store = EvalStore(tmp_path / "eval")
    run = create_run_from_session("session_run1", evidence=evidence, store=store, seed=3)
    case = store.first_incomplete(run.evaluation_run_id)
    assert case is not None
    case.status = CaseStatus.IN_PROGRESS
    case.draft = HumanResponse(overall_feel=OverallFeel.GOOD, would_change=Ternary.NO, notes="gordo pero ok")
    store.save_case(case)
    resumed = store.first_incomplete(run.evaluation_run_id)
    assert resumed is not None
    assert resumed.draft is not None
    assert resumed.draft.notes == "gordo pero ok"
    case.response = case.draft
    case.status = CaseStatus.COMPLETED
    digest = label_hash(response=case.response, audio_sha256=case.audio_sha256)
    case.human_label_hash = digest
    case.status = CaseStatus.LOCKED
    store.save_case(case)
    skipped = [item for item in store.load_cases(run.evaluation_run_id) if item.case_id != case.case_id][0]
    skipped.status = CaseStatus.SKIPPED
    store.save_case(skipped)
    counts = store.counts(run.evaluation_run_id)
    assert counts["labeled"] == 1
    assert counts["skipped"] == 1
    rows = export_run(run.evaluation_run_id, store=store)
    assert len(rows) == 3
    labeled = next(row for row in rows if row["status"] == "LOCKED")
    assert labeled["response"]["notes"] == "gordo pero ok"
    assert labeled["human_label_hash"] == digest
    assert labeled["audio_sha256"] == case.audio_sha256


def test_playback_duplicates_silent_right(tmp_path) -> None:
    from io import BytesIO

    from copilot.human_eval.server import playback_wav_bytes

    path = tmp_path / "left_only.wav"
    stereo = np.zeros((2205, 2), dtype=np.float32)
    stereo[:, 0] = 0.2
    sf.write(path, stereo, 44100)
    folded, _sr = sf.read(BytesIO(playback_wav_bytes(path)), always_2d=True)
    assert folded.shape[1] == 2
    assert float(np.abs(folded[:, 1]).max()) > 0.1
    assert np.allclose(folded[:, 0], folded[:, 1])


def test_http_next_payload_is_blind(tmp_path) -> None:
    import urllib.request

    from copilot.human_eval import server as srv

    evidence = _session_report(tmp_path)
    store = EvalStore(tmp_path / "eval")
    run = create_run_from_session("session_run1", evidence=evidence, store=store, seed=1)
    srv.STATE.store = store
    srv.STATE.active_run_id = run.evaluation_run_id
    httpd = srv.serve("127.0.0.1", 0, run_id=run.evaluation_run_id, blocking=False)
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/next") as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert_blind(payload)
        assert "region" not in payload
        assert payload["audio_url"].endswith("/audio")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{payload['audio_url']}") as audio:
            assert audio.headers.get_content_type() == "audio/wav"
            assert len(audio.read()) > 44
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_astra_blocked_until_locked(tmp_path, monkeypatch) -> None:
    evidence = _session_report(tmp_path)
    store = EvalStore(tmp_path / "eval")
    run = create_run_from_session("session_run1", evidence=evidence, store=store, seed=2)
    monkeypatch.setattr("copilot.human_eval.gate.EvalStore", lambda: store)
    pack = pack_clear_no_action()
    pack.project_token = "token-human-eval-test"
    try:
        assert_astra_allowed_for_pack(pack, store=store)
        raised = False
    except AstraPrelockBlocked:
        raised = True
    assert raised
    for case in store.load_cases(run.evaluation_run_id):
        case.response = HumanResponse(overall_feel=OverallFeel.UNSURE, would_change=Ternary.UNSURE)
        case.status = CaseStatus.LOCKED
        case.human_label_hash = label_hash(response=case.response, audio_sha256=case.audio_sha256)
        store.save_case(case)
    assert_run_locked(run.evaluation_run_id, store=store)
    result = reason(pack, scripted_provider())
    assert result.musical_writes == 0


def test_reason_fixtures_not_blocked_by_missing_eval() -> None:
    result = reason(pack_clear_no_action(), scripted_provider())
    assert result.accepted
    assert result.musical_writes == 0
