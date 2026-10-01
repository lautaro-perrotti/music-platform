from __future__ import annotations

from pathlib import Path

from copilot.music_generation.ace_step import AceStepProvider, choose_acestep_profile
from copilot.music_generation.ace_cloud import (
    ACE_CLOUD_LM_MODEL,
    ACE_CLOUD_MODEL_ID,
    ACE_CLOUD_MODEL_REVISION,
    ACE_CLOUD_MIN_VRAM_GIB,
    AceStepCloudProvider,
)
from copilot.music_generation.elevenlabs import (
    ElevenLabsMusicProvider,
    build_elevenlabs_prompt,
)
from copilot.music_generation.benchmark import (
    CandidateRecord,
    ProviderComparisonReport,
    TechnicalValidation,
    build_provider_comparison_benchmark,
    persist_human_evaluation,
    run_best_of_n,
    validate_generated_audio,
)
from copilot.music_generation.executive_producer import ExecutiveProducerAdapter, ExecutiveProducerContext
from copilot.music_generation.schemas import GeneratedAsset, GenerationBatch, GenerationBrief, GeneratorHealth, GeneratorRequest, ModelManifest, PerformanceManifest, RightsManifest
from copilot.music_generation.registry import MusicGeneratorRegistry
from copilot.music_generation.resources import StorageVolume, WorkerResources, choose_execution_route


def test_four_gb_profile_is_dit_only_and_not_xl() -> None:
    profile = choose_acestep_profile(4.0)
    assert profile["dit"] == "acestep-v15-turbo"
    assert profile["lm"] is None
    assert profile["offload"] is True


def test_cloud_ace_provider_is_xl_quality_boundary_and_fail_closed_without_auth(monkeypatch) -> None:
    monkeypatch.delenv("ACESTEP_CLOUD_API_URL", raising=False)
    monkeypatch.delenv("ACESTEP_CLOUD_API_KEY", raising=False)
    provider = AceStepCloudProvider()
    descriptor = provider.describe()
    assert descriptor.provider_id == "ace-cloud-high-quality"
    assert descriptor.model.model_id == ACE_CLOUD_MODEL_ID
    assert descriptor.model.revision == ACE_CLOUD_MODEL_REVISION
    assert descriptor.health == GeneratorHealth.UNAVAILABLE
    assert descriptor.hardware_requirements["selected_lm"] == ACE_CLOUD_LM_MODEL
    assert descriptor.hardware_requirements["minimum_vram_gib"] == ACE_CLOUD_MIN_VRAM_GIB


