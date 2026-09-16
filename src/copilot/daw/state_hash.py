from __future__ import annotations

from typing import Any

from copilot.daw.state_tokens import attach_tokens, canonical_project
from copilot.schemas.session import SessionState

FLOAT_PLACES = 6


def canonical_session(session: SessionState) -> dict[str, Any]:
    """Observed musical/DAW state only. No IDs, playback cursor, or revision.

    Kept as the PROJECT canonical body for existing tests. Volume/mute live in
    the AUDIBLE token, not here.
    """
    return canonical_project(session)


def state_hash(session: SessionState) -> str:
    attach_tokens(
        session,
        path=getattr(session, "project_path", None),
        name=getattr(session, "project_name", None),
    )
    return session.state_hash


class ObservedRevision:
    """In-process observation counter derived from tokens, not write count.

    Resets when this Core process reconnects. Tokens do not. Plans must use
    tokens, not this integer.
    """

    def __init__(self) -> None:
        self.revision = 0
        self.hash: str | None = None

    def observe(self, session: SessionState) -> SessionState:
        attach_tokens(
            session,
            path=session.project_path,
            name=session.project_name,
        )
        digest = session.state_hash
        if self.hash is None:
            self.hash = digest
        elif digest != self.hash:
            self.revision += 1
            self.hash = digest
        session.revision = self.revision
        return session
