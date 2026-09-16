"""AudioAsset reuse keyed by audible state.

Lookup is allowed only when project identity, scoped audible token, region,
source/view/signal_point, capture protocol, and sample rate match, and the
stored provenance still verifies.

Incorrect reuse is forbidden. Conservative over-invalidation is allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copilot.audio.capture_capability import CORE_CAPTURE_VERSION
from copilot.daw.state_errors import CACHE_MISS, CACHE_STALE, StateTrustError
from copilot.daw.state_tokens import audible_token
from copilot.schemas.session import SessionState

REVISION_FENCE_REQUIRED = False


@dataclass(frozen=True)
class AssetCacheKey:
    project_identity: str
    audible_token: str
    region: str
    source: str
    view: str
    signal_point: str
    capture_protocol: str
    sample_rate: int
    core_capture_version: str = CORE_CAPTURE_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_identity": self.project_identity,
            "audible_token": self.audible_token,
            "region": self.region,
            "source": self.source,
            "view": self.view,
            "signal_point": self.signal_point,
            "capture_protocol": self.capture_protocol,
            "sample_rate": self.sample_rate,
            "core_capture_version": self.core_capture_version,
        }


def scoped_audible_token(
    session: SessionState, *, view: str, source_name: str | None
) -> str:
    """MASTER_CONTEXT depends on the whole set. Isolated views depend on source."""
    if view == "MASTER_CONTEXT" or source_name in {None, "", "MASTER"}:
        return audible_token(session)
    return audible_token(session, track_names=frozenset({source_name}))


def make_key(
    session: SessionState,
    *,
    region: str,
    source: str,
    view: str,
    signal_point: str,
    capture_protocol: str,
    sample_rate: int,
    source_name: str | None = None,
) -> AssetCacheKey:
    return AssetCacheKey(
        project_identity=session.project_identity,
        audible_token=scoped_audible_token(
            session, view=view, source_name=source_name or source
        ),
        region=region,
        source=source,
        view=view,
        signal_point=signal_point,
        capture_protocol=capture_protocol,
        sample_rate=int(sample_rate),
    )


def _provenance_ok(record: dict[str, Any]) -> str | None:
    path_value = record.get("file_path")
    if path_value:
        path = Path(str(path_value))
        if not path.is_file():
            return "file missing"
        expected_hash = record.get("sha256")
        if expected_hash:
            import hashlib

            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                return "hash mismatch"
    status = record.get("journal_status")
    if status is not None and status != "VERIFIED":
        return "journal not VERIFIED"
    if record.get("protocol_compatible") is False:
        return "capture protocol incompatible"
    stored_point = (record.get("key") or {}).get("signal_point")
    if stored_point and record.get("signal_point") not in {None, stored_point}:
        return "signal_point mismatch"
    return None


class AssetCache:
    def __init__(self, store: dict[str, Any] | None = None) -> None:
        self.store = store if store is not None else {}

    def _slot(self, key: AssetCacheKey) -> str:
        return "|".join(
            [
                key.project_identity,
                key.audible_token,
                key.region,
                key.source,
                key.view,
                key.signal_point,
                key.capture_protocol,
                str(key.sample_rate),
                key.core_capture_version,
            ]
        )

    def lookup(self, key: AssetCacheKey) -> dict[str, Any] | None:
        if REVISION_FENCE_REQUIRED:
            return None
        record = self.store.get(self._slot(key))
        if record is None:
            return None
        stored_key = record.get("key") or {}
        if stored_key.get("audible_token") != key.audible_token:
            return None
        if stored_key.get("project_identity") != key.project_identity:
            return None
        reason = _provenance_ok({**record, "key": key.as_dict()})
        if reason:
            return None
        return record

    def put(self, key: AssetCacheKey, record: dict[str, Any]) -> None:
        self.store[self._slot(key)] = {**record, "key": key.as_dict()}

    def invalidate_keys_matching(self, *, audible_token: str | None = None) -> int:
        removed = 0
        for slot in list(self.store):
            record = self.store[slot]
            stored = (record.get("key") or {}).get("audible_token")
            if audible_token is not None and stored == audible_token:
                del self.store[slot]
                removed += 1
        return removed


def lookup(key: AssetCacheKey, store: dict[str, Any] | None = None) -> Any | None:
    cache = AssetCache(store if store is not None else {})
    if store is None:
        return None
    found = cache.lookup(key)
    if found is None:
        return None
    return found.get("payload", found)


def require_hit(key: AssetCacheKey, store: dict[str, Any]) -> dict[str, Any]:
    cache = AssetCache(store)
    found = cache.lookup(key)
    if found is None:
        raise StateTrustError(CACHE_MISS, "no reusable asset")
    reason = _provenance_ok(found)
    if reason:
        raise StateTrustError(CACHE_STALE, reason)
    return found
