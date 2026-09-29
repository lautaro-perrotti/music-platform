import json

from copilot.cli import main


def test_missing_duration_blocks_before_any_live_or_copy_access(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    def unexpected(*args, **kwargs):
        raise AssertionError("must not open Live or create a working copy")
    monkeypatch.setattr("copilot.importing.new_project_v1.prepare_new_project", unexpected)

    code = main([
        "produce-tech-house",
        "--template", "empty.als",
        "--sample-index", "index.json",
        "--sample-root", "library",
        "--workspace", "output",
        "BPM: 127",
    ])

    assert code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BLOCKED"
    assert report["MUSICAL_WRITES"] == 0
    assert "GOAL_REQUIRES_ONE_EXPLICIT_BPM_AND_DURATION" in report["reason"]
    assert len(list((tmp_path / "logs" / "producer").glob("*/report.json"))) == 1
