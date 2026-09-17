"""ACTIVE_SOURCE_REGION_ANALYSIS_V1 — Post Mixer WAVs vs frozen Main events. No Live."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import soundfile as sf

from copilot.audio.analyze_region_v1 import load_region_events, target_source_id
from copilot.audio.astra_external_reasoning_v1 import (
    RecordingProvider,
    classify_rejection,
    load_persisted_pack,
    pack_payload_hash,
)
from copilot.audio.file_hash import sha256_file
from copilot.audio.midi_read_only_v1 import load_persisted_ref, pack_region
from copilot.audio.producer_analyze_v1 import apply_reasoning_result
from copilot.audio.tap_trust import JOURNAL_DIR, VERIFIED
from copilot.human_eval.store import now_iso
from copilot.reasoning.pipeline import _parse_output, reason
from copilot.reasoning.provider import ReasoningProvider, configured_http_provider
from copilot.reasoning.session_astra import ASTRA_TIMEOUT_S
from copilot.schemas.diagnosis import DiagnosisStatus
from copilot.schemas.evidence import (
    CaptureQuality,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)

MILESTONE = "ACTIVE_SOURCE_REGION_ANALYSIS_V1"
ARTIFACT = "active_source_region_analysis_v1.json"
STATUS = "IMPLEMENTED"
ANALYSIS_VERSION = "active-source-region-v1"
EXPECTED_PARENT_PACK_ID = "pack_98ca922d4e01"
PARENT_PACK_PATH = Path("logs") / "analyze_region_v1.json"
CAPTURE_ROOTS = (Path("logs") / "captures",)
# Frozen thresholds from source-audio-trace / fullmix-obs-1 / live_capture.
SILENCE_RMS = 1.0e-4
SILENCE_PEAK = 1.0e-3
NEAR_SILENCE_RMS = 1.0e-3
STRONG_DIP_DB = -12.0
ALIGNMENT_ENVELOPE_S = 0.052
FORBIDDEN_JUDGMENT_RE = re.compile(
    r"\b(mix fault|device failure|whole mix quiet|should be loud)\b",
    re.IGNORECASE,
)

SOURCE_HAS_SIGNAL = "SOURCE_HAS_SIGNAL"
SOURCE_ENERGY_DIP = "SOURCE_ENERGY_DIP"
SOURCE_NEAR_SILENCE = "SOURCE_NEAR_SILENCE"
SOURCE_NO_MATERIAL = "SOURCE_NO_MATERIAL"
SOURCE_EVIDENCE_UNAVAILABLE = "SOURCE_EVIDENCE_UNAVAILABLE"
QUIET_CLASSES = {SOURCE_ENERGY_DIP, SOURCE_NEAR_SILENCE, SOURCE_NO_MATERIAL}
ALL_CAPTURED_SOURCES_DIP = "ALL_CAPTURED_SOURCES_DIP"
MULTIPLE_SOURCES_DIP = "MULTIPLE_SOURCES_DIP"
ONLY_ONE_CAPTURED_SOURCE_DIP = "ONLY_ONE_CAPTURED_SOURCE_DIP"
MAIN_DIP_WITH_CAPTURED_SOURCES_ACTIVE = "MAIN_DIP_WITH_CAPTURED_SOURCES_ACTIVE"
CAPTURED_SOURCE_SET_INCOMPLETE = "CAPTURED_SOURCE_SET_INCOMPLETE"


def _mono(samples: np.ndarray) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim == 1:
        return data
    if data.shape[0] <= 8 and data.shape[0] < data.shape[1]:
        return np.mean(data, axis=0)
    return np.mean(data, axis=1)


def _db(num: float, den: float) -> float:
    return 20.0 * math.log10(max(float(num), 1e-12) / max(float(den), 1e-12))


def _signal_class(rms: float, peak: float) -> str:
    if rms <= SILENCE_RMS and peak <= SILENCE_PEAK:
        return "SILENCE"
    if rms <= NEAR_SILENCE_RMS:
        return "NEAR_SILENCE"
    return "HAS_SIGNAL"


def _slice_stats(mono: np.ndarray, sample_rate: int, start_s: float, end_s: float) -> dict[str, float]:
    start_i = max(0, int(round(start_s * sample_rate)))
    end_i = min(len(mono), int(round(end_s * sample_rate)))
    if end_i <= start_i:
        return {"rms": 0.0, "peak": 0.0, "samples": 0.0}
    window = mono[start_i:end_i]
    rms = float(np.sqrt(np.mean(window * window))) if len(window) else 0.0
    peak = float(np.max(np.abs(window))) if len(window) else 0.0
    return {"rms": rms, "peak": peak, "samples": float(len(window))}


def analyze_source_event_window(
    wav_path: Path,
    *,
    event_audio_start_s: float,
    event_audio_end_s: float,
    envelope_s: float = ALIGNMENT_ENVELOPE_S,
    context_pad_s: float = 0.5,
) -> dict[str, Any]:
    """Same window stats as source-audio-trace, with the LIMITED ±52 ms envelope."""
    samples, sample_rate = sf.read(str(wav_path), always_2d=True)
    mono = _mono(samples)
    duration_s = float(len(mono) / float(sample_rate)) if sample_rate else 0.0
    start_s = max(0.0, event_audio_start_s - envelope_s)
    end_s = min(duration_s, event_audio_end_s + envelope_s)
    event = _slice_stats(mono, sample_rate, start_s, end_s)
    before = _slice_stats(mono, sample_rate, max(0.0, start_s - context_pad_s), start_s)
    after = _slice_stats(mono, sample_rate, end_s, min(duration_s, end_s + context_pad_s))
    ref_candidates = [before["rms"], after["rms"]]
    local_ref = (
        max(ref_candidates)
        if max(ref_candidates) > 0
        else float(np.sqrt(np.mean(mono * mono)) if len(mono) else 0.0)
    )
    klass = _signal_class(event["rms"], event["peak"])
    relative_drop_db = _db(event["rms"], local_ref) if local_ref > 0 else 0.0
    if klass == "SILENCE":
        relation = SOURCE_NO_MATERIAL
    elif klass == "NEAR_SILENCE":
        relation = SOURCE_NEAR_SILENCE
    elif relative_drop_db <= STRONG_DIP_DB and _signal_class(local_ref, local_ref) == "HAS_SIGNAL":
        relation = SOURCE_ENERGY_DIP
    else:
        relation = SOURCE_HAS_SIGNAL
    return {
        "event_window_start_s": start_s,
        "event_window_end_s": end_s,
        "event_rms": event["rms"],
        "event_peak": event["peak"],
        "before_rms": before["rms"],
        "after_rms": after["rms"],
        "local_reference_rms": local_ref,
        "relative_drop_db": relative_drop_db,
        "signal_class": klass,
        "relation": relation,
        "sample_rate": int(sample_rate),
        "duration_s": duration_s,
        "envelope_s": envelope_s,
    }


_WAV_INDEX: dict[tuple[str, ...], dict[str, Path]] = {}


def _wav_index(roots: list[Path]) -> dict[str, Path]:
    key = tuple(str(path.resolve()) if path.exists() else str(path) for path in roots)
    cached = _WAV_INDEX.get(key)
    if cached is not None:
        return cached
    index: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.wav")):
            digest = sha256_file(path)
            if digest and digest not in index:
                index[digest] = path
    _WAV_INDEX[key] = index
    return index


def locate_wav_by_sha256(digest: str, roots: list[Path] | None = None) -> Path | None:
    if not digest:
        return None
    return _wav_index(roots or list(CAPTURE_ROOTS)).get(digest)


def journal_for_source_hash(
    digest: str, *, journal_dir: Path | None = None
) -> dict[str, Any]:
    directory = Path(journal_dir or JOURNAL_DIR)
    if not directory.is_dir():
        return {"status": "MISSING", "pass_id": None, "path": None}
    for path in sorted(directory.glob("*.jsonl")):
        status = None
        pass_id = None
        matched = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            hashes = row.get("hashes") or {}
            if hashes.get("source") == digest:
                matched = True
            status = row.get("status") or status
            pass_id = row.get("pass_id") or pass_id
        if matched:
            return {
                "status": status,
                "pass_id": pass_id,
                "path": str(path),
                "verified": status == VERIFIED,
            }
    return {"status": "MISSING", "pass_id": None, "path": None}


def pack_source_captures(pack: EvidencePack) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in pack.items:
        if item.name != "source_capture":
            continue
        value = item.value if isinstance(item.value, dict) else {}
        fingerprint = str(item.source_ref or value.get("ref") or "")
        rows.append(
            {
                "evidence_id": item.evidence_id,
                "content_fingerprint": fingerprint,
                "declared_sha256": str(value.get("audio_sha256") or ""),
                "declared_path": value.get("path"),
                "region": item.region,
                "quality": item.quality.value if hasattr(item.quality, "value") else str(item.quality),
                "signal_class": value.get("signal_class"),
                "view": item.view,
            }
        )
    return rows


def clip_index(pack: EvidencePack) -> dict[str, dict[str, Any]]:
    by_identity: dict[str, dict[str, Any]] = {}
    for item in pack.items:
        if item.name != "region_clip" or not isinstance(item.value, dict):
            continue
        identity = str(item.value.get("clip_identity") or "")
        if identity:
            by_identity[identity] = item.value
    return by_identity


def arrangement_context(pack: EvidencePack, event_id: str) -> dict[str, Any]:
    item = pack.by_id().get(f"region.event.{event_id}")
    if item is None or not isinstance(item.value, dict):
        return {
            "active_clip_identities": [],
            "active_track_count": None,
            "active_clip_count": None,
        }
    return item.value


def _assert_factual(payload: Any) -> None:
    blob = json.dumps(payload, ensure_ascii=False)
    hit = FORBIDDEN_JUDGMENT_RE.search(blob)
    if hit:
        raise ValueError(f"source-region evidence leaked diagnosis language: {hit.group(0)}")


def classify_event_sources(relations: list[str], *, incomplete: bool) -> list[str]:
    available = [row for row in relations if row != SOURCE_EVIDENCE_UNAVAILABLE]
    quiet = [row for row in available if row in QUIET_CLASSES]
    active = [row for row in available if row == SOURCE_HAS_SIGNAL]
    labels: list[str] = []
    if incomplete:
        labels.append(CAPTURED_SOURCE_SET_INCOMPLETE)
    if available and len(quiet) == len(available):
        labels.append(ALL_CAPTURED_SOURCES_DIP)
    if len(quiet) >= 2:
        labels.append(MULTIPLE_SOURCES_DIP)
    if len(quiet) == 1 and available:
        labels.append(ONLY_ONE_CAPTURED_SOURCE_DIP)
    if active:
        labels.append(MAIN_DIP_WITH_CAPTURED_SOURCES_ACTIVE)
    return labels


def captured_locator_index(provenances: list[dict[str, Any]]) -> dict[str, str]:
    """Unique PersistentObjectRef locator → capture fingerprint. Names stay locators."""
    hits: dict[str, set[str]] = {}
    for row in provenances:
        if not row.get("ok"):
            continue
        name = str(row.get("locator_name") or "").strip()
        fingerprint = str(row.get("content_fingerprint") or "")
        if not name or not fingerprint:
            continue
        hits.setdefault(name, set()).add(fingerprint)
    return {name: next(iter(fps)) for name, fps in hits.items() if len(fps) == 1}


def coverage_for_event(
    *,
    active_identities: list[str],
    clips: dict[str, dict[str, Any]],
    captured_fingerprints: set[str],
    locator_to_fingerprint: dict[str, str] | None = None,
) -> dict[str, Any]:
    locators = locator_to_fingerprint or {}
    uncaptured: list[dict[str, Any]] = []
    captured_active: list[dict[str, Any]] = []
    for identity in active_identities:
        clip = clips.get(identity) or {}
        fingerprint = clip.get("content_fingerprint")
        join = None
        if fingerprint and fingerprint in captured_fingerprints:
            join = "pack_fingerprint"
        elif not fingerprint:
            locator = str(clip.get("locator_name") or "")
            joined = locators.get(locator)
            if joined and joined in captured_fingerprints:
                fingerprint = joined
                join = "captured_source_locator_join"
        row = {
            "clip_identity": identity,
            "locator_name": clip.get("locator_name"),
            "role": clip.get("role") or "UNKNOWN",
            "content_fingerprint": fingerprint,
            "identity_join": join,
        }
        if join:
            captured_active.append(row)
        else:
            uncaptured.append(row)
    return {
        "ACTIVE_SOURCE_COUNT": len(active_identities),
        "CAPTURED_SOURCE_COUNT": len(captured_fingerprints),
        "CAPTURED_ACTIVE_SOURCE_COUNT": len(captured_active),
        "UNCAPTURED_ACTIVE_SOURCE_COUNT": len(uncaptured),
        "captured_active": captured_active,
        "uncaptured_active": uncaptured,
        "incomplete": len(uncaptured) > 0,
    }


def _item(
    evidence_id: str,
    name: str,
    value: Any,
    *,
    pack: EvidencePack,
    region: str,
    source_ref: str,
    kind: EvidenceKind = EvidenceKind.FACT,
    unit: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        source_ref=source_ref,
        region=region,
        view="TRACK_ISOLATED",
        analysis_version=ANALYSIS_VERSION,
        name=name,
        value=value,
        unit=unit,
        quality=CaptureQuality.LIMITED,
        limitations=["MEASURE_ONLY", "ALIGNMENT_LIMITED", "SOURCE_LOCAL_WINDOW"],
        project_token=pack.project_token,
        audible_token=pack.audible_token,
        target_token=pack.target_token,
    )


def source_evidence_items(
    *,
    pack: EvidencePack,
    region: dict[str, Any],
    provenances: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    questions: dict[str, Any],
    envelope_s: float,
) -> list[EvidenceItem]:
    label = str(region["label"])
    items: list[EvidenceItem] = [
        _item(
            "srcwin.alignment_envelope_ms",
            "source_window_alignment_envelope_ms",
            envelope_s * 1000.0,
            pack=pack,
            region=label,
            source_ref="alignment",
            unit="ms",
        ),
        _item(
            "srcwin.strong_dip_threshold_db",
            "source_window_strong_dip_threshold_db",
            STRONG_DIP_DB,
            pack=pack,
            region=label,
            source_ref="fullmix-obs-1",
            unit="dB",
        ),
        _item(
            "srcwin.question_a",
            "source_window_question_a",
            questions["A"],
            pack=pack,
            region=label,
            source_ref="srcwin",
        ),
        _item(
            "srcwin.question_b",
            "source_window_question_b",
            questions["B"],
            pack=pack,
            region=label,
            source_ref="srcwin",
        ),
        _item(
            "srcwin.question_c",
            "source_window_question_c",
            questions["C"],
            pack=pack,
            region=label,
            source_ref="srcwin",
        ),
    ]
    for row in provenances:
        prefix = f"srcwin.{row['evidence_id']}"
        ref = row["content_fingerprint"]
        items.extend(
            [
                _item(
                    f"{prefix}.provenance",
                    "source_window_provenance",
                    row,
                    pack=pack,
                    region=label,
                    source_ref=ref,
                ),
                _item(
                    f"{prefix}.available",
                    "source_window_available",
                    bool(row.get("ok")),
                    pack=pack,
                    region=label,
                    source_ref=ref,
                ),
            ]
        )
    for event in event_rows:
        eprefix = f"srcwin.{event['event_id']}"
        items.append(
            _item(
                f"{eprefix}.context",
                "source_window_event_context",
                event,
                pack=pack,
                region=label,
                source_ref="srcwin",
            )
        )
        items.append(
            _item(
                f"{eprefix}.labels",
                "source_window_event_labels",
                event.get("labels") or [],
                pack=pack,
                region=label,
                source_ref="srcwin",
            )
        )
        cov = event.get("coverage") or {}
        items.extend(
            [
                _item(
                    f"{eprefix}.active_source_count",
                    "source_window_active_source_count",
                    cov.get("ACTIVE_SOURCE_COUNT"),
                    pack=pack,
                    region=label,
                    source_ref="srcwin",
                    unit="count",
                ),
                _item(
                    f"{eprefix}.captured_source_count",
                    "source_window_captured_source_count",
                    cov.get("CAPTURED_SOURCE_COUNT"),
                    pack=pack,
                    region=label,
                    source_ref="srcwin",
                    unit="count",
                ),
                _item(
                    f"{eprefix}.uncaptured_active_source_count",
                    "source_window_uncaptured_active_source_count",
                    cov.get("UNCAPTURED_ACTIVE_SOURCE_COUNT"),
                    pack=pack,
                    region=label,
                    source_ref="srcwin",
                    unit="count",
                ),
            ]
        )
        for src in event.get("sources") or []:
            sref = str(src.get("content_fingerprint") or src.get("evidence_id"))
            sprefix = f"{eprefix}.{src['evidence_id']}"
            items.append(
                _item(
                    f"{sprefix}.relation",
                    "source_window_relation",
                    src.get("relation"),
                    pack=pack,
                    region=label,
                    source_ref=sref,
                )
            )
            if src.get("event_rms") is not None:
                items.append(
                    _item(
                        f"{sprefix}.event_rms",
                        "source_window_event_rms",
                        src["event_rms"],
                        pack=pack,
                        region=label,
                        source_ref=sref,
                        kind=EvidenceKind.MEASUREMENT,
                    )
                )
            if src.get("relative_drop_db") is not None:
                items.append(
                    _item(
                        f"{sprefix}.relative_drop_db",
                        "source_window_relative_drop_db",
                        src["relative_drop_db"],
                        pack=pack,
                        region=label,
                        source_ref=sref,
                        kind=EvidenceKind.MEASUREMENT,
                        unit="dB",
                    )
                )
    _assert_factual([item.model_dump(mode="json") for item in items])
    return items


def build_child_pack(parent: EvidencePack, added: list[EvidenceItem]) -> EvidencePack:
    limitations = list(parent.limitations)
    limitations.append(
        ObservationLimitation(
            code="ACTIVE_SOURCE_REGION_ANALYSIS_V1",
            detail=(
                "Source-local Post Mixer windows around frozen Main events. "
                "Captured-source quiet ≠ whole-mix rest. MIDI present ≠ a device cause."
            ),
            precision_ms=float(parent.alignment_envelope_ms),
            capability_ms=[float(parent.alignment_envelope_ms)],
        )
    )
    limitations.append(
        ObservationLimitation(
            code="CROSS_SOURCE_ALIGNMENT_LIMITED",
            detail="Cross-source audio relation remains LIMITED ±52 ms. No sample-accurate coincidence.",
            precision_ms=float(parent.alignment_envelope_ms),
            capability_ms=[float(parent.alignment_envelope_ms)],
        )
    )
    existing = {item.evidence_id for item in parent.items}
    extra = [item for item in added if item.evidence_id not in existing]
    return parent.model_copy(
        update={
            "pack_id": f"pack_{uuid4().hex[:12]}",
            "items": list(parent.items) + extra,
            "limitations": limitations,
        }
    )


def _question_for_event(event: dict[str, Any], *, target_id: str) -> dict[str, Any]:
    sources = event.get("sources") or []
    cov = event.get("coverage") or {}
    producing = [
        {
            "evidence_id": row["evidence_id"],
            "content_fingerprint": row.get("content_fingerprint"),
            "relation": row.get("relation"),
            "arrangement_active": bool(row.get("arrangement_active")),
        }
        for row in sources
        if row.get("relation") == SOURCE_HAS_SIGNAL
    ]
    target = next(
        (row for row in sources if row.get("content_fingerprint") == target_id),
        None,
    )
    return {
        "event_id": event["event_id"],
        "kind": event.get("kind"),
        "start_qn": event.get("start_qn"),
        "ACTIVE_SOURCE_COUNT": cov.get("ACTIVE_SOURCE_COUNT"),
        "CAPTURED_ACTIVE_SOURCE_COUNT": cov.get("CAPTURED_ACTIVE_SOURCE_COUNT"),
        "UNCAPTURED_ACTIVE_SOURCE_COUNT": cov.get("UNCAPTURED_ACTIVE_SOURCE_COUNT"),
        "four_active_sources_all_captured": cov.get("ACTIVE_SOURCE_COUNT") == cov.get(
            "CAPTURED_ACTIVE_SOURCE_COUNT"
        )
        and (cov.get("ACTIVE_SOURCE_COUNT") or 0) > 0,
        "captured_sources_with_signal": producing,
        "labels": event.get("labels"),
        "target_source": None
        if target is None
        else {
            "relation": target.get("relation"),
            "signal_class": target.get("signal_class"),
            "event_rms": target.get("event_rms"),
        },
        "note": (
            "Incomplete captured-source coverage cannot be generalized to the whole mix. "
            "MIDI present with source near-silent does not establish a device cause."
        ),
    }


def collect_source_windows(
    *,
    parent_path: Path | None = None,
    capture_roots: list[Path] | None = None,
    journal_dir: Path | None = None,
) -> dict[str, Any]:
    parent_path = Path(parent_path or PARENT_PACK_PATH)
    parent_bytes = parent_path.read_bytes()
    parent_file_sha = hashlib.sha256(parent_bytes).hexdigest()
    parent = load_persisted_pack(parent_path)
    region = pack_region(parent)
    envelope_s = float(parent.alignment_envelope_ms) / 1000.0
    report: dict[str, Any] = {
        "milestone": MILESTONE,
        "ts": now_iso(),
        "parent_pack_id": parent.pack_id,
        "parent_payload_sha256": pack_payload_hash(parent),
        "parent_file_sha256": parent_file_sha,
        "parent_path": str(parent_path),
        "NO LIVE": True,
        "NO RECAPTURE": True,
        "NO MIDI WRITE": True,
        "NO MUSICAL WRITE": True,
        "NO NEW MIDI READ": True,
        "NO ROUTING READ": True,
        "MUSICAL WRITES": 0,
        "alignment_claim": parent.alignment_claim,
        "alignment_envelope_ms": parent.alignment_envelope_ms,
    }
    captures = pack_source_captures(parent)
    clips = clip_index(parent)
    target_id = target_source_id(parent)
    provenances: list[dict[str, Any]] = []
    located: dict[str, dict[str, Any]] = {}
    for capture in captures:
        digest = capture["declared_sha256"]
        wav = locate_wav_by_sha256(digest, capture_roots)
        journal = journal_for_source_hash(digest, journal_dir=journal_dir)
        ref = load_persisted_ref(capture["content_fingerprint"], journal_dir=journal_dir)
        ok = wav is not None and bool(digest) and sha256_file(wav) == digest
        info = None
        if ok and wav is not None:
            meta = sf.info(str(wav))
            info = {
                "duration_s": float(meta.duration),
                "sample_rate": int(meta.samplerate),
                "channels": int(meta.channels),
            }
        row = {
            **capture,
            "ok": ok,
            "wav_path": None if wav is None else str(wav),
            "sha256": None if wav is None else sha256_file(wav),
            "hash_match": ok,
            "journal_status": journal.get("status"),
            "journal_verified": journal.get("verified"),
            "pass_id": journal.get("pass_id"),
            "locator_name": None if ref is None else ref.name,
            "name_is_locator_only": True,
            "persistent_object_ref": None if ref is None else ref.model_dump(mode="json"),
            "wav_info": info,
            "error": None if ok else ("SOURCE_WAV_MISSING" if wav is None else "HASH_MISMATCH"),
        }
        provenances.append(row)
        located[capture["evidence_id"]] = row
    captured_fps = {row["content_fingerprint"] for row in provenances if row.get("ok")}
    locator_to_fp = captured_locator_index(provenances)
    events = [event for event in load_region_events(parent) if event.get("relevant")]
    event_rows: list[dict[str, Any]] = []
    for event in events:
        context = arrangement_context(parent, event["event_id"])
        identities = list(context.get("active_clip_identities") or [])
        coverage = coverage_for_event(
            active_identities=identities,
            clips=clips,
            captured_fingerprints=captured_fps,
            locator_to_fingerprint=locator_to_fp,
        )
        active_captured_fps = {
            str(row.get("content_fingerprint"))
            for row in coverage.get("captured_active") or []
            if row.get("content_fingerprint")
        }
        sources: list[dict[str, Any]] = []
        for capture in provenances:
            base = {
                "evidence_id": capture["evidence_id"],
                "content_fingerprint": capture["content_fingerprint"],
                "locator_name": capture.get("locator_name"),
                "arrangement_active": capture["content_fingerprint"] in active_captured_fps,
            }
            if not capture.get("ok"):
                sources.append({**base, "relation": SOURCE_EVIDENCE_UNAVAILABLE})
                continue
            stats = analyze_source_event_window(
                Path(capture["wav_path"]),
                event_audio_start_s=float(event["start_s"]),
                event_audio_end_s=float(event["end_s"]),
                envelope_s=envelope_s,
            )
            sources.append({**base, **stats})
        label_relations = [
            row["relation"]
            for row in sources
            if row.get("arrangement_active")
        ] or [row["relation"] for row in sources]
        labels = classify_event_sources(
            label_relations,
            incomplete=bool(coverage["incomplete"]),
        )
        start_qn = context.get("start_qn")
        end_qn = context.get("end_qn")
        event_rows.append(
            {
                "event_id": event["event_id"],
                "kind": event["kind"],
                "start_s": event["start_s"],
                "end_s": event["end_s"],
                "start_qn": start_qn,
                "end_qn": end_qn,
                "sources": sources,
                "coverage": coverage,
                "labels": labels,
                "alignment_envelope_s": envelope_s,
            }
        )
    by_id = {row["event_id"]: row for row in event_rows}
    questions = {
        "A": _question_for_event(by_id.get("fm.event.3") or {"event_id": "fm.event.3", "sources": [], "coverage": {}}, target_id=target_id),
        "B": _question_for_event(by_id.get("fm.event.4") or {"event_id": "fm.event.4", "sources": [], "coverage": {}}, target_id=target_id),
        "C": _question_for_event(by_id.get("fm.event.5") or {"event_id": "fm.event.5", "sources": [], "coverage": {}}, target_id=target_id),
    }
    if by_id.get("fm.event.3") and by_id.get("fm.event.4"):
        questions["B"]["repeated_multi_source_loss"] = (
            MULTIPLE_SOURCES_DIP in (by_id["fm.event.3"].get("labels") or [])
            and MULTIPLE_SOURCES_DIP in (by_id["fm.event.4"].get("labels") or [])
        )
    added = source_evidence_items(
        pack=parent,
        region=region,
        provenances=provenances,
        event_rows=event_rows,
        questions=questions,
        envelope_s=envelope_s,
    )
    child = build_child_pack(parent, added)
    after_parent = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    report.update(
        {
            "status": "VERIFIED",
            MILESTONE: "PACK_READY",
            "new_pack_id": child.pack_id,
            "new_payload_sha256": pack_payload_hash(child),
            "provenances": provenances,
            "event_rows": event_rows,
            "QUESTIONS": questions,
            "parent_file_unchanged": after_parent == parent_file_sha,
            "alignment_claim": child.alignment_claim,
            "alignment_envelope_ms": child.alignment_envelope_ms,
            "pack": child.model_dump(mode="json"),
        }
    )
    return report


def _next_evidence_request(
    accepted: bool,
    status: str,
    requested: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    if not accepted or status != DiagnosisStatus.INSUFFICIENT_EVIDENCE.value:
        return None, None
    kinds = [str(item.get("request_kind") or "") for item in requested]
    mapped: list[str] = []
    explanation: dict[str, Any] = {}
    if "READ_MIDI" in kinds:
        mapped.append("READ_MIDI_OTHER_SOURCES")
        explanation["READ_MIDI_OTHER_SOURCES"] = (
            "Note content on contributing tracks that were arrangement-active but not note-read."
        )
    if "READ_ROUTING" in kinds:
        mapped.append("READ_ROUTING")
        explanation["READ_ROUTING"] = (
            "Path from an inspected source to Main before treating low Post Mixer level as a device cause."
        )
    if "CAPTURE_VIEW" in kinds:
        mapped.append("CAPTURE_VIEW")
        explanation["CAPTURE_VIEW"] = (
            "Devices, clips, automation, and causal state of a source that has MIDI or clips while audio is quiet."
        )
    if "ANALYZE_REGION" in kinds:
        mapped.append("ANALYZE_REGION")
        explanation["ANALYZE_REGION"] = "Exact request after source-window evidence was added."
    if not mapped:
        if requested:
            return json.dumps(requested, ensure_ascii=False), explanation or None
        return None, None
    return "+".join(mapped), explanation


def replay_source_windows(
    *,
    evidence: Path | None = None,
    parent_path: Path | None = None,
    capture_roots: list[Path] | None = None,
    journal_dir: Path | None = None,
    provider: ReasoningProvider | None = None,
    timeout_s: float = ASTRA_TIMEOUT_S,
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    built = collect_source_windows(
        parent_path=parent_path,
        capture_roots=capture_roots,
        journal_dir=journal_dir,
    )
    if built.get("status") == "BLOCKED":
        _persist(built, evidence)
        return built
    pack = EvidencePack.model_validate(built["pack"])
    http = provider if provider is not None else configured_http_provider()
    if http is None:
        built["status"] = "BLOCKED"
        built[MILESTONE] = "BLOCKED"
        built["BLOCKER"] = "ASTRA_NOT_CONFIGURED"
        _persist(built, evidence)
        return built
    recorder = RecordingProvider(http)
    result = reason(pack, recorder, timeout_s=timeout_s)
    raw = recorder.raws[-1] if recorder.raws else ""
    parsed, parse_error = _parse_output(raw) if raw else (result.output, None)
    applied = apply_reasoning_result(result)
    classification = classify_rejection(
        accepted=result.accepted,
        failure=None if result.failure is None else result.failure.value,
        parse_error=parse_error,
        issues=list(result.audit.issues),
        output=result.output,
    )
    requested = (
        [item.model_dump(mode="json") for item in result.output.requested_evidence]
        if result.output is not None
        else []
    )
    astra_status = (
        result.output.status.value
        if result.accepted and result.output is not None
        else applied["status"]
    )
    next_request, next_explain = _next_evidence_request(
        result.accepted, astra_status, requested
    )
    built.update(
        {
            "accepted": result.accepted,
            "ASTRA RESULT": astra_status,
            "parsed_result": None if parsed is None else parsed.model_dump(mode="json"),
            "parse_error": parse_error,
            "validator_result": {
                "accepted": result.accepted,
                "failure": None if result.failure is None else result.failure.value,
                "issues": result.audit.issues,
            },
            "rejection": classification,
            "gate": applied["gate"],
            "diagnosis": applied["diagnosis"],
            "reasoning_audit": applied["reasoning_audit"],
            "requested_evidence": requested,
            "NEXT_EVIDENCE_REQUEST": next_request,
            "NEXT_EVIDENCE_EXPLANATION": next_explain,
        }
    )
    if result.accepted:
        built["status"] = "VERIFIED"
        built[MILESTONE] = "VERIFIED"
    else:
        built["status"] = "BLOCKED"
        built[MILESTONE] = "BLOCKED"
        built["BLOCKER"] = classification.get("detail") or classification.get("label")
    _persist(built, evidence)
    return built


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    report["artifact"] = str(path)
    return path


def main() -> int:
    report = replay_source_windows()
    summary = {
        "milestone": MILESTONE,
        "parent_pack_id": report.get("parent_pack_id"),
        "new_pack_id": report.get("new_pack_id"),
        "QUESTIONS": report.get("QUESTIONS"),
        "accepted": report.get("accepted"),
        "ASTRA RESULT": report.get("ASTRA RESULT"),
        "NEXT_EVIDENCE_REQUEST": report.get("NEXT_EVIDENCE_REQUEST"),
        MILESTONE: report.get(MILESTONE),
        "parent_file_unchanged": report.get("parent_file_unchanged"),
        "NO NEW MIDI READ": True,
        "NO ROUTING READ": True,
        "NO RECAPTURE": True,
        "NO LIVE": True,
        "MUSICAL WRITES": 0,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get(MILESTONE) == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
