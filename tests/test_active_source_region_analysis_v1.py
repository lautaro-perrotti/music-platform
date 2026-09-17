from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.active_source_region_analysis_v1 import (
    CAPTURED_SOURCE_SET_INCOMPLETE,
    MAIN_DIP_WITH_CAPTURED_SOURCES_ACTIVE,
    MULTIPLE_SOURCES_DIP,
    ONLY_ONE_CAPTURED_SOURCE_DIP,
    SOURCE_EVIDENCE_UNAVAILABLE,
    SOURCE_HAS_SIGNAL,
    SOURCE_NEAR_SILENCE,
    collect_source_windows,
)
from copilot.audio.file_hash import sha256_file
from copilot.daw.object_ref import PersistentObjectRef
from copilot.schemas.evidence import (
    CaptureQuality,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)

FP_A = "fp_source_a"
FP_B = "fp_source_b"


def _tone(path: Path, *, seconds: float, sr: int = 44100, amp: float = 0.2, quiet: tuple[float, float] | None = None) -> Path:
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float64) / sr
    wave = amp * np.sin(2 * np.pi * 110.0 * t)
    if quiet is not None:
        lo, hi = quiet
        wave[int(lo * sr) : int(hi * sr)] *= 0.002
    stereo = np.column_stack([wave, wave]).astype(np.float32)
    sf.write(str(path), stereo, sr)
    return path


def _ref(fingerprint: str, name: str) -> PersistentObjectRef:
    return PersistentObjectRef(
        project_identity="id",
        role="midi",
        name=name,
        device_names=[name],
        device_classes=["OriginalSimpler"],
        content_fingerprint=fingerprint,
        target_state_token="tok",
    )


def _capture_item(evidence_id: str, fingerprint: str, wav: Path) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=EvidenceKind.MEASUREMENT,
        source_ref=fingerprint,
        region="AUTO_36_68:36.0-68.0",
        view="TRACK_ISOLATED",
        analysis_version="lowend-obs-1",
        name="source_capture",
        value={
            "ok": True,
            "signal_class": "HAS_SIGNAL",
            "audio_sha256": sha256_file(wav),
            "path": None,
            "ref": fingerprint,
        },
        quality=CaptureQuality.LIMITED,
        project_token="pt",
        audible_token="at",
    )


def _event_items(idx: int, kind: str, start_s: float, end_s: float, active: list[str]) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            evidence_id=f"fm.event.{idx}.kind",
            kind=EvidenceKind.FACT,
            source_ref="fullmix-obs-1",
            region="AUTO_36_68",
            analysis_version="fullmix-obs-1",
            name="fullmix_energy_event_kind",
            value=kind,
            project_token="pt",
            audible_token="at",
        ),
        EvidenceItem(
            evidence_id=f"fm.event.{idx}.start_s",
            kind=EvidenceKind.MEASUREMENT,
            source_ref="fullmix-obs-1",
            region="AUTO_36_68",
            analysis_version="fullmix-obs-1",
            name="fullmix_energy_event_start_s",
            value=start_s,
            unit="s",
            project_token="pt",
            audible_token="at",
        ),
        EvidenceItem(
            evidence_id=f"fm.event.{idx}.end_s",
            kind=EvidenceKind.MEASUREMENT,
            source_ref="fullmix-obs-1",
            region="AUTO_36_68",
            analysis_version="fullmix-obs-1",
            name="fullmix_energy_event_end_s",
            value=end_s,
            unit="s",
            project_token="pt",
            audible_token="at",
        ),
        EvidenceItem(
            evidence_id=f"region.event.fm.event.{idx}",
            kind=EvidenceKind.FACT,
            source_ref="arrangement_region",
            region="AUTO_36_68:36.0-68.0",
            analysis_version="analyze-region-v1",
            name="region_event_context",
            value={
                "event_id": f"fm.event.{idx}",
                "kind": kind,
                "start_s": start_s,
                "end_s": end_s,
                "start_qn": 36.0 + start_s * 2.1,
                "end_qn": 36.0 + end_s * 2.1,
                "active_clip_identities": active,
                "active_track_count": len(active),
                "active_clip_count": len(active),
            },
            project_token="pt",
            audible_token="at",
        ),
    ]


