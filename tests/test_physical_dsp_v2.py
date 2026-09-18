from __future__ import annotations

from pathlib import Path

import pytest

from copilot.audio.fullmix import compute_fullmix_observation
from copilot.audio.lowend_features import compute_lowend_features
from copilot.audio.physical_dsp_v2.audio import buffer_from_samples
from copilot.audio.physical_dsp_v2.baseline import write_baseline
from copilot.audio.physical_dsp_v2.cache import cache_key
from copilot.audio.physical_dsp_v2.capability import register_physical_dsp_v2
from copilot.audio.physical_dsp_v2.contract import ANALYZER_VERSION, CAPABILITY_ID
from copilot.audio.physical_dsp_v2.fixtures import impulse_train, mixture, silence, sine, write_wav
from copilot.audio.physical_dsp_v2.judgment import assert_bundle_no_judgment
from copilot.audio.physical_dsp_v2.pipeline import analyze_buffer, analyze_pair, analyze_path
from copilot.audio.physical_dsp_v2.wrappers import audio_asset_from_wav, wrap_fullmix, wrap_lowend
from copilot.runtime.capabilities import build_registry
from copilot.runtime.registry import CapabilityRegistry
from copilot.schemas.dsp import (
    AnalyzerFamily,
    DspGranularity,
    DspObservation,
    DspQuality,
    DspSubject,
    DspSubjectKind,
    TimeSpan,
)
from copilot.schemas.evidence import EvidenceItem, EvidenceKind
from copilot.schemas.fullmix import EnergyEventKind


def _cache(tmp_path: Path) -> Path:
    return tmp_path / "pdsp_cache"


def test_common_contract_typed_observation(tmp_path: Path) -> None:
    buf = buffer_from_samples(sine(44100, 0.8, 440.0, 0.2), 44100)
    bundle = analyze_buffer(buf, use_cache=True, cache_dir=_cache(tmp_path))
    assert bundle.observations
    for obs in bundle.observations:
        assert isinstance(obs, DspObservation)
        assert obs.observation_id
        assert obs.analyzer_id
        assert obs.analyzer_version == ANALYZER_VERSION
        assert obs.subject
        assert obs.time_span.end_s >= obs.time_span.start_s
        assert obs.source_artifact_hash == buf.artifact_hash or ":" in obs.source_artifact_hash
        assert obs.provenance.source_artifact_hash
        assert obs.provenance.parameters_hash
        assert obs.provenance.cache_key
        assert obs.provenance.method
        assert not isinstance(obs.values, dict)
        items = obs.to_evidence_items(project_token="p", audible_token="a")
        assert items
        assert all(isinstance(item, EvidenceItem) for item in items)
    assert_bundle_no_judgment(bundle)


