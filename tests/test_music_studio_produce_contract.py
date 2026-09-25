from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import copilot.studio.service as service_module
from copilot.daw.mock import MockAbletonAdapter
from copilot.studio.contracts import VariationPreview, VariationRecord
from copilot.studio.server import StudioHandler
from copilot.studio.service import ProduceExecutionBlocked, StudioService


def _service(tmp_path: Path) -> tuple[StudioService, str]:
    service = StudioService(tmp_path / "studio")
    project = service.create_project("Rhythm Ashanti")
    return service, project.project_id


def _fake_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x00" * 44100)


def test_capability_report_certifies_only_the_one_variation_slice(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    report = service.produce_capabilities()
    assert report["generation_available"] is True
    assert report["scope"] == "ONE_BASS_VARIATION_ONLY"
    assert "VARIATION_PLANNER_N" in report["missing"]
    real = {row["name"] for row in report["capabilities"] if row["state"] == "REAL"}
    assert {"SINGLE_VARIATION_PLAN", "WRITE_MIDI_CLIP", "VARIATION_PREVIEW_CAPTURE", "KEEP_VARIATION"} <= real


def test_generate_blocks_n_variations_before_live_write(tmp_path: Path) -> None:
    service, project_id = _service(tmp_path)
    with pytest.raises(ValueError, match="PRODUCE_VARIATION_COUNT_UNSUPPORTED_FOR_V1"):
        service.produce_generate(project_id, {"scope": "region", "instruction": "make a bass variation", "variations": 5, "length_bars": 16})


@pytest.mark.parametrize("payload, code", [
    ({"instruction": "", "variations": 1}, "PRODUCE_INSTRUCTION_REQUIRED"),
    ({"instruction": "x", "variations": 4}, "PRODUCE_VARIATION_COUNT_INVALID"),
    ({"instruction": "x", "variations": 1, "length_bars": 12}, "PRODUCE_LENGTH_INVALID"),
    ({"instruction": "x", "variations": 1, "scope": "song"}, "PRODUCE_SCOPE_INVALID"),
])
def test_generate_validates_request_before_live_write(tmp_path: Path, payload: dict, code: str) -> None:
    service, project_id = _service(tmp_path)
    with pytest.raises(ValueError, match=code):
        service.produce_generate(project_id, payload)


def test_real_bridge_compiles_writes_captures_and_rolls_back_owned_material(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_id = _service(tmp_path)
    daw = MockAbletonAdapter()
    daw.session_path = str(tmp_path / "Rhythm Ashanti Working Copy.als")
    daw.session_name = "Rhythm Ashanti Working Copy"
    daw.connect()
    monkeypatch.setattr(service, "_open_variation_live", lambda: (daw, daw.snapshot()))

    def fake_preflight(*args, **kwargs):
        return {"pass": True, "revision": daw.snapshot().revision, "capture_hosts": {}}

    captured = tmp_path / "captured.wav"

    def fake_capture(*args, **kwargs):
        _fake_wav(captured)
        return {"ok": True, "wav_path": str(captured), "duration_s": 1.0, "sample_rate": 44100, "audio_sha256": "fixture"}

    monkeypatch.setattr(service_module, "preflight_session", fake_preflight)
    monkeypatch.setattr(service_module, "capture_source_post_mixer_ref", fake_capture)

    output = service.produce_generate(project_id, {"scope": "region", "instruction": "Make a bass variation inspired by this section", "variations": 1, "length_bars": 8})
    variation = output["variation"]
    assert output["write_authority"] == "ProductionCompiler->SafeWriteExecutor"
    assert output["musical_writes"] == 3
    assert variation["status"] == "READY"
    assert variation["ownership"]["owner"] == "COPILOT"
    assert variation["preview_url"].startswith("/api/artifacts/")
    generated = daw.snapshot().track_by_name(variation["ownership"]["track_name"])
    assert generated is not None
    assert generated.clips[0].notes
    assert len(daw.snapshot().tracks) == 1

    kept = service.variation_action(variation["variation_id"], "keep")
    assert kept["status"] == "KEPT"
    discarded = service.variation_action(variation["variation_id"], "discard")
    assert discarded["rollback_verified"] is True
    assert daw.snapshot().tracks == []


def test_disconnected_live_fails_truthfully(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_id = _service(tmp_path)
    monkeypatch.setattr(service, "_open_variation_live", lambda: (_ for _ in ()).throw(ProduceExecutionBlocked("ABLETON_SESSION_NOT_READY")))
    with pytest.raises(ProduceExecutionBlocked) as blocked:
        service.produce_generate(project_id, {"instruction": "make a bass variation", "variations": 1, "length_bars": 8})
    assert blocked.value.reason == "ABLETON_SESSION_NOT_READY"
    assert service.list_variations(project_id) == {"variations": []}


def test_capture_failure_rolls_back_the_new_owned_track(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_id = _service(tmp_path)
    daw = MockAbletonAdapter()
    daw.session_path = str(tmp_path / "Working Copy.als")
    daw.session_name = "Working Copy"
    daw.connect()
    monkeypatch.setattr(service, "_open_variation_live", lambda: (daw, daw.snapshot()))
    monkeypatch.setattr(
        service_module,
        "preflight_session",
        lambda *args, **kwargs: {"pass": True, "revision": daw.snapshot().revision, "capture_hosts": {}},
    )
    monkeypatch.setattr(
        service_module,
        "capture_source_post_mixer_ref",
        lambda *args, **kwargs: {"ok": False, "signal_status": "CAPTURE_FAILED", "error": "fixture capture failure"},
    )
    with pytest.raises(ProduceExecutionBlocked, match="PREVIEW_CAPTURE_FAILED"):
        service.produce_generate(project_id, {"scope": "region", "instruction": "make a bass variation", "variations": 1, "length_bars": 8})
    daw.connect()
    assert [track for track in daw.snapshot().tracks if track.name.startswith("Copilot Variation ")] == []
    assert service.list_variations(project_id) == {"variations": []}


def test_variation_preview_contract_uses_existing_artifact_route(tmp_path: Path) -> None:
    service, project_id = _service(tmp_path)
    record = VariationRecord(
        variation_id="variation_1", project_id=project_id, request_id="req_1", index=1, status="READY",
        ableton_track_ref="track:copilot-var-1", ableton_clip_ref="clip:copilot-var-1:0",
        preview=VariationPreview(artifact_id="artifact_abc", bars=16, duration_s=30.0), created_at="2026-09-24T00:00:00Z",
    )
    service.store.set_state(project_id, "variations", [record.model_dump(mode="json")])
    listed = service.list_variations(project_id)["variations"]
    assert listed[0]["preview_url"] == "/api/artifacts/artifact_abc/audio"
    assert service.variation_action("variation_1", "open")["variation"]["variation_id"] == "variation_1"


def test_http_produce_routes_expose_real_capability_and_typed_blocker(tmp_path: Path) -> None:
    service, project_id = _service(tmp_path)
    service._open_variation_live = lambda: (_ for _ in ()).throw(
        ProduceExecutionBlocked("ABLETON_SESSION_NOT_READY")
    )
    handler = type("TestHandler", (StudioHandler,), {"service": service, "static_root": Path(__file__).parents[1] / "src" / "copilot" / "studio" / "static", "log_message": lambda *a: None})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/api/produce/capabilities") as response:
            body = json.load(response)
            assert body["generation_available"] is True
            assert body["scope"] == "ONE_BASS_VARIATION_ONLY"
        request = urllib.request.Request(f"{base}/api/projects/{project_id}/produce", method="POST",
                                         data=json.dumps({"instruction": "make a bass variation", "variations": 1}).encode(),
                                         headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code in {400, 409}
        assert json.load(error.value)["error"] in {"PRODUCE_EXECUTION_BLOCKED", "ValueError"}
    finally:
        server.shutdown()
        server.server_close()