def _clip_item(idx: int, identity: str, fingerprint: str | None, locator: str) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"region.clip.{idx}",
        kind=EvidenceKind.FACT,
        source_ref="arrangement_region",
        region="AUTO_36_68:36.0-68.0",
        analysis_version="analyze-region-v1",
        name="region_clip",
        value={
            "clip_identity": identity,
            "locator_name": locator,
            "content_fingerprint": fingerprint,
            "role": "midi" if fingerprint else "UNKNOWN",
        },
        project_token="pt",
        audible_token="at",
    )


def _pack(items: list[EvidenceItem], entities: list[dict]) -> EvidencePack:
    return EvidencePack(
        pack_id="pack_parent_srcwin",
        analysis_version="lowend-obs-1",
        prompt_schema_version="music-diagnosis-reason-1",
        region="AUTO_36_68:36.0-68.0",
        project_token="pt",
        audible_token="at",
        alignment_claim="LIMITED",
        alignment_envelope_ms=52.0,
        items=[
            EvidenceItem(
                evidence_id="ev.project.identity",
                kind=EvidenceKind.STATE_TOKEN,
                source_ref="session.project_identity",
                region="AUTO_36_68:36.0-68.0",
                analysis_version="lowend-obs-1",
                name="project_identity",
                value="proj",
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="ev.region",
                kind=EvidenceKind.FACT,
                source_ref="region",
                region="AUTO_36_68:36.0-68.0",
                analysis_version="lowend-obs-1",
                name="region",
                value={"id": "AUTO_36_68", "start_qn": 36.0, "end_qn": 68.0},
                project_token="pt",
                audible_token="at",
            ),
            EvidenceItem(
                evidence_id="midi.source.entity_id",
                kind=EvidenceKind.FACT,
                source_ref=FP_A,
                region="AUTO_36_68:36.0-68.0",
                analysis_version="midi-read-only-v1",
                name="midi_source_entity_id",
                value=FP_A,
                project_token="pt",
                audible_token="at",
            ),
            *items,
        ],
        entities=entities,
        limitations=[
            ObservationLimitation(
                code="ALIGNMENT_LIMITED",
                detail="musical alignment remains LIMITED ±52 ms",
                precision_ms=52.0,
                capability_ms=[52.0],
            )
        ],
    )


def _write_parent(path: Path, pack: EvidencePack) -> Path:
    path.write_text(json.dumps({"pack": pack.model_dump(mode="json")}, indent=2), encoding="utf-8")
    return path


def _journal(directory: Path, fingerprint: str, name: str, digest: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    ref = _ref(fingerprint, name).model_dump(mode="json")
    lines = [
        json.dumps({"ref": ref, "status": "PREPARED", "pass_id": "pass"}),
        json.dumps({"hashes": {"source": digest}, "status": "VERIFIED", "pass_id": "pass"}),
    ]
    (directory / f"{fingerprint[:8]}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_source_signal_during_main_dip(tmp_path: Path) -> None:
    wav = _tone(tmp_path / "a.wav", seconds=2.0, amp=0.2)
    _journal(tmp_path / "journal", FP_A, "Pad", sha256_file(wav) or "")
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_A, wav),
            *_event_items(3, "NEAR_SILENCE", 1.0, 1.1, ["clip:a"]),
            _clip_item(0, "clip:a", FP_A, "Pad"),
        ],
        [{"entity_id": FP_A, "kind": "TRACK", "name": "", "role": "midi"}],
    )
    parent = _write_parent(tmp_path / "parent.json", pack)
    report = collect_source_windows(
        parent_path=parent,
        capture_roots=[tmp_path],
        journal_dir=tmp_path / "journal",
    )
    event = next(row for row in report["event_rows"] if row["event_id"] == "fm.event.3")
    assert event["sources"][0]["relation"] == SOURCE_HAS_SIGNAL
    assert MAIN_DIP_WITH_CAPTURED_SOURCES_ACTIVE in event["labels"]
    assert report["pack"]["alignment_envelope_ms"] == 52.0


