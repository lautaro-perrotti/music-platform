# LUCAS_BRANCH_INTEGRATION_AUDIT_V1

Date: 2026-09-17
Repository: `<repo-root>`

## Verdict

**LUCAS_BRANCH_AUDIT = INCOMPLETE — the branch does not exist.**

This is not a search failure. The audit brief instructed "do not guess Lucas's
branch name; use git evidence." The git evidence is unambiguous and is recorded
below in full. There is no parallel branch, no second contributor, and no
unmerged work to classify.

## Evidence

```
$ git branch -a -vv
* main                3caa792 [origin/main] Keep shell installers as LF ...
  remotes/origin/HEAD -> origin/main
  remotes/origin/main 3caa792 Keep shell installers as LF ...

$ git fetch --all --prune          # no output: nothing new to fetch
$ git ls-remote --heads origin
3caa7929be8ace7db9b0d69434610e0421e4dddb        refs/heads/main

$ git for-each-ref
refs/heads/main          3caa792  2026-09-17  LSP20xx
refs/remotes/origin/HEAD 3caa792  2026-09-17  LSP20xx
refs/remotes/origin/main 3caa792  2026-09-17  LSP20xx
refs/stash               3fa885c  2026-09-16  LSP20xx

$ git log --all --format='%an <%ae>' | sort | uniq -c
      8 LSP20xx <lsperrotti@gmail.com>

$ git fsck --lost-found            # no dangling commits
$ git worktree list
<repo-root> 3caa792 [main]
```

Findings:

- **One branch**: `main`, fully in sync with `origin/main`.
- **One remote**: `origin` → `github.com/lautaro-perrotti/music-platform`, which
  advertises exactly one head.
- **One author** across all 8 reachable objects: `LSP20xx <lsperrotti@gmail.com>`.
- **No merge base to compute**, because there is no second branch.
- **No unreachable/dangling commits** (`git fsck --lost-found` is empty).
- **No additional worktrees.**
- **Working tree is clean** — zero uncommitted changes.

Therefore: no commit list, no changed-file inventory, no merge-conflict risk,
no code-ownership conflict with the causal-analysis work, and no integration
plan. Phases 1 and 28 of the brief have no subject matter.

If work exists elsewhere, it is not in this repository or its remote. It would
have to be supplied as a bundle, a patch, an unpushed clone on another machine,
or a fork URL before it can be audited.

---

## The one piece of parallel work that does exist: `stash@{0}`

`refs/stash` is the only object in the repository that is not reachable from
`main`. It is one commit by the same author.

```
stash@{0}: WIP on main: d7309c2 Add AME V3 and freeze source isolation ...
 src/copilot/cli.py | 92 +++++++++++++++++++++++++++++++++++++++++++++++++-
 1 file changed, 91 insertions(+), 1 deletion(-)
```

### Contents

Three additions to `cli.py`, all CLI plumbing:

1. A `project-bootstrap` command with a `_project_bootstrap` handler.
2. A `--cross-project-bootstrap` flag on `production-write`, dispatching to
   `cross_project_bootstrap_v1.run_cross_project_bootstrap_v1`.
3. A `--cross-project-preflight` flag on `production-write`, dispatching to
   `cross_project_preflight_v1.run_cross_project_preflight_v1`.

### Classification

| item | classification | reason |
| --- | --- | --- |
| `project-bootstrap` command | **DUPLICATES_EXISTING_CAPABILITY** | Already on `main` (`cli.py:2428`), in `CANONICAL_COMMANDS`, and in the help epilogue. |
| `_project_bootstrap` handler | **REJECT — superseded** | The stash version calls `AbletonTcpAdapter()` and `connect()` directly. The version on `main` uses `_connect_live_or_block`, writes a blocker evidence artifact on failure, and returns the correct exit code. `main`'s is strictly better. |
| `--cross-project-bootstrap` | **BREAKS_ARCHITECTURE (would not run)** | Imports `run_cross_project_bootstrap_v1`, which does not exist. The function was renamed to `bootstrap_project` (`cross_project_bootstrap_v1.py:468`). This code path would raise `ImportError` at runtime. |
| `--cross-project-preflight` | **BREAKS_ARCHITECTURE (would not run)** | Imports `copilot.audio.cross_project_preflight_v1`, a module that does not exist anywhere in the tree. |
| `ensure_ascii=True` comment | **KEEP as documentation** | "Windows cp1252 consoles choke on arrows in nested dumps." Independently reconfirmed during this audit — see below. |

### Does it still apply?

No.

```
$ git apply --check stash.patch
error: patch failed: src/copilot/cli.py:99
error: src/copilot/cli.py: patch does not apply
```

`cli.py` has moved substantially since `d7309c2`.

### Integration decision

**REJECT the stash. Do not apply it.**

Every behaviour it adds either already exists on `main` in a better form, or
targets modules that were since renamed or removed. Applying it would produce a
duplicate `_project_bootstrap` definition and two `production-write` flags that
fail with `ImportError`.

No useful work is destroyed by this decision — `project-bootstrap` shipped, and
`CROSS_PROJECT_BOOTSTRAP_V1` is reachable today via `bootstrap_project`. The
only orphan is `CROSS_PROJECT_PREFLIGHT_V1`: `logs/cross_project_preflight_v1.json`
(33 KB, 2026-09-16) proves it was executed at least once, but its implementing
module is gone from the tree. That artifact is the sole surviving evidence of a
capability the codebase no longer has.

**Recommended action:** decide explicitly whether `CROSS_PROJECT_PREFLIGHT_V1`
is still wanted.
- If yes, re-specify it from the recorded artifact's shape
  (`isolation_dependency`, `new_project_check`, `discovery`, `STOP`) rather than
  from the stash, which only ever contained the CLI wiring, never the module.
- If no, `git stash drop stash@{0}` to stop it appearing as pending work.

Either way the stash itself contributes nothing and should not be merged.

---

## Corroborated finding

The stash comment about Windows consoles is correct and still live. During this
audit, `print()` of box-drawing characters in the new performance tracer raised:

```
UnicodeEncodeError: 'charmap' codec can't encode characters in position 69-70
  File "...\Lib\encodings\cp1252.py", line 19, in encode
```

`copilot/perf/trace.py:render()` now defaults to ASCII glyphs, and
`tests/test_performance_trace_v1.py::test_render_is_ascii_safe_for_the_windows_console`
asserts the output encodes as cp1252. Worth applying the same rule to any future
console output: this environment is cp1252, not UTF-8.
