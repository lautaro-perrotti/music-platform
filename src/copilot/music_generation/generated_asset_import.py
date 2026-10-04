"""Generated-audio staging and MusicPlan construction.

The generator only produces an immutable file.  This module prepares a
manifest-backed working copy and emits a normal LOAD_SAMPLE MusicPlan; it
does not call a DAW adapter or create a second write authority.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from copilot.daw.object_ref import ref_from_track, runtime_from_track
from copilot.importing.working_copy_manager_v1 import is_copilot_working_copy
from copilot.music_generation.schemas import GeneratedAsset
from copilot.schemas.musicplan import (
    ActionPrecondition,
    ActionTarget,
    DiagnosisBinding,
    ExecutionVerificationSpec,
    ExpectedEffect,
    MusicPlan,
    MusicalVerificationSpec,
    PlanAction,
    PlanIntentClass,
    PlanStatus,
    ProductionActionKind,
    RollbackSpec,
    SampleLoadActionParams,
    VerificationSpec,
)
from copilot.schemas.session import SessionState, TrackState


class StagedGeneratedAsset(BaseModel):
    asset_id: str
    source_path: Path
    staged_path: Path
    browser_staged_path: Path
    sample_uri: str
    sha256: str
    working_copy: Path
    original_project_untouched: bool = True


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_verified(source: Path, target: Path, expected: str) -> None:
    if target.is_file():
        if _file_sha256(target) != expected:
            raise ValueError("GENERATED_ASSET_EXISTING_STAGE_HASH_MISMATCH")
        return
    try:
        with source.open("rb") as reader, target.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
    except FileExistsError:
        if _file_sha256(target) != expected:
            raise ValueError("GENERATED_ASSET_EXISTING_STAGE_HASH_MISMATCH") from None
        return
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    if _file_sha256(target) != expected:
        target.unlink(missing_ok=True)
        raise ValueError("GENERATED_ASSET_STAGE_HASH_MISMATCH")


def stage_generated_asset(
    asset: GeneratedAsset, *, working_als: Path, user_library: Path | None = None
) -> StagedGeneratedAsset:
    """Stage verified bytes in the working copy and Live's discovered Browser root."""
    working_als = Path(working_als)
    if not is_copilot_working_copy(working_als):
        raise ValueError("GENERATED_ASSET_IMPORT_REQUIRES_MANIFEST_BACKED_WORKING_COPY")
    source = Path(asset.path)
    if not source.is_absolute():
        raise ValueError("GENERATED_ASSET_SOURCE_ABSOLUTE_PATH_REQUIRED")
    if not source.is_file():
        raise FileNotFoundError(source)
    if _file_sha256(source) != asset.sha256:
        raise ValueError("GENERATED_ASSET_SOURCE_HASH_MISMATCH")
    if user_library is None:
        from copilot.importing.m4l_runtime_v1 import discover_user_library

        discovery = discover_user_library()
        if discovery.get("status") != "VERIFIED" or not discovery.get("user_library"):
            raise ValueError("GENERATED_ASSET_USER_LIBRARY_UNRESOLVED")
        user_library = Path(str(discovery["user_library"]))
    library_root = Path(user_library).resolve()
    if not library_root.is_dir():
        raise ValueError("GENERATED_ASSET_USER_LIBRARY_UNRESOLVED")
    project_root = working_als.parent
    imported = project_root / "Samples" / "Imported"
    browser_imported = library_root / "Samples" / "Imported"
    if not imported.resolve().is_relative_to(project_root.resolve()):
        raise ValueError("GENERATED_ASSET_STAGE_PATH_ESCAPE")
    if not browser_imported.resolve().is_relative_to(library_root):
        raise ValueError("GENERATED_ASSET_STAGE_PATH_ESCAPE")
    imported.mkdir(parents=True, exist_ok=True)
    browser_imported.mkdir(parents=True, exist_ok=True)
    safe_name = "generated_" + asset.sha256[:16] + ".wav"
    staged = imported / safe_name
    browser_staged = browser_imported / safe_name
    if (not staged.resolve().is_relative_to(project_root.resolve())
            or not browser_staged.resolve().is_relative_to(library_root)):
        raise ValueError("GENERATED_ASSET_STAGE_PATH_ESCAPE")
    _copy_verified(source, staged, asset.sha256)
    _copy_verified(source, browser_staged, asset.sha256)
    return StagedGeneratedAsset(
        asset_id=asset.asset_id,
        source_path=source,
        staged_path=staged,
        browser_staged_path=browser_staged,
        sample_uri=f"Samples/Imported/{safe_name}",
        sha256=asset.sha256,
        working_copy=working_als,
    )


