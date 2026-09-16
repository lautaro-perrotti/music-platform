"""Capability certification cache. Not AudioAsset cache.

A certification that already passed is not re-run on a normal production
capture. Invalidate only when a key field that can affect the claim changes.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

CORE_CAPTURE_VERSION = "capture-prod-1"
DEFAULT_PATH = Path("logs") / "capture_capability.json"

CERT_TAP_PROTOCOL_3 = "tap_protocol_3"
CERT_MULTI_TAP_ISOLATION = "multi_tap_isolation"
CERT_REC_CLOSE = "rec_close"
CERT_ALIGNMENT_ENVELOPE = "alignment_envelope"
CERT_ROUTING_API = "routing_api_semantics"
CERT_SLOT_OWNERSHIP = "slot_ownership"
CERT_MAIN_FINAL = "main_final"


class CaptureMode(str, Enum):
    CERTIFICATION = "CERTIFICATION"
    PRODUCTION = "PRODUCTION"


def capability_key(
    *,
    live_version: str | None,
    max_version: str | None,
    remote_script_protocol: str | None,
    tap_protocol: int | None,
    core_capture_version: str = CORE_CAPTURE_VERSION,
    sample_rate: int | None,
    configuration: str | None = "parallel_3tap",
) -> dict[str, Any]:
    return {
        "live_version": live_version,
        "max_version": max_version,
        "remote_script_protocol": remote_script_protocol,
        "tap_protocol": tap_protocol,
        "core_capture_version": core_capture_version,
        "sample_rate": sample_rate,
        "configuration": configuration,
    }


def keys_match(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not left or not right:
        return False
    fields = (
        "live_version",
        "max_version",
        "remote_script_protocol",
        "tap_protocol",
        "core_capture_version",
        "sample_rate",
        "configuration",
    )
    return all(left.get(name) == right.get(name) for name in fields)


class CapabilityCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PATH
        self.data: dict[str, Any] = {"key": None, "certs": {}}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(loaded, dict):
            self.data = loaded
            self.data.setdefault("certs", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, indent=2, default=str), encoding="utf-8"
        )

    def bind(self, key: dict[str, Any]) -> None:
        if not keys_match(self.data.get("key"), key):
            self.data = {"key": key, "certs": {}}
            self.save()

    def certified(self, name: str) -> bool:
        entry = (self.data.get("certs") or {}).get(name) or {}
        return str(entry.get("status") or "") in {
            "YES",
            "CERTIFIED",
            "FIXTURE_VERIFIED",
        }

    def put(
        self,
        name: str,
        *,
        status: str,
        evidence: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        certs = self.data.setdefault("certs", {})
        certs[name] = {
            "status": status,
            "evidence": evidence,
            "details": details or {},
        }
        self.save()

    def skip_reason(self, name: str) -> str | None:
        if self.certified(name):
            return f"{name} already certified for this capability key"
        return None