def test_multiple_sources_dipping(tmp_path: Path) -> None:
    a = _tone(tmp_path / "a.wav", seconds=2.0, quiet=(0.9, 1.2))
    b = _tone(tmp_path / "b.wav", seconds=2.0, quiet=(0.9, 1.2))
    _journal(tmp_path / "journal", FP_A, "A", sha256_file(a) or "")
    _journal(tmp_path / "journal", FP_B, "B", sha256_file(b) or "")
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_A, a),
            _capture_item("ev.source.1.capture", FP_B, b),
            *_event_items(3, "NEAR_SILENCE", 1.0, 1.1, ["clip:a", "clip:b"]),
            *_event_items(4, "NEAR_SILENCE", 1.0, 1.1, ["clip:a", "clip:b"]),
            _clip_item(0, "clip:a", FP_A, "A"),
            _clip_item(1, "clip:b", FP_B, "B"),
        ],
        [
            {"entity_id": FP_A, "kind": "TRACK", "name": "", "role": "midi"},
            {"entity_id": FP_B, "kind": "TRACK", "name": "", "role": "midi"},
        ],
    )
    report = collect_source_windows(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        capture_roots=[tmp_path],
        journal_dir=tmp_path / "journal",
    )
    event = next(row for row in report["event_rows"] if row["event_id"] == "fm.event.3")
    relations = {row["relation"] for row in event["sources"]}
    assert SOURCE_HAS_SIGNAL not in relations
    assert MULTIPLE_SOURCES_DIP in event["labels"]
    assert report["QUESTIONS"]["B"]["repeated_multi_source_loss"] is True


def test_one_source_dipping_while_others_remain_active(tmp_path: Path) -> None:
    quiet = _tone(tmp_path / "a.wav", seconds=2.0, amp=0.0004)
    loud = _tone(tmp_path / "b.wav", seconds=2.0, amp=0.2)
    _journal(tmp_path / "journal", FP_A, "A", sha256_file(quiet) or "")
    _journal(tmp_path / "journal", FP_B, "B", sha256_file(loud) or "")
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_A, quiet),
            _capture_item("ev.source.1.capture", FP_B, loud),
            *_event_items(5, "STRONG_ENERGY_DIP", 1.0, 1.1, ["clip:a", "clip:b"]),
            _clip_item(0, "clip:a", FP_A, "A"),
            _clip_item(1, "clip:b", FP_B, "B"),
        ],
        [
            {"entity_id": FP_A, "kind": "TRACK", "name": "", "role": "midi"},
            {"entity_id": FP_B, "kind": "TRACK", "name": "", "role": "midi"},
        ],
    )
    report = collect_source_windows(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        capture_roots=[tmp_path],
        journal_dir=tmp_path / "journal",
    )
    event = next(row for row in report["event_rows"] if row["event_id"] == "fm.event.5")
    by_fp = {row["content_fingerprint"]: row["relation"] for row in event["sources"]}
    assert by_fp[FP_A] == SOURCE_NEAR_SILENCE
    assert by_fp[FP_B] == SOURCE_HAS_SIGNAL
    assert ONLY_ONE_CAPTURED_SOURCE_DIP in event["labels"]
    assert MAIN_DIP_WITH_CAPTURED_SOURCES_ACTIVE in event["labels"]
    assert report["QUESTIONS"]["C"]["target_source"]["relation"] == SOURCE_NEAR_SILENCE


