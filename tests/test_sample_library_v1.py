from __future__ import annotations

import numpy as np
import soundfile as sf

from copilot.sample_library import library_v1 as sl
from copilot.sample_library.schemas import (
    LibraryIndex,
    SampleAsset,
    SampleRole,
    SampleSetContext,
    SampleType,
    SearchResult,
)


def test_classify_role_from_filename():
    role, conf, ev = sl._classify_role("MH_Kick_17.wav", "Drums/Kicks")
    assert role == SampleRole.KICK
    assert conf >= 0.65


def test_classify_role_from_folder():
    role, conf, ev = sl._classify_role("audio_0032.wav", "Bass")
    assert role == SampleRole.BASS
    assert conf >= 0.7


def test_classify_type_one_shot_vs_loop():
    t, conf, ev = sl._classify_type(0.4, "kick.wav")
    assert t == SampleType.ONE_SHOT
    t2, _, _ = sl._classify_type(8.0, "top loop 128.wav")
    assert t2 == SampleType.LOOP


def test_extract_filename_bpm():
    assert sl._extract_filename_bpm("HG LTH 2 Bass Loop 01 120 Eminor.wav") == 120.0
    assert sl._extract_filename_bpm("kick.wav") is None
    assert sl._extract_filename_bpm("no bpm here.wav") is None


def test_search_filters_by_role(tmp_path):
    a = SampleAsset(
        id="asset_1", path=str(tmp_path / "kick.wav"), filename="kick.wav",
        library_root=str(tmp_path), relative_path="kick.wav", extension=".wav",
        size_bytes=10, sha256="a" * 64, semantic_role=SampleRole.KICK,
    )
    idx = LibraryIndex(assets={"a" * 64: a})
    hits = sl.search(idx, "kick", role=SampleRole.KICK)
    assert isinstance(hits[0], SearchResult)


def test_build_sample_set_context_shortlist(tmp_path):
    assets = {}
    for i, role in enumerate([SampleRole.KICK, SampleRole.KICK, SampleRole.OPEN_HAT]):
        sha = f"{i}" * 64
        assets[sha] = SampleAsset(
            id=f"asset_{i}", path=str(tmp_path / f"s{i}.wav"), filename=f"s{i}.wav",
            library_root=str(tmp_path), relative_path=f"s{i}.wav", extension=".wav",
            size_bytes=10, sha256=sha, semantic_role=role,
        )
    idx = LibraryIndex(assets=assets)
    ctx = sl.build_sample_set_context(idx, task_id="t1", wanted_roles=[SampleRole.KICK], per_role=2)
    assert isinstance(ctx, SampleSetContext)
    assert len(ctx.roles["KICK"]) == 2
