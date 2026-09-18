"""Post-build structured critique (the critic in the producer operating system).

Structural readback of the built track -> Astra self-evaluation -> top-3 issues +
a finalize|improve verdict. This is NOT an audio analysis: it critiques what the
plan + readback can verify (structure, density, routing, chains, arrangement) and
explicitly flags what requires listening (low-end translation, mono, balance).
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from copilot.schemas.musicplan import MusicPlan, PlanAction
from copilot.schemas.session import SessionState

CRITIQUE_TIMEOUT_S = 120.0


class CritiqueIssue(BaseModel):
    priority: int
    area: str
    issue: str
    minimal_fix: str


class CritiqueResult(BaseModel):
    verdict: str  # "finalize" | "improve"
    top_3_issues: list[CritiqueIssue] = Field(default_factory=list)
    reasoning: str = ""


def _ref_name(action: PlanAction) -> str:
    ref = action.target.ref
    if isinstance(ref, dict):
        return str(ref.get("name", "?"))
    return getattr(ref, "name", "?")


def build_track_state_summary(*, plan: MusicPlan, session: SessionState) -> str:
    """Readback: the structural state of the built track (no audio)."""
    from copilot.musicplan.tech_house import GROOVY_LATIN_GROOVE

    by_type = {
        "CREATE_TRACK": [a for a in plan.actions if a.action_type.value == "CREATE_TRACK"],
        "SAMPLE_LOAD": [a for a in plan.actions if a.action_type.value == "SAMPLE_LOAD"],
        "CREATE_PATTERN": [a for a in plan.actions if a.action_type.value == "CREATE_PATTERN"],
        "DEVICE_LOAD": [a for a in plan.actions if a.action_type.value == "DEVICE_LOAD"],
        "SET_TRACK_ROUTING": [a for a in plan.actions if a.action_type.value == "SET_TRACK_ROUTING"],
        "SET_DEVICE_ROUTING": [a for a in plan.actions if a.action_type.value == "SET_DEVICE_ROUTING"],
    }

    # Elements (audio vs MIDI-groove percussion).
    elements: list[str] = []
    midi_percussion: list[str] = []
    for a in by_type["CREATE_TRACK"]:
        name = a.params.track_name
        if name in ("DRUMS", "BASS BUS", "SYNTHS", "FX BUS", "VOCALS"):
            continue
        elements.append(name)
    midi_percussion = [_ref_name(a) for a in by_type["CREATE_PATTERN"]]

    # Routing element -> bus.
    routing: dict[str, str] = {}
    for a in by_type["SET_TRACK_ROUTING"]:
        routing[_ref_name(a)] = str(a.params.routing_type)

    # Mixing chains per element.
    chains: dict[str, list[str]] = {}
    for a in by_type["DEVICE_LOAD"]:
        chains.setdefault(_ref_name(a), []).append(str(a.params.device_name))

    # Sidechain.
    sidechain: list[str] = []
    for a in by_type["SET_DEVICE_ROUTING"]:
        sidechain.append(
            f"{_ref_name(a)}.device[{a.params.device_index}] <- {a.params.routing_type} "
            f"({a.params.routing_channel})"
        )

    # Final section state (from the live session): active vs muted elements.
    muted = {t.name for t in session.tracks if t.mixer.mute}
    active = [e for e in elements if e not in muted]

    lines: list[str] = []
    lines.append(f"BPM: 127 | elements: {len(elements)} | active in final section: {len(active)}")
    lines.append(f"Elements ({len(elements)}): {', '.join(elements)}")
    lines.append(f"MIDI groove percussion (Simpler + swing pattern): {', '.join(midi_percussion)}")
    lines.append(f"Muted in final section (DROP): {', '.join(sorted(muted)) or 'none'}")
    lines.append(f"Active in final section: {', '.join(active)}")
    lines.append("Routing (element -> bus): " + "; ".join(f"{k}->{v}" for k, v in sorted(routing.items())))
    lines.append("Sidechain: " + ("; ".join(sidechain) if sidechain else "none"))
    for e in elements:
        devs = chains.get(e, [])
        if devs:
            lines.append(f"  chain[{e}]: {' -> '.join(devs)}")
    lines.append("Arrangement: 7 sections; guitar <-> sax call-and-response (DROP uses guitar, BREAK/DROP2 use sax).")
    return "\n".join(lines)


def build_critique_prompt(*, state_summary: str) -> str:
    from copilot.musicplan.decision_system import build_decision_context

    return "\n".join(
        [
            build_decision_context(),
            "",
            "=== STRUCTURAL READBACK OF THE BUILT TRACK (no audio available) ===",
            state_summary,
            "",
            "Critique what the readback can verify (structure, density, routing, chains, arrangement).",
            "Flag audio-only concerns (mono translation, low-end translation, final balance) as 'needs listening'.",
            "Return ONLY a JSON object:",
            '{"verdict": "finalize"|"improve", "top_3_issues": [{"priority": 1-3, "area": "groove|low_end|identity|arrangement|density|mix|dancefloor", "issue": "...", "minimal_fix": "the smallest effective intervention"}], "reasoning": "short"}',
        ]
    )


def parse_critique(raw: str) -> CritiqueResult:
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        raw = m.group(0)
    data = json.loads(raw)
    return CritiqueResult(
        verdict=str(data.get("verdict", "improve")),
        top_3_issues=[CritiqueIssue(**i) for i in data.get("top_3_issues", [])],
        reasoning=str(data.get("reasoning", "")),
    )


def critique_track(
    *,
    plan: MusicPlan,
    session: SessionState,
    provider=None,
    timeout_s: float = CRITIQUE_TIMEOUT_S,
) -> CritiqueResult | None:
    """Run Astra's structured self-evaluation. Returns None if Astra is unavailable."""
    if provider is None:
        from copilot.reasoning.provider import configured_http_provider

        provider = configured_http_provider()
    if provider is None:
        return None

    summary = build_track_state_summary(plan=plan, session=session)
    prompt = build_critique_prompt(state_summary=summary)

    try:
        fn = getattr(provider, "reason_json_object", None) or provider.reason
        raw = fn(prompt, timeout_s=timeout_s)
        return parse_critique(raw)
    except Exception:  # noqa: BLE001 — critique is advisory; never block the build on it
        return None
