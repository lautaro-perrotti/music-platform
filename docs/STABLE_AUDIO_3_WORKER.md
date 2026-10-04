# Stable Audio 3 Medium worker boundary

Core uses `StableAudio3Provider` through the existing
`MusicGeneratorProvider` contract. It does not install weights, import
PyTorch, call Ableton from the worker, or silently fall back to replay.

The isolated worker is `scripts/stable_audio_3_worker.py`. It requires the
official [Stable Audio 3 repository](https://github.com/Stability-AI/stable-audio-3)
checked out at `3a82c807b69cf4b7c5c05270011a5d5e47abac18`. It verifies
the Git revision and imported package location before loading `medium`.
Use the upstream installation instructions and a measured CUDA/Flash Attention
GPU environment; this repository does not provision one automatically.

On the GPU host, with that repository's Python environment active:

```bash
python /path/to/music-platform/scripts/stable_audio_3_worker.py \
  --repo-path /path/to/stable-audio-3 \
  --worker-id gpu-worker-1
```

The default bind is loopback. To reach it from Core on another machine, place
an authenticated HTTPS reverse proxy in front of the worker. Set
`STABLE_AUDIO_API_KEY` on the worker and in Core. Never expose the worker
directly on a public, unauthenticated port or send the key over remote HTTP.
Configure Core with `STABLE_AUDIO_API_URL` and optionally
`STABLE_AUDIO_TIMEOUT_S`. These values belong to the selected worker
environment, not to a hardcoded cloud vendor or repository source file.

The worker accepts only `GET /health` and `POST /generate` for
`text_to_music`, model `medium`, with prompt, duration, explicit seed and
inference steps. It returns WAV plus model/revision/seed/steps/sample-rate/
worker provenance headers. Core validates that attestation, decodes the WAV,
and calls the existing `validate_generated_audio` before producing a
`GeneratedAsset`. `cfg_scale` and `negative_prompt` are deliberately not
exposed for this post-trained checkpoint; the [upstream inference guide](https://github.com/Stability-AI/stable-audio-3/blob/main/docs/workflows/inference.md)
states they have no effect there.

The model's rights remain `UNKNOWN` for output use. The upstream repository
points to the Stability AI Community License for model use; the code
repository's MIT license must not be substituted for an output-rights grant.
An explicit human or legal determination is required for commercial use.

Studio's existing generation job path accepts
`provider="stable-audio-3"` and builds a `GenerationBrief → GeneratorRequest`
before calling the registry provider. The existing
`ExecutiveProducerAdapter` accepts a typed producer-provided
`GenerationBrief` without giving Lucas filesystem or Ableton authority.
This milestone does **not** claim that the natural-language Lucas conversation
has been certified to produce that brief automatically.

With no configured worker, health is `UNAVAILABLE` and generation returns a
typed failure. Local fixture tests exercise the HTTP contract only; they do
not certify real GPU inference or musical quality. The existing
`GeneratedAsset → stage_generated_asset → ProductionCompiler → SafeWrite`
path remains the only Ableton import route.