def test_cloud_ace_provider_does_not_fallback_to_local_worker(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ACESTEP_CLOUD_API_URL", raising=False)
    monkeypatch.delenv("ACESTEP_CLOUD_API_KEY", raising=False)
    provider = AceStepCloudProvider()
    brief = GenerationBrief(brief_id="cloud-no-fallback", user_intent="instrumental house", target_duration_s=60)
    batch = provider.generate(
        GeneratorRequest(request_id="cloud-no-fallback", brief=brief, seed=1, output_dir=tmp_path)
    )
    assert batch.status == "GENERATOR_UNAVAILABLE"
    assert batch.failures[0]["code"] == "CREDENTIAL_REQUIRED"
    assert not list(tmp_path.glob("*.wav"))


def test_ace_provider_is_truthful_when_checkpoint_is_absent(tmp_path: Path) -> None:
    provider = AceStepProvider(model_path=tmp_path / "missing")
    assert provider.health() is GeneratorHealth.UNAVAILABLE
    brief = GenerationBrief(brief_id="test", user_intent="instrumental electronic music", target_duration_s=10)
    batch = provider.generate(GeneratorRequest(request_id="candidate-a", brief=brief, seed=1, output_dir=tmp_path))
    assert batch.status == "GENERATOR_UNAVAILABLE"
    assert batch.assets == []
    assert batch.failures[0]["code"] == "MODEL_MISSING"


def test_generator_registry_does_not_conflate_reasoning_provider() -> None:
    registry = MusicGeneratorRegistry()
    provider = AceStepProvider(model_path="missing")
    registry.register(provider)
    assert registry.descriptors()[0].provider_id == "ace-step"
    assert registry.descriptors()[0].health is GeneratorHealth.UNAVAILABLE


def test_official_worker_request_is_dit_only_and_provenance_safe(tmp_path: Path, monkeypatch) -> None:
    provider = AceStepProvider(api_url="http://127.0.0.1:8001")
    brief = GenerationBrief(
        brief_id="worker-test",
        user_intent="instrumental electronic groove",
        target_duration_s=10,
        tempo_bpm=128,
        key_context="A minor",
        meter="4/4",
    )
    request = GeneratorRequest(request_id="candidate-worker", brief=brief, seed=7, output_dir=tmp_path)
    calls = []
    monkeypatch.setattr(provider, "_request_json", lambda method, path, payload, timeout: calls.append((method, path, payload)) or {"data": {"task_id": "task-1"}})

    assert provider._submit(request) == "task-1"
    method, path, payload = calls[0]
    assert (method, path) == ("POST", "/release_task")
    assert payload["model"] == "acestep-v15-turbo"
    assert payload["thinking"] is False
    assert payload["audio_format"] == "wav"
    assert payload["task_type"] == "text2music"
    assert payload["seed"] == 7


def test_official_worker_repaint_uses_multipart_and_preserves_lineage(tmp_path: Path, monkeypatch) -> None:
    provider = AceStepProvider(api_url="http://127.0.0.1:8001")
    source = tmp_path / "parent.wav"
    source.write_bytes(b"RIFF-test-audio")
    brief = GenerationBrief(
        brief_id="repaint-test",
        user_intent="preserve groove and repaint transition",
        target_duration_s=10,
    )
    request = GeneratorRequest(
        request_id="repaint-request",
        brief=brief.model_copy(update={"generation_mode": "repaint"}),
        seed=9,
        output_dir=tmp_path,
        source_audio_path=source,
        edit_region_start_s=2.0,
        edit_region_end_s=4.0,
        lineage=["ace-step:parent", "parent-sha256", "repaint:2.0-4.0s"],
    )
    calls = []
    monkeypatch.setattr(
        provider,
        "_request_multipart_audio",
        lambda path, payload, *, field_name, file_path, timeout: calls.append(
            (path, payload, field_name, file_path, timeout)
        ) or {"data": {"task_id": "repaint-task"}},
    )

    assert provider._submit(request) == "repaint-task"
    path, payload, field_name, file_path, timeout = calls[0]
    assert path == "/release_task"
    assert field_name == "src_audio"
    assert file_path == source
    assert payload["task_type"] == "repaint"
    assert payload["repainting_start"] == 2.0
    assert payload["repainting_end"] == 4.0
    assert "src_audio_path" not in payload

    # The request contract carries lineage; the worker boundary cannot erase it.
    assert request.lineage == ["ace-step:parent", "parent-sha256", "repaint:2.0-4.0s"]


def test_worker_result_path_can_be_extracted_from_official_wrapped_payload() -> None:
    payload = {"data": [{"result": '[{"file": "C:\\\\audio\\\\candidate.wav"}]', "status": 1}]}
    assert AceStepProvider._result_audio_source(payload) == "C:\\audio\\candidate.wav"


def test_worker_route_uses_measured_volume_and_does_not_degrade_model() -> None:
    resources = WorkerResources(
        operating_system="Darwin",
        architecture="arm64",
        processor="Apple M3 Pro",
        memory_bytes=36 * 1024**3,
        volumes=[
            StorageVolume(
                mount="/",
                filesystem="apfs",
                total_bytes=1_000,
                free_bytes=20 * 1024**3,
                writable=True,
            )
        ],
    )
    route = choose_execution_route(resources, required_bytes=10 * 1024**3)
    assert route.route == "LOCAL"
    assert route.selected_mount == "/"


def test_worker_route_fails_to_cloud_when_mac_or_windows_volume_is_too_small() -> None:
    resources = WorkerResources(
        operating_system="Darwin",
        architecture="arm64",
        processor="Apple Silicon",
        memory_bytes=16 * 1024**3,
        volumes=[
            StorageVolume(
                mount="/",
                filesystem="apfs",
                total_bytes=1_000,
                free_bytes=5 * 1024**3,
                writable=True,
            )
        ],
    )
    route = choose_execution_route(resources, required_bytes=10 * 1024**3)
    assert route.route == "CLOUD_REQUIRED"
    assert route.selected_mount is None


def test_worker_route_checks_mac_unified_memory_without_quality_degradation() -> None:
    resources = WorkerResources(
        operating_system="Darwin",
        architecture="arm64",
        processor="Apple M2",
        memory_bytes=16 * 1024**3,
        memory_kind="UNIFIED",
        volumes=[
            StorageVolume(
                mount="/",
                filesystem="apfs",
                total_bytes=100 * 1024**3,
                free_bytes=40 * 1024**3,
                writable=True,
            )
        ],
    )
    route = choose_execution_route(
        resources,
        required_bytes=10 * 1024**3,
        required_memory_bytes=24 * 1024**3,
    )
    assert route.route == "CLOUD_REQUIRED"
    assert route.resource_checks == {"memory": "INSUFFICIENT", "storage": "PASS"}
    assert route.required_memory_bytes == 24 * 1024**3


def test_worker_route_checks_gpu_vram_independently_of_storage() -> None:
    resources = WorkerResources(
        operating_system="Windows",
        architecture="AMD64",
        processor="GPU worker",
        memory_bytes=32 * 1024**3,
        gpu_vram_bytes=6 * 1024**3,
        volumes=[
            StorageVolume(
                mount="D:\\",
                filesystem="ntfs",
                total_bytes=100 * 1024**3,
                free_bytes=50 * 1024**3,
                writable=True,
            )
        ],
    )
    route = choose_execution_route(
        resources,
        required_bytes=10 * 1024**3,
        required_gpu_vram_bytes=8 * 1024**3,
    )
    assert route.route == "CLOUD_REQUIRED"
    assert route.resource_checks == {"gpu_vram": "INSUFFICIENT", "storage": "PASS"}


def test_generated_asset_validation_is_factual_and_hash_bound(tmp_path: Path) -> None:
    import hashlib
    import soundfile as sf
    import numpy as np

    path = tmp_path / "candidate.wav"
    sf.write(path, np.ones((4800, 1), dtype=np.float32) * 0.1, 48_000)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    asset = GeneratedAsset(
        asset_id="ace-step:candidate",
        path=path,
        sha256=digest,
        bytes=path.stat().st_size,
        duration_s=0.1,
        sample_rate=48_000,
        non_silent=True,
        model=ModelManifest(provider="ace-step", model_id="test", quality_tier="LOCAL_COST_TIER"),
        seed=7,
        prompt="instrumental test",
        performance=PerformanceManifest(device="test"),
        rights_manifest=RightsManifest(),
    )
    result = validate_generated_audio(asset, expected_duration_s=0.1)
    assert result.status == "VALID"
    assert result.hash_matches is True
    assert result.provenance_complete is True


def test_five_candidate_flow_preserves_identity_provenance_and_human_choice(tmp_path: Path, monkeypatch) -> None:
    import hashlib

    import numpy as np
    import soundfile as sf

    class FixtureProvider:
        def generate(self, request: GeneratorRequest) -> GenerationBatch:
            request.output_dir.mkdir(parents=True, exist_ok=True)
            path = request.output_dir / f"{request.request_id}.wav"
            level = 0.05 + (request.seed % 5) * 0.01
            sf.write(path, np.full((4800, 2), level, dtype=np.float32), 48_000)
            asset = GeneratedAsset(
                asset_id=f"fixture-provider:{request.request_id}",
                path=path,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bytes=path.stat().st_size,
                duration_s=0.1,
                sample_rate=48_000,
                non_silent=True,
                model=ModelManifest(provider="fixture-provider", model_id="fixture-model", revision="fixture-v1"),
                seed=request.seed,
                prompt=request.brief.user_intent,
                performance=PerformanceManifest(device="test-fixture"),
                rights_manifest=request.brief.rights_manifest,
                provider_request={"request_id": request.request_id, "seed": request.seed},
                provider_metadata={"generation_id": f"fixture-generation-{request.seed}"},
            )
            return GenerationBatch(
                batch_id=request.request_id,
                status="GENERATED",
                provider="fixture-provider",
                model=asset.model,
                brief_id=request.brief.brief_id,
                assets=[asset],
            )

    # Keep this orchestration test local and deterministic; analysis is not its subject.
    monkeypatch.setattr("copilot.music_generation.benchmark.analyze_candidate", lambda *args, **kwargs: None)
    brief = GenerationBrief(
        brief_id="five-candidate-boundary",
        user_intent="instrumental electronic groove",
        target_duration_s=0.1,
        candidate_count=5,
    )
    report = run_best_of_n(
        FixtureProvider(),
        brief,
        output_root=tmp_path / "five-candidate-run",
        desired_candidates=brief.candidate_count,
        max_generation_attempts=5,
        seed_start=410,
    )

    assert len(report.attempts) == len(report.candidates) == 5
    assert len({candidate.candidate_id for candidate in report.candidates}) == 5
    assert len({candidate.asset.asset_id for candidate in report.candidates}) == 5
    assert [candidate.asset.seed for candidate in report.candidates] == [410, 411, 412, 413, 414]
    assert all(candidate.technical_validation.status == "VALID" for candidate in report.candidates)
    assert report.musical_winner is None
    assert report.quality_status == "CANDIDATES_READY / HUMAN_EVALUATION_PENDING"
    for candidate in report.candidates:
        assert (candidate.bundle_path / "raw.wav").is_file()
        assert (candidate.bundle_path / "provider_request.json").is_file()
        assert (candidate.bundle_path / "provider_metadata.json").is_file()


def test_provider_comparison_benchmark_hides_identity_and_records_preference(tmp_path: Path) -> None:
    import hashlib
    import numpy as np
    import soundfile as sf

    records = {}
    for provider_id, level in (("ace-local", 0.1), ("premium-cloud", 0.2)):
        bundle = tmp_path / provider_id / "candidate_001"
        bundle.mkdir(parents=True)
        path = bundle / "raw.wav"
        sf.write(path, np.ones((4800, 1), dtype=np.float32) * level, 48_000)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        asset = GeneratedAsset(
            asset_id=f"{provider_id}:candidate-001",
            path=path,
            sha256=digest,
            bytes=path.stat().st_size,
            duration_s=0.1,
            sample_rate=48_000,
            non_silent=True,
            model=ModelManifest(provider=provider_id, model_id="test", quality_tier="TEST"),
            seed=1,
            prompt="same brief",
            performance=PerformanceManifest(device="test"),
            rights_manifest=RightsManifest(),
        )
        records[provider_id] = type("Report", (), {
            "candidates": [CandidateRecord(
                candidate_id="candidate_001",
                attempt=1,
                asset=asset,
                bundle_path=bundle,
                technical_validation=TechnicalValidation(
                    readable=True, non_empty=True, non_silent=True,
                    expected_duration=True, valid_channels=True,
                    valid_sample_rate=True, finite_samples=True,
                    hash_matches=True, provenance_complete=True,
                    status="VALID",
                ),
            )]
        })()

    report = build_provider_comparison_benchmark(records, baseline_manifest=None, output_dir=tmp_path / "benchmark")
    assert isinstance(report, ProviderComparisonReport)
    assert report.providers == ["ace-local", "premium-cloud"]
    manifest = (report.listener_dir / "listener_manifest.json").read_text(encoding="utf-8")
    assert "ace-local" not in manifest
    assert "premium-cloud" not in manifest
    evaluation_path = persist_human_evaluation(report, [{"blind_id": "blind_001", "score": 5}])
    assert evaluation_path.is_file()


def test_executive_producer_boundary_does_not_invent_writes() -> None:
    brief = GenerationBrief(brief_id="boundary", user_intent="instrumental groove", target_duration_s=10)
    decision = ExecutiveProducerAdapter().build_decision(
        ExecutiveProducerContext(user_intent="instrumental groove"),
        generation_brief=brief,
        provider_status="REASONING_PROVIDER_LIMITED",
    )
    assert decision.no_musical_invention is True
    assert decision.no_ableton_access is True
    assert decision.production_refinement_intents == []


def test_elevenlabs_provider_stops_at_real_credential_boundary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    provider = ElevenLabsMusicProvider(api_key=None)
    assert provider.health().value == "UNAVAILABLE"
    brief = GenerationBrief(
        brief_id="eleven-credential",
        user_intent="instrumental electronic music",
        target_duration_s=10,
    )
    batch = provider.generate(
        GeneratorRequest(request_id="eleven-credential", brief=brief, seed=1, output_dir=tmp_path)
    )
    assert batch.status == "GENERATOR_UNAVAILABLE"
    assert batch.failures[0]["code"] == "CREDENTIAL_REQUIRED"


def test_elevenlabs_simple_prompt_request_is_pinned_and_truthful_about_seed(tmp_path: Path, monkeypatch) -> None:
    import json

    import numpy as np
    import soundfile as sf

    provider = ElevenLabsMusicProvider(api_key="test-secret", api_url="https://unit.test")
    brief = GenerationBrief(
        brief_id="eleven-simple",
        user_intent="Futuristic French house instrumental with warm syncopated bass",
        target_duration_s=10,
        tempo_bpm=124,
        instrumental=True,
    )
    request = GeneratorRequest(request_id="req-candidate-03", brief=brief, seed=1723, output_dir=tmp_path)
    captured = {}

    class Headers(dict):
        def get(self, key, default=None):
            return super().get(key.lower(), default)

    class Response:
        headers = Headers({"content-type": "audio/mpeg", "song-id": "song-abc", "x-request-id": "req-abc"})

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *_args):
            return b"mock-mp3"

    def fake_urlopen(http_request, timeout):
        captured["url"] = http_request.full_url
        captured["headers"] = dict(http_request.header_items())
        captured["body"] = json.loads(http_request.data)
        captured["timeout"] = timeout
        return Response()

    def fake_decode(_source, target):
        audio = np.full((48000 * 10, 2), 0.05, dtype=np.float32)
        sf.write(target, audio, 48000)

    monkeypatch.setattr("copilot.music_generation.elevenlabs.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("copilot.music_generation.elevenlabs.shutil.which", lambda _name: "ffmpeg")
    monkeypatch.setattr(provider, "_decode_mp3_to_wav", fake_decode)
    batch = provider.generate(request)

    assert batch.status == "GENERATED"
    asset = batch.assets[0]
    assert captured["url"] == "https://unit.test/v1/music?output_format=auto"
    assert captured["body"] == {
        "prompt": build_elevenlabs_prompt(brief),
        "music_length_ms": 10_000,
        "model_id": "music_v2_5",
        "force_instrumental": True,
        "store_for_inpainting": False,
        "sign_with_c2pa": False,
    }
    assert "xi-api-key" not in asset.provider_request
    assert asset.provider_request["provider_seed_applied"] is False
    assert asset.provider_request["internal_candidate_seed"] == 1723
    assert asset.provider_metadata["song_id"] == "song-abc"
    assert asset.provider_metadata["provider_seed_applied"] is False
    assert asset.sha256 and asset.path.is_file()


def test_elevenlabs_retries_transient_status_once_but_not_auth(tmp_path: Path, monkeypatch) -> None:
    import io
    import urllib.error

    provider = ElevenLabsMusicProvider(api_key="test-secret")
    request = GeneratorRequest(
        request_id="retry-contract",
        brief=GenerationBrief(brief_id="retry", user_intent="instrumental", target_duration_s=5),
        seed=7,
        output_dir=tmp_path,
    )
    calls = []

    def transient_then_auth(_request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.HTTPError("https://unit.test", 503, "unavailable", {}, io.BytesIO(b"retry"))
        raise urllib.error.HTTPError("https://unit.test", 401, "unauthorized", {}, io.BytesIO(b"test-secret"))

    monkeypatch.setattr("copilot.music_generation.elevenlabs.urllib.request.urlopen", transient_then_auth)
    monkeypatch.setattr("copilot.music_generation.elevenlabs.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("copilot.music_generation.elevenlabs.shutil.which", lambda _name: "ffmpeg")
    batch = provider.generate(request)
    assert len(calls) == 2
    failure = batch.failures[0]
    assert failure["provider_error"] == "AUTHENTICATION_FAILED"
    assert failure["http_status"] == 401
    assert "test-secret" not in str(failure)


def test_elevenlabs_prompt_rejects_oversize_without_call() -> None:
    brief = GenerationBrief(brief_id="too-long", user_intent="x" * 4101, target_duration_s=10)
    try:
        build_elevenlabs_prompt(brief)
    except ValueError as exc:
        assert str(exc) == "PROMPT_TOO_LONG"
    else:
        raise AssertionError("oversize prompts must be rejected, not silently truncated")


def test_elevenlabs_refuses_c2pa_when_normalizing_to_wav(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("copilot.music_generation.elevenlabs.shutil.which", lambda _name: "ffmpeg")
    provider = ElevenLabsMusicProvider(api_key="test-secret")
    request = GeneratorRequest(
        request_id="c2pa-preservation",
        brief=GenerationBrief(brief_id="c2pa", user_intent="instrumental", target_duration_s=5),
        seed=1,
        output_dir=tmp_path,
        settings={"sign_with_c2pa": True},
    )
    monkeypatch.setattr(provider, "_post_audio", lambda *_args: (_ for _ in ()).throw(AssertionError("must not call provider")))
    batch = provider.generate(request)
    assert batch.failures[0]["code"] == "OUTPUT_INVALID"
    assert batch.failures[0]["reason"] == "C2PA_SIGNED_MP3_PRESERVATION_NOT_SUPPORTED_BY_WAV_NORMALIZATION"


def test_elevenlabs_stem_separation_validates_zip_and_preserves_opaque_names(tmp_path: Path, monkeypatch) -> None:
    import hashlib
    import io
    import shutil
    import zipfile

    import numpy as np
    import soundfile as sf
    from copilot.music_generation.schemas import RightsClassification

    source = tmp_path / "source.wav"
    sf.write(source, np.full((48_000, 2), 0.05, dtype=np.float32), 48_000)
    stem_buffer = io.BytesIO()
    sf.write(stem_buffer, np.full((48_000, 2), 0.03, dtype=np.float32), 48_000, format="WAV")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("provider-folder/unspecified_01.wav", stem_buffer.getvalue())

    class Headers(dict):
        def get(self, key, default=None):
            return super().get(key.lower(), default)

    class Response:
        headers = Headers({"content-type": "application/zip"})

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *_args):
            return zip_buffer.getvalue()

    captured = {}
    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["content_type"] = dict(req.header_items()).get("Content-type")
        captured["data"] = req.data
        return Response()

    provider = ElevenLabsMusicProvider(api_key="test-secret")
    monkeypatch.setattr("copilot.music_generation.elevenlabs.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("copilot.music_generation.elevenlabs.shutil.which", lambda _name: "ffmpeg")
    monkeypatch.setattr(provider, "_decode_mp3_to_wav", lambda source_path, target: shutil.copyfile(source_path, target))
    model = ModelManifest(provider="elevenlabs-music", model_id="music_v2_5")
    source_asset = GeneratedAsset(
        asset_id="elevenlabs-music:source-id", path=source,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(), bytes=source.stat().st_size,
        duration_s=1.0, sample_rate=48_000, non_silent=True, model=model, seed=42,
        prompt="instrumental", performance=PerformanceManifest(device="elevenlabs-music"),
        rights_manifest=RightsManifest(output_use=RightsClassification.COMMERCIAL_ALLOWED),
    )
    derived = provider.separate_stems(source_asset, output_dir=tmp_path / "stems")
    assert len(derived) == 1
    assert derived[0]["provider_member_name"] == "unspecified_01.wav"
    assert derived[0]["provider_request"]["stem_variation_id"] == "six_stems_v1"
    assert derived[0]["source_asset_id"] == source_asset.asset_id
    assert Path(derived[0]["path"]).is_file()
    assert "stem_variation_id=six_stems_v1" in captured["url"]
    assert b"test-secret" not in captured["data"]


def test_internal_seed_does_not_claim_eleven_provider_determinism(tmp_path: Path) -> None:
    import hashlib

    import numpy as np
    import soundfile as sf

    from copilot.music_generation.benchmark import CandidateRecord, TechnicalValidation, _relations

    records = []
    for ordinal, level in enumerate((0.05, 0.08), 1):
        path = tmp_path / f"seed-{ordinal}.wav"
        sf.write(path, np.full((4800, 1), level, dtype=np.float32), 48_000)
        asset = GeneratedAsset(
            asset_id=f"elevenlabs-music:seed-{ordinal}", path=path,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size,
            duration_s=0.1, sample_rate=48_000, non_silent=True,
            model=ModelManifest(provider="elevenlabs-music", model_id="music_v2_5"),
            seed=99, prompt="same prompt", performance=PerformanceManifest(device="elevenlabs-music"),
            rights_manifest=RightsManifest(), provider_metadata={"provider_seed_applied": False},
        )
        records.append(CandidateRecord(
            candidate_id=f"candidate-{ordinal}", attempt=ordinal, asset=asset,
            bundle_path=tmp_path / f"bundle-{ordinal}", technical_validation=TechnicalValidation(status="VALID"),
        ))
    relation = _relations(records, {})[0]
    assert relation.same_seed is False  # provider never received this bookkeeping seed
    assert relation.effectively_duplicate is False
    assert "same_provider_seed_and_model" not in relation.basis
