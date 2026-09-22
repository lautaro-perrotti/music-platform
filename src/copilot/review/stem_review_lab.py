"""Local blind listening laboratory for the real stem benchmark.

The server deliberately keeps the private candidate mapping in memory.  The
listener API exposes only opaque candidate IDs and allowlisted audio routes;
provider/model identity becomes available only after explicit finalization and
reveal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import argparse
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import numpy as np
import pyloudnorm as pyln
import soundfile as sf


DEFAULT_ROOT = Path(os.environ.get("STEM_BENCHMARK_ROOT", "stem-benchmark"))
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8791
REVIEW_DIR_NAME = "reviews"
PUBLIC_ROLES = ("drums", "bass", "other", "vocals")
REQUIRED_ROLES = ("drums", "bass", "other")
REGIONS = {
    "full": None,
    "region_1": (8.0, 16.0),
    "region_2": (48.0, 64.0),
    "region_3": (80.0, 96.0),
    "region_4": (104.0, 116.0),
}
DIMENSIONS = {
    "drums": ("isolation_purity", "completeness", "transient_integrity", "artifact_severity", "low_end_contamination", "harmonic_contamination"),
    "bass": ("isolation_purity", "completeness", "note_continuity", "kick_contamination", "harmonic_contamination", "artifact_severity"),
    "other": ("isolation_purity", "keyboard_completeness", "bass_contamination", "drum_contamination", "tonal_stability", "tail_naturalness", "artifact_severity"),
    "vocals": ("isolation_purity", "completeness", "artifact_severity", "tonal_stability"),
}
DECISIONS = ("PREFERRED", "USABLE", "REFERENCE_ONLY", "REJECTED", "UNREVIEWED")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _db(value: float) -> float | None:
    if value <= 1e-12 or not math.isfinite(value):
        return None
    return 20.0 * math.log10(value)


def compute_audio_metrics(path: Path) -> dict[str, Any]:
    """Compute factual metrics only; no separation-quality judgement."""
    info = sf.info(str(path))
    audio, rate = sf.read(str(path), always_2d=True, dtype="float32")
    if audio.size == 0:
        return {
            "sample_rate": int(rate), "channels": int(info.channels), "frames": 0,
            "duration_s": 0.0, "lufs": None, "rms_dbfs": None, "peak_dbfs": None,
            "true_peak_dbfs": None, "crest_factor_db": None, "clipping": False,
            "silence_ratio": 1.0,
        }
    finite = np.isfinite(audio).all()
    if not finite:
        raise ValueError(f"NON_FINITE_AUDIO:{path}")
    values = audio.astype(np.float64, copy=False)
    rms = float(np.sqrt(np.mean(values * values)))
    peak = float(np.max(np.abs(values)))
    # 4x linear interpolation is a conservative true-peak diagnostic.  It is
    # not used to score candidates.
    true_peak = peak
    if len(values) > 1:
        from scipy.signal import resample_poly
        true_peak = float(np.max(np.abs(resample_poly(values, 4, 1, axis=0))))
    try:
        loudness = float(pyln.Meter(int(rate)).integrated_loudness(values))
        lufs = loudness if math.isfinite(loudness) else None
    except (ValueError, RuntimeError):
        lufs = None
    frame = max(1, int(rate * 0.1))
    frames = values[: len(values) - (len(values) % frame)].reshape(-1, frame, values.shape[1]) if len(values) >= frame else values[None, ...]
    frame_rms = np.sqrt(np.mean(frames * frames, axis=(1, 2)))
    silence_ratio = float(np.mean(frame_rms <= 10 ** (-60 / 20)))
    rms_db = _db(rms)
    peak_db = _db(peak)
    return {
        "sample_rate": int(rate),
        "channels": int(info.channels),
        "frames": int(info.frames),
        "duration_s": float(info.duration),
        "lufs": lufs,
        "rms_dbfs": rms_db,
        "peak_dbfs": peak_db,
        "true_peak_dbfs": _db(true_peak),
        "crest_factor_db": (peak_db - rms_db) if peak_db is not None and rms_db is not None else None,
        "clipping": bool(peak >= 1.0),
        "silence_ratio": silence_ratio,
    }


def constant_gain_db(metrics: dict[str, Any], target_lufs: float) -> float:
    """Return a gain-only LUFS target, capped before clipping."""
    lufs = metrics.get("lufs")
    if lufs is None:
        return 0.0
    requested = float(target_lufs) - float(lufs)
    peak = metrics.get("peak_dbfs")
    if peak is not None:
        requested = min(requested, -1.0 - float(peak))
    return float(requested)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


@dataclass(frozen=True)
class Candidate:
    public_id: str
    private_id: str
    raw_roles: dict[str, Path]
    latency_s: float | None
    runtime: str | None
    model: str | None
    technical_status: str | None


class ReviewLab:
    def __init__(self, root: Path = DEFAULT_ROOT, *, reviewer: str = "human") -> None:
        self.root = root.resolve()
        self.manifest_path = self.root / "manifests" / "benchmark_manifest.json"
        self.mapping_path = self.root / "manifests" / "private_candidate_mapping.json"
        self.blind_root = (self.root / "blind").resolve()
        self.review_root = self.root / REVIEW_DIR_NAME
        self.loudness_path = self.review_root / "loudness_analysis.json"
        self.session_path = self.review_root / "review_session.json"
        self.selection_path = self.review_root / "final_selection.json"
        self.lock = threading.RLock()
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.mapping = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        self.benchmark_id = str(self.manifest["benchmark_id"])
        if self.mapping.get("benchmark_id") != self.benchmark_id:
            raise ValueError("BENCHMARK_MAPPING_MISMATCH")
        self.candidates = self._load_candidates()
        self.analysis = self._load_or_measure()
        self.reviewer = reviewer
        self.review = self._load_review()
        self.waveform_cache: dict[tuple[str, str], list[float]] = {}

    def _load_candidates(self) -> dict[str, Candidate]:
        runs = {str(row["candidate_id"]): row for row in self.manifest.get("runs", [])}
        result: dict[str, Candidate] = {}
        for public_id, private_id in sorted(self.mapping["public_to_private"].items()):
            run = runs.get(private_id)
            if run is None:
                raise ValueError(f"MISSING_RUN:{private_id}")
            raw_roles = {str(row["role"]): Path(row["path"]).resolve() for row in run.get("roles", [])}
            for role, path in raw_roles.items():
                if not path.is_file():
                    raise FileNotFoundError(str(path))
            result[public_id] = Candidate(
                public_id=public_id,
                private_id=private_id,
                raw_roles=raw_roles,
                latency_s=run.get("latency_s"),
                runtime=(run.get("separator") or {}).get("runtime_revision"),
                model=(run.get("separator") or {}).get("model"),
                technical_status=run.get("technical_status"),
            )
        return result

    def _analysis_key(self, public_id: str, role: str) -> str:
        return f"{public_id}:{role}"

    def _load_or_measure(self) -> dict[str, Any]:
        if self.loudness_path.is_file():
            try:
                stored = json.loads(self.loudness_path.read_text(encoding="utf-8"))
                if stored.get("benchmark_id") == self.benchmark_id and stored.get("analysis_version") == "v2":
                    return stored
            except (OSError, json.JSONDecodeError):
                pass
        raw_metrics: dict[str, dict[str, Any]] = {}
        for public_id, candidate in self.candidates.items():
            for role, path in candidate.raw_roles.items():
                raw_metrics[self._analysis_key(public_id, role)] = compute_audio_metrics(path)
        targets: dict[str, float | None] = {}
        for role in PUBLIC_ROLES:
            values = [
                float(raw_metrics[self._analysis_key(public_id, role)]["lufs"])
                for public_id in self.candidates
                if raw_metrics[self._analysis_key(public_id, role)].get("lufs") is not None
            ]
            targets[role] = float(np.median(values)) if values else None
        rows: dict[str, Any] = {}
        for public_id, candidate in self.candidates.items():
            for role in candidate.raw_roles:
                raw = raw_metrics[self._analysis_key(public_id, role)]
                gain_db = float(self.mapping.get("gain_db_by_public_role", {}).get(self._analysis_key(public_id, role)) or 0.0)
                rows[self._analysis_key(public_id, role)] = {
                    "raw": raw,
                    "raw_sha256": sha256_file(candidate.raw_roles[role]),
                    "raw_path_private": True,
                    "target_lufs_reference": targets[role],
                    "matched_gain_db": gain_db,
                    "matched_peak_dbfs": (raw.get("peak_dbfs") + gain_db) if raw.get("peak_dbfs") is not None else None,
                    "matched_true_peak_dbfs": (raw.get("true_peak_dbfs") + gain_db) if raw.get("true_peak_dbfs") is not None else None,
                    "method": "existing benchmark blind derivative; constant gain only; RMS-median target",
                }
        payload = {"analysis_version": "v2", "benchmark_id": self.benchmark_id, "created_at": now_iso(), "roles": list(PUBLIC_ROLES), "entries": rows}
        _atomic_json(self.loudness_path, payload)
        return payload

    def _load_review(self) -> dict[str, Any]:
        if self.session_path.is_file():
            try:
                payload = json.loads(self.session_path.read_text(encoding="utf-8"))
                if payload.get("benchmark_id") == self.benchmark_id:
                    return payload
            except (OSError, json.JSONDecodeError):
                pass
        payload = {
            "benchmark_id": self.benchmark_id,
            "review_id": uuid4().hex,
            "reviewer": self.reviewer,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "finalized_at": None,
            "revealed_at": None,
            "votes": {},
            "pairwise": {},
            "kept_by_role": {},
        }
        _atomic_json(self.session_path, payload)
        return payload

    def _save_review(self) -> None:
        self.review["updated_at"] = now_iso()
        _atomic_json(self.session_path, self.review)

    def _vote_key(self, role: str, public_id: str) -> str:
        return f"{role}:{public_id}"

    def vote(self, role: str, public_id: str, dimensions: dict[str, Any], decision: str, notes: str) -> dict[str, Any]:
        with self.lock:
            self._check_candidate_role(role, public_id)
            if self.review.get("finalized_at"):
                raise ValueError("REVIEW_FINALIZED")
            expected = set(DIMENSIONS[role])
            clean: dict[str, int] = {}
            for key, value in dimensions.items():
                if key not in expected:
                    raise ValueError(f"UNKNOWN_DIMENSION:{key}")
                number = int(value)
                if number < 1 or number > 5:
                    raise ValueError(f"DIMENSION_OUT_OF_RANGE:{key}")
                clean[key] = number
            if decision not in DECISIONS:
                raise ValueError("INVALID_DECISION")
            self.review["votes"][self._vote_key(role, public_id)] = {
                "role": role,
                "candidate_id": public_id,
                "dimensions": clean,
                "decision": decision,
                "notes": str(notes or "")[:4000],
                "updated_at": now_iso(),
            }
            self._save_review()
            return self.public_state()

    def pairwise(self, role: str, candidate_a: str, candidate_b: str, choice: str) -> dict[str, Any]:
        with self.lock:
            self._check_candidate_role(role, candidate_a)
            self._check_candidate_role(role, candidate_b)
            if candidate_a == candidate_b:
                raise ValueError("PAIR_REQUIRES_TWO_CANDIDATES")
            if self.review.get("finalized_at"):
                raise ValueError("REVIEW_FINALIZED")
            if choice not in ("A_BETTER", "B_BETTER", "TOO_CLOSE"):
                raise ValueError("INVALID_PAIRWISE_CHOICE")
            self.review.setdefault("pairwise", {})[f"{role}:{candidate_a}:{candidate_b}"] = {
                "role": role, "candidate_a": candidate_a, "candidate_b": candidate_b,
                "choice": choice, "updated_at": now_iso(),
            }
            self._save_review()
            return self.public_state()

    def keep(self, role: str, candidate_id: str) -> dict[str, Any]:
        with self.lock:
            self._check_candidate_role(role, candidate_id)
            if self.review.get("finalized_at"):
                raise ValueError("REVIEW_FINALIZED")
            self.review.setdefault("kept_by_role", {})[role] = candidate_id
            self._save_review()
            return self.public_state()

    def complete(self) -> bool:
        votes = self.review.get("votes", {})
        for role in REQUIRED_ROLES:
            for public_id in self.candidates:
                vote = votes.get(self._vote_key(role, public_id))
                if not vote or vote.get("decision") in (None, "UNREVIEWED"):
                    return False
                if set(vote.get("dimensions", {})) != set(DIMENSIONS[role]):
                    return False
        return True

    def finalize(self) -> dict[str, Any]:
        with self.lock:
            if not self.complete():
                raise ValueError("REVIEW_INCOMPLETE")
            if not self.review.get("finalized_at"):
                self.review["finalized_at"] = now_iso()
                self._save_review()
            self._write_selection(revealed=False)
            return self.public_state()

    def reveal(self) -> dict[str, Any]:
        with self.lock:
            if not self.review.get("finalized_at"):
                raise ValueError("FINALIZE_REQUIRED")
            if not self.review.get("revealed_at"):
                self.review["revealed_at"] = now_iso()
                self._save_review()
            self._write_selection(revealed=True)
            return self.public_state(include_reveal=True)

    def _write_selection(self, *, revealed: bool) -> None:
        selected: dict[str, str | None] = {}
        usable: dict[str, list[str]] = {}
        for role in REQUIRED_ROLES:
            votes = [self.review["votes"].get(self._vote_key(role, cid), {}) for cid in self.candidates]
            preferred = [v.get("candidate_id") for v in votes if v.get("decision") == "PREFERRED"]
            usable[role] = [v.get("candidate_id") for v in votes if v.get("decision") in ("PREFERRED", "USABLE")]
            selected[role] = preferred[0] if preferred else (usable[role][0] if usable[role] else None)
        payload: dict[str, Any] = {
            "benchmark_id": self.benchmark_id,
            "review_id": self.review["review_id"],
            "reviewer": self.review["reviewer"],
            "status": "REVEALED" if revealed else "BLIND_FINALIZED",
            "selected_by_role": selected,
            "usable_by_role": usable,
            "kept_by_role": self.review.get("kept_by_role", {}),
            "votes": self.review["votes"],
            "created_at": self.review["created_at"],
            "finalized_at": self.review.get("finalized_at"),
            "revealed_at": self.review.get("revealed_at"),
        }
        if revealed:
            payload["revealed_candidates"] = {
                public_id: {
                    "private_id": candidate.private_id,
                    "model": candidate.model,
                    "runtime": candidate.runtime,
                    "latency_s": candidate.latency_s,
                    "technical_status": candidate.technical_status,
                    "raw_assets": {
                        role: {"path": str(path), "sha256": sha256_file(path)}
                        for role, path in candidate.raw_roles.items()
                    },
                }
                for public_id, candidate in self.candidates.items()
            }
        _atomic_json(self.selection_path, payload)

    def _check_candidate_role(self, role: str, public_id: str) -> None:
        if role not in PUBLIC_ROLES or public_id not in self.candidates:
            raise ValueError("UNKNOWN_CANDIDATE_OR_ROLE")
        if role not in self.candidates[public_id].raw_roles:
            raise ValueError("ROLE_MISSING")

    def _matched_path(self, public_id: str, role: str, region: str) -> Path:
        self._check_candidate_role(role, public_id)
        if region == "full":
            path = self.blind_root / role / f"{public_id}.wav"
        elif region in REGIONS:
            path = self.blind_root / role / f"{public_id}_excerpt_{int(region.rsplit('_', 1)[1]):02d}.wav"
        else:
            raise ValueError("UNKNOWN_REGION")
        path = path.resolve()
        if self.blind_root not in path.parents or not path.is_file():
            raise FileNotFoundError("PUBLIC_AUDIO_MISSING")
        return path

    def audio_bytes(self, public_id: str, role: str, mode: str, region: str) -> tuple[bytes, str]:
        self._check_candidate_role(role, public_id)
        if mode == "matched":
            path = self._matched_path(public_id, role, region)
            return path.read_bytes(), "audio/wav"
        if mode != "raw" or region not in REGIONS:
            raise ValueError("INVALID_AUDIO_MODE")
        path = self.candidates[public_id].raw_roles[role]
        bounds = REGIONS[region]
        if bounds is None:
            return path.read_bytes(), "audio/wav"
        info = sf.info(str(path))
        start = int(bounds[0] * info.samplerate)
        stop = min(info.frames, int(bounds[1] * info.samplerate))
        audio, rate = sf.read(str(path), start=start, stop=stop, always_2d=True, dtype="float32")
        buf = BytesIO()
        sf.write(buf, audio, rate, format="WAV", subtype="PCM_16")
        return buf.getvalue(), "audio/wav"

    def waveform(self, role: str, region: str, points: int = 180) -> list[float]:
        if role not in PUBLIC_ROLES or region not in REGIONS:
            raise ValueError("UNKNOWN_ROLE_OR_REGION")
        key = (role, region)
        if key in self.waveform_cache:
            return self.waveform_cache[key]
        levels: list[np.ndarray] = []
        for public_id in self.candidates:
            path = self._matched_path(public_id, role, region)
            audio, _ = sf.read(str(path), always_2d=True, dtype="float32")
            mono = np.max(np.abs(audio), axis=1) if audio.size else np.zeros(1, dtype=np.float32)
            count = min(points, max(1, len(mono)))
            edges = np.linspace(0, len(mono), count + 1, dtype=int)
            envelope = np.asarray([
                float(np.max(mono[edges[i]:max(edges[i + 1], edges[i] + 1)]))
                for i in range(count)
            ])
            levels.append(envelope)
        result = np.mean(np.stack(levels), axis=0).tolist() if levels else [0.0] * points
        peak = max(result) if result else 0.0
        if peak > 0:
            result = [min(1.0, value / peak) for value in result]
        self.waveform_cache[key] = result
        return result

    def public_state(self, *, include_reveal: bool = False) -> dict[str, Any]:
        votes = self.review.get("votes", {})
        candidates = []
        for public_id in sorted(self.candidates):
            row: dict[str, Any] = {"id": public_id, "label": public_id.replace("candidate_", "Candidate ").upper()}
            row["roles"] = [role for role in PUBLIC_ROLES if role in self.candidates[public_id].raw_roles]
            row["metrics"] = {role: self.analysis.get("entries", {}).get(self._analysis_key(public_id, role), {}) for role in row["roles"]}
            row["reviewed"] = {role: self._vote_key(role, public_id) in votes for role in REQUIRED_ROLES}
            candidates.append(row)
        payload: dict[str, Any] = {
            "ok": True,
            "benchmark_id": self.benchmark_id,
            "title": "RHYTHM ASHANTI — STEM REVIEW LAB",
            "roles": list(PUBLIC_ROLES),
            "required_roles": list(REQUIRED_ROLES),
            "regions": list(REGIONS),
            "context_mode": "NOT_IMPLEMENTED_V1",
            "candidate_count": len(candidates),
            "candidates": candidates,
            "votes": votes,
            "pairwise": self.review.get("pairwise", {}),
            "kept_by_role": self.review.get("kept_by_role", {}),
            "quick_reviewed": {
                role: sum(
                    1 for cid in self.candidates
                    if self._vote_key(role, cid) in votes
                    or any(
                        item.get("role") == role and cid in (item.get("candidate_a"), item.get("candidate_b"))
                        for item in self.review.get("pairwise", {}).values()
                    )
                )
                for role in REQUIRED_ROLES
            },
            "region_bounds": {key: list(value) if value else None for key, value in REGIONS.items()},
            "complete": self.complete(),
            "finalized": bool(self.review.get("finalized_at")),
            "revealed": bool(self.review.get("revealed_at")),
            "musical_writes": 0,
            "ableton_import": "HOLD",
        }
        if include_reveal and self.review.get("revealed_at"):
            payload["revealed_candidates"] = {
                public_id: {
                    "private_id": candidate.private_id,
                    "model": candidate.model,
                    "runtime": candidate.runtime,
                    "latency_s": candidate.latency_s,
                    "technical_status": candidate.technical_status,
                }
                for public_id, candidate in self.candidates.items()
            }
        return payload


class LabHandler(BaseHTTPRequestHandler):
    server_version = "StemReviewLab/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    @property
    def lab(self) -> ReviewLab:
        return self.server.lab  # type: ignore[attr-defined]

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def _static(self, name: str) -> None:
        target = Path(__file__).parent / "static" / name
        if target.parent != (Path(__file__).parent / "static").resolve() or not target.is_file():
            self.send_error(404)
            return
        raw = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                return self._static("index.html")
            if parsed.path == "/api/state":
                return self._json(200, self.lab.public_state(include_reveal=bool(self.lab.review.get("revealed_at"))))
            if parsed.path == "/api/audio":
                query = parse_qs(parsed.query)
                candidate = query.get("candidate", [""])[0]
                role = query.get("role", [""])[0]
                mode = query.get("mode", ["matched"])[0]
                region = query.get("region", ["full"])[0]
                direct_path: Path | None = None
                if mode == "matched":
                    direct_path = self.lab._matched_path(candidate, role, region)
                elif mode == "raw" and region == "full":
                    self.lab._check_candidate_role(role, candidate)
                    direct_path = self.lab.candidates[candidate].raw_roles[role]
                if direct_path is not None:
                    total = direct_path.stat().st_size
                    range_header = self.headers.get("Range")
                    start, end, status = 0, total - 1, 200
                    if range_header:
                        try:
                            unit, value = range_header.split("=", 1)
                            if unit != "bytes" or "," in value:
                                raise ValueError
                            left, right = value.split("-", 1)
                            start = int(left) if left else max(0, total - int(right))
                            end = int(right) if right else end
                            end = min(end, total - 1)
                            if start < 0 or start > end or start >= total:
                                raise ValueError
                            status = 206
                        except (ValueError, TypeError):
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{total}")
                            self.end_headers()
                            return
                    with direct_path.open("rb") as handle:
                        handle.seek(start)
                        data = handle.read(end - start + 1)
                    content_type = "audio/wav"
                else:
                    data, content_type = self.lab.audio_bytes(candidate, role, mode, region)
                    total = len(data)
                    start, end, status = 0, total - 1, 200
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.path == "/api/waveform":
                query = parse_qs(parsed.query)
                role = query.get("role", [""])[0]
                region = query.get("region", ["full"])[0]
                return self._json(200, {"ok": True, "role": role, "region": region, "points": self.lab.waveform(role, region)})
            self.send_error(404)
        except (ValueError, FileNotFoundError, KeyError) as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # pragma: no cover - server guard
            self._json(500, {"ok": False, "error": type(exc).__name__})

    def do_POST(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            body = self._body()
            if path == "/api/vote":
                payload = self.lab.vote(str(body.get("role")), str(body.get("candidate_id")), dict(body.get("dimensions") or {}), str(body.get("decision")), str(body.get("notes") or ""))
                return self._json(200, payload)
            if path == "/api/pairwise":
                payload = self.lab.pairwise(str(body.get("role")), str(body.get("candidate_a")), str(body.get("candidate_b")), str(body.get("choice")))
                return self._json(200, payload)
            if path == "/api/keep":
                return self._json(200, self.lab.keep(str(body.get("role")), str(body.get("candidate_id"))))
            if path == "/api/finalize":
                return self._json(200, self.lab.finalize())
            if path == "/api/reveal":
                return self._json(200, self.lab.reveal())
            if path == "/api/export":
                if not self.lab.review.get("finalized_at"):
                    raise ValueError("FINALIZE_REQUIRED")
                self.lab._write_selection(revealed=bool(self.lab.review.get("revealed_at")))
                return self._json(200, {"ok": True, "path": str(self.lab.selection_path), "revealed": bool(self.lab.review.get("revealed_at"))})
            self.send_error(404)
        except ValueError as exc:
            self._json(409, {"ok": False, "error": str(exc)})
        except Exception as exc:  # pragma: no cover - server guard
            self._json(500, {"ok": False, "error": type(exc).__name__})


class LabServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], lab: ReviewLab) -> None:
        super().__init__(address, LabHandler)
        self.lab = lab


def serve(*, root: Path = DEFAULT_ROOT, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, reviewer: str = "human") -> LabServer:
    lab = ReviewLab(root, reviewer=reviewer)
    server = LabServer((host, port), lab)
    print(json.dumps({"ok": True, "url": f"http://{host}:{port}/", "benchmark_id": lab.benchmark_id, "candidates": len(lab.candidates), "ableton_import": "HOLD", "musical_writes": 0}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local blind stem review lab")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reviewer", default="human")
    args = parser.parse_args(argv)
    serve(root=args.root, host=args.host, port=args.port, reviewer=args.reviewer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
