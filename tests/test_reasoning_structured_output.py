from __future__ import annotations

from copilot.reasoning.openai_schema import reasoning_json_schema, schema_uses_existing_category_enum
from copilot.reasoning.provider import _extract_responses_text, _uses_responses_api
from copilot.schemas.diagnosis import FindingType


def test_structured_schema_keeps_existing_category_enum() -> None:
    schema = reasoning_json_schema()
    assert schema_uses_existing_category_enum(schema)
    finding = schema["$defs"]["FindingType"]["enum"]
    assert "LOWEND_OVERLAP" not in finding
    assert "TEMPORAL_MASKING" in finding
    assert set(finding) == {item.value for item in FindingType}


def test_structured_schema_is_openai_strict() -> None:
    schema = reasoning_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    hypo = schema["$defs"]["GroundedHypothesis"]
    assert hypo["additionalProperties"] is False
    assert set(hypo["required"]) == set(hypo["properties"])


def test_astra_uses_responses_api() -> None:
    assert _uses_responses_api("gpt-6-astra") is True
    assert _uses_responses_api("gpt-4o-mini") is False


def test_extract_responses_output_text() -> None:
    payload = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"category":"NO_ACTION_REQUIRED"}'}],
            }
        ]
    }
    assert _extract_responses_text(payload) == '{"category":"NO_ACTION_REQUIRED"}'
    assert _extract_responses_text({"output_text": '{"ok":true}'}) == '{"ok":true}'
