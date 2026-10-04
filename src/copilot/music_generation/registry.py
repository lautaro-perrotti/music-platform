"""Registry separating dense music generators from reasoning providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from copilot.music_generation.schemas import (
    GenerationBatch,
    GenerationBrief,
    GeneratorCapability,
    GeneratorDescriptor,
    GeneratorHealth,
    GeneratorRequest,
)


class MusicGeneratorProvider(Protocol):
    def describe(self) -> GeneratorDescriptor: ...
    def health(self) -> GeneratorHealth: ...
    def generate(self, request: GeneratorRequest) -> GenerationBatch: ...
    def cancel(self, request_id: str) -> None: ...


@dataclass(frozen=True)
class GeneratorRoute:
    status: str
    provider_id: str | None = None
    request: GeneratorRequest | None = None
    reason: str = ""


class MusicGeneratorRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, MusicGeneratorProvider] = {}

    def register(self, provider: MusicGeneratorProvider) -> None:
        descriptor = provider.describe()
        self._providers[descriptor.provider_id] = provider

    def get(self, provider_id: str) -> MusicGeneratorProvider:
        return self._providers[provider_id]

    def descriptors(self) -> list[GeneratorDescriptor]:
        return [self._providers[key].describe() for key in sorted(self._providers)]

    def prepare_request(
        self, brief: GenerationBrief, *, seed: int, output_dir: Path,
    ) -> GeneratorRoute:
        """Capability-fit only. Never invokes a generator or creates files."""
        if not brief.no_write or brief.generation_mode != "text_to_music":
            return GeneratorRoute(status="UNSUPPORTED", reason="TEXT_TO_MUSIC_NO_WRITE_REQUIRED")
        required = {GeneratorCapability.TEXT_TO_MUSIC}
        required.add(GeneratorCapability.INSTRUMENTAL if brief.instrumental else GeneratorCapability.VOCALS)
        outputs = set(brief.requested_outputs)
        if not outputs.issubset({"stereo", "stems", "midi"}):
            return GeneratorRoute(status="UNSUPPORTED", reason="UNKNOWN_REQUESTED_OUTPUT")
        if "stems" in outputs:
            required.add(GeneratorCapability.STEM_OUTPUT)
        if "midi" in outputs:
            required.add(GeneratorCapability.MIDI_OUTPUT)
        matches: list[GeneratorDescriptor] = []
        for descriptor in self.descriptors():
            if not required.issubset(set(descriptor.capabilities)):
                continue
            if descriptor.duration_min_s is None or descriptor.duration_max_s is None:
                continue  # Unknown duration support is not certified support.
            if not descriptor.duration_min_s <= brief.target_duration_s <= descriptor.duration_max_s:
                continue
            matches.append(descriptor)
        if not matches:
            return GeneratorRoute(status="UNSUPPORTED", reason="NO_PROVIDER_WITH_VERIFIED_CAPABILITY_FIT")
        if len(matches) != 1:
            return GeneratorRoute(status="AMBIGUOUS_PROVIDER", reason="MULTIPLE_CAPABILITY_MATCHES_REQUIRE_SELECTION")
        descriptor = matches[0]
        request = GeneratorRequest(
            request_id=f"{brief.brief_id}-candidate-{seed}", brief=brief,
            seed=seed, output_dir=Path(output_dir), model_manifest=descriptor.model,
            no_ableton_access=True,
        )
        status = {
            GeneratorHealth.HEALTHY: "ROUTED",
            GeneratorHealth.CONFIGURED: "ROUTED_UNVERIFIED",
            GeneratorHealth.UNKNOWN: "ROUTED_UNVERIFIED",
            GeneratorHealth.UNAVAILABLE: "ROUTED_UNAVAILABLE",
        }[descriptor.health]
        return GeneratorRoute(status=status, provider_id=descriptor.provider_id, request=request)

