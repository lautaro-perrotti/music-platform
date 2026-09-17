from copilot.audio.working_copy_policy_v1 import evaluate_working_copy
from copilot.audio.capability_matrix_v1 import capability_matrix
from copilot.audio.m4l_control_contract_v1 import FROZEN, control_contract
from copilot.cli import CANONICAL_COMMANDS, HELP_EPILOG, main


def test_working_copy_refuses_original() -> None:
    policy = evaluate_working_copy(r"C:\x\pista.als")
    assert policy["operate"] is False
    assert policy["refuse_original"] is True
    assert policy["autonomous_writes_ok"] is False
    assert policy["reason"] == "ORIGINAL_SET_OPEN"


def test_working_copy_development_allows_autonomous() -> None:
    policy = evaluate_working_copy(r"C:\x\pista_copilot_eval.als")
    assert policy["operate"] is True
    assert policy["autonomous_writes_ok"] is True
    assert policy["musical_holdout"] is False


def test_working_copy_fixture_is_plumbing_not_music() -> None:
    policy = evaluate_working_copy(
        r"C:\Users\lsper\Desktop\pista Project\copilot_bootstrap_fixture.als"
    )
    assert policy["operate"] is True
    assert policy["read_only_ok"] is True
    assert policy["autonomous_writes_ok"] is False
    assert policy["plumbing_only"] is True
    assert policy["musical_holdout"] is False


def test_working_copy_external_is_holdout_read_only() -> None:
    policy = evaluate_working_copy(r"C:\songs\new_song.als")
    assert policy["kind"] == "external"
    assert policy["operate"] is True
    assert policy["read_only_ok"] is True
    assert policy["autonomous_writes_ok"] is False
    assert policy["musical_holdout"] is True
    assert policy["OPERATOR_MUST_DUPLICATE"] is True


def test_capability_matrix_does_not_claim_musical_generalization() -> None:
    matrix = capability_matrix()
    assert "FROZEN=True" in matrix["LIVE_SESSION_READINESS_V1"]
    assert matrix["musical_generalization"] == "UNPROVEN_UNTIL_SECOND_REAL_SONG"
    assert matrix["NO MOCK SUCCESS"] is True
    ids = {row["id"] for row in matrix["rows"]}
    assert "LIVE_SESSION_READINESS_V1" in ids
    assert "PRODUCER_ANALYZE_V1" in ids
    assert "autonomous writes on an unvalidated external song" in matrix["unsupported"]


def test_m4l_contract_is_frozen_and_volume_only() -> None:
    contract = control_contract()
    assert contract["frozen"] is True
    assert FROZEN is True
    commands = {row["command"] for row in contract["commands"]}
    assert commands == {
        "ANALYZE",
        "STATUS",
        "DIAGNOSIS",
        "PROPOSED ACTION",
        "APPLY",
        "ROLLBACK",
    }
    assert contract["APPLY_VOCABULARY"] == ["SET_TRACK_VOLUME"]
    assert "IN_DOUBT" in contract["APPLY_RESULTS"]
    assert contract["NO FRONTEND"] is True


def test_canonical_cli_help_lists_envelope(capsys) -> None:
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "onboard-project" in out
    assert "SESSION_READY" in out
    assert "Canonical supported envelope" in HELP_EPILOG
    assert "onboard-project" in CANONICAL_COMMANDS
    assert "cross-project-validate" in CANONICAL_COMMANDS
    assert "import-project" in CANONICAL_COMMANDS
    assert "import-project" in HELP_EPILOG


def test_capabilities_command_offline(capsys) -> None:
    code = main(["capabilities"])
    assert code == 0
    blob = capsys.readouterr().out
    assert "LIVE_SESSION_READINESS_V1" in blob
    assert "m4l_control_contract" in blob
    assert "onboard-project" in blob
