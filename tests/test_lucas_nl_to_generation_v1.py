"""Offline contract tests: Lucas interpretation never invokes music inference."""

import json

from copilot.music_generation.lucas_nl_brief import (
    lucas_nl_to_generation_brief, prepare_lucas_generation_request,
)
from copilot.music_generation.registry import MusicGeneratorRegistry
from copilot.music_generation.schemas import BriefFieldOrigin
from copilot.music_generation.stable_audio import StableAudio3Provider


class LucasJSON:
    identity = "lucas-contract-test"

    def __init__(self, **proposal):
        self.proposal = proposal
        self.calls = 0

    def reason_json_object(self, prompt, *, timeout_s=30.0):
        self.calls += 1
        assert "No DAW actions" in prompt
        return json.dumps(self.proposal)


def _stable_route(brief, tmp_path):
    registry = MusicGeneratorRegistry()
    registry.register(StableAudio3Provider(api_url=""))  # No worker and no inference.
    return registry.prepare_request(brief, seed=831101, output_dir=tmp_path)


def test_fully_specified_nl_to_stable_request_without_inference(tmp_path):
    lucas = LucasJSON(
        tempo_bpm=125, meter="4/4", target_duration_s=30, instrumental=True,
        groove_intent={"value": "restrained four-on-the-floor kick", "source_quote": "kick four-on-the-floor contenido"},
        key_context={"value": "sparse minor harmony", "source_quote": "armonía menor escasa"},
        structural_intent=[{"value": "progressive layering and subtraction", "source_quote": "agregando y sacando capas"}],
    )
    text = ("Haceme un tech-house oscuro e hipnótico a 125 BPM, 4/4, instrumental, "
            "30 segundos, con kick four-on-the-floor contenido, bajo sincopado repetitivo, "
            "armonía menor escasa y un hook corto de synth. Que evolucione agregando y sacando capas.")
    handoff = lucas_nl_to_generation_brief(text, reasoning_provider=lucas)
    assert handoff.status == "READY"
    brief = handoff.brief
    assert brief.user_intent == text
    assert (brief.tempo_bpm, brief.meter, brief.target_duration_s, brief.instrumental) == (125, "4/4", 30, True)
    assert brief.field_origins["target_duration_s"] == BriefFieldOrigin.USER_EXPLICIT
    assert brief.field_origins["groove_intent"] == BriefFieldOrigin.MODEL_INTERPRETATION
    assert brief.field_evidence_quotes["groove_intent"] == "kick four-on-the-floor contenido"
    route = _stable_route(brief, tmp_path)
    assert route.status == "ROUTED_UNAVAILABLE"  # Correct provider, no running GPU.
    assert route.provider_id == "stable-audio-3"
    assert route.request.brief == brief
    assert route.request.seed == 831101
    assert route.request.no_ableton_access is True
    assert route.request.model_manifest.provider == "stable-audio-3"
    assert lucas.calls == 1


def test_single_entrypoint_prepares_request_without_running_provider(tmp_path):
    registry = MusicGeneratorRegistry()
    provider = StableAudio3Provider(api_url="")
    registry.register(provider)
    prepared = prepare_lucas_generation_request(
        "Tech-house instrumental a 125 BPM, 30 segundos.",
        reasoning_provider=LucasJSON(tempo_bpm=125, target_duration_s=30, instrumental=True),
        registry=registry, seed=42, output_dir=tmp_path,
    )
    assert prepared.handoff.status == "READY"
    assert prepared.route.provider_id == provider.provider_id
    assert prepared.route.request.seed == 42
    assert prepared.route.status == "ROUTED_UNAVAILABLE"
    assert not list(tmp_path.iterdir())


def test_missing_duration_uses_declared_product_default_not_user_fact(tmp_path):
    handoff = lucas_nl_to_generation_brief(
        "Haceme un tech-house oscuro a 125 BPM sin voces.",
        reasoning_provider=LucasJSON(tempo_bpm=125, instrumental=True),
    )
    assert handoff.status == "READY"
    assert handoff.brief.target_duration_s == 30.0
    assert handoff.brief.field_origins["target_duration_s"] == BriefFieldOrigin.PRODUCT_DEFAULT
    assert handoff.brief.tempo_bpm == 125
    assert _stable_route(handoff.brief, tmp_path).provider_id == "stable-audio-3"


