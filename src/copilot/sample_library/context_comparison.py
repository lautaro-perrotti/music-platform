"""Reproducible, non-mutating sample A/B evidence, not a semantic listening verdict."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from copilot.sample_library.schemas import LibraryIndex


def compare_shortlist(
    index: LibraryIndex, candidates: dict[str, list[dict[str, Any]]], *,
    authorized_root: Path, preview_root: Path, bpm: float,
) -> dict[str, list[dict[str, Any]]]:
    """Render same-bar, same-level previews and factual descriptors for each role.

    A one-shot plays once at the bar start; a loop repeats within the same bar.
    Context competition is a *spectral proxy*, not evidence that anyone heard
    the candidates together. Source bytes are checked before reading.
    """
    if not math.isfinite(bpm) or bpm <= 0:
        raise ValueError("PREVIEW_BPM_INVALID")
    root = authorized_root.resolve(strict=True)
    preview_root.mkdir(parents=True, exist_ok=True)
    bar_seconds = 240.0 / bpm
    enriched: dict[str, list[dict[str, Any]]] = {}
    for role, rows in candidates.items():
        enriched[role] = []
        for row in rows:
            digest = str(row["sha256"])
            asset = index.assets.get(digest)
            if asset is None:
                raise ValueError(f"PREVIEW_ASSET_UNINDEXED:{role}")
            source = Path(asset.path).resolve(strict=True)
            if not source.is_file() or not source.is_relative_to(root):
                raise ValueError(f"PREVIEW_ASSET_UNAUTHORIZED:{role}")
            if source.stat().st_size > 64 * 1024 * 1024:
                raise ValueError(f"PREVIEW_ASSET_TOO_LARGE:{role}")
            if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                raise ValueError(f"PREVIEW_ASSET_DIGEST_MISMATCH:{role}")
            with sf.SoundFile(source) as stream:
                sr = stream.samplerate
                audio = stream.read(
                    frames=max(1, math.ceil(sr * bar_seconds)),
                    always_2d=True, dtype="float32",
                )
            if sr <= 0 or not len(audio) or not np.isfinite(audio).all():
                raise ValueError(f"PREVIEW_AUDIO_INVALID:{role}")
            mono = audio.mean(axis=1)
            count = max(1, math.floor(sr * bar_seconds + 0.5))
            signal = np.zeros(count, dtype=np.float32)
            if asset.sample_type.value == "LOOP":
                signal[:] = np.resize(mono, count)
            else:
                signal[:min(count, len(mono))] = mono[:count]
            peak = float(np.max(np.abs(signal)))
            if peak <= 0:
                raise ValueError(f"PREVIEW_SAMPLE_SILENT:{role}")
            # Identical reference peak makes sample attacks comparable while
            # retaining each candidate's dynamic shape; this is not LUFS.
            signal *= 0.5 / peak
            name = f"{role.lower().replace(' ', '_')}-{digest[:16]}-{bpm:g}.wav"
            preview = preview_root / name
            sf.write(str(preview), signal, sr, subtype="PCM_24")
            descriptor = asset.descriptors
            enriched[role].append({
                **row,
                "preview": {
                    "path": str(preview),
                    "sha256": hashlib.sha256(preview.read_bytes()).hexdigest(),
                    "duration_s": count / sr, "sample_rate": sr,
                    "peak_target": 0.5,
                    "pattern": "bar_start_once" if asset.sample_type.value != "LOOP" else "one_bar_loop",
                    "semantic_listening": False,
                },
                "ab_facts": {
                    "source_sha256": digest,
                    "bpm_confidence": asset.bpm.confidence,
                    "pitch_confidence": asset.pitch.confidence,
                    "transient_strength": descriptor.transient_strength,
                    "crest_factor": descriptor.crest_factor,
                    "low_band_energy": descriptor.low_band_energy,
                    "spectral_centroid_hz": descriptor.spectral_centroid_hz,
                    "stereo_width": descriptor.stereo_width,
                    "duration_s": descriptor.duration_s,
                    "license": asset.provenance.get("license", "UNKNOWN"),
                },
                "ab_limitations": [
                    "A single bar at matched peak is not an audible mix-context comparison.",
                    "Spectral overlap and metadata cannot establish musical fit or rights.",
                ],
            })
    reference = enriched.get("Kick") or enriched.get("Bass") or []
    if reference:
        baseline = reference[0]
        baseline_audio, baseline_sr = sf.read(baseline["preview"]["path"], dtype="float32")
        for role, rows in enriched.items():
            if role == "Kick" and enriched.get("Bass"):
                context = enriched["Bass"][0]
                context_audio, context_sr = sf.read(context["preview"]["path"], dtype="float32")
            elif role == "Kick":
                continue
            else:
                context = baseline
                context_audio, context_sr = baseline_audio, baseline_sr
            for row in rows:
                candidate_audio, sr = sf.read(row["preview"]["path"], dtype="float32")
                background = resample_poly(context_audio, sr, context_sr)
                length = min(len(candidate_audio), len(background))
                composite = np.zeros(len(candidate_audio), dtype=np.float32)
                composite[:length] = (
                    0.5 * candidate_audio[:length] + 0.5 * background[:length]
                )
                frequencies = np.fft.rfftfreq(length, 1 / sr)
                band = frequencies < 150
                left = np.abs(np.fft.rfft(candidate_audio[:length]))[band]
                right = np.abs(np.fft.rfft(background[:length]))[band]
                denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
                row["ab_facts"]["context_low_band_overlap_proxy"] = (
                    round(float(np.dot(left, right)) / denominator, 4)
                    if denominator > 0 else None
                )
                output = preview_root / (
                    f"context-{role.lower().replace(' ', '_')}-"
                    f"{row['sha256'][:16]}-{context['sha256'][:16]}-{bpm:g}.wav"
                )
                sf.write(str(output), composite, sr, subtype="PCM_24")
                row["context_preview"] = {
                    "path": str(output),
                    "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                    "reference_sha256": context["sha256"],
                    "fixed_candidate_gain": 0.5,
                    "fixed_reference_gain": 0.5,
                    "semantic_listening": False,
                    "limitation": "Single-bar proxy; not an in-project Live capture.",
                }
    return enriched
