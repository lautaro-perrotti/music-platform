# CAPTURE_HOST_BASELINE_V1

Status: **VERIFIED / FROZEN**.

A newly created Live audio track is initialization input, not product state.
Live defaults (`Ext. In` + `Main`) must not become a capture-host rollback
baseline.

## Canonical parked state

Idle Copilot capture host (`CaptureHostParkedState`):

- input: `Resampling` (empty channel)
- output: `Sends Only` (`No Output` accepted as equivalent)
- monitoring: `off`
- Rec: 0
- Device On: 1
- sends silent
- assigned Slot
- discoverable as a Copilot capture host

Forbidden while parked: `Ext. In`, `Main`/`Master`, leftover source routing,
armed recording.

## Lifecycle

```
HOST_CREATED
    → HOST_NORMALIZING
    → HOST_PARKED_VERIFIED
    → HOST_AVAILABLE
    → TEMPORARY_CAPTURE_MUTATION
    → CAPTURE
    → RESTORE_TO_PARKED_BASELINE
    → HOST_AVAILABLE
```

A host is not pool capacity until parked state is applied and freshly
verified via `READ_CAPTURE_HOST_STATE`. Failed init is
`HOST_PROVISION_FAILED` and is not advertised. Capture against a
non-parked host is `HOST_NOT_READY` (fail closed, no mutation).

Provisioning is Copilot infrastructure initialization, not a rollback of
Live defaults. After init, the parked state is the legitimate baseline.

## Groove Rider recovery

`Copilot Capture 3` / `Copilot Capture 4` were repaired in place (Copilot-owned
infra only). Historical capture journals `de45b2377087_*` remain `FAILED`.
Independent `HOST_RECOVERY` journals record the repair.