def build_generated_asset_load_plan(
    asset: GeneratedAsset,
    *,
    staged: StagedGeneratedAsset,
    session: SessionState,
    track: TrackState,
    clip_index: int = 0,
) -> MusicPlan:
    """Create one canonical LOAD_SAMPLE action for ProductionCompiler."""
    if session.project_identity is None:
        raise ValueError("GENERATED_ASSET_IMPORT_REQUIRES_PROJECT_IDENTITY")
    ref = ref_from_track(track, project_identity=session.project_identity)
    runtime = runtime_from_track(track, session_incarnation_id=session.session_incarnation_id)
    action_id = f"generated_asset_{asset.sha256[:12]}"
    action = PlanAction(
        action_id=action_id,
        # The frozen compiler vocabulary uses SAMPLE_LOAD for producer plans;
        # ProductionCompiler maps it to the canonical SafeWrite LOAD_SAMPLE
        # mutation.  Do not create a second generated-asset action kind.
        action_type=ProductionActionKind.SAMPLE_LOAD,
        target=ActionTarget(
            ref=ref.model_dump(mode="json"),
            runtime_id=runtime.model_dump(mode="json"),
            track_index_locator=track.index,
        ),
        params=SampleLoadActionParams(clip_index=clip_index, sample_uri=staged.sample_uri),
        reason=f"import generated asset {asset.asset_id} through SafeWrite",
        evidence_refs=[f"generated_asset:{asset.sha256}", "generated_asset_import.staged_hash"],
        preconditions=[
            ActionPrecondition(code="TOKENS_CURRENT", detail="revalidate project and target tokens before write"),
            ActionPrecondition(code="WORKING_COPY_ONLY", detail="original project remains outside the write target"),
            ActionPrecondition(code="ASSET_HASH_VERIFIED", detail=staged.sha256),
        ],
        expected_effect=ExpectedEffect(
            affected_target=f"{track.name}.clip[{clip_index}].sample",
            direction="load",
            description=f"load {staged.sample_uri}",
            measurement_to_compare_after="sample reference and clip identity",
        ),
        verification=VerificationSpec(
            execution=ExecutionVerificationSpec(parameter=f"clip[{clip_index}].sample", expected_after=1.0, unit="loaded"),
            musical=MusicalVerificationSpec(deferred=True, note="Technical import only; musical quality is not certified here."),
        ),
        rollback=RollbackSpec(parameter="clip", unit="slot", restore_value=-1.0),
    )
    return MusicPlan(
        plan_id=f"generated-asset-import-{asset.sha256[:12]}",
        status=PlanStatus.DRAFT,
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        diagnosis=DiagnosisBinding(
            diagnosis_id="generated_asset_import",
            diagnosis_status="SUPPORTED",
            diagnosis_accepted=True,
            cause_status="CAUSE_SUPPORTED",
        ),
        project_state_token=session.project_token or "",
        audible_state_token=session.audible_token or "",
        target_state_tokens={track.stable_id: ref.target_state_token},
        evidence_refs=[f"generated_asset:{asset.sha256}"],
        actions=[action],
        created_at=datetime.now(timezone.utc).isoformat(),
        notes=["Generated audio import is offline-prepared; execution belongs exclusively to ProductionCompiler and SafeWrite."],
        gate={"MUSICAL_WRITES": 1, "ORIGINAL_PROJECT_WRITES": 0},
    )
