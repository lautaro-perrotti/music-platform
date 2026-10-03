"""One real Lucas prompt interpretation, then offline symbolic candidates.

No Ableton access or musical writes. Persisted results are reused on rerun;
the model is never called again once the accepted interpretation exists.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from copilot.producer.prompt_groove_v1 import (
    generate_prompt_groove_candidates, select_structural_candidate,
)
from copilot.producer.track_spec import PromptTranslationResult, translate_planner_payload
from copilot.reasoning.provider import configured_http_provider


BRIEF = """Create a dark, hypnotic tech-house groove.

125 BPM. 4/4. 16 bars.
Strong but restrained four-on-the-floor kick.
Syncopated repetitive bassline with a memorable rhythmic identity.
Sparse minor-key harmony. One short melodic or synth hook.
Progressive energy through layering and subtraction, not an EDM-style buildup.
Club-oriented and repetitive, but with enough variation to avoid sounding like a static loop.
No vocals."""


def _planner_prompt() -> str:
    return (
        "You are Lucas, the producer planner. Interpret the user's brief as musical intent only. "
        "Return a single JSON object with a 'track_spec' object; no Ableton commands, "
        "no source audio, no reference track, no measurements invented. "
        "The TrackSpec requires title, intent, bpm=125, key as an explicit minor key "
        "you choose, meter_numerator=4, meter_denominator=4, duration_bars=16, "
        "vocals='none', style, primary_hook as a short string of at most 160 characters, "
        "hook_role='HOOK', and sections. "
        "Each section requires name, bars, energy 0..1, active_roles, variation, transition. "
        "Sections must sum to 16 bars and have audible layering/subtraction. "
        "For this bounded executable experiment, active_roles may use only these labels: "
        "KICK, HAT, BASS, HARMONY, HOOK. Across all sections, use all five. "
        "If sound_palette is included, it MUST be an object mapping each role to an array "
        "of strings, never a single string. Describe constraints as an array of strings. "
        "Do not copy music. "
        "Include a 'reasoning' string explaining the section/energy/key choices. "
        "User brief verbatim:\n" + BRIEF
    )


def run(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    interpretation_path = output_dir / "lucas_track_spec.json"
    raw_path = output_dir / "lucas_raw_response.json"
    if interpretation_path.exists():
        saved = json.loads(interpretation_path.read_text(encoding="utf-8"))
        translation = PromptTranslationResult.model_validate(saved["translation"])
        if saved["brief"] != BRIEF:
            raise RuntimeError("PERSISTED_BRIEF_MISMATCH")
        reused = True
    else:
        if raw_path.exists():
            raw_record = json.loads(raw_path.read_text(encoding="utf-8"))
            if raw_record["brief"] != BRIEF:
                raise RuntimeError("PERSISTED_BRIEF_MISMATCH")
            payload = raw_record["raw_response"]
            provider = None
            provider_identity = raw_record["provider"]
            model = raw_record["model"]
            contract = raw_record["provider_contract"]
            prompt = raw_record["planner_prompt"]
        else:
            provider = configured_http_provider()
            if provider is None:
                raise RuntimeError("LUCAS_PROVIDER_NOT_CONFIGURED")
            prompt = _planner_prompt()
            raw = provider.reason_json_object(prompt, timeout_s=90)
            payload = json.loads(raw)
            provider_identity = provider.identity
            model = provider.version
            contract = provider.last_request_contract
            raw_path.write_text(json.dumps({
                "brief": BRIEF, "planner_prompt": prompt,
                "provider": provider_identity, "model": model,
                "provider_contract": contract, "raw_response": payload,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        translation = translate_planner_payload(
            payload, provider=SimpleNamespace(identity=provider_identity, version=model),
        )
        saved = {
            "brief": BRIEF, "planner_prompt": prompt,
            "provider": provider_identity, "model": model,
            "provider_contract": contract,
            "raw_response": payload,
            "translation": translation.model_dump(mode="json"),
        }
        interpretation_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        reused = False
    candidates = generate_prompt_groove_candidates(translation.track_spec)
    chosen, reason = select_structural_candidate(candidates)
    candidate_path = output_dir / "symbolic_candidates.json"
    if candidate_path.exists():
        prior = json.loads(candidate_path.read_text(encoding="utf-8"))
        if prior["selected_candidate"] != chosen.candidate_id:
            raise RuntimeError("PERSISTED_CANDIDATE_MISMATCH")
    else:
        candidate_path.write_text(json.dumps({
            "brief": BRIEF,
            "track_spec": translation.track_spec.model_dump(mode="json"),
            "candidates": [c.model_dump(mode="json") for c in candidates],
            "selected_candidate": chosen.candidate_id,
            "selection_kind": "STRUCTURAL_SELECTION",
            "selection_reason": reason,
            "musical_winner": None,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ACCEPTED", "model": saved["model"],
            "interpretation_reused": reused, "key": translation.track_spec.key,
            "sections": [s.model_dump(mode="json") for s in translation.track_spec.sections],
            "selected_candidate": chosen.candidate_id,
            "candidate_note_counts": {c.candidate_id: {role: len(notes) for role, notes in c.notes_by_role.items()}
                                      for c in candidates},
            "interpretation_path": str(interpretation_path), "candidate_path": str(candidate_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir), ensure_ascii=False, indent=2))
