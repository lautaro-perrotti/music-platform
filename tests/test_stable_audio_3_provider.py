import io
import hashlib
import json
import threading
import time

import numpy as np
import pytest
import soundfile as sf

from copilot.music_generation.registry import MusicGeneratorRegistry
from copilot.music_generation.schemas import (
    GenerationBrief, GeneratorHealth, GeneratorRequest, RightsClassification,
)
from copilot.music_generation.stable_audio import (
    MODEL_ID, REPOSITORY_REVISION, StableAudio3Provider,
)
from copilot.studio.service import StudioService
from scripts.stable_audio_3_worker import OfficialBackend, create_server


def _request(tmp_path, *, settings=None):
    brief = GenerationBrief(
        brief_id="sa3-test", user_intent="Dark hypnotic tech-house groove",
        target_duration_s=1, tempo_bpm=125, instrumental=True,
    )
    return GeneratorRequest(
        request_id="sa3-candidate", brief=brief, seed=831001,
        output_dir=tmp_path, settings=settings or {},
    )


def _wav():
    rate = 44100
    t = np.arange(rate) / rate
    samples = np.column_stack((0.1 * np.sin(2 * np.pi * 220 * t),) * 2)
    buffer = io.BytesIO()
    sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


class FakeBackend:
    worker_id = "fixture-worker"

    def __init__(self, *, wav=None, fail=False, delay=0):
        self.payloads = []
        self.wav = wav if wav is not None else _wav()
        self.fail = fail
        self.delay = delay

    def render(self, payload):
        self.payloads.append(payload)
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("fake failure")
        return self.wav, {
            "X-Stable-Audio-Model": MODEL_ID,
            "X-Stable-Audio-Revision": REPOSITORY_REVISION,
            "X-Stable-Audio-Seed": str(payload["seed"]),
            "X-Stable-Audio-Steps": str(payload["inference_steps"]),
            "X-Stable-Audio-Sample-Rate": "44100",
            "X-Stable-Audio-Worker-Id": self.worker_id,
            "X-Stable-Audio-Elapsed-S": "0.01",
            "X-Stable-Audio-Sha256": hashlib.sha256(self.wav).hexdigest(),
        }


@pytest.fixture
def worker():
    servers = []

    def start(backend, *, api_key=None):
        server = create_server("127.0.0.1", 0, backend, api_key=api_key)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_unconfigured_and_registry_interoperability(tmp_path):
    provider = StableAudio3Provider(api_url="")
    assert provider.health() is GeneratorHealth.UNAVAILABLE
    assert provider.describe().model.model_id == "medium"
    registry = MusicGeneratorRegistry()
    registry.register(provider)
    assert registry.get("stable-audio-3") is provider
    batch = provider.generate(_request(tmp_path))
    assert batch.status == "GENERATOR_UNAVAILABLE"
    assert batch.assets == []
    assert StudioService(tmp_path / "studio")._default_provider("stable-audio-3").provider_id == "stable-audio-3"


def test_exact_request_and_valid_asset(worker, tmp_path):
    backend = FakeBackend()
    provider = StableAudio3Provider(api_url=worker(backend))
    assert provider.health() is GeneratorHealth.HEALTHY
    request = _request(tmp_path, settings={"inference_steps": 8})
    batch = provider.generate(request)
    assert batch.status == "GENERATED"
    assert backend.payloads == [{
        "prompt": "Dark hypnotic tech-house groove\nTarget tempo: 125 BPM.\nInstrumental; no vocals.",
        "duration_s": 1.0, "seed": 831001, "inference_steps": 8, "model": "medium",
    }]
    asset = batch.assets[0]
    assert asset.path.is_file() and asset.non_silent
    assert asset.seed == request.seed
    assert asset.sample_rate == 44100 and asset.duration_s == 1.0
    assert asset.model.revision == REPOSITORY_REVISION
    assert asset.rights_manifest.output_use is RightsClassification.UNKNOWN
    assert asset.provider_request == backend.payloads[0]
    assert asset.provider_metadata["worker_id"] == "fixture-worker"
    assert asset.provider_metadata["worker_sha256"] == asset.sha256
    assert asset.no_ableton_access is True
    from copilot.music_generation.benchmark import validate_generated_audio

    assert validate_generated_audio(asset, expected_duration_s=1).status == "VALID"


@pytest.mark.parametrize("settings", [
    {"cfg_scale": 7}, {"negative_prompt": "bad"}, {"inference_steps": 0},
])
def test_unsupported_or_ineffective_controls_fail_closed(worker, tmp_path, settings):
    backend = FakeBackend()
    provider = StableAudio3Provider(api_url=worker(backend))
    batch = provider.generate(_request(tmp_path, settings=settings))
    assert batch.assets == [] and batch.status == "INFERENCE_FAILED"
    assert backend.payloads == []


@pytest.mark.parametrize("wav", [b"not wav", None])
def test_invalid_and_silent_wav_are_not_accepted(worker, tmp_path, wav):
    if wav is None:
        buffer = io.BytesIO()
        sf.write(buffer, np.zeros((44100, 2)), 44100, format="WAV")
        wav = buffer.getvalue()
    provider = StableAudio3Provider(api_url=worker(FakeBackend(wav=wav)))
    batch = provider.generate(_request(tmp_path))
    assert batch.assets == [] and batch.failures[0]["code"] == "OUTPUT_INVALID"


