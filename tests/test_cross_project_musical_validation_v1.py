from copilot.audio.cross_project_bootstrap_v1 import classify_project
from copilot.audio.cross_project_musical_validation_v1 import (
    NEXT_AUDIT_ACTION,
    NEXT_AUDIT_EVIDENCE,
    NEXT_FIX_BUG,
    NEXT_NEED_SONG,
    NEXT_VOLUME_LOOP,
    interpret_read_only,
    refuse_known_lab,
)
from copilot.audio.working_copy_policy_v1 import evaluate_working_copy


def test_refuses_fixture_pista_and_dev_copy() -> None:
    for path in (
        r"C:\x\pista.als",
        r"C:\x\pista_copilot_eval.als",
        r"C:\x\copilot_bootstrap_fixture.als",
        r"C:\Users\lsper\Desktop\pista Project\Sin título.als",
    ):
        identity = classify_project(path)
        policy = evaluate_working_copy(path)
        blocked = refuse_known_lab(identity, policy)
        assert blocked is not None
        assert blocked["status"] == NEXT_NEED_SONG
        assert blocked["NO WRITE"] is True
        assert blocked["MUSICAL WRITES"] == 0


def test_accepts_unseen_external_song() -> None:
    path = r"C:\songs\holdout_never_seen.als"
    identity = classify_project(path)
    policy = evaluate_working_copy(path)
    assert identity["kind"] == "external"
    assert policy["musical_holdout"] is True
    assert refuse_known_lab(identity, policy) is None


def test_interpret_plumbing_bug_before_features() -> None:
    out = interpret_read_only(plumbing_ok=False, analyze_status=None, plumbing_reason="zombie_port")
    assert out["next"] == NEXT_FIX_BUG
    assert out["NO WRITE"] is True


def test_interpret_insufficient_evidence_audits_perception() -> None:
    out = interpret_read_only(
        plumbing_ok=True,
        analyze_status="INSUFFICIENT_EVIDENCE",
        gate={"reason": "reason_not_accepted"},
    )
    assert out["next"] == NEXT_AUDIT_EVIDENCE
    assert "Perception" in out["develop"] or "perception" in out["develop"].lower()


def test_interpret_action_not_available_names_the_gap() -> None:
    out = interpret_read_only(
        plumbing_ok=True,
        analyze_status="ACTION_NOT_AVAILABLE",
        gate={"reason": "supported_requires_non_volume_tool", "required_tools": ["REDUCE_LOW_BAND_ENERGY"]},
    )
    assert out["next"] == NEXT_AUDIT_ACTION
    assert out["required_tools"] == ["REDUCE_LOW_BAND_ENERGY"]


def test_interpret_supported_volume_stays_read_only_on_first_pass() -> None:
    out = interpret_read_only(
        plumbing_ok=True,
        analyze_status="SUPPORTED",
        gate={"write_justified_if_autonomous": True},
    )
    assert out["next"] == NEXT_VOLUME_LOOP
    assert out["NO WRITE"] is True
    assert out.get("autonomous_candidate") is True
