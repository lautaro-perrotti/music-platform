from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import os
import tempfile
import time
from uuid import uuid4

from copilot.human_eval.schema import (
    SCHEMA_VERSION,
    CaseStatus,
    EvalCase,
    EvalRun,
    HumanResponse,
    LabelRevision,
    SkipReason,
)

ROOT = Path("logs") / "human_eval"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canonical_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def label_hash(*, response: HumanResponse, audio_sha256: str, schema_version: str = SCHEMA_VERSION) -> str:
    blob = canonical_dumps(
        {
            "audio_sha256": audio_sha256,
            "schema_version": schema_version,
            "response": response.model_dump(mode="json"),
        }
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


class EvalStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or ROOT)

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def run_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run.json"

    def case_path(self, run_id: str, case_id: str) -> Path:
        return self.run_dir(run_id) / "cases" / f"{case_id}.json"

    def session_log(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "sessions.jsonl"

    def list_run_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            path.parent.name
            for path in self.root.glob("*/run.json")
        )

    def save_run(self, run: EvalRun) -> None:
        run.updated_at = now_iso()
        atomic_write(self.run_path(run.evaluation_run_id), run.model_dump(mode="json"))

    def load_run(self, run_id: str) -> EvalRun | None:
        path = self.run_path(run_id)
        if not path.is_file():
            return None
        return EvalRun.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save_case(self, case: EvalCase) -> None:
        case.updated_at = now_iso()
        atomic_write(
            self.case_path(case.evaluation_run_id, case.case_id),
            case.model_dump(mode="json"),
        )

    def load_case(self, run_id: str, case_id: str) -> EvalCase | None:
        path = self.case_path(run_id, case_id)
        if not path.is_file():
            return None
        return EvalCase.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def load_cases(self, run_id: str) -> list[EvalCase]:
        run = self.load_run(run_id)
        if run is None:
            return []
        cases = []
        for case_id in run.case_ids:
            case = self.load_case(run_id, case_id)
            if case is not None:
                cases.append(case)
        return sorted(cases, key=lambda item: item.presentation_order)

    def append_session(self, run_id: str, event: dict[str, Any]) -> None:
        path = self.session_log(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": now_iso(), **event}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def find_run_by_project_token(self, project_token: str | None) -> EvalRun | None:
        if not project_token:
            return None
        for run_id in self.list_run_ids():
            run = self.load_run(run_id)
            if run and run.project_token == project_token:
                return run
        return None

    def first_incomplete(self, run_id: str) -> EvalCase | None:
        for case in self.load_cases(run_id):
            if case.status in {CaseStatus.PENDING, CaseStatus.IN_PROGRESS}:
                return case
        return None

    def counts(self, run_id: str) -> dict[str, int]:
        cases = self.load_cases(run_id)
        tallies = {status.value: 0 for status in CaseStatus}
        for case in cases:
            tallies[case.status.value] += 1
        labeled = tallies[CaseStatus.COMPLETED.value] + tallies[CaseStatus.LOCKED.value]
        return {
            "total": len(cases),
            "labeled": labeled,
            "skipped": tallies[CaseStatus.SKIPPED.value],
            "pending": tallies[CaseStatus.PENDING.value] + tallies[CaseStatus.IN_PROGRESS.value],
            "locked": tallies[CaseStatus.LOCKED.value],
            **{f"status_{key.lower()}": value for key, value in tallies.items()},
        }

    def all_required_locked(self, run_id: str) -> bool:
        cases = self.load_cases(run_id)
        required = [case for case in cases if case.status != CaseStatus.SKIPPED]
        if not required:
            return False
        return all(case.status == CaseStatus.LOCKED for case in required)

    def new_session_id(self) -> str:
        return uuid4().hex[:12]
