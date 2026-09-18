"""Canonical capability matrix for the supported envelope. No mock success."""

from __future__ import annotations

from typing import Any

from copilot.audio.m4l_control_contract_v1 import MILESTONE as M4L_MILESTONE
from copilot.audio.working_copy_policy_v1 import MILESTONE as WORKING_COPY_MILESTONE
from copilot.daw.session_ready_v1 import FROZEN as SESSION_FROZEN
from copilot.daw.session_ready_v1 import MILESTONE as SESSION_MILESTONE
from copilot.daw.session_ready_v1 import STATUS as SESSION_STATUS

MILESTONE = "CAPABILITY_MATRIX_V1"

# claim: what is true today. Not a promise of musical generalization.
ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": SESSION_MILESTONE,
        "command": "doctor",
        "claim": "TCP + hello/request-id + light snapshot + project identity",
        "status": SESSION_STATUS,
        "frozen": SESSION_FROZEN,
        "writes": False,
    },
    {
        "id": WORKING_COPY_MILESTONE,
        "command": "onboard-project",
        "claim": "Refuse pista.als; treat open .als as the working copy; fixture is plumbing",
        "status": "VERIFIED",
        "frozen": False,
        "writes": False,
    },
    {
        "id": "CROSS_PROJECT_BOOTSTRAP_V1",
        "command": "project-bootstrap",
        "claim": "Install Main tap + capture hosts; second run NO_CHANGES_REQUIRED",
        "status": "VERIFIED",
        "frozen": False,
        "writes": False,
        "note": "Infra writes only. No musical mutations. Fixture live-validated.",
    },
    {
        "id": "PROJECT_READY_V1",
        "command": "onboard-project",
        "claim": "Identity -> bootstrap -> generic or session preflight",
        "status": "VERIFIED",
        "frozen": False,
        "writes": False,
        "note": "Fixture VERIFIED. Alias: project-ready.",
    },
    {
        "id": "PRODUCER_RUNTIME_V1",
        "command": "analyze-project",
        "claim": "One high-level call. Agent expresses intent; runtime compiles/schedules/executes.",
        "status": "VERIFIED",
        "frozen": True,
        "writes": False,
        "note": "FROZEN. Wraps canonical producer-analyze. Capture batching frozen at max_batch_sources=2.",
    },
    {
        "id": "RPC_OPTIMIZATION_V2",
        "command": "analyze-project",
        "claim": "Domain-scoped ProjectReadView + bulk host-state reads. Zero precision loss.",
        "status": "VERIFIED",
        "frozen": True,
        "writes": False,
        "note": "Groove Rider 174→109 RPC, 82.70s→54.54s RPC wall. Do not reopen read squeezing.",
    },
    {
        "id": "CAPTURE_BATCHING_V1",
        "command": None,
        "claim": "Max 2 sources + Main sidecar per playback pass. PRE_ROLL_QN=16.",
        "status": "VERIFIED",
        "frozen": True,
        "writes": False,
        "note": "Legacy width 2 when TapProtocol 3. Wider passes are CAPTURE_SCALABILITY_V2.",
    },
    {
        "id": "CAPTURE_HOST_BASELINE_V1",
        "command": None,
        "claim": "Canonical parked capture-host state. Live Ext. In+Main is not baseline.",
        "status": "VERIFIED",
        "frozen": True,
        "writes": False,
        "note": "HOST_AVAILABLE only after parked apply+fresh verify. Restore returns to Resampling + Sends Only + monitor off.",
    },
    {
        "id": "CAPTURE_SCALABILITY_V2",
        "command": "analyze-project",
        "claim": "Discovered capture-pool capacity. TapProtocol 4 up to 8 source slots + Main.",
        "status": "VERIFIED",
        "frozen": True,
        "writes": False,
        "note": "Groove Rider AUTO_36_68 one-pass: 4 sources + Main, playback_pass_count=1, PRE_ROLL_QN=16, MUSICAL WRITES=0.",
    },
    {
        "id": "ABLETON_MUTATION_PROTOCOL_V1",
        "command": None,
        "claim": "Whitelisted compound capture-host mutations with per-step results and sequential fallback.",
        "status": "VERIFIED",
        "frozen": True,
        "writes": False,
        "note": "Groove Rider Live: 109→34 RPC, 54.54s→16.20s RPC wall. execute_mutation_batch x4. Sequential fallback preserved. Not for musical writes.",
    },
    {
        "id": "EVIDENCE_SYSTEM_V2",
        "command": None,
        "claim": "EvidenceGraph above immutable EvidencePacks. Fusion, freshness, scoped views.",
        "status": "VERIFIED",
        "frozen": True,
        "writes": False,
        "note": "LLM results cannot masquerade as MEASUREMENT. Timestamp is not freshness authority.",
    },
    {
        "id": "PHYSICAL_DSP_V2",
        "command": None,
        "claim": "Factual DSP families behind DspObservation. Measures; does not judge mix quality.",
        "status": "VERIFIED",
        "frozen": True,
        "writes": False,
        "note": "Wraps FullMix/LowEnd. Limitations: no ITU LRA, true-peak 4x approx, no Essentia/librosa. Key is candidate set.",
    },
    {
        "id": "SAFE_WRITE_FOUNDATION_V2",
        "command": None,
        "claim": "Generic musical mutation lifecycle. SET_TRACK_VOLUME remains the only certified action.",
        "status": "VERIFIED",
        "frozen": True,
        "writes": "development_working_copy_only",
        "note": "Mock Live. AnalyzeProject stays MUSICAL WRITES=0. Not ABLETON_MUTATION_PROTOCOL_V1. No EQ/MIDI/arrangement.",
    },
    {
        "id": "FOUNDATION_INTEGRATION_CHECKPOINT_V1",
        "command": None,
        "claim": "DSP, Evidence, and Safe Write share one repo state. AnalyzeProject stays read-only.",
        "status": "VERIFIED",
        "frozen": True,
        "writes": False,
        "note": "DspObservation.to_evidence_item(s) ingest into EvidenceGraph. Relational DSP is RELATIONSHIP. SET_TRACK_VOLUME only certified musical action.",
    },
    {
        "id": "PRODUCER_ANALYZE_V1",
        "command": "producer-analyze",
        "claim": "Read-only capture -> evidence pack -> Astra once -> gate. Statuses preserved.",
        "status": "COMMAND_VERIFIED",
        "frozen": False,
        "writes": False,
        "note": "Fixture INSUFFICIENT_EVIDENCE (empty arrangement). Musical generalization unproven.",
    },
    {
        "id": "PRODUCER_RUN_V1",
        "command": "producer-run",
        "claim": "analyze always; autonomous SET_TRACK_VOLUME only on development working copy",
        "status": "COMMAND_VERIFIED",
        "frozen": False,
        "writes": "development_working_copy_only",
        "note": "External projects: ACTION_NOT_AVAILABLE until CROSS_PROJECT_MUSICAL_VALIDATION_V1.",
    },
    {
        "id": "CROSS_PROJECT_MUSICAL_VALIDATION_V1",
        "command": "cross-project-validate",
        "claim": "First unseen real song, read-only. Result decides the next capability.",
        "status": "WAITING_FOR_EXTERNAL_SONG",
        "frozen": False,
        "writes": False,
        "note": "pista / fixture / untitled are not this test.",
    },
    {
        "id": M4L_MILESTONE,
        "command": None,
        "claim": "ANALYZE/STATUS/DIAGNOSIS/PROPOSED ACTION/APPLY/ROLLBACK - no frontend",
        "status": "DOCUMENTED",
        "frozen": True,
        "writes": "APPLY only when gate justifies SET_TRACK_VOLUME",
    },
    {
        "id": "SECOND_MACHINE_INSTALLER_V1",
        "command": "install",
        "claim": "Windows installer: venv, Copilot Remote Script, M4L, config template. No secrets.",
        "status": "IMPLEMENTED",
        "frozen": False,
        "writes": False,
        "note": "Infra only. SECOND_MACHINE_PORTABILITY_V1 waits on a friend machine.",
    },
    {
        "id": "REGRESSION_V1",
        "command": "regression-v1",
        "claim": "Offline supported-envelope suite. No live destructive writes.",
        "status": "VERIFIED",
        "frozen": False,
        "writes": False,
    },
)

UNSUPPORTED = (
    "EQ actions",
    "compressor actions",
    "MIDI editing",
    "arrangement editing",
    "unbounded plugin control",
    "web / Electron / React UI",
    "silent mock success",
    "collapsing producer statuses",
    "autonomous writes on an unvalidated external song",
)


def capability_matrix() -> dict[str, Any]:
    return {
        "milestone": MILESTONE,
        "LIVE_SESSION_READINESS_V1": f"{SESSION_STATUS} / FROZEN={SESSION_FROZEN}",
        "rows": list(ROWS),
        "unsupported": list(UNSUPPORTED),
        "NO MOCK SUCCESS": True,
        "musical_generalization": "UNPROVEN_UNTIL_SECOND_REAL_SONG",
    }
