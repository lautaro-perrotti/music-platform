"""Bounded real-Live Alpha orchestration.

This module is intentionally a thin Core coordinator.  Lucas remains the
source of musical decisions; Core owns context assembly, identity, compilation,
SafeWrite, readback, capture, and artifact persistence.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from copilot.audio.advanced_perception_v1 import run_advanced_perception
from copilot.audio.music_analyzer import analyze_reference_music
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.human_eval.store import now_iso
from copilot.integration.lucas_core_v1 import (
    _single_action_plan,
    build_lucas_input,
    build_project_context,
    build_reference_context,
    normalize_sample_uri_for_working_copy,
    rebind_sample_load_action,
    run_lucas_critique_with_provider_failover,
    run_lucas_planner,
)
from copilot.integration.mixing_mastering_v1 import capture_and_analyze_master
from copilot.importing.working_copy_manager_v1 import is_copilot_working_copy
from copilot.musicplan import build_duplicate_clip_to_arrangement_action
from copilot.sample_library.library_v1 import (
    build_sample_set_context,
    index_library,
    load_index,
)
from copilot.sample_library.schemas import LibraryIndex, SampleRole
from copilot.schemas.lucas_integration import StyleContext, UserIntent
from copilot.schemas.musicplan import MusicPlan, ProductionActionKind
from copilot.reasoning.provider import configured_http_provider

MILESTONE = "AUTONOMOUS_PRODUCER_ALPHA_V1"
REAL_LUCAS = "REAL_LUCAS"
CONTROLLED_FIXTURE = "CONTROLLED_FIXTURE"
SUPPORTED_ALPHA_ACTIONS = frozenset(
    {
        ProductionActionKind.CREATE_TRACK,
        ProductionActionKind.SAMPLE_LOAD,
        ProductionActionKind.LOAD_SAMPLE,
        ProductionActionKind.LOAD_DEVICE,
        ProductionActionKind.DEVICE_LOAD,
        ProductionActionKind.DEVICE_TWEAK,
        ProductionActionKind.SET_TRACK_VOLUME,
        ProductionActionKind.DUPLICATE_CLIP_TO_ARRANGEMENT,
    }
)


class RealLucasRequired(RuntimeError):
    """Raised when the stable planner did not produce a real model plan."""


class LucasReasoningOutputAdapter:
    """Translate the configured Astra reasoning envelope to Lucas's stable JSON.

    The provider is allowed to return the repository's grounded reasoning
    envelope.  The adapter only unwraps model-authored candidate strategies;
    it never chooses samples, invents sections, or applies Core taste logic.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.identity = str(getattr(inner, "identity", type(inner).__name__))
        self.version = str(getattr(inner, "version", "unknown"))
        self.response_adapter = "lucas_reasoning_candidate_strategies_v1"
        self.last_raw = ""

    @staticmethod
    def _decode(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    @classmethod
    def unwrap(cls, raw: str) -> str:
        payload = json.loads(raw)
        if "selections" in payload or "arrangement" in payload:
            return raw
        strategies = payload.get("candidate_strategies") or []
        output: dict[str, Any] = {}
        for row in strategies:
            if not isinstance(row, dict):
                continue
            name = str(row.get("strategy") or "")
            value = cls._decode(row.get("reason"))
            if name in {"selections", "arrangement", "patch_contracts"}:
                output[name] = value
            elif name == "reasoning":
                output["reasoning"] = value
            elif "candidate" in name.lower():
                selections: dict[str, int] = {}
                for match in re.finditer(r"([A-Za-z][A-Za-z ]*?)\s+candidate\s+(\d+)", name, re.I):
                    track = " ".join(match.group(1).strip().split())
                    track = re.sub(r"^(select|choose|and)\s+", "", track, flags=re.I).strip()
                    if track:
                        selections[track] = int(match.group(2))
                if selections:
                    output.setdefault("selections", {}).update(selections)
            elif ":" in name and "bar" in name.lower():
                from copilot.musicplan.arrangement import ALL_TRACKS

                sections: list[dict[str, Any]] = []
                for match in re.finditer(
                    r"([^:]+):\s*(\d+)\s+bars?,\s*(?:active\s+)?([^\.]+)",
                    name,
                    re.I,
                ):
                    active = []
                    active_text = match.group(3)
                    for track_name in sorted(ALL_TRACKS, key=len, reverse=True):
                        if re.search(rf"(?<![A-Za-z]){re.escape(track_name)}(?![A-Za-z])", active_text, re.I):
                            active.append(track_name)
                    active.sort(key=lambda item: active_text.lower().find(item.lower()))
                    if active:
                        sections.append({
                            "name": re.sub(r"[^A-Z0-9]+", "_", match.group(1).upper()).strip("_") or "SECTION",
                            "bars": int(match.group(2)),
                            "active": active,
                        })
                if sections:
                    output.setdefault("arrangement", []).extend(sections)
        if not any(key in output for key in ("selections", "arrangement", "patch_contracts")):
            raise RealLucasRequired("LUCAS_PROVIDER_OUTPUT_HAS_NO_PRODUCER_STRATEGY")
        return json.dumps(output)

    def reason(self, prompt: str, *, timeout_s: float = 30.0) -> str:
        raw = self.inner.reason(prompt, timeout_s=timeout_s)
        self.last_raw = raw
        return self.unwrap(raw)


class LucasPlanningProviderAdapter:
    """Use the configured provider with Lucas's producer-output contract.

    The generic Astra provider deliberately requests the diagnosis schema. That
    schema is correct for reasoning/evidence calls but cannot carry Lucas's
    selections and arrangement. This adapter keeps the same configured model,
    endpoint, and credentials while requesting the stable Lucas JSON object.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.identity = str(getattr(inner, "identity", type(inner).__name__))
        self.version = str(getattr(inner, "version", "unknown"))
        self.response_adapter = "lucas_producer_plan_schema_v1"
        self.last_raw = ""

    @staticmethod
    def _schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "selections": {"type": "object", "additionalProperties": {"type": "integer"}},
                "arrangement": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "bars": {"type": "integer"},
                            "active": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "bars", "active"],
                        "additionalProperties": False,
                    },
                },
                "reasoning": {"type": "string"},
                "patch_contracts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "track": {"type": "string"},
                            "device": {"type": "string"},
                            "writes": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "index": {"type": "integer"},
                                        "name": {"type": "string"},
                                        "value": {"type": "number"},
                                    },
                                    "required": ["index", "name", "value"],
                                    "additionalProperties": False,
                                },
                            },
                            "constraints": {
                                "type": "object",
                                "properties": {
                                    "max_delta_norm": {"type": "number"},
                                    "max_writes": {"type": "integer"},
                                    "forbid_device_on_toggle": {"type": "boolean"},
                                },
                                "required": ["max_delta_norm", "max_writes", "forbid_device_on_toggle"],
                                "additionalProperties": False,
                            },
                        },
                        "required": ["track", "device", "writes", "constraints"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["selections", "arrangement", "reasoning", "patch_contracts"],
            "additionalProperties": False,
        }

    def reason(self, prompt: str, *, timeout_s: float = 30.0) -> str:
        model = str(getattr(self.inner, "_model", ""))
        if "astra" not in model.lower():
            raw = self.inner._reason_chat_json_object(prompt, timeout_s=timeout_s)
            self.last_raw = raw
            return raw
        payload = {
            "model": model,
            "reasoning": {"effort": os.environ.get("COPILOT_REASONING_EFFORT") or "high"},
            "input": [
                {"role": "system", "content": "Return only the Lucas producer JSON object. No Ableton operations."},
                {"role": "user", "content": prompt},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "LucasProducerPlan",
                    "schema": self._schema(),
                    "strict": False,
                }
            },
        }
        raw_payload = self.inner._post("/responses", payload, timeout_s=timeout_s)
        from copilot.reasoning.provider import _extract_responses_text

        text = _extract_responses_text(raw_payload)
        if not text:
            raise RealLucasRequired("LUCAS_PRODUCER_PLAN_RESPONSE_EMPTY")
        self.last_raw = text
        return text


def discover_reference(evidence: Path, *, project_path: Path | None = None) -> Path:
    """Find an existing WAV for analysis without copying it into Ableton."""
    candidates: list[Path] = []
    for root in (evidence / "captures", Path.home() / "CopilotProjects" / "captures"):
        if root.is_dir():
            candidates.extend(
                path for path in root.glob("*.wav")
                if not path.name.lower().endswith("_raw.wav")
                and not path.name.lower().startswith("_next")
                and path.stat().st_size > 44
            )
    if project_path is not None:
        candidates.extend(
            path for path in project_path.parent.glob("*.wav")
            if not path.name.lower().endswith("_raw.wav")
            and path.stat().st_size > 44
        )
    if not candidates:
        raise FileNotFoundError("NO_REAL_REFERENCE_AUDIO_DISCOVERED")
    # Capture scratch files can be newer than the last valid take and still be
    # zero-byte/invalid WAVs.  Select the newest file that the frozen analyzer
    # can actually decode, preserving deterministic discovery without trusting
    # filename recency alone.
    import soundfile as sf

    readable: list[Path] = []
    for path in sorted(candidates, key=lambda item: item.stat().st_mtime_ns, reverse=True):
        try:
            if int(sf.info(path).frames) > 0:
                readable.append(path)
        except Exception:
            continue
    if not readable:
        raise FileNotFoundError("NO_READABLE_REAL_REFERENCE_AUDIO_DISCOVERED")
    return readable[0]


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _plan_action_name(action: Any) -> str:
    return str(getattr(action.action_type, "value", action.action_type))


def _actualize_action(action: Any, current: Any) -> tuple[Any | None, str | None]:
    name = str(action.target.ref.get("name") or "")
    track = current.track_by_name(name) if name else None
    if action.action_type is ProductionActionKind.CREATE_TRACK:
        if track is not None:
            return None, "TARGET_ALREADY_EXISTS"
        return action, None
    if track is None:
        return None, "TARGET_NOT_FOUND_OR_AMBIGUOUS"
    from copilot.daw.object_ref import ref_from_track, runtime_from_track

    target = action.target.model_copy(update={
        "ref": ref_from_track(track, project_identity=current.project_identity).model_dump(mode="json"),
        "runtime_id": runtime_from_track(track, session_incarnation_id=current.session_incarnation_id).model_dump(mode="json"),
        "track_index_locator": track.index,
    })
    actual = action.model_copy(update={"target": target})
    if action.action_type is ProductionActionKind.SAMPLE_LOAD:
        sample_uri = str(actual.params.sample_uri).replace("\\", "/")
        if track.role == "audio":
            # Live's audio-track loader resolves a project-relative path through
            # the browser path command.  The query URI form is for instruments
            # and effects loaded on MIDI tracks; using it on an audio track
            # leaves the clip absent on authoritative readback.
            if sample_uri.startswith("Samples/"):
                sample_uri = sample_uri[len("Samples/"):]
            parts = [part for part in sample_uri.strip("/").split("/") if part]
            if (
                not parts
                or sample_uri.startswith("/")
                or ":" in parts[0]
                or any(part in {".", ".."} for part in parts)
            ):
                return None, "SAMPLE_PATH_OUTSIDE_AUTHORIZED_LIBRARY"
            actual = actual.model_copy(update={
                "params": actual.params.model_copy(update={"sample_uri": sample_uri})
            })
        else:
            sample_uri = normalize_sample_uri_for_working_copy(sample_uri)
        actual = rebind_sample_load_action(
            actual.model_copy(update={
                "params": actual.params.model_copy(update={
                    "sample_uri": sample_uri,
                })
            }),
            track=track,
            session=current,
        )
    if action.action_type in {
        ProductionActionKind.LOAD_DEVICE,
        ProductionActionKind.DEVICE_LOAD,
    } and bool(getattr(track, "grouped", False)):
        return None, "TARGET_TRACK_GROUPED_NOT_VISIBLE_FOR_DEVICE_LOAD"
    return actual, None


def _execute_one(action: Any, *, daw: Any, persist_dir: Path) -> dict[str, Any]:
    from copilot.daw.state_tokens import attach_tokens
    from copilot.runtime.production_compiler import ProductionCompiler
    from copilot.runtime.safe_write import build_safe_write_executor

    # SafeWrite's authoritative precondition snapshot includes the complete
    # session payload. Compile against that same view; the lightweight
    # include_notes=False view can carry a different observed revision.
    current = daw.snapshot()
    attach_tokens(current)
    planned_name = str(action.target.ref.get("name") or "")
    planned_track = current.track_by_name(planned_name) if planned_name else None
    if action.action_type is ProductionActionKind.SAMPLE_LOAD and planned_track is not None:
        if planned_track.role == "audio" and "browser.load" not in getattr(daw, "capabilities", set()):
            return {
                "action_id": action.action_id,
                "action_type": _plan_action_name(action),
                "status": "EXECUTION_DEFERRED",
                "reason": "BRIDGE_CAPABILITY_BROWSER_LOAD_UNAVAILABLE",
            }
        if planned_track.role != "audio" and planned_track.devices:
            return {
                "action_id": action.action_id,
                "action_type": _plan_action_name(action),
                "status": "EXECUTION_DEFERRED",
                "reason": "TARGET_MIDI_TRACK_ALREADY_HAS_DEVICE_READBACK_AMBIGUOUS",
            }
    actual, reason = _actualize_action(action, current)
    if actual is None:
        return {
            "action_id": action.action_id,
            "action_type": _plan_action_name(action),
            "status": "EXECUTION_DEFERRED",
            "reason": reason,
        }
    single = _single_action_plan(actual, session=current, plan_id=f"alpha_{action.action_id}")
    compiled = ProductionCompiler().compile(single, session=current)
    if compiled.status != "COMPILED" or compiled.intent is None:
        return {
            "action_id": action.action_id,
            "action_type": _plan_action_name(action),
            "status": "EXECUTION_DEFERRED",
            "reason": "; ".join(compiled.reasons) or compiled.status,
        }
    executor = build_safe_write_executor(
        daw,
        journal_path=persist_dir / f"{action.action_id}_safe_write.jsonl",
        persist_dir=persist_dir,
    )
    result = executor.run(compiled.intent)
    row = {
        "action_id": action.action_id,
        "action_type": _plan_action_name(action),
        "status": "VERIFIED" if result.ok else "FAILED",
        "readbacks": [item.model_dump(mode="json") for item in result.readbacks],
    }
    if not result.ok:
        row["error"] = result.error or "SAFE_WRITE_FAILED"
    return row


def _arrangement_actions(plan: MusicPlan, metadata: dict[str, Any], daw: Any) -> list[Any]:
    sections = metadata.get("arrangement") or []
    if not sections:
        return []
    actions: list[Any] = []
    cursor = 0.0
    session = daw.snapshot()
    attach_tokens(session)
    for section in sections:
        if isinstance(section, dict):
            section_name = str(section.get("name") or "SECTION")
            bars = int(section.get("bars") or 0)
            active_names = list(section.get("active") or [])
        else:
            section_name = str(section.name)
            bars = int(section.bars)
            active_names = list(section.active)
        if bars <= 0:
            continue
        length = float(bars * 4)
        for name in active_names:
            track = session.track_by_name(str(name))
            if track is None or not any(clip.slot_index == 0 for clip in track.clips):
                continue
            actions.append(build_duplicate_clip_to_arrangement_action(
                track=track,
                project_identity=session.project_identity,
                clip_index=0,
                destination_time=cursor,
                length=length,
                reason=f"REAL_LUCAS arrangement {section_name} ({bars} bars)",
                evidence_refs=list(plan.evidence_refs),
                session_incarnation_id=session.session_incarnation_id,
            ))
        cursor += length
    return actions


def _build_sample_index(project_path: Path, evidence: Path) -> tuple[LibraryIndex, dict[str, Any]]:
    roots = [project_path.parent / "Samples"]
    roots = [root for root in roots if root.is_dir()]
    if not roots:
        raise FileNotFoundError("PROJECT_SAMPLE_LIBRARY_MISSING")
    path = evidence / "alpha_sample_library_index.json"
    counts = index_library(roots, path)
    index = load_index(path)
    if index is None:
        raise RuntimeError("SAMPLE_LIBRARY_INDEX_FAILED")
    return index, counts


def run_alpha(*, evidence: Path = Path("logs")) -> dict[str, Any]:
    """Run one bounded Alpha pass against the currently authorized Live set."""
    evidence.mkdir(parents=True, exist_ok=True)
    started = now_iso()
    artifact_path = evidence / "autonomous_producer_alpha_v1.json"
    report: dict[str, Any] = {
        "milestone": MILESTONE,
        "started_at": started,
        "MUSICAL_WRITES": 0,
        "strategy_provenance": REAL_LUCAS,
        "lucas_owned_files_modified": 0,
    }
    daw = AbletonTcpAdapter()
    try:
        daw.connect()
        session = daw.snapshot(include_notes=False)
        attach_tokens(session)
        if not session.project_path or not is_copilot_working_copy(session.project_path):
            raise RuntimeError("CONTROLLED_WORKING_COPY_REQUIRED")
        report["SESSION_READY"] = "VERIFIED"
        report["project"] = {
            "path": session.project_path,
            "identity": session.project_identity,
            "token": session.project_token,
            "track_count_before": len(session.tracks),
        }

        project_path = Path(session.project_path)
        reference_path = Path(os.environ["COPILOT_ALPHA_REFERENCE"]) if os.environ.get("COPILOT_ALPHA_REFERENCE") else discover_reference(evidence, project_path=project_path)
        index, index_counts = _build_sample_index(project_path, evidence)
        pack = analyze_reference_music(
            reference_path,
            reference_state_token=f"reference:alpha:{reference_path.stem}",
            target_state_token=f"target:alpha:{session.project_identity}",
            tempo_bpm=float(session.transport.tempo),
            use_cache=False,
        )
        reference = build_reference_context(pack)
        roles = [
            SampleRole.KICK, SampleRole.CLAP, SampleRole.CLOSED_HAT,
            SampleRole.SHAKER, SampleRole.PERCUSSION, SampleRole.TOP_LOOP,
            SampleRole.BASS, SampleRole.VOCAL, SampleRole.SYNTH, SampleRole.FX,
            SampleRole.IMPACT, SampleRole.TEXTURE,
        ]
        samples = build_sample_set_context(
            index, task_id="autonomous-producer-alpha-v1", wanted_roles=roles,
            per_role=3, bpm=float(session.transport.tempo),
        )
        context = build_lucas_input(
            user_intent=UserIntent(description=(
                "CONTROLLED_ALPHA_INTENT: create and develop approximately 32 bars "
                "of an electronic track using the available project, real sample "
                "library, and reference context, with a coherent groove, bass/low-end "
                "role, musical texture, and basic mix balance. You must make the "
                "musical decisions and return a non-empty 5-to-8-section arrangement."
            )),
            reference=reference,
            samples=samples,
            project=build_project_context(session),
            style=StyleContext(),
        )
        provider = configured_http_provider()
        if provider is None:
            raise RealLucasRequired("REAL_LUCAS_PROVIDER_UNAVAILABLE")
        planner_provider = LucasPlanningProviderAdapter(provider)
        planner_run = run_lucas_planner(
            input_context=context,
            index=index,
            session=session,
            provider=planner_provider,
            plan_id="autonomous_producer_alpha_v1",
        )
        report["lucas"] = {
            "entrypoint": "build_plan_from_prompt",
            "strategy_provenance": REAL_LUCAS if planner_run.planner_metadata.get("astra_used") else CONTROLLED_FIXTURE,
            "provider": planner_provider.identity,
            "provider_version": planner_provider.version,
            "response_adapter": planner_provider.response_adapter,
            "metadata": planner_run.planner_metadata,
        }
        if not planner_run.planner_metadata.get("astra_used"):
            report["lucas"]["provider_raw_response"] = planner_provider.last_raw
            raise RealLucasRequired("LUCAS_PLANNER_FELL_BACK_TO_DETERMINISTIC_RECIPE")
        plan = planner_run.plan
        report.update({
            "reference": {
                "path": str(reference_path),
                "identity": reference.identity,
                "rights_state": "UNKNOWN_EXTERNAL_RECORDING",
                "role": "groove_lowend_texture_analysis_only",
                "pack": pack.model_dump(mode="json"),
            },
            "sample_set_context": samples.model_dump(mode="json"),
            "sample_index": index_counts,
            "style_context": StyleContext().model_dump(mode="json"),
            "lucas": {
                "entrypoint": "build_plan_from_prompt",
                "strategy_provenance": REAL_LUCAS,
                "provider": planner_provider.identity,
                "provider_version": planner_provider.version,
                "response_adapter": planner_provider.response_adapter,
                "metadata": planner_run.planner_metadata,
                "plan": plan.model_dump(mode="json"),
            },
        })

        persist_dir = evidence / "autonomous_producer_alpha_v1" / "safe_write"
        persist_dir.mkdir(parents=True, exist_ok=True)
        dispositions: list[dict[str, Any]] = []
        ordered = sorted(
            plan.actions,
            key=lambda action: 0 if action.action_type is ProductionActionKind.CREATE_TRACK else 1 if action.action_type in {ProductionActionKind.SAMPLE_LOAD, ProductionActionKind.LOAD_SAMPLE} else 2,
        )
        for action in ordered:
            if action.action_type not in SUPPORTED_ALPHA_ACTIONS:
                dispositions.append({
                    "action_id": action.action_id,
                    "action_type": _plan_action_name(action),
                    "status": "EXECUTION_DEFERRED",
                    "reason": "UNSUPPORTED_OR_NOT_CERTIFIED",
                })
                continue
            row = _execute_one(action, daw=daw, persist_dir=persist_dir)
            row["source"] = "REAL_LUCAS_MUSICPLAN"
            dispositions.append(row)

        arrangement_actions = _arrangement_actions(plan, planner_run.planner_metadata, daw)
        for action in arrangement_actions:
            row = _execute_one(action, daw=daw, persist_dir=persist_dir)
            row["source"] = "REAL_LUCAS_ARRANGEMENT"
            dispositions.append(row)
        report["execution"] = {
            "actions": dispositions,
            "executable_verified": sum(row.get("status") == "VERIFIED" for row in dispositions),
            "deferred": sum(row.get("status") == "EXECUTION_DEFERRED" for row in dispositions),
            "failed": sum(row.get("status") == "FAILED" for row in dispositions),
            "direct_lucas_writes": 0,
            "direct_soniq_writes": 0,
            "safe_write_authorities": 1,
        }
        if report["execution"]["failed"]:
            report["status"] = "FAILED_EXECUTION"
            return report

        after_session = daw.snapshot(include_notes=False)
        attach_tokens(after_session)
        audio = capture_and_analyze_master(
            daw, after_session, label="alpha_initial_pass",
        )
        perception = run_advanced_perception(
            audio["music_analysis"], audio_path=Path(audio["path"]),
        )
        post_context = {
            "project_identity": after_session.project_identity,
            "plan_id": plan.plan_id,
            "audio": {
                "capture_id": audio["capture_id"], "path": audio["path"],
                "rms": audio["rms"], "peak": audio["peak"], "region": audio["region"],
            },
            "music_analysis": audio["music_analysis"].model_dump(mode="json"),
            "physical_dsp": _jsonable(audio["dsp"]),
            "advanced_perception": perception.model_dump(mode="json"),
            "reference_identity": reference.identity,
            "limitations": list(dict.fromkeys(reference.limitations + list(pack.limitations))),
        }
        report["post_change_context"] = post_context
        critique = run_lucas_critique_with_provider_failover(
            plan=plan,
            session=after_session,
            evidence_context=post_context,
            timeout_s=30.0,
        )
        report["lucas_feedback"] = critique
        report["critique_provider_limited"] = critique.get("status") != "CRITIQUE_COMPLETE"
        if report["critique_provider_limited"]:
            report["status"] = "PRODUCTION_PASS_VERIFIED / REVISION_PROVIDER_LIMITED"
        else:
            report["status"] = "PRODUCTION_PASS_VERIFIED"
        report["final"] = {
            "original_untouched": True,
            "direct_lucas_writes": 0,
            "direct_soniq_writes": 0,
            "safe_write_authorities": 1,
            "transport_stopped": not bool(after_session.transport.playing),
        }
        return report
    finally:
        try:
            report["finished_at"] = now_iso()
            artifact_path.write_text(
                json.dumps(_jsonable(report), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:  # artifact persistence must not hide run status
            report["artifact_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            daw.disconnect()


def main() -> int:
    report = run_alpha()
    print(json.dumps(_jsonable(report), indent=2, ensure_ascii=False))
    return 0 if str(report.get("status", "")).startswith(("VERIFIED", "PRODUCTION_PASS_VERIFIED")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
