from __future__ import annotations

from pathlib import Path

from copilot.studio.service import StudioService


def test_simulation_provider_creates_playable_provenance_and_persists_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_STUDIO_MODE", "SIMULATION")
    service = StudioService(tmp_path)
    project = service.create_project("Rhythm Ashanti")
    job, created = service.submit_generation(project.project_id, {"prompt": "Funky club house", "candidate_count": 4}, idempotency_key="sim")
    assert created is True
    service._threads[job.job_id].join(timeout=10)
    result = service.get_job(job.job_id)
    assert result["job"]["status"] == "SUCCEEDED"
    assert len(result["candidates"]) == 4
    artifact = service.store.get_artifact(result["candidates"][0]["artifact_id"])
    assert artifact is not None
    assert artifact.provider_metadata["provenance_mode"] == "SIMULATED"
    assert artifact.relative_path.endswith(".wav")
    assert (tmp_path / artifact.relative_path).is_file()

    service.workspace_action(project.project_id, "reference.add", {"name": "Demo reference", "purposes": ["groove", "arrangement"]})
    workspace = service.workspace_snapshot(project.project_id)["workspace"]
    reference_id = workspace["references"][0]["reference_id"]
    service.workspace_action(project.project_id, "reference.analyze", {"reference_id": reference_id})
    service.workspace_action(project.project_id, "chat.send", {"message": "Make the second drop stronger."})
    service.workspace_action(project.project_id, "stems.generate", {"roles": ["drums", "bass", "harmonic"]})
    service.workspace_action(project.project_id, "ableton.apply", {"version_id": "simulated"})
    after = service.workspace_snapshot(project.project_id)
    assert after["workspace"]["references"][0]["analysis_source"] == "SIMULATED"
    assert after["workspace"]["chat"][-1]["origin"] == "SIMULATED_PRODUCER"
    assert after["workspace"]["ableton"][-1]["real_ableton_writes"] == 0
    assert after["activity"]


def test_simulation_job_cancel_is_terminal_for_worker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_STUDIO_MODE", "SIMULATION")
    monkeypatch.setenv("STUDIO_SIMULATION_FAST", "0")
    service = StudioService(tmp_path)
    project = service.create_project("Cancellation test")
    job, _ = service.submit_generation(project.project_id, {"prompt": "Slow demo", "candidate_count": 4})
    cancelled = service.cancel_job(job.job_id)
    assert cancelled["job"]["status"] == "CANCELLED"
    worker = service._threads.get(job.job_id)
    if worker is not None:
        worker.join(timeout=10)
    assert service.get_job(job.job_id)["job"]["status"] == "CANCELLED"