def test_incomplete_source_coverage(tmp_path: Path) -> None:
    wav = _tone(tmp_path / "a.wav", seconds=2.0)
    _journal(tmp_path / "journal", FP_A, "A", sha256_file(wav) or "")
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_A, wav),
            *_event_items(3, "NEAR_SILENCE", 1.0, 1.1, ["clip:a", "clip:uncaptured"]),
            _clip_item(0, "clip:a", FP_A, "A"),
            _clip_item(1, "clip:uncaptured", None, "Vinyl"),
        ],
        [{"entity_id": FP_A, "kind": "TRACK", "name": "", "role": "midi"}],
    )
    report = collect_source_windows(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        capture_roots=[tmp_path],
        journal_dir=tmp_path / "journal",
    )
    event = next(row for row in report["event_rows"] if row["event_id"] == "fm.event.3")
    assert event["coverage"]["ACTIVE_SOURCE_COUNT"] == 2
    assert event["coverage"]["CAPTURED_ACTIVE_SOURCE_COUNT"] == 1
    assert event["coverage"]["UNCAPTURED_ACTIVE_SOURCE_COUNT"] == 1
    assert CAPTURED_SOURCE_SET_INCOMPLETE in event["labels"]
    assert report["QUESTIONS"]["A"]["four_active_sources_all_captured"] is False


def test_locator_join_counts_captured_active_without_clip_fingerprint(tmp_path: Path) -> None:
    wav = _tone(tmp_path / "a.wav", seconds=2.0)
    _journal(tmp_path / "journal", FP_A, "Filter Kick", sha256_file(wav) or "")
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_A, wav),
            *_event_items(3, "NEAR_SILENCE", 1.0, 1.1, ["clip:kick"]),
            _clip_item(0, "clip:kick", None, "Filter Kick"),
        ],
        [{"entity_id": FP_A, "kind": "TRACK", "name": "", "role": "midi"}],
    )
    report = collect_source_windows(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        capture_roots=[tmp_path],
        journal_dir=tmp_path / "journal",
    )
    event = next(row for row in report["event_rows"] if row["event_id"] == "fm.event.3")
    assert event["coverage"]["CAPTURED_ACTIVE_SOURCE_COUNT"] == 1
    assert event["coverage"]["UNCAPTURED_ACTIVE_SOURCE_COUNT"] == 0
    assert event["coverage"]["captured_active"][0]["identity_join"] == "captured_source_locator_join"
    assert event["sources"][0]["arrangement_active"] is True


def test_inactive_captured_silence_does_not_count_as_multi_source_dip(tmp_path: Path) -> None:
    loud = _tone(tmp_path / "a.wav", seconds=2.0, amp=0.2)
    silent = _tone(tmp_path / "b.wav", seconds=2.0, amp=0.0)
    _journal(tmp_path / "journal", FP_A, "Kick", sha256_file(loud) or "")
    _journal(tmp_path / "journal", FP_B, "Hat", sha256_file(silent) or "")
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_A, loud),
            _capture_item("ev.source.1.capture", FP_B, silent),
            *_event_items(3, "NEAR_SILENCE", 1.0, 1.1, ["clip:a"]),
            _clip_item(0, "clip:a", FP_A, "Kick"),
        ],
        [
            {"entity_id": FP_A, "kind": "TRACK", "name": "", "role": "midi"},
            {"entity_id": FP_B, "kind": "TRACK", "name": "", "role": "midi"},
        ],
    )
    report = collect_source_windows(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        capture_roots=[tmp_path],
        journal_dir=tmp_path / "journal",
    )
    event = next(row for row in report["event_rows"] if row["event_id"] == "fm.event.3")
    assert event["sources"][0]["relation"] == SOURCE_HAS_SIGNAL
    assert event["sources"][1]["arrangement_active"] is False
    assert MULTIPLE_SOURCES_DIP not in event["labels"]
    assert MAIN_DIP_WITH_CAPTURED_SOURCES_ACTIVE in event["labels"]


