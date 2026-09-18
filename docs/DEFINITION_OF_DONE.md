# DEFINITION_OF_DONE.md

A milestone may say `VERIFIED` only if all of the following hold.

## Product

1. Public behavior is reachable from Producer Runtime (or a documented debug CLI).
2. Agent-facing Analyze still has command count 1 unless the milestone is a new
   public task with its own `producer.<task>`.
3. `MUSICAL WRITES` is 0 for observation/analysis work.
4. Terminal safety holds on the success path and on documented failure paths.

## Precision

5. Precision parity vs the previous verified baseline on the same project/region:
   identity, tracks/sources, region, capture count, signal class, routing,
   monitoring, sends, devices, EvidenceRefs, FullMix/LowEnd observations,
   Astra **gate/status** (wording may differ), terminal.
6. No missing evidence, no reduced source coverage, no weakened alignment claim.
7. Tests were not edited to expect less safety so the optimization could pass.

## Safety

8. Invariants in `docs/SAFETY_INVARIANTS.md` still hold.
9. `IN_DOUBT`, partial failure, cancel, and disconnect have explicit tests or a
   documented BLOCKED reason (not silence).
10. Sequential fallback exists if a Live capability is optional.

## Performance

11. Before/after numbers are measured, not inferred from unit tests.
12. RPC accounting is method-by-method when the milestone is an RPC change.
13. Wall split is attributed; `UNATTRIBUTED ≈ 0` or explained.
14. Astra variance is reported separately from Ableton/capture savings.

## Freeze

15. Docs, capability matrix, and runtime comments agree on VERIFIED vs FROZEN.
16. Reopen rules are stated: reproducible bug only, unless the next named
    milestone explicitly supersedes.

## BLOCKED is allowed

If Live cannot execute a new Remote Script command without an unauthorized CS
reload, the client path may be implemented + tested, and Live measurement of
the **fallback** reported. Compound execution is not VERIFIED until it actually
ran against Live. Do not pretend.
