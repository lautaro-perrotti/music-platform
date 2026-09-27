from __future__ import annotations

import json
import hashlib
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import copilot.studio.service as service_module
from copilot.daw.mock import MockAbletonAdapter
from copilot.studio.contracts import VariationPreview, VariationRecord
from copilot.studio.server import StudioHandler
from copilot.studio.service import ProduceExecutionBlocked, StudioService
from copilot.schemas.music_analysis import MusicAnalysisPack, ReferenceStateTokens
from copilot.schemas.musical_understanding import MusicalUnderstanding


def _service(tmp_path: Path) -> tuple[StudioService, str]:
    service = StudioService(tmp_path / "studio")
    project = service.create_project("Rhythm Ashanti")
    return service, project.project_id


def _reference_payload(tmp_path: Path, project_identity: str) -> dict:
    pack_path = tmp_path / "reference_pack.json"
    understanding_path = tmp_path / "understanding.json"
    pack = MusicAnalysisPack(
        tokens=ReferenceStateTokens(reference_state_token="reference:real", target_state_token="target:live"),
        reference_id="real-reference", project_id=project_identity, tempo_bpm=167,
        timeline={"start_qn": 160.0, "end_qn": 288.0},
    )
    pack_path.write_text(pack.model_dump_json(), encoding="utf-8")
    events = [
        {"event_id": f"midi:{index}",
         "grid": {"onset_s": onset * 60 / 167, "onset_qn": onset, "bar": onset // 4 + 1,
                  "beat_in_bar": onset % 4 + 1, "subdivision": "quarter", "nearest_grid_qn": onset,
                  "deviation_qn": 0, "deviation_ms": 0},
         "offset_s": (onset + 0.5) * 60 / 167,
         "midi_note": [33, 37, 40, 42, 33, 37, 40, 42][index],
         "onset_qn": onset, "offset_qn": onset + 0.5, "duration_qn": 0.5,
         "confidence": 1.0, "source_kind": "ABLETON_MIDI", "status": "RELIABLE"}
        for index, onset in enumerate([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    ]
    understanding = MusicalUnderstanding.model_validate({
        "reference_id": "real-reference", "source_analysis_id": "reference_pack",
        "stem_analysis_id": "stem-analysis", "tempo_bpm": 167, "timeline": {},
        "bass": {"status": "SUPPORTED", "source_kind": "ABLETON_MIDI", "pitch_events": events,
                 "source_diagnostics": {"expected_project_identity": project_identity,
                                        "actual_project_identity": project_identity,
                                        "reconciliation": {"ok": True}},
                 "rhythmic_structure": {"event_count": len(events), "density_per_bar": 1}},
        "drums": {"pulse_structure": {}, "rhythmic_structure": {"event_count": 0, "density_per_bar": 0}},
        "relationships": {}, "provenance": {"midi_pack_path": str(pack_path.resolve())},
    })
    understanding_path.write_text(understanding.model_dump_json(), encoding="utf-8")
    return {"reference_analysis_path": str(pack_path), "musical_understanding_path": str(understanding_path),
            "start_qn": 160.0}


def _fake_wav(path: Path, duration_s: float, *, silent: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.zeros(int(round(duration_s * 44100)), dtype=np.float32)
    if not silent:
        samples[:] = 0.1 * np.sin(2 * np.pi * 110 * np.arange(len(samples)) / 44100)
    sf.write(str(path), samples, 44100)


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
    ({"instruction": "x", "variations": 1, "length_bars": 16}, "PRODUCE_LENGTH_INVALID"),
    ({"instruction": "x", "variations": 1, "scope": "song"}, "PRODUCE_SCOPE_INVALID"),
])
def test_generate_validates_request_before_live_write(tmp_path: Path, payload: dict, code: str) -> None:
    service, project_id = _service(tmp_path)
    with pytest.raises(ValueError, match=code):
        service.produce_generate(project_id, payload)


def test_generate_uses_project_bound_reference_when_ui_omits_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_id = _service(tmp_path)
    reference = _reference_payload(tmp_path, "mock-project")
    service.store.set_state(project_id, "reference_variation_config", {
        **reference, "end_qn": 192.0,
    })
    monkeypatch.setattr(service, "_produce_one_real_variation", lambda _project, request: request.model_dump(mode="json"))
    request = service.produce_generate(project_id, {"scope": "region", "instruction": "syncopate bass", "variations": 1, "length_bars": 8})
    assert request["reference_analysis_path"] == reference["reference_analysis_path"]
    assert request["musical_understanding_path"] == reference["musical_understanding_path"]
    assert request["start_qn"] == 160.0 and request["end_qn"] == 192.0


def test_real_bridge_compiles_writes_captures_and_rolls_back_owned_material(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_id = _service(tmp_path)
    daw = MockAbletonAdapter()
    daw.session_path = str(tmp_path / "Rhythm Ashanti Working Copy.als")
    daw.session_name = "Rhythm Ashanti Working Copy"
    daw.connect()
    monkeypatch.setattr(service, "_open_variation_live", lambda: (daw, daw.snapshot()))

    def fake_preflight(*args, **kwargs):
        return {"pass": False, "revision": daw.snapshot().revision, "capture_hosts": {},
                "missing": ["TARGET_SOURCE_UNSUPPORTED: legacy kick routing",
                            "Copilot Capture Bass routing claim=FAILED"]}

    captured = tmp_path / "captured.wav"
    reference = _reference_payload(tmp_path, daw.snapshot().project_identity)

    def fake_capture(*args, **kwargs):
        assert kwargs["preflight"]["status"] == "GENERIC_TRACK_CAPTURE_READY"
        duration = 32 * 60.0 / daw.snapshot().transport.tempo
        _fake_wav(captured, duration)
        return {"ok": True, "wav_path": str(captured), "duration_s": duration, "sample_rate": 44100,
                "audio_sha256": hashlib.sha256(captured.read_bytes()).hexdigest(),
                "signal_status": "HAS_SIGNAL", "ref": kwargs["target_ref"].model_dump(mode="json")}

    monkeypatch.setattr(service_module, "preflight_session", fake_preflight)
    monkeypatch.setattr(service_module, "capture_source_post_mixer_ref", fake_capture)

    output = service.produce_generate(project_id, {"scope": "region", "instruction": "Make a bass variation inspired by this section", "variations": 1, "length_bars": 8, **reference})
    variation = output["variation"]
    assert output["write_authority"] == "ProductionCompiler->SafeWriteExecutor"
    assert output["musical_writes"] == 4
    assert variation["status"] == "READY"
    assert variation["ownership"]["owner"] == "COPILOT"
    assert variation["preview_url"].startswith("/api/artifacts/")
    assert variation["preview"]["signal_status"] == "HAS_SIGNAL"
    assert len(variation["ownership"]["arrangement_clip_ids"]) == 1
    arrangement = daw.get_arrangement_clips()["clips"]
    assert len(arrangement) == 1
    assert arrangement[0]["start_time"] == 160.0
    assert arrangement[0]["length"] == 32.0
    generated = daw.snapshot().track_by_name(variation["ownership"]["track_name"])
    assert generated is not None
    assert generated.clips[0].notes
    assert len(daw.snapshot().tracks) == 1
    assert variation["provenance"]["symbolic_validation"]["status"] == "VERIFIED"
    assert variation["provenance"]["source_not_copied"] is True
    trace = variation["provenance"]["event_traceability"]
    assert trace and any(item["changed"] for item in trace)
    assert not all(item["direct_copy"] for item in trace)

    kept = service.variation_action(variation["variation_id"], "keep")
    assert kept["status"] == "KEPT"
    discarded = service.variation_action(variation["variation_id"], "discard")
    assert discarded["rollback_verified"] is True
    assert daw.snapshot().tracks == []


def test_disconnected_live_fails_truthfully(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_id = _service(tmp_path)
    monkeypatch.setattr(service, "_open_variation_live", lambda: (_ for _ in ()).throw(ProduceExecutionBlocked("ABLETON_SESSION_NOT_READY")))
    reference = _reference_payload(tmp_path, "mock-project")
    with pytest.raises(ProduceExecutionBlocked) as blocked:
        service.produce_generate(project_id, {"instruction": "make a bass variation", "variations": 1, "length_bars": 8, **reference})
    assert blocked.value.reason == "ABLETON_SESSION_NOT_READY"
    assert service.list_variations(project_id) == {"variations": []}


@pytest.mark.parametrize("raises", [False, True])
def test_capture_failure_rolls_back_the_new_owned_track(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raises: bool) -> None:
    service, project_id = _service(tmp_path)
    daw = MockAbletonAdapter()
    daw.session_path = str(tmp_path / "Working Copy.als")
    daw.session_name = "Working Copy"
    daw.connect()
    monkeypatch.setattr(service, "_open_variation_live", lambda: (daw, daw.snapshot()))
    reference = _reference_payload(tmp_path, daw.snapshot().project_identity)
    monkeypatch.setattr(
        service_module,
        "preflight_session",
        lambda *args, **kwargs: {"pass": True, "revision": daw.snapshot().revision, "capture_hosts": {}},
    )
    def failed_capture(*args, **kwargs):
        if raises:
            raise RuntimeError("fixture capture exception")
        return {"ok": False, "signal_status": "CAPTURE_FAILED", "error": "fixture capture failure"}

    monkeypatch.setattr(service_module, "capture_source_post_mixer_ref", failed_capture)
    with pytest.raises(ProduceExecutionBlocked, match="PREVIEW_CAPTURE_FAILED"):
        service.produce_generate(project_id, {"scope": "region", "instruction": "make a bass variation", "variations": 1, "length_bars": 8, **reference})
    daw.connect()
    assert [track for track in daw.snapshot().tracks if track.name.startswith("Copilot Variation ")] == []
    assert service.list_variations(project_id)["variations"][0]["failure_reason"] == "PREVIEW_CAPTURE_FAILED"


def test_session_clip_without_arrangement_readback_never_becomes_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_id = _service(tmp_path)
    daw = MockAbletonAdapter()
    daw.session_path = str(tmp_path / "Working Copy.als")
    daw.session_name = "Working Copy"
    daw.connect()
    monkeypatch.setattr(service, "_open_variation_live", lambda: (daw, daw.snapshot()))
    monkeypatch.setattr(daw, "duplicate_clip_to_arrangement", lambda *args, **kwargs: {"duplicated": True})
    reference = _reference_payload(tmp_path, daw.snapshot().project_identity)
    with pytest.raises(ProduceExecutionBlocked, match="SAFE_WRITE_FAILED"):
        service.produce_generate(project_id, {"scope": "region", "instruction": "syncopate bass",
                                              "variations": 1, "length_bars": 8, **reference})
    daw.connect()
    assert daw.get_arrangement_clips()["clips"] == []
    assert daw.snapshot().tracks == []
    assert not any(item["status"] == "READY" for item in service.list_variations(project_id)["variations"])


def test_silent_isolated_preview_never_becomes_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, project_id = _service(tmp_path)
    daw = MockAbletonAdapter()
    daw.session_path = str(tmp_path / "Working Copy.als")
    daw.session_name = "Working Copy"
    daw.connect()
    monkeypatch.setattr(service, "_open_variation_live", lambda: (daw, daw.snapshot()))
    reference = _reference_payload(tmp_path, daw.snapshot().project_identity)
    monkeypatch.setattr(service_module, "preflight_session", lambda *args, **kwargs: {"pass": True})
    captured = tmp_path / "silent.wav"

    def silent_capture(*args, **kwargs):
        duration = 32 * 60.0 / daw.snapshot().transport.tempo
        _fake_wav(captured, duration, silent=True)
        return {"ok": True, "wav_path": str(captured), "audio_sha256": hashlib.sha256(captured.read_bytes()).hexdigest(),
                "signal_status": "SILENCE", "ref": kwargs["target_ref"].model_dump(mode="json")}

    monkeypatch.setattr(service_module, "capture_source_post_mixer_ref", silent_capture)
    with pytest.raises(ProduceExecutionBlocked, match="SILENT_PREVIEW"):
        service.produce_generate(project_id, {"scope": "region", "instruction": "syncopate bass", "variations": 1,
                                              "length_bars": 8, **reference})
    daw.connect()
    assert daw.snapshot().tracks == []
    assert daw.get_arrangement_clips()["clips"] == []
    failed = service.list_variations(project_id)["variations"][0]
    assert failed["status"] == "FAILED" and failed["failure_reason"] == "SILENT_PREVIEW"


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
        assert error.value.code in {400, 409, 422}
        assert json.load(error.value)["error"] == "AUTHORITATIVE_REFERENCE_MIDI_REQUIRED"
    finally:
        server.shutdown()
        server.server_close()