def test_unavailable_source_wav(tmp_path: Path) -> None:
    missing = tmp_path / "missing.wav"
    pack = _pack(
        [
            EvidenceItem(
                evidence_id="ev.source.0.capture",
                kind=EvidenceKind.MEASUREMENT,
                source_ref=FP_A,
                region="AUTO_36_68:36.0-68.0",
                analysis_version="lowend-obs-1",
                name="source_capture",
                value={"ok": True, "audio_sha256": "deadbeef", "path": str(missing), "ref": FP_A},
                quality=CaptureQuality.LIMITED,
                project_token="pt",
                audible_token="at",
            ),
            *_event_items(3, "NEAR_SILENCE", 1.0, 1.1, ["clip:a"]),
            _clip_item(0, "clip:a", FP_A, "A"),
        ],
        [{"entity_id": FP_A, "kind": "TRACK", "name": "", "role": "midi"}],
    )
    report = collect_source_windows(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        capture_roots=[tmp_path],
        journal_dir=tmp_path / "journal",
    )
    event = next(row for row in report["event_rows"] if row["event_id"] == "fm.event.3")
    assert event["sources"][0]["relation"] == SOURCE_EVIDENCE_UNAVAILABLE
    assert report["provenances"][0]["ok"] is False


def test_cross_source_timing_limit_and_parent_unchanged(tmp_path: Path) -> None:
    wav = _tone(tmp_path / "a.wav", seconds=2.0)
    _journal(tmp_path / "journal", FP_A, "A", sha256_file(wav) or "")
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_A, wav),
            *_event_items(3, "NEAR_SILENCE", 1.0, 1.1, ["clip:a"]),
            _clip_item(0, "clip:a", FP_A, "A"),
        ],
        [{"entity_id": FP_A, "kind": "TRACK", "name": "", "role": "midi"}],
    )
    parent = _write_parent(tmp_path / "parent.json", pack)
    before = hashlib.sha256(parent.read_bytes()).hexdigest()
    report = collect_source_windows(
        parent_path=parent,
        capture_roots=[tmp_path],
        journal_dir=tmp_path / "journal",
    )
    assert hashlib.sha256(parent.read_bytes()).hexdigest() == before
    assert report["parent_file_unchanged"] is True
    assert report["pack"]["alignment_claim"] == "LIMITED"
    assert report["pack"]["alignment_envelope_ms"] == 52.0
    assert report["pack"]["pack_id"] != "pack_parent_srcwin"
    codes = {row["code"] for row in report["pack"]["limitations"]}
    assert "CROSS_SOURCE_ALIGNMENT_LIMITED" in codes
    event = report["event_rows"][0]
    assert event["alignment_envelope_s"] == 0.052
    assert event["sources"][0]["envelope_s"] == 0.052
    assert report["NO RECAPTURE"] is True
    assert report["NO NEW MIDI READ"] is True
    assert report["NO ROUTING READ"] is True
    assert report["MUSICAL WRITES"] == 0


def test_no_live_access_during_offline_collect(tmp_path: Path, monkeypatch) -> None:
    source = Path("src/copilot/audio/active_source_region_analysis_v1.py").read_text(encoding="utf-8")
    assert "AbletonTcpAdapter" not in source
    assert "route_host_post_mixer" not in source

    def boom(*_args, **_kwargs):
        raise RuntimeError("Live accessed")

    monkeypatch.setattr("copilot.daw.ableton_tcp.AbletonTcpAdapter.__init__", boom)
    wav = _tone(tmp_path / "a.wav", seconds=1.5)
    _journal(tmp_path / "journal", FP_A, "A", sha256_file(wav) or "")
    pack = _pack(
        [
            _capture_item("ev.source.0.capture", FP_A, wav),
            *_event_items(3, "NEAR_SILENCE", 0.4, 0.5, ["clip:a"]),
            _clip_item(0, "clip:a", FP_A, "A"),
        ],
        [{"entity_id": FP_A, "kind": "TRACK", "name": "", "role": "midi"}],
    )
    report = collect_source_windows(
        parent_path=_write_parent(tmp_path / "parent.json", pack),
        capture_roots=[tmp_path],
        journal_dir=tmp_path / "journal",
    )
    assert report["NO LIVE"] is True
    assert report["status"] == "VERIFIED"