def test_missing_tempo_and_meter_stay_unspecified(tmp_path):
    handoff = lucas_nl_to_generation_brief(
        "Haceme un track ambient oscuro sin voces.", reasoning_provider=LucasJSON(instrumental=True),
    )
    assert handoff.status == "READY"
    assert handoff.brief.tempo_bpm is None and handoff.brief.meter is None
    assert "tempo_bpm" not in handoff.brief.field_origins
    assert _stable_route(handoff.brief, tmp_path).provider_id == "stable-audio-3"


def test_ambiguous_vocal_request_abstains_before_reasoning():
    lucas = LucasJSON(instrumental=True)
    handoff = lucas_nl_to_generation_brief(
        "Quiero algo con una voz o textura vocal pero no sé.", reasoning_provider=lucas,
    )
    assert handoff.status == "CLARIFICATION_NEEDED"
    assert handoff.brief is None and lucas.calls == 0


def test_unsupported_vocal_request_does_not_fabricate_stable_support(tmp_path):
    handoff = lucas_nl_to_generation_brief(
        "Haceme una canción con voz.", reasoning_provider=LucasJSON(instrumental=False),
    )
    assert handoff.status == "READY" and handoff.brief.instrumental is False
    route = _stable_route(handoff.brief, tmp_path)
    assert route.status == "UNSUPPORTED" and route.request is None


def test_duration_outside_stable_capability_is_not_routed(tmp_path):
    handoff = lucas_nl_to_generation_brief(
        "Haceme música instrumental de 500 segundos.",
        reasoning_provider=LucasJSON(target_duration_s=500, instrumental=True),
    )
    assert handoff.status == "READY"
    route = _stable_route(handoff.brief, tmp_path)
    assert route.status == "UNSUPPORTED" and route.request is None


def test_explicit_stems_request_is_not_silently_recast_as_stereo_music(tmp_path):
    handoff = lucas_nl_to_generation_brief(
        "Separar los stems de esta canción.", reasoning_provider=LucasJSON(),
    )
    assert handoff.status == "READY"
    assert handoff.brief.generation_mode == "stem_separation"
    assert handoff.brief.requested_outputs == ["stems"]
    assert _stable_route(handoff.brief, tmp_path).status == "UNSUPPORTED"


def test_existing_exact_canonical_request_routes_to_stable(tmp_path):
    text = ("Dark hypnotic tech-house groove. Strong restrained four-on-the-floor kick, "
            "syncopated repetitive bassline, sparse minor-key harmony, short memorable synth hook, "
            "progressive layering and subtraction, club-oriented, instrumental, no vocals.")
    handoff = lucas_nl_to_generation_brief(text, reasoning_provider=LucasJSON(instrumental=True))
    assert handoff.status == "READY"
    route = _stable_route(handoff.brief, tmp_path)
    assert route.request is not None and route.request.brief.user_intent == text
    assert route.request.brief.target_duration_s == 30.0
    assert route.request.brief.field_origins["target_duration_s"] == BriefFieldOrigin.PRODUCT_DEFAULT
    assert route.request.brief.tempo_bpm is None  # Historical run had a typed 125 BPM override.


def test_lucas_cannot_invent_numeric_facts_or_unsupported_quotes():
    invented_tempo = lucas_nl_to_generation_brief(
        "Haceme algo oscuro", reasoning_provider=LucasJSON(tempo_bpm=125),
    )
    assert invented_tempo.status == "MODEL_OUTPUT_REJECTED"
    invented_style = lucas_nl_to_generation_brief(
        "Haceme algo oscuro", reasoning_provider=LucasJSON(
            groove_intent={"value": "fast techno", "source_quote": "fast techno"},
        ),
    )
    assert invented_style.status == "MODEL_OUTPUT_REJECTED"
    invented_duration = lucas_nl_to_generation_brief(
        "Haceme algo oscuro", reasoning_provider=LucasJSON(target_duration_s=30),
    )
    assert invented_duration.status == "MODEL_OUTPUT_REJECTED"
    invented_meter = lucas_nl_to_generation_brief(
        "Haceme algo oscuro", reasoning_provider=LucasJSON(meter="4/4"),
    )
    assert invented_meter.status == "MODEL_OUTPUT_REJECTED"
    extra_authority = lucas_nl_to_generation_brief(
        "Haceme algo oscuro", reasoning_provider=LucasJSON(ableton_command="create_track"),
    )
    assert extra_authority.status == "MODEL_OUTPUT_REJECTED"
