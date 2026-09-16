from __future__ import annotations

from typing import Any

from copilot.reasoning.schema import ReasoningOutput
from copilot.schemas.diagnosis import FindingType

SCHEMA_NAME = "music_diagnosis"


def reasoning_json_schema() -> dict[str, Any]:
    """Existing ReasoningOutput schema, strictified for OpenAI Structured Outputs.

    Does not add invented enum values such as LOWEND_OVERLAP.
    """
    raw = ReasoningOutput.model_json_schema()
    defs = {key: _strict_node(value) for key, value in (raw.get("$defs") or {}).items()}
    root = {key: value for key, value in raw.items() if key != "$defs"}
    root = _strict_node(root)
    if defs:
        root["$defs"] = defs
    return root


def schema_uses_existing_category_enum(schema: dict[str, Any]) -> bool:
    finding = (schema.get("$defs") or {}).get("FindingType") or {}
    values = list(finding.get("enum") or [])
    allowed = {item.value for item in FindingType}
    return set(values) == allowed and "LOWEND_OVERLAP" not in values


def _strict_node(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return {"$ref": node["$ref"]}
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in {"title", "default"}:
            continue
        if key == "anyOf":
            out[key] = [_strict_node(item) for item in value]
        elif key == "items":
            out[key] = _strict_node(value)
        elif key == "properties":
            out[key] = {name: _strict_node(item) for name, item in value.items()}
        elif key == "$defs":
            out[key] = {name: _strict_node(item) for name, item in value.items()}
        else:
            out[key] = value
    if "properties" in out:
        out["type"] = "object"
        out["additionalProperties"] = False
        out["required"] = list(out["properties"].keys())
    return out
