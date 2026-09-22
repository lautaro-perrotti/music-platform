"""Self-hosted cloud ACE-Step provider boundary.

The provider speaks the same private official-worker contract as the local
provider.  Provisioning remains vendor-neutral: no cloud account, payment,
SSH key, or public endpoint is created by this module.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from copilot.music_generation.ace_step import AceStepProvider
from copilot.music_generation.schemas import GeneratorFailureCode, GeneratorHealth, GeneratorRequest, GenerationBatch


ACE_CLOUD_MODEL_ID = "acestep-v15-xl-turbo"
ACE_CLOUD_MODEL_REVISION = "d4a0b288b83ebb7e25a8c0b32c573c22e134e8ee"
ACE_CLOUD_RUNTIME_REVISION = "ca1e85fe9430179831e6bc6be790c332190a3866"
ACE_CLOUD_LM_MODEL = "acestep-5Hz-lm-4B"
ACE_CLOUD_LM_MODEL_REVISION = "0a3ec94b557aea7d508da38b31cfe7341f6ff737"
ACE_CLOUD_MIN_VRAM_GIB = 24


class AceStepCloudProvider(AceStepProvider):
    """Official ACE-Step XL/Turbo worker expected behind private auth."""

    provider_id = "ace-cloud-high-quality"

    def __init__(self, *, api_url: str | None = None, api_key: str | None = None) -> None:
        super().__init__(
            api_url=(api_url if api_url is not None else os.environ.get("ACESTEP_CLOUD_API_URL", "")),
            api_key=(api_key if api_key is not None else os.environ.get("ACESTEP_CLOUD_API_KEY", "")),
            model_id=ACE_CLOUD_MODEL_ID,
            provider_id=self.provider_id,
            quality_tier="BEST_SELF_HOSTED_QUALITY",
            compute_tier="CLOUD_GPU",
            benchmark_role="QUALITY_COMPARATOR",
        )
        self.profile.update({
            "dit": ACE_CLOUD_MODEL_ID,
            "lm": ACE_CLOUD_LM_MODEL,
            "offload": False,
            "quantization": None,
            "required_vram_gib": ACE_CLOUD_MIN_VRAM_GIB,
        })
        self.model_id = ACE_CLOUD_MODEL_ID

    def _model_manifest(self):
        manifest = super()._model_manifest()
        manifest.revision = ACE_CLOUD_MODEL_REVISION
        manifest.checkpoint_path = f"hf://ACE-Step/{ACE_CLOUD_MODEL_ID}@{ACE_CLOUD_MODEL_REVISION}"
        return manifest

    def describe(self):
        descriptor = super().describe()
        descriptor.hardware_requirements.update({
            "selected_dit": ACE_CLOUD_MODEL_ID,
            "selected_lm": ACE_CLOUD_LM_MODEL,
            "lm_checkpoint_revision": ACE_CLOUD_LM_MODEL_REVISION,
            "minimum_vram_gib": ACE_CLOUD_MIN_VRAM_GIB,
            "runtime_revision": ACE_CLOUD_RUNTIME_REVISION,
            "checkpoint_revision": ACE_CLOUD_MODEL_REVISION,
            "private_worker_required": True,
        })
        descriptor.runtime = f"official-acestep-api-worker@{ACE_CLOUD_RUNTIME_REVISION}"
        return descriptor

    def health(self) -> GeneratorHealth:
        if not self.api_url or not self.api_key:
            return GeneratorHealth.UNAVAILABLE
        return super().health()

    def generate(self, request: GeneratorRequest) -> GenerationBatch:
        if not self.api_url or not self.api_key:
            return self._failure(
                request,
                GeneratorFailureCode.CREDENTIAL_REQUIRED,
                reason="ACE_CLOUD_WORKER_AUTH_REQUIRED",
                selected_model=ACE_CLOUD_MODEL_ID,
            )
        return super().generate(request)