def test_deterministic_cache_uses_hash_not_path(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    audio = sine(44100, 0.6, 220.0, 0.15)
    a = tmp_path / "one.wav"
    b = tmp_path / "two.wav"
    write_wav(a, audio)
    write_wav(b, audio)
    first = analyze_path(a, use_cache=True, cache_dir=cache)
    second = analyze_path(b, use_cache=True, cache_dir=cache)
    assert first.cache_misses >= 1
    assert second.cache_hits >= 1
    key_a = cache_key("abc", AnalyzerFamily.LEVEL_DYNAMICS.value, ANALYZER_VERSION, {"x": 1})
    key_b = cache_key("abc", AnalyzerFamily.LEVEL_DYNAMICS.value, ANALYZER_VERSION, {"x": 1})
    key_c = cache_key("abc", AnalyzerFamily.LEVEL_DYNAMICS.value, ANALYZER_VERSION, {"x": 2})
    assert key_a == key_b
    assert key_a != key_c
    named = cache_key("abc", AnalyzerFamily.LEVEL_DYNAMICS.value, ANALYZER_VERSION, {"path": "never"})
    assert "one.wav" not in named


def test_fact_judgment_boundary(tmp_path: Path) -> None:
    buf = buffer_from_samples(mixture(44100, 0.8, [(80.0, 0.3), (3000.0, 0.2)]), 44100)
    bundle = analyze_buffer(buf, use_cache=False, cache_dir=_cache(tmp_path))
    parts: list[str] = []
    for obs in bundle.observations:
        parts.extend(
            [
                obs.analyzer_id,
                *[lim.code for lim in obs.limitations],
                *[lim.detail for lim in obs.limitations],
                *[item.name for item in obs.values],
            ]
        )
    text = " ".join(parts).lower()
    for term in ("muddy", "professional", "weak drop", "bad kick", "needs compression", "masking"):
        assert term not in text
    pair = analyze_pair(
        buffer_from_samples(sine(44100, 0.8, 60.0, 0.3), 44100),
        buffer_from_samples(sine(44100, 0.8, 70.0, 0.3), 44100),
        use_cache=False,
        cache_dir=_cache(tmp_path),
    )
    rel = pair.by_analyzer(AnalyzerFamily.RELATIONAL.value)[0]
    assert rel.get("relationship").value in {"POTENTIAL_OVERLAP", "LOW_MEASURED_OVERLAP"}
    assert rel.get("potential_overlap") is not None
    assert "masking" not in " ".join(rel.limitation_codes()).lower()


def test_capability_registration_does_not_need_graph() -> None:
    registry = build_registry()
    ids = {row["capability_id"] for row in registry.contents()}
    assert CAPABILITY_ID in ids
    cap = registry.get(CAPABILITY_ID)
    assert cap.version == "2"
    assert cap.cache_semantics.startswith("audio_hash")
    skipped = cap.execute(None, {}, None)
    assert skipped["status"] == "SKIPPED"
    extra = CapabilityRegistry()
    register_physical_dsp_v2(extra)
    assert extra.get(CAPABILITY_ID).capability_id == CAPABILITY_ID


def test_fullmix_and_lowend_wrappers_still_work(tmp_path: Path) -> None:
    path = tmp_path / "fm.wav"
    write_wav(path, sine(44100, 0.7, 110.0, 0.2))
    obs = compute_fullmix_observation(path, region_id="WRAP", use_cache=False)
    assert obs.analyzer_id == "fullmix-obs-1"
    wrapped = wrap_fullmix(
        path,
        subject=DspSubject(kind=DspSubjectKind.MIX, label="main"),
        time_span=TimeSpan(start_s=0.0, end_s=obs.duration_s),
        use_cache=False,
    )
    assert wrapped.analyzer_id == AnalyzerFamily.FULLMIX_WRAP.value
    assert wrapped.get("energy_event_count") is not None

    kick = tmp_path / "kick.wav"
    bass = tmp_path / "bass.wav"
    master = tmp_path / "master.wav"
    write_wav(kick, impulse_train(44100, 1.5, 0.5))
    write_wav(bass, sine(44100, 1.5, 55.0, 0.2))
    write_wav(master, sine(44100, 1.5, 55.0, 0.2) * 0.5 + impulse_train(44100, 1.5, 0.5) * 0.5)
    features = compute_lowend_features(
        {
            "kick": audio_asset_from_wav(kick),
            "bass": audio_asset_from_wav(bass),
            "master": audio_asset_from_wav(master),
        }
    )
    assert features["ok"] is True
    low = wrap_lowend(
        {
            "kick": audio_asset_from_wav(kick),
            "bass": audio_asset_from_wav(bass),
            "master": audio_asset_from_wav(master),
        },
        time_span=TimeSpan(start_s=0.0, end_s=1.5),
    )
    assert low.analyzer_id == AnalyzerFamily.LOWEND_WRAP.value
    assert low.get("attack_count") is not None


def test_multi_granularity_with_tempo(tmp_path: Path) -> None:
    buf = buffer_from_samples(impulse_train(44100, 2.0, 0.5), 44100)
    bundle = analyze_buffer(
        buf,
        analyzers=(AnalyzerFamily.LEVEL_DYNAMICS.value,),
        granularities=(DspGranularity.REGION, DspGranularity.BEAT, DspGranularity.BAR),
        tempo_bpm=120.0,
        start_qn=0.0,
        use_cache=False,
        cache_dir=_cache(tmp_path),
    )
    beats = [obs for obs in bundle.observations if obs.time_span.granularity is DspGranularity.BEAT]
    bars = [obs for obs in bundle.observations if obs.time_span.granularity is DspGranularity.BAR]
    assert len(beats) >= 3
    assert bars
    assert all(obs.time_span.tempo_bpm == 120.0 for obs in beats)
    missing = analyze_buffer(
        buf,
        analyzers=(AnalyzerFamily.LEVEL_DYNAMICS.value,),
        granularities=(DspGranularity.BEAT,),
        use_cache=False,
        cache_dir=_cache(tmp_path),
    )
    codes = [lim.code for obs in missing.observations for lim in obs.limitations]
    assert "ABLETON_TIMING_NOT_PROVIDED" in codes


def test_pyloudnorm_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from copilot.audio.physical_dsp_v2 import providers
    from copilot.audio.physical_dsp_v2 import signal as signal_mod
    from copilot.schemas.dsp import DspLimitation

    monkeypatch.setattr(
        signal_mod,
        "pyloudnorm_status",
        lambda: providers.ProviderStatus(
            "pyloudnorm",
            False,
            "missing",
            DspLimitation("PYLOUDNORM_UNAVAILABLE", "forced"),
        ),
    )
    buf = buffer_from_samples(sine(44100, 1.0, 220.0, 0.2), 44100)
    bundle = analyze_buffer(
        buf,
        analyzers=(AnalyzerFamily.LEVEL_DYNAMICS.value,),
        use_cache=False,
        cache_dir=_cache(tmp_path),
    )
    level = bundle.by_analyzer(AnalyzerFamily.LEVEL_DYNAMICS.value)[0]
    assert "PYLOUDNORM_UNAVAILABLE" in level.limitation_codes()
    assert level.get("integrated_loudness").value is None
    assert level.quality in {DspQuality.LIMITED, DspQuality.UNSUPPORTED}


def test_performance_baseline_json(tmp_path: Path) -> None:
    out = tmp_path / "physical_dsp_v2.json"
    payload = write_baseline(out, cache_dir=_cache(tmp_path))
    assert out.is_file()
    assert payload["measurements"]["first_pass_cache_miss"]["cache_misses"] >= 1
    assert payload["measurements"]["second_pass_cache_hit"]["cache_hits"] >= 1
    assert "Live" not in payload["environment"]["note"] or "Not a Live" in payload["environment"]["note"]
    assert payload["capability_id"] == CAPABILITY_ID


def test_existing_fullmix_silence_compat(tmp_path: Path) -> None:
    path = tmp_path / "silence.wav"
    write_wav(path, silence(44100, 1.0))
    obs = compute_fullmix_observation(path, region_id="SIL", use_cache=False)
    assert any(ev.kind is EnergyEventKind.SILENCE for ev in obs.energy_events)


def test_evidence_kind_mapping(tmp_path: Path) -> None:
    buf = buffer_from_samples(sine(44100, 0.5, 330.0, 0.1), 44100)
    bundle = analyze_buffer(
        buf, analyzers=(AnalyzerFamily.LEVEL_DYNAMICS.value,), use_cache=False, cache_dir=_cache(tmp_path)
    )
    obs = bundle.observations[0]
    item = obs.to_evidence_item(obs.values[0], project_token="p", audible_token="a")
    assert item.kind is EvidenceKind.MEASUREMENT
    assert item.source_ref.startswith("artifact:")
    assert item.analysis_version == ANALYZER_VERSION
