"""Minimal isolated HTTP worker for official Stable Audio 3 Medium.

Run this with the pinned upstream repository's Python environment on a GPU
host. Core does not import this module or its model dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = "medium"
REVISION = "3a82c807b69cf4b7c5c05270011a5d5e47abac18"
MAX_REQUEST_BYTES = 16 * 1024


def load_official_model(repo_path: Path):
    repo = repo_path.resolve()
    actual = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, timeout=10,
    ).strip()
    if actual != REVISION:
        raise RuntimeError("OFFICIAL_REPOSITORY_REVISION_MISMATCH")
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import stable_audio_3  # type: ignore[import-not-found]
    from stable_audio_3 import StableAudioModel  # type: ignore[import-not-found]

    if not Path(stable_audio_3.__file__).resolve().is_relative_to(repo):
        raise RuntimeError("OFFICIAL_PACKAGE_PATH_MISMATCH")
    model = StableAudioModel.from_pretrained(MODEL, device="cuda")
    if int(model.model.sample_rate) != 44100:
        raise RuntimeError("OFFICIAL_MODEL_SAMPLE_RATE_MISMATCH")
    return model


class OfficialBackend:
    def __init__(self, model, worker_id: str) -> None:
        self.model = model
        self.worker_id = worker_id
        self.lock = threading.Lock()

    def render(self, payload: dict) -> tuple[bytes, dict[str, str]]:
        import numpy as np
        import soundfile as sf

        started = time.perf_counter()
        with self.lock:
            audio = self.model.generate(
                prompt=payload["prompt"], duration=payload["duration_s"],
                steps=payload["inference_steps"], seed=payload["seed"],
                batch_size=1,
            )
        array = audio[0].detach().cpu().numpy().T
        if array.ndim != 2 or array.shape[1] != 2 or not np.isfinite(array).all():
            raise RuntimeError("INVALID_MODEL_AUDIO")
        result = io.BytesIO()
        sf.write(result, array, 44100, format="WAV", subtype="PCM_16")
        return result.getvalue(), {
            "X-Stable-Audio-Model": MODEL,
            "X-Stable-Audio-Revision": REVISION,
            "X-Stable-Audio-Seed": str(payload["seed"]),
            "X-Stable-Audio-Steps": str(payload["inference_steps"]),
            "X-Stable-Audio-Sample-Rate": "44100",
            "X-Stable-Audio-Worker-Id": self.worker_id,
            "X-Stable-Audio-Elapsed-S": str(time.perf_counter() - started),
            "X-Stable-Audio-Sha256": hashlib.sha256(result.getvalue()).hexdigest(),
        }


def create_server(host: str, port: int, backend, *, api_key: str | None = None) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"} and not api_key:
        raise ValueError("NON_LOOPBACK_WORKER_REQUIRES_AUTH")

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if not api_key:
                return True
            return hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {api_key}")

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self._json(404, {"code": "NOT_FOUND"})
            elif not self._authorized():
                self._json(401, {"code": "UNAUTHORIZED"})
            else:
                self._json(200, {
                    "ready": True, "model": MODEL, "revision": REVISION,
                    "worker_id": backend.worker_id,
                })

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/generate":
                self._json(404, {"code": "NOT_FOUND"})
                return
            if not self._authorized():
                self._json(401, {"code": "UNAUTHORIZED"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST_BYTES:
                    raise ValueError("INVALID_REQUEST_SIZE")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != {
                    "prompt", "duration_s", "seed", "inference_steps", "model",
                }:
                    raise ValueError("INVALID_REQUEST_FIELDS")
                if payload["model"] != MODEL or not isinstance(payload["prompt"], str) or not payload["prompt"].strip():
                    raise ValueError("INVALID_MODEL_OR_PROMPT")
                if type(payload["seed"]) is not int or payload["seed"] < 0:
                    raise ValueError("INVALID_SEED")
                if type(payload["inference_steps"]) is not int or not 1 <= payload["inference_steps"] <= 100:
                    raise ValueError("INVALID_STEPS")
                if type(payload["duration_s"]) not in {int, float} or not 1 <= payload["duration_s"] <= 380:
                    raise ValueError("INVALID_DURATION")
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"code": "INVALID_REQUEST"})
                return
            try:
                wav, metadata = backend.render(payload)
            except Exception:
                self._json(500, {"code": "WORKER_INFERENCE_FAILED"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            for key, value in metadata.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(wav)

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--worker-id", required=True)
    args = parser.parse_args()
    key = os.environ.get("STABLE_AUDIO_API_KEY")
    backend = OfficialBackend(load_official_model(args.repo_path), args.worker_id)
    server = create_server(args.host, args.port, backend, api_key=key)
    server.serve_forever()


if __name__ == "__main__":
    main()
