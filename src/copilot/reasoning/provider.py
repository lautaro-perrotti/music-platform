from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from copilot.reasoning.errors import ProviderError, ReasoningFailure
from copilot.reasoning.openai_schema import SCHEMA_NAME, reasoning_json_schema
from copilot.reasoning.schema import ReasoningOutput


class ReasoningProvider(ABC):
    """Minimal vendor-neutral reasoning slot. Not a ModelRouter."""

    identity: str
    version: str

    @abstractmethod
    def reason(self, prompt: str, *, timeout_s: float = 30.0) -> str:
        """Return a JSON object matching ReasoningOutput. Never execute DAW ops."""


class ScriptedProvider(ReasoningProvider):
    """Deterministic fixture provider. Used in unit/eval tests."""

    def __init__(self, outputs: dict[str, ReasoningOutput | str], *, identity: str = "scripted") -> None:
        self.identity = identity
        self.version = "scripted-1"
        self._outputs = outputs
        self.calls = 0
        self.last_prompt = ""

    def reason(self, prompt: str, *, timeout_s: float = 30.0) -> str:
        del timeout_s
        self.calls += 1
        self.last_prompt = prompt
        key = _pack_id_from_prompt(prompt)
        payload = self._outputs.get(key) or self._outputs.get("*")
        if payload is None:
            raise ProviderError(ReasoningFailure.MODEL_UNAVAILABLE, f"no scripted output for {key}")
        if isinstance(payload, str):
            return payload
        return payload.model_dump_json()


class SequenceProvider(ReasoningProvider):
    """Returns successive payloads, then repeats the last one."""

    def __init__(self, payloads: list[str | ReasoningOutput], *, identity: str = "sequence") -> None:
        self.identity = identity
        self.version = "sequence-1"
        self._payloads = payloads
        self.calls = 0

    def reason(self, prompt: str, *, timeout_s: float = 30.0) -> str:
        del prompt, timeout_s
        idx = min(self.calls, len(self._payloads) - 1)
        self.calls += 1
        payload = self._payloads[idx]
        if isinstance(payload, str):
            return payload
        return payload.model_dump_json()


class FailingProvider(ReasoningProvider):
    def __init__(self, kind: ReasoningFailure, message: str = "provider failed") -> None:
        self.identity = "failing"
        self.version = "failing-1"
        self.kind = kind
        self.message = message

    def reason(self, prompt: str, *, timeout_s: float = 30.0) -> str:
        del prompt, timeout_s
        raise ProviderError(self.kind, self.message)


class OpenAICompatibleProvider(ReasoningProvider):
    """Optional HTTP smoke. Not required to close grounding validators."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        identity: str | None = None,
    ) -> None:
        self.identity = identity or "openai-compatible"
        self.version = model
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self.last_request_contract: dict[str, Any] = {}

    def reason(self, prompt: str, *, timeout_s: float = 30.0) -> str:
        if _uses_responses_api(self._model):
            return self._reason_responses(prompt, timeout_s=timeout_s)
        return self._reason_chat_json_object(prompt, timeout_s=timeout_s)

    def _reason_responses(self, prompt: str, *, timeout_s: float) -> str:
        schema = reasoning_json_schema()
        payload: dict[str, Any] = {
            "model": self._model,
            "reasoning": {"effort": os.environ.get("COPILOT_REASONING_EFFORT") or "high"},
            "input": [
                {
                    "role": "system",
                    "content": "Return only a JSON object. No markdown. No Ableton operations.",
                },
                {"role": "user", "content": prompt},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": SCHEMA_NAME,
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        self.last_request_contract = {
            "api": "responses",
            "endpoint": "/v1/responses",
            "text.format.type": "json_schema",
            "text.format.name": SCHEMA_NAME,
            "text.format.strict": True,
            "json_object": False,
            "temperature_sent": False,
            "top_p_sent": False,
            "tools": False,
            "reasoning.effort": payload["reasoning"]["effort"],
            "category_enum": list(
                ((schema.get("$defs") or {}).get("FindingType") or {}).get("enum") or []
            ),
        }
        raw = self._post("/responses", payload, timeout_s=timeout_s)
        text = _extract_responses_text(raw)
        if not text:
            raise ProviderError(ReasoningFailure.MODEL_OUTPUT_INVALID, "missing responses output_text")
        return text

    def _reason_chat_json_object(self, prompt: str, *, timeout_s: float) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return only a JSON object. No markdown. No Ableton operations.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        temperature = os.environ.get("COPILOT_REASONING_TEMPERATURE")
        if temperature not in (None, ""):
            payload["temperature"] = float(temperature)
        self.last_request_contract = {
            "api": "chat.completions",
            "endpoint": "/v1/chat/completions",
            "response_format.type": "json_object",
            "json_schema": False,
            "temperature_sent": "temperature" in payload,
        }
        raw = self._post("/chat/completions", payload, timeout_s=timeout_s)
        try:
            return str(raw["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(ReasoningFailure.MODEL_OUTPUT_INVALID, "missing message content") from exc

    def _post(self, path: str, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        request = Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise ProviderError(ReasoningFailure.MODEL_TIMEOUT, str(exc)) from exc
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise ProviderError(ReasoningFailure.MODEL_UNAVAILABLE, f"http {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ProviderError(ReasoningFailure.MODEL_UNAVAILABLE, str(exc.reason)) from exc


def configured_http_provider() -> OpenAICompatibleProvider | None:
    key = os.environ.get("COPILOT_REASONING_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        key = _windows_user_env("COPILOT_REASONING_API_KEY") or _windows_user_env("OPENAI_API_KEY")
    if not key:
        return None
    base = (
        os.environ.get("COPILOT_REASONING_BASE_URL")
        or _windows_user_env("COPILOT_REASONING_BASE_URL")
        or "https://api.openai.com/v1"
    )
    model = (
        os.environ.get("COPILOT_REASONING_MODEL")
        or _windows_user_env("COPILOT_REASONING_MODEL")
        or "gpt-4o-mini"
    )
    return OpenAICompatibleProvider(base_url=base, model=model, api_key=key)


def _windows_user_env(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as hive:
            value, _ = winreg.QueryValueEx(hive, name)
    except OSError:
        return None
    text = str(value).strip()
    return text or None


def _uses_responses_api(model: str) -> bool:
    return "astra" in model.lower()


def _extract_responses_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") in {"output_text", "text"}:
                    text = content.get("text") or content.get("output_text") or ""
                    if text:
                        chunks.append(str(text))
    return "".join(chunks)


def _pack_id_from_prompt(prompt: str) -> str:
    marker = "PACK_ID:"
    for line in prompt.splitlines():
        if line.startswith(marker):
            return line.split(":", 1)[1].strip()
    return "*"
