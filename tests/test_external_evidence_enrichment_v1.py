from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.astra_external_reasoning_v1 import pack_payload_hash
from copilot.audio.external_evidence_enrichment_v1 import (
    REDUNDANT_NAMES,
    SELECTED_EVENT_NAMES,
    descriptor_audit,
    enrich_pack,
    locate_main_wav,
    select_event_items,
)
from copilot.audio.file_hash import sha256_file
from copilot.audio.fullmix import compute_fullmix_observation
from copilot.reasoning.errors import ReasoningFailure
from copilot.reasoning.fixtures import output_hallucination_trap, pack_hallucination_trap
from copilot.reasoning.grounding import validate_reasoning
from copilot.schemas.evidence import (
    CaptureQuality,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)


def _write_wav(path: Path, *, seconds: float = 1.0, sr: int = 44100) -> Path:
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float64) / sr
    tone = 0.05 * np.sin(2 * np.pi * 110.0 * t)
    tone[int(0.4 * sr) : int(0.55 * sr)] *= 0.01
    stereo = np.column_stack([tone, tone]).astype(np.float32)
    sf.write(str(path), stereo, sr)
    return path


def _mini_pack(wav: Path) -> EvidencePack:
    digest = sha256_file(wav) or ""
    return EvidencePack(
        pack_id="pack_parent_test",
        analysis_version="lowend-obs-1",
        prompt_schema_version="music-diagnosis-reason-1",
        region="AUTO_36_68:36.0-68.0",
        project_token="pt_test",
        audible_token="at_test",
        alignment_claim="LIMITED",
        alignment_envelope_ms=52.0,
        domain="producer",
        items=[
            EvidenceItem(
                evidence_id="ev.main.capture",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="main_sidecar",
                region="AUTO_36_68:36.0-68.0",
                view="MASTER_CONTEXT",
                analysis_version="lowend-obs-1",
                name="main_capture",
                value={
                    "ok": True,
                    "signal_class": "HAS_SIGNAL",
                    "audio_sha256": digest,
                    "path": str(wav),
                },
                quality=CaptureQuality.LIMITED,
                project_token="pt_test",
                audible_token="at_test",
            ),
            EvidenceItem(
                evidence_id="ev.fullmix",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="fullmix-obs-1",
                region="AUTO_36_68:36.0-68.0",
                view="MASTER_CONTEXT",
                analysis_version="lowend-obs-1",
                name="fullmix_observation",
                value={"analyzer_id": "fullmix-obs-1", "event_count": 1, "ok": True},
                project_token="pt_test",
                audible_token="at_test",
            ),
        ],
        limitations=[
            ObservationLimitation(
                code="ALIGNMENT_LIMITED",
                detail="musical alignment remains LIMITED ±52 ms",
                precision_ms=52.0,
                capability_ms=[52.0],
            )
        ],
    )


def test_same_wav_descriptors_are_stable(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "main.wav")
    first = compute_fullmix_observation(wav, region_id="R", region_label="R", use_cache=False)
    second = compute_fullmix_observation(wav, region_id="R", region_label="R", use_cache=False)
    assert first.energy_events == second.energy_events
    pack = _mini_pack(wav)
    a = select_event_items(first, pack)
    b = select_event_items(second, pack)
    assert [item.model_dump() for item in a] == [item.model_dump() for item in b]
    assert all(item.name in SELECTED_EVENT_NAMES for item in a)
    assert "fullmix_energy_event_count" not in {item.name for item in a}


def test_parent_pack_file_and_provenance_preserved(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "main.wav")
    pack = _mini_pack(wav)
    parent_path = tmp_path / "evidence_pack_v1.json"
    parent_path.write_text(
        json.dumps({"pack": pack.model_dump(mode="json")}, indent=2),
        encoding="utf-8",
    )
    before = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    report = enrich_pack(parent_path=parent_path, repo=tmp_path)
    after = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    assert after == before
    assert report["parent_file_unchanged"] is True
    assert report["pack"]["project_token"] == "pt_test"
    assert report["pack"]["audible_token"] == "at_test"
    assert report["pack"]["pack_id"] != "pack_parent_test"
    assert report["pack"]["alignment_claim"] == "LIMITED"
    assert report["pack"]["alignment_envelope_ms"] == 52.0
    assert any(
        row["code"] == "MAIN_LOCAL_TIMING_NOT_CROSS_SOURCE"
        for row in report["pack"]["limitations"]
    )
    assert report["wav"]["hash_match"] is True
    names = {item["name"] for item in report["added_observations"]}
    assert "fullmix_energy_event_kind" in names
    assert "fullmix_energy_event_count" not in names


def test_audit_marks_truncated_count_redundant() -> None:
    wav_dummy = Path("missing.wav")
    pack = EvidencePack(
        pack_id="p",
        analysis_version="lowend-obs-1",
        prompt_schema_version="music-diagnosis-reason-1",
        region="R",
        project_token="pt",
        audible_token="at",
        items=[
            EvidenceItem(
                evidence_id="ev.fullmix",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="fullmix-obs-1",
                region="R",
                analysis_version="lowend-obs-1",
                name="fullmix_observation",
                value={"event_count": 6, "ok": True},
                project_token="pt",
                audible_token="at",
            )
        ],
    )
    audit = descriptor_audit(pack)
    assert "fullmix_energy_event_kind" in audit["MISSING"]
    assert "fullmix_energy_event_count" in REDUNDANT_NAMES
    del wav_dummy


def test_grounding_still_rejects_invented_measurements() -> None:
    report = validate_reasoning(output_hallucination_trap(), pack_hallucination_trap())
    assert report.accepted is False
    assert ReasoningFailure.UNKNOWN_EVIDENCE_REF in {issue.kind for issue in report.issues}


def test_locate_main_wav_requires_exact_hash(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "main.wav")
    pack = _mini_pack(wav)
    pack.items[0].value["audio_sha256"] = "deadbeef"
    located = locate_main_wav(pack, repo=tmp_path)
    assert located["ok"] is False
    assert located["hash_match"] is False
    assert pack_payload_hash(pack)
