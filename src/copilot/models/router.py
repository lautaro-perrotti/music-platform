from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSlot:
    name: str
    provider: str
    model_id: str
    local: bool
    notes: str


class ModelRouter:
    """Replaceable model map. No single model owns the product."""

    def __init__(self) -> None:
        self.conversation_model = ModelSlot(
            "conversation_model",
            "openai-compatible",
            "unassigned",
            local=False,
            notes="Tool-calling chat; swap without changing SessionState.",
        )
        self.planning_model = ModelSlot(
            "planning_model",
            "openai-compatible",
            "unassigned",
            local=False,
            notes="Produces MusicPlan JSON only.",
        )
        self.audio_caption_model = ModelSlot(
            "audio_caption_model",
            "local",
            "deferred",
            local=True,
            notes="Optional captioning after MusicObservation is measured.",
        )
        self.asr_model = ModelSlot(
            "asr_model",
            "qwen",
            "Qwen/Qwen3-ASR-0.6B",
            local=True,
            notes="PRIMARY for speech + singing. 0.6B fits 6GB VRAM better than 1.7B.",
        )
        self.embedding_model = ModelSlot(
            "embedding_model",
            "laion",
            "laion/larger_clap_music",
            local=True,
            notes="MIT weights. MERT/MuQ rejected for CC-BY-NC.",
        )
        self.music_generation_model = ModelSlot(
            "music_generation_model",
            "ace-step",
            "ACE-Step/acestep-v15-base",
            local=True,
            notes="Not used in vertical slice 1. XL rejected on 6GB GPU.",
        )
        self.transcription_model = ModelSlot(
            "transcription_model",
            "spotify",
            "basic-pitch",
            local=True,
            notes="Apache-2.0 polyphonic AMT for MVP.",
        )
        self.separation_model = ModelSlot(
            "separation_model",
            "demucs",
            "htdemucs",
            local=True,
            notes="Wrap adefossez/demucs; 6GB VRAM may force CPU/segments.",
        )

    def as_dict(self) -> dict[str, dict[str, str | bool]]:
        return {
            slot.name: {
                "provider": slot.provider,
                "model_id": slot.model_id,
                "local": slot.local,
                "notes": slot.notes,
            }
            for slot in (
                self.conversation_model,
                self.planning_model,
                self.audio_caption_model,
                self.asr_model,
                self.embedding_model,
                self.music_generation_model,
                self.transcription_model,
                self.separation_model,
            )
        }
