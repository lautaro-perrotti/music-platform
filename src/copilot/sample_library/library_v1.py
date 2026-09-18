"""SAMPLE_LIBRARY_INTELLIGENCE_V1 — discovery, indexing, DSP, classification, retrieval.

Read-only. No Ableton writes. Reuses the project's existing hashing and (where
practical) analysis primitives; per-sample factual DSP is computed with
numpy/scipy/soundfile only (no CLAP/torch in V1).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from copilot.audio.file_hash import sha256_file
from copilot.human_eval.store import now_iso
from copilot.sample_library.schemas import (
    AssetStatus,
    AudioDescriptors,
    BpmEstimate,
    LibraryIndex,
    PitchEstimate,
    SampleAsset,
    SampleRole,
    SampleSetContext,
    SampleType,
    SearchResult,
)

MILESTONE = "SAMPLE_LIBRARY_INTELLIGENCE_V1"
SUPPORTED_EXTENSIONS = {".wav", ".aiff", ".aif", ".flac"}

# --- semantic role keyword vocabulary (filename/folder priors) ---
_ROLE_KEYWORDS: dict[SampleRole, list[str]] = {
    SampleRole.KICK: ["kick", "kik", "bd", "bassdrum", "bass drum"],
    SampleRole.SNARE: ["snare", "snr"],
    SampleRole.CLAP: ["clap", "clp"],
    SampleRole.RIM: ["rim", "rimshot"],
    SampleRole.CLOSED_HAT: ["closed hat", "chh", "closedhat", "hat cl"],
    SampleRole.OPEN_HAT: ["open hat", "ohh", "openhat", "hat op"],
    SampleRole.RIDE: ["ride", "crash", "cymbal"],
    SampleRole.SHAKER: ["shaker", "cabasa", "tambourine", "maraca"],
    SampleRole.PERCUSSION: ["perc", "conga", "bongo", "djembe", "tamb", "cowbell", "clave", "woodblock"],
    SampleRole.TOP_LOOP: ["top loop", "toploop", "top_loop", "tops", "hat loop", "perc loop"],
    SampleRole.DRUM_LOOP: ["drum loop", "drumloop", "drum_loop", "beat loop", "full loop"],
    SampleRole.BASS: ["bass", "sub", "808", "subbass", "low end"],
    SampleRole.VOCAL: ["vocal", "vox", "acapella", "acappella", "voice"],
    SampleRole.VOCAL_CHOP: ["vocal chop", "chop", "vox chop"],
    SampleRole.FX: ["fx", "effect", "sfx", "noise"],
    SampleRole.IMPACT: ["impact", "boom", "downer", "hit"],
    SampleRole.RISER: ["riser", "build", "buildup", "build up", "sweep up"],
    SampleRole.DOWNLIFTER: ["downlifter", "downlift", "fall", "sweep down"],
    SampleRole.TEXTURE: ["texture", "pad", "atmos", "ambient", "drone"],
    SampleRole.SYNTH: ["synth", "lead", "pluck", "arp"],
    SampleRole.CHORD: ["chord", "stab", "keys", "piano", "organ", "ep "],
    SampleRole.MELODY: ["melody", "hook", "riff"],
    SampleRole.AMBIENCE: ["ambience", "ambient", "atmos", "space", "air"],
}


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[_\-\.]+", " ", text)
    text = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", text)  # camelCase / digit boundaries
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def discover(root: Path) -> list[Path]:
    root = Path(root)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
            out.append(p)
    return sorted(out)


def _classify_role(filename: str, folder: str) -> tuple[SampleRole, float, list[str]]:
    fname = _normalize(filename)
    fold = _normalize(folder)
    best_role: SampleRole = SampleRole.UNKNOWN
    best_conf = 0.0
    best_ev: list[str] = []
    for role, kws in _ROLE_KEYWORDS.items():
        for kw in kws:
            # folder evidence is a stronger prior than filename
            if kw in fold:
                conf = 0.75
                ev = f"folder contains '{kw}'"
            elif kw in fname:
                conf = 0.65
                ev = f"filename contains '{kw}'"
            else:
                continue
            if conf > best_conf:
                best_role, best_conf, best_ev = role, conf, [ev]
            elif conf == best_conf and role != best_role:
                best_ev.append(ev)
                best_conf -= 0.05  # ambiguity penalty
    return best_role, round(best_conf, 3), best_ev


def _classify_type(duration_s: float | None, filename: str) -> tuple[SampleType, float, list[str]]:
    fn = _normalize(filename)
    if duration_s is None:
        return SampleType.UNKNOWN, 0.0, []
    if " loop" in f" {fn} " or fn.endswith(" loop"):
        return SampleType.LOOP, 0.7, ["filename contains 'loop'"]
    if duration_s <= 1.5:
        return SampleType.ONE_SHOT, 0.8, [f"short duration {duration_s:.2f}s"]
    if duration_s >= 4.0:
        return SampleType.LOOP, 0.5, [f"long duration {duration_s:.2f}s"]
    return SampleType.UNKNOWN, 0.0, []


def _extract_filename_bpm(filename: str) -> float | None:
    """Prefer strong BPM hints embedded in filenames (e.g. '...120 Eminor...')."""
    m = re.findall(r"\b(\d{2,3})\b", filename)
    for tok in m:
        v = int(tok)
        if 60 <= v <= 180:
            return float(v)
    return None


def _estimate_bpm(mono: np.ndarray, sr: int) -> BpmEstimate:
    """Energy-envelope autocorrelation BPM. Low confidence, honest."""
    if len(mono) < sr * 2:
        return BpmEstimate(value=None, confidence=None)
    hop = int(0.01 * sr)
    n = 1 + (len(mono) - hop) // hop
    if n < 128:
        return BpmEstimate(value=None, confidence=None)
    env = np.empty(n, dtype=np.float64)
    for i in range(n):
        env[i] = np.sqrt(np.mean(mono[i * hop : i * hop + hop] ** 2) + 1e-12)
    env = env - np.mean(env)
    ac = np.correlate(env, env, mode="full")[len(env) - 1 :]
    ac = ac / (ac[0] + 1e-12)
    # search 60..180 BPM in lag domain (hop == 0.01 s)
    lo = max(2, int((60.0 / 180.0) / 0.01))   # ~33 hops (180 BPM)
    hi = int((60.0 / 60.0) / 0.01)            # 100 hops (60 BPM)
    if hi <= lo + 2:
        return BpmEstimate(value=None, confidence=None)
    peak_lag = int(np.argmax(ac[lo:hi]) + lo)
    if peak_lag <= lo:
        return BpmEstimate(value=None, confidence=None)
    # half-tempo correction: strong half-period peak => double the BPM
    half_lag = peak_lag // 2
    if half_lag >= lo and ac[half_lag] > 0.5 * ac[peak_lag]:
        peak_lag = half_lag
    bpm = 60.0 / (peak_lag * 0.01)
    conf = float(ac[peak_lag])
    if not (60.0 <= bpm <= 180.0) or conf < 0.25:
        return BpmEstimate(value=None, confidence=None)
    return BpmEstimate(value=round(bpm, 1), confidence=round(min(conf, 0.6), 3))


def _dsp(path: Path) -> AudioDescriptors | None:
    try:
        data, sr = sf.read(str(path), always_2d=True)
    except Exception:
        return None
    if data.size == 0:
        return None
    audio = data.astype(np.float64)
    channels = audio.shape[1]
    mono = np.mean(audio, axis=1)
    duration = float(len(mono) / sr)
    rms = float(np.sqrt(np.mean(mono**2) + 1e-12))
    peak = float(np.max(np.abs(mono)) + 1e-12)
    crest = float(peak / (rms + 1e-12))

    # spectral centroid + band energies via FFT
    n_fft = min(len(mono), 8192)
    spec = np.abs(np.fft.rfft(mono, n=n_fft))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    total = float(np.sum(spec) + 1e-12)
    centroid = float(np.sum(freqs * spec) / total) if total > 0 else None
    def band(lo, hi):
        m = (freqs >= lo) & (freqs < hi)
        return float(np.sum(spec[m]) / total)
    low_e = band(20, 250)
    mid_e = band(250, 4000)
    high_e = band(4000, 16000)

    # transient strength via energy flux
    hop = max(1, int(0.005 * sr))
    win = max(hop * 2, int(0.02 * sr))
    nf = 1 + (len(mono) - win) // hop
    transient = None
    if nf > 3:
        env = np.array([np.sqrt(np.mean(mono[i * hop : i * hop + win] ** 2) + 1e-12) for i in range(nf)])
        flux = np.diff(env)
        transient = float(np.percentile(np.maximum(flux, 0.0), 95)) if len(flux) else None

    # stereo width
    stereo_width = None
    if channels >= 2:
        mid = (audio[:, 0] + audio[:, 1]) / 2.0
        side = (audio[:, 0] - audio[:, 1]) / 2.0
        stereo_width = float(np.sqrt(np.mean(side**2)) / (np.sqrt(np.mean(mid**2)) + 1e-12))

    # silence ratio
    silence_ratio = None
    if nf > 3:
        env = np.array([np.sqrt(np.mean(mono[i * hop : i * hop + win] ** 2) + 1e-12) for i in range(nf)])
        silence_ratio = float(np.mean(env < (0.05 * max(rms, 1e-12))))

    return AudioDescriptors(
        duration_s=duration,
        sample_rate=sr,
        channels=channels,
        rms=round(rms, 6),
        peak=round(peak, 6),
        crest_factor=round(crest, 3),
        spectral_centroid_hz=round(centroid, 1) if centroid is not None else None,
        low_band_energy=round(low_e, 4),
        mid_band_energy=round(mid_e, 4),
        high_band_energy=round(high_e, 4),
        transient_strength=round(transient, 6) if transient is not None else None,
        stereo_width=round(stereo_width, 4) if stereo_width is not None else None,
        silence_ratio=round(silence_ratio, 4) if silence_ratio is not None else None,
    )


def analyze_sample(path: Path, library_root: Path) -> SampleAsset:
    sha = sha256_file(path)
    st = path.stat()
    ext = path.suffix.lower()
    rel = path.relative_to(library_root).as_posix()
    folder = Path(rel).parent.as_posix()

    if sha is None:
        return SampleAsset(
            id=f"asset_{abs(hash(str(path))) % 10**12:012d}",
            path=str(path), filename=path.name, library_root=str(library_root),
            relative_path=rel, extension=ext, size_bytes=st.st_size, mtime_ns=st.st_mtime_ns,
            sha256="", status=AssetStatus.DECODE_FAILED, error="unhashable",
        )

    asset_id = f"asset_{sha[:12]}"
    descriptors = _dsp(path)
    if descriptors is None:
        return SampleAsset(
            id=asset_id, path=str(path), filename=path.name, library_root=str(library_root),
            relative_path=rel, extension=ext, size_bytes=st.st_size, mtime_ns=st.st_mtime_ns,
            sha256=sha, status=AssetStatus.DECODE_FAILED, error="decode failed",
        )

    role, role_conf, role_ev = _classify_role(path.name, folder)
    stype, stype_conf, stype_ev = _classify_type(descriptors.duration_s, path.name)
    bpm = BpmEstimate(value=None, confidence=None)
    fname_bpm = _extract_filename_bpm(path.name)
    if fname_bpm is not None:
        bpm = BpmEstimate(value=fname_bpm, confidence=0.8)
    elif stype == SampleType.LOOP and descriptors.duration_s and descriptors.duration_s >= 2.0:
        try:
            data, sr = sf.read(str(path), always_2d=True)
            bpm = _estimate_bpm(np.mean(data.astype(np.float64), axis=1), sr)
        except Exception:
            pass

    provenance = {"filename_evidence": role_ev, "type_evidence": stype_ev, "method": "deterministic-dsp-v1"}
    confidence = None
    if role_conf or stype_conf:
        confidence = round(max(role_conf, stype_conf), 3)

    return SampleAsset(
        id=asset_id,
        path=str(path),
        filename=path.name,
        library_root=str(library_root),
        relative_path=rel,
        extension=ext,
        size_bytes=st.st_size,
        mtime_ns=st.st_mtime_ns,
        sha256=sha,
        sample_type=stype,
        semantic_role=role,
        bpm=bpm,
        pitch=PitchEstimate(value=None, confidence=None),
        descriptors=descriptors,
        classification_confidence=confidence,
        provenance=provenance,
        status=AssetStatus.INDEXED,
    )


def load_index(index_path: Path) -> LibraryIndex | None:
    if not index_path.is_file():
        return None
    try:
        return LibraryIndex.model_validate(json.loads(index_path.read_text(encoding="utf-8")))
    except Exception:
        return None


def save_index(index: LibraryIndex, index_path: Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index.model_dump_json(indent=2), encoding="utf-8")


def index_library(roots: list[Path], index_path: Path, *, progress: Any = None) -> dict[str, Any]:
    existing = load_index(index_path) or LibraryIndex()
    now = now_iso()
    counts: dict[str, Any] = {
        "NEW": 0, "UNCHANGED": 0, "MODIFIED": 0, "MISSING": 0, "FAILED": 0,
        "duplicates": 0,
    }

    discovered: dict[str, Path] = {}
    for root in roots:
        for fp in discover(root):
            discovered[str(fp)] = fp

    # known path -> sha (primary path + duplicate paths), reconstructed from index
    known_sha_by_path: dict[str, str] = {}
    for sha, asset in existing.assets.items():
        known_sha_by_path[asset.path] = sha
    for sha, paths in existing.duplicates.items():
        for pth in paths:
            known_sha_by_path[pth] = sha

    assets: dict[str, SampleAsset] = {}
    paths_by_sha: dict[str, list[str]] = {}
    to_analyze: list[Path] = []

    for path_str, fp in discovered.items():
        sha = known_sha_by_path.get(path_str)
        if sha is not None:
            try:
                st = fp.stat()
            except OSError:
                counts["MISSING"] += 1
                continue
            prev = existing.assets.get(sha)
            if prev is not None and st.st_size == prev.size_bytes and st.st_mtime_ns == prev.mtime_ns:
                counts["UNCHANGED"] += 1
                assets.setdefault(sha, prev)
                paths_by_sha.setdefault(sha, []).append(path_str)
                continue
            counts["MODIFIED"] += 1
            to_analyze.append(fp)
        else:
            counts["NEW"] += 1
            to_analyze.append(fp)

    for fp in to_analyze:
        asset = analyze_sample(fp, _root_for(fp, roots))
        sha = asset.sha256
        if sha and asset.status == AssetStatus.INDEXED:
            assets[sha] = asset
        else:
            counts["FAILED"] += 1
            continue
        paths_by_sha.setdefault(sha, []).append(str(fp))

    # missing = known paths no longer on disk
    for path_str in known_sha_by_path:
        if path_str not in discovered:
            counts["MISSING"] += 1

    # duplicates: sha -> extra paths (beyond the primary path)
    duplicates: dict[str, list[str]] = {}
    for sha, paths in paths_by_sha.items():
        if len(paths) > 1:
            primary = assets[sha].path if sha in assets else paths[0]
            extras = [pt for pt in paths if pt != primary]
            if extras:
                duplicates[sha] = extras

    index = LibraryIndex(
        version=existing.version,
        analysis_version="sample-library-v1",
        roots=[str(r) for r in roots],
        assets=assets,
        duplicates=duplicates,
        created_at=existing.created_at or now,
        updated_at=now,
    )
    save_index(index, index_path)
    counts["total_indexed"] = len(assets)
    counts["duplicates"] = len(duplicates)
    return counts


def _root_for(p: Path, roots: list[Path]) -> Path:
    for r in roots:
        try:
            p.relative_to(r)
            return r
        except ValueError:
            continue
    return Path(p.parent.parent)


def search(
    index: LibraryIndex,
    query: str,
    *,
    role: SampleRole | None = None,
    bpm: float | None = None,
    bpm_tol: float = 5.0,
    top_k: int = 10,
) -> list[SearchResult]:
    q = _normalize(query)
    results: list[SearchResult] = []
    for asset in index.assets.values():
        if asset.status != AssetStatus.INDEXED:
            continue
        score = 0.0
        reasons: list[str] = []
        hay = _normalize(asset.filename + " " + asset.relative_path)
        for kw in q.split():
            if kw and kw in hay:
                score += 1.0
                reasons.append(f"token '{kw}' in filename/path")
        if role and asset.semantic_role == role:
            score += 3.0
            reasons.append(f"semantic_role == {role.value}")
        elif role:
            continue
        if role and asset.semantic_role == SampleRole.UNKNOWN:
            score += 0.0
        if bpm and asset.bpm.value:
            if abs(asset.bpm.value - bpm) <= bpm_tol:
                score += 2.0
                reasons.append(f"bpm {asset.bpm.value} within {bpm_tol} of {bpm}")
        if score <= 0:
            continue
        results.append(SearchResult(asset=asset, score=score, reasons=reasons))
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_k]


def build_sample_set_context(
    index: LibraryIndex,
    *,
    task_id: str,
    wanted_roles: list[SampleRole],
    per_role: int = 3,
    bpm: float | None = None,
) -> SampleSetContext:
    ctx = SampleSetContext(task_id=task_id)
    for role in wanted_roles:
        hits = search(index, "", role=role, bpm=bpm, top_k=per_role)
        role_list = []
        for h in hits:
            a = h.asset
            summary = {
                "id": a.id,
                "filename": a.filename,
                "relative_path": a.relative_path,
                "bpm": a.bpm.value,
                "duration_s": a.descriptors.duration_s,
                "centroid_hz": a.descriptors.spectral_centroid_hz,
                "confidence": a.classification_confidence,
            }
            role_list.append(summary)
            ctx.selection_reasons[a.id] = "; ".join(h.reasons)
        ctx.roles[role.value] = role_list
        ctx.candidates.extend(role_list)
    return ctx
