from __future__ import annotations

import hashlib
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.drum_events_v1 import build_drum_event_set
from copilot.studio.drum_workbench import load_drum_workbench
from copilot.studio.server import StudioHandler
from copilot.studio.service import ProduceExecutionBlocked, StudioService, StudioTransportUnavailable


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    source = tmp_path / "drums.wav"
    signal = np.zeros((48_000, 1), dtype=np.float32)
    signal[[3_000, 15_000, 27_000, 39_000], 0] = [0.8, 0.7, 0.9, 0.75]
    sf.write(source, signal, 48_000)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    asset_id = "test-drums-asset"
    event_set = build_drum_event_set(
        source,
        source_asset_id=asset_id,
        source_sha256=digest,
        tempo_bpm=120,
        tempo_source="TEST_FIXTURE",
        region_end_seconds=1,
    )
    artifact = tmp_path / "events.json"
    artifact.write_text(event_set.model_dump_json(), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "rights_status": "UNKNOWN",
        "remote_upload_allowed": False,
        "records": [{"role": "DRUMS", "manifest": {
            "source_asset_id": asset_id,
            "immutable_sha256": digest,
            "immutable_path": str(source),
            "immutable_source": True,
        }}],
    }), encoding="utf-8")
    return artifact, manifest, source, digest


def test_drum_workbench_requires_explicit_configuration(monkeypatch) -> None:
    monkeypatch.delenv("COPILOT_STUDIO_DRUM_EVENT_SET", raising=False)
    monkeypatch.delenv("COPILOT_STUDIO_ASSET_SET_MANIFEST", raising=False)
    result = load_drum_workbench(artifact_path=None, manifest_path=None)
    assert result["status"] == "NOT_CONFIGURED"
    assert result["events"] == []
    assert result["musical_writes"] == 0


def test_produce_execution_blocker_keeps_its_typed_error_contract() -> None:
    result = ProduceExecutionBlocked("SAFE_WRITE_BLOCKED", detail="test").to_dict()
    assert result == {
        "error": "PRODUCE_EXECUTION_BLOCKED",
        "reason": "SAFE_WRITE_BLOCKED",
        "detail": "test",
        "evidence": {},
    }


def test_drum_workbench_projects_validated_events_without_local_paths(tmp_path: Path) -> None:
    artifact, manifest, _source, digest = _write_inputs(tmp_path)
    result = load_drum_workbench(artifact_path=artifact, manifest_path=manifest)
    assert result["status"] == "READY"
    assert result["source"]["sha256"] == digest
    assert result["source"]["rights_status"] == "UNKNOWN"
    assert result["grid"]["tempo_status"] == "PROVISIONAL"
    assert result["events"]
    assert all(row["ableton_status"] == "NOT_REALIZED" for row in result["events"])
    assert all(row["selected_sample_asset_id"] is None for row in result["events"])
    assert all("source_path" not in row for row in result["events"])
    assert str(tmp_path) not in json.dumps(result)
    assert result["musical_writes"] == 0


def test_drum_workbench_fails_closed_on_source_digest_mismatch(tmp_path: Path) -> None:
    artifact, manifest, source, _digest = _write_inputs(tmp_path)
    source.write_bytes(source.read_bytes() + b"changed")
    result = load_drum_workbench(artifact_path=artifact, manifest_path=manifest)
    assert result["status"] == "SOURCE_HASH_MISMATCH"
    assert result["events"] == []


def test_studio_http_exposes_read_only_drum_contract(tmp_path: Path, monkeypatch) -> None:
    artifact, manifest, _source, _digest = _write_inputs(tmp_path)
    monkeypatch.setenv("COPILOT_STUDIO_DRUM_EVENT_SET", str(artifact))
    monkeypatch.setenv("COPILOT_STUDIO_ASSET_SET_MANIFEST", str(manifest))
    service = StudioService(tmp_path / "studio")
    handler = type("DrumHandler", (StudioHandler,), {
        "service": service,
        "static_root": Path(__file__).parents[1] / "src" / "copilot" / "studio" / "static",
        "log_message": lambda *args: None,
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/drums/events") as response:
            body = json.load(response)
        assert body["status"] == "READY"
        assert body["musical_writes"] == 0
        assert body["events"]
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/ui/drums.html") as response:
            assert b"Read-only drum event workbench" in response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_transport_sse_reports_unavailable_without_fabricating_play_state(tmp_path: Path) -> None:
    service = StudioService(tmp_path / "studio")
    service.open_transport_event_client = lambda: (_ for _ in ()).throw(
        StudioTransportUnavailable("LIVE_UNAVAILABLE", "PORT_CLOSED")
    )
    handler = type("TransportHandler", (StudioHandler,), {
        "service": service,
        "static_root": Path(__file__).parents[1] / "src" / "copilot" / "studio" / "static",
        "log_message": lambda *args: None,
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/ableton/transport/stream") as response:
            body = response.read().decode()
        assert "event: bridge.status" in body
        assert '"status": "LIVE_UNAVAILABLE"' in body
        assert '"playing"' not in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
