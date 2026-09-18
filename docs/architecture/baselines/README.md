# Committed performance baselines

Canonical measurements. These survive a clone; `logs/` does not (it is runtime
and gitignored).

| file | what it is | confidence |
| --- | --- | --- |
| `performance_baseline_v1.json` | RPC latency, local CPU/IO, model latency, regression counts | MEASURED |
| `runtime_execution_graph_v1.json` | traced execution graph per workflow, with RPC costs | MEASURED + traced |
| `wait_inventory_v1.json` | every sleep/poll/backoff in `src/`, classified | AST-derived |
| `critical_path_v1.json` | per-operation intrinsic / RPC / model / overhead split | MEASURED |
| `producer_analyze_batching_v1.json` | before/after for SOURCE_CAPTURE_BATCH_V1 | MEASURED |
| `physical_dsp_v2.json` | PHYSICAL_DSP_V2 CPU/wall/cache on synthetic 2 s audio | MEASURED (not Live) |

## Rules

- A number here is either MEASURED or explicitly labelled otherwise. No
  estimates presented as measurements.
- Regenerate the live parts with
  `python -m copilot.cli performance-report`, and traced runs with
  `--trace-performance` on `producer-analyze` / `production-write`.
- Do not point tests at `logs/`. It is a set of mutable runtime slots — a real
  product run overwrites them. Frozen test inputs belong in `fixtures/frozen/`.
