"""Offline comparison of persisted worker outputs; no model or Ableton calls.

Workers run their official runtimes independently, on resources measured on
that worker. This command consumes only the resulting GenerationBundle JSONs.
It never treats a missing provider output as a successful benchmark.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from copilot.music_generation.symbolic_bundle import GenerationBundle, compare_editability


PROVIDERS = frozenset({"OUR_SYMBOLIC", "YUE2", "MUSECOCO"})


def run(bundle_paths: list[Path]) -> dict[str, object]:
    bundles = [GenerationBundle.model_validate_json(path.read_text(encoding="utf-8"))
               for path in bundle_paths]
    providers = {bundle.provider for bundle in bundles}
    if not providers.issubset(PROVIDERS):
        raise ValueError("UNKNOWN_BENCHMARK_PROVIDER")
    rows = compare_editability(bundles)
    missing = sorted(PROVIDERS - providers)
    return {
        "status": "COMPARABLE_OUTPUTS_READY" if not missing else "PROVIDER_OUTPUTS_PENDING",
        "providers_missing": missing,
        "results": rows,
        "musical_winner": None,
        "model_calls": 0,
        "ableton_writes": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.bundle), ensure_ascii=False, indent=2))