def test_worker_error_and_timeout_fail_closed(worker, tmp_path):
    failing = StableAudio3Provider(api_url=worker(FakeBackend(fail=True)))
    assert failing.generate(_request(tmp_path)).assets == []
    slow = StableAudio3Provider(api_url=worker(FakeBackend(delay=0.2)), timeout_s=0.01)
    assert slow.generate(_request(tmp_path / "other")).assets == []


def test_worker_rejects_unsupported_fields_and_requires_auth(worker):
    import urllib.error
    import urllib.request

    backend = FakeBackend()
    url = worker(backend, api_key="test-only")
    req = urllib.request.Request(
        url + "/generate", data=json.dumps({"prompt": "x"}).encode(), method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(req, timeout=2)
    assert error.value.code == 401
    req.add_header("Authorization", "Bearer test-only")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(req, timeout=2)
    assert error.value.code == 400
    assert backend.payloads == []


def test_remote_plain_http_is_refused(tmp_path):
    provider = StableAudio3Provider(api_url="http://example.org", timeout_s=0.01)
    batch = provider.generate(_request(tmp_path))
    assert batch.assets == []
    assert provider.health() is GeneratorHealth.CONFIGURED


def test_no_write_and_instrumental_boundary(worker, tmp_path):
    backend = FakeBackend()
    provider = StableAudio3Provider(api_url=worker(backend))
    request = _request(tmp_path)
    request.no_ableton_access = False
    assert provider.generate(request).assets == []
    request.no_ableton_access = True
    request.brief.instrumental = False
    assert provider.generate(request).assets == []
    assert backend.payloads == []


def test_no_duplicate_inference_for_existing_request(worker, tmp_path):
    backend = FakeBackend()
    provider = StableAudio3Provider(api_url=worker(backend))
    request = _request(tmp_path)
    assert provider.generate(request).status == "GENERATED"
    again = provider.generate(request)
    assert again.assets == []
    assert again.failures[0]["reason"] == "OUTPUT_ALREADY_EXISTS"
    assert len(backend.payloads) == 1


def test_worker_attestation_mismatch_is_rejected(worker, tmp_path):
    class WrongRevision(FakeBackend):
        def render(self, payload):
            wav, metadata = super().render(payload)
            metadata["X-Stable-Audio-Revision"] = "wrong-revision"
            return wav, metadata

    batch = StableAudio3Provider(api_url=worker(WrongRevision())).generate(_request(tmp_path))
    assert batch.assets == []
    assert batch.failures[0]["reason"] == "WORKER_ATTESTATION_MISMATCH"


def test_worker_sha256_mismatch_is_rejected_before_persisting(worker, tmp_path):
    class WrongHash(FakeBackend):
        def render(self, payload):
            wav, metadata = super().render(payload)
            metadata["X-Stable-Audio-Sha256"] = "0" * 64
            return wav, metadata

    batch = StableAudio3Provider(api_url=worker(WrongHash())).generate(_request(tmp_path))
    assert batch.assets == []
    assert batch.failures[0]["reason"] == "WORKER_SHA256_MISMATCH"
    assert list(tmp_path.glob("*.wav")) == []


def test_unreachable_worker_is_not_a_fake_success(tmp_path):
    provider = StableAudio3Provider(api_url="http://127.0.0.1:1", timeout_s=0.1)
    batch = provider.generate(_request(tmp_path))
    assert batch.status == "GENERATOR_UNAVAILABLE"
    assert batch.assets == []


def test_official_worker_call_shape_without_loading_gpu():
    class FakeTensor:
        def __init__(self, array):
            self.array = array

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self.array

    class FakeModel:
        def __init__(self):
            self.calls = []

        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return [FakeTensor(np.ones((2, 44100), dtype=np.float32) * 0.01)]

    model = FakeModel()
    wav, metadata = OfficialBackend(model, "gpu-test").render({
        "prompt": "house", "duration_s": 1, "seed": 44,
        "inference_steps": 8, "model": MODEL_ID,
    })
    assert model.calls == [{
        "prompt": "house", "duration": 1, "steps": 8,
        "seed": 44, "batch_size": 1,
    }]
    assert sf.info(io.BytesIO(wav)).samplerate == 44100
    assert metadata["X-Stable-Audio-Revision"] == REPOSITORY_REVISION
    assert metadata["X-Stable-Audio-Sha256"] == hashlib.sha256(wav).hexdigest()


def test_core_provider_has_no_daw_or_model_runtime_imports():
    import ast
    from pathlib import Path
    import copilot.music_generation.stable_audio as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imports = [
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert not any(name.startswith(("copilot.daw", "stable_audio_3", "torch")) for name in imports)


def test_worker_does_not_bind_non_loopback_without_auth():
    with pytest.raises(ValueError, match="NON_LOOPBACK_WORKER_REQUIRES_AUTH"):
        create_server("0.0.0.0", 0, FakeBackend())
