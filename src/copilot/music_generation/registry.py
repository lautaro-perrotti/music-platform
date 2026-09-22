"""Registry separating dense music generators from reasoning providers."""

from __future__ import annotations

from typing import Protocol

from copilot.music_generation.schemas import (
    GenerationBatch,
    GeneratorDescriptor,
    GeneratorHealth,
    GeneratorRequest,
)


class MusicGeneratorProvider(Protocol):
    def describe(self) -> GeneratorDescriptor: ...
    def health(self) -> GeneratorHealth: ...
    def generate(self, request: GeneratorRequest) -> GenerationBatch: ...
    def cancel(self, request_id: str) -> None: ...


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

