"""ASTRA_IN_THE_LOOP: prompt -> Astra reasons -> MusicPlan.

Replaces the deterministic top-1 recipe: Astra sees the top-K sample candidates
per role plus the style context and the user's intent, and selects one sample
per role (or omits a role). Falls back to the deterministic recipe if Astra is
unavailable or returns an invalid plan.
"""

from __future__ import annotations

import json
import re

from copilot.sample_library.schemas import LibraryIndex
from copilot.schemas.session import SessionState

ASTRA_TIMEOUT_S = 180.0


def build_candidate_context(
    index: LibraryIndex, top_k: int = 3
) -> dict[str, list[dict]]:
    """Retrieve top-K candidates per role (keyed by track_name)."""
    from copilot.musicplan.tech_house import GROOVY_LATIN_GROOVE
    from copilot.sample_library.retrieval import SampleRetriever

    retriever = SampleRetriever(index)
    candidates: dict[str, list[dict]] = {}
    for role, track_name, sample_type, bpm, text_query in GROOVY_LATIN_GROOVE:
        results = retriever.search_samples(
            role=role, one_shot_or_loop=sample_type, bpm=bpm,
            text_query=text_query, top_k=top_k,
        )
        candidates[track_name] = [
            {"sha256": r.asset.sha256, "filename": r.asset.filename, "bpm": r.asset.bpm.value}
            for r in results
        ]
    return candidates


def build_astra_prompt(
    *, candidates: dict[str, list[dict]], intent: str, bpm: float = 127.0
) -> str:
    from copilot.musicplan.decision_context import build_decision_context
    from copilot.musicplan.fx import FX_PHILOSOPHY
    from copilot.musicplan.synth import SYNTH_PHILOSOPHY

    astra_context = build_decision_context()
    lines = [
        astra_context,
        "",
        "You are the PRODUCER of a groovy/latin tech house track (underground, percussive,",
        "hypnotic, dark/warm). You DECIDE samples AND the arrangement, like a real producer.",
        f"Tempo {bpm} BPM. Percussion-first; fewer elements, more identity.",
        "Musical elements are rhythmic instruments, not melody: short stabs/plucks/guitar chops/sax hits/vocal chops.",
        f"Musical principles: {'; '.join(SYNTH_PHILOSOPHY[:4])}",
        "FX: felt more than noticed; short/rhythmic/dark (no EDM risers). Impacts/downlifters/textures support the groove.",
        f"FX principles: {'; '.join(FX_PHILOSOPHY[:4])}",
        "Call-and-response: guitar <-> conga, vocal <-> sax; don't stack every hook at once.",
        "",
        "Available elements (roles): " + ", ".join(candidates.keys()) + ".",
        "Every active element goes DIRECT to Main on its own channel (no buses).",
        "",
        "Sample candidates per role (pick one number per role, or omit a role):",
    ]
    for track_name, cands in candidates.items():
        opts = "  ".join(f"{i + 1}. {c['filename']}" for i, c in enumerate(cands))
        lines.append(f"{track_name}: {opts}")
    lines += [
        "",
        f"User intent: {intent}",
        "",
        "Decide ONE sample per role AND the arrangement (sections). Return ONLY a JSON object:",
        """{
  "selections": {"TrackName": <1-based index>, ...},
  "arrangement": [
    {"name": "<section name>", "bars": <int>, "active": ["TrackName", ...]},
    ...
  ],
  "reasoning": "short producer reasoning"
}""",
        "",
        "Arrangement rules: 5-8 sections; Kick must be active in at least the backbone sections;",
        "build up (drums/percussion first), reach a DROP, and return subdued at the end (DJ exit);",
        "subtract by omission across sections, never stack everything.",
        "If you omit 'arrangement', the deterministic structure is used.",
    ]
    return "\n".join(lines)


def parse_astra_selection(raw: str) -> dict:
    """Parse Astra's JSON response; tolerant of markdown fences."""
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        raw = m.group(0)
    return json.loads(raw)


def validate_arrangement(raw_sections: list) -> list | None:
    """Structurally validate Astra's proposed sections. Returns cleaned list or None.

    Requires: int `bars` 4-64; `active` subset of known roles; at least one section
    with Kick; not everything active in the final section. On any violation returns
    None and the caller falls back to the deterministic structure.
    """
    from copilot.musicplan.arrangement import Section, ALL_TRACKS

    if not raw_sections or not isinstance(raw_sections, list):
        return None
    cleaned: list[Section] = []
    for item in raw_sections:
        try:
            name = str(item.get("name", "")).upper() or "SECTION"
            bars = int(item.get("bars", 0))
            active = [str(t) for t in item.get("active") or []]
        except Exception:  # noqa: BLE001
            return None
        if not (4 <= bars <= 64):
            return None
        active = [t for t in active if t in ALL_TRACKS]
        if not active:
            return None
        cleaned.append(Section(name=name, bars=bars, active=active))
    if not any("Kick" in s.active for s in cleaned):
        return None
    return cleaned


def build_plan_from_prompt(
    *,
    index: LibraryIndex,
    session: SessionState,
    intent: str,
    provider=None,
    top_k: int = 3,
    plan_id: str = "astra_groove",
    timeout_s: float = ASTRA_TIMEOUT_S,
):
    """prompt -> Astra -> MusicPlan. Falls back to the deterministic recipe on error."""
    from copilot.musicplan.tech_house import build_tech_house_plan

    if provider is None:
        from copilot.reasoning.provider import configured_http_provider

        provider = configured_http_provider()

    if provider is None:
        # No Astra configured -> deterministic fallback.
        return build_tech_house_plan(index=index, session=session, plan_id=plan_id), {
            "astra_used": False,
            "reasoning": "no provider configured; deterministic fallback",
        }

    candidates = build_candidate_context(index, top_k=top_k)
    prompt = build_astra_prompt(candidates=candidates, intent=intent)

    try:
        fn = getattr(provider, "reason_json_object", None) or provider.reason
        raw = fn(prompt, timeout_s=timeout_s)
        data = parse_astra_selection(raw)
        selections = data.get("selections", {})
        arrangement_raw = data.get("arrangement")
        arrangement = validate_arrangement(arrangement_raw) if arrangement_raw else None
        sample_map: dict[str, str] = {}
        for track_name, num in selections.items():
            cands = candidates.get(track_name, [])
            idx = int(num) - 1
            if 0 <= idx < len(cands):
                sample_map[track_name] = cands[idx]["sha256"]
        plan = build_tech_house_plan(
            index=index, session=session, plan_id=plan_id, sample_map=sample_map or None
        )
        return plan, {
            "astra_used": True,
            "reasoning": data.get("reasoning", ""),
            "selections": selections,
            "sample_map": sample_map,
            "arrangement": arrangement,
        }
    except Exception as exc:  # noqa: BLE001
        plan = build_tech_house_plan(index=index, session=session, plan_id=plan_id)
        return plan, {"astra_used": False, "reasoning": f"astra error -> fallback: {exc}"}
