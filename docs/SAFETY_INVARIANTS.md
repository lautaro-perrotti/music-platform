# SAFETY_INVARIANTS.md

These do not yield to performance. An optimization that violates one is invalid
even if RPC count drops.

Detail for tokens: `docs/core/STATE_TRUST.md`.
Detail for capture views: `docs/audio/SUPPORTED_CAPTURE_ENVELOPE.md`.

## Identity

1. Index is a locator, never identity.
2. Reconciliation is exactly one of `RESOLVED` / `TARGET_NOT_FOUND` /
   `TARGET_AMBIGUOUS` / `PROJECT_MISMATCH`. No best guess.
3. `PROJECT_MISMATCH` executes zero mutation steps. Do not auto-heal identity.
4. Untitled/unsaved sets are not a durable project identity.

## Writes

5. AnalyzeProject musical writes = 0.
6. Temporary capture-host mutations are allowed only with durable pre-state,
   per-host journals, restore, and terminal verification.
7. Core persists rollback **before** Live mutates.
8. Lost ack after dispatch is `IN_DOUBT`. Never blindly retry the mutation.
9. Remote Script does not hidden-rollback. Core is the recovery authority.
10. Compound operations return per-step statuses (`APPLIED` / `FAILED` /
    `NOT_ATTEMPTED` / `UNKNOWN`). Never a single `success=true` for N steps.
11. Partial failure is visible. No fake atomicity.

## Capture isolation

12. A source host must not leak into Main (`through_main` is hard failure).
13. Accepted isolation claims: `OFF_MIX_GRAPH`, `OFF_DIRECT_MAIN` only.
14. Duplicate tap slots fail closed.
15. One source failing never reports success for another.
16. Restore mismatch is `RESTORE_VERIFICATION_FAILED` / fail closed.
17. Terminal: transport stopped, taps idle, hosts restored, no open capture
    journals, no unresolved `IN_DOUBT`.

## Observation honesty

18. No musical fact exists because Astra said it. Grounding validates refs.
19. `INSUFFICIENT_EVIDENCE` is a valid success of the pipeline, not a crash.
20. Do not collapse producer statuses to make a demo green.
21. Alignment remains `LIMITED ±52 ms` until recertified with a new capability key.
22. Constant tempo only. Tempo automation fail-closed.
23. `PRE_ROLL_QN = 16` is part of the observation contract, not a knob.

## Protocol

24. Localhost only.
25. One JSON request / one JSON response. No concatenated frames.
26. No eval / LOM expressions / LLM-supplied method names on the Remote Script.
27. Whitelist operations only for compound mutation V1 (temporary observation).
28. Unsupported Remote Script versions use the sequential path. Product stays usable.
29. Do not kill/relaunch the user's Live session unless explicitly authorized.

## Working copy

30. Refuse original sets (`pista.als` and policy equivalents).
31. Autonomous musical writes: development working copy only, after gate.
32. External songs: read-only until `CROSS_PROJECT_MUSICAL_VALIDATION_V1`.
