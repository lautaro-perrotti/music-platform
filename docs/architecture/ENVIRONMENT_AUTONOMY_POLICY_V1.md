# ENVIRONMENT_AUTONOMY_POLICY_V1

Status: **VERIFIED / CURRENT-HOST**.

The agent owns normal Ableton validation orchestration. It discovers the host
platform, architecture, user, repository, Live installation, preferences,
Remote Script location, and available process controls at runtime. No path or
executable from another developer's machine is a repository contract.

The lifecycle is:

```text
DISCOVER_ENVIRONMENT
→ ENSURE_INTEGRATION
→ START_ABLETON
→ WAIT_PROCESS
→ WAIT_BRIDGE
→ PROTOCOL_HELLO
→ CAPABILITY_NEGOTIATION
→ PROJECT_RECONCILIATION
→ PROJECT_READY
```

The public entry point is:

```python
from copilot.runtime import ensure_ableton_ready

report = ensure_ableton_ready(source_als="...", project_root="...")
```

The source project is copied or reused under the existing manifest-backed
working-copy policy. A raw `.als` without `copilot_import.json` is rejected.
Remote Script provisioning is idempotent and owned-file safe. Live is started
with the discovered executable using native process controls and no shell
interpolation. Readiness is not inferred from a port: the existing handshake,
light snapshot, and project identity probe must succeed.

If Ableton cannot expose the bridge because the Control Surface requires an
interactive OS/UI action, the report is `HUMAN_ACTION_REQUIRED` and includes
the exact automated attempts. The agent never claims `PROJECT_READY` in that
case and never performs a musical write during orchestration.

This policy does not change Analyzer, Capture, EvidenceGraph, Astra, or the
SafeWrite authority. It only prepares a verified environment for those
existing contracts.

Current-host validation (2026-09-21): `python -m copilot.cli doctor` returned
`READY` with Ableton Live 12.4.6, `SESSION_READY`, the Remote Script bridge,
and no open journals or transactions. The musical project topology remains a
separate readiness gate; this milestone certifies platform discovery and
environment orchestration only.
