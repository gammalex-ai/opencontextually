"""Session state for authenticated users.

Sessions are created at login and looked up on every request. Each session
carries a `created_at` and `last_seen_at` timestamp so callers can decide
whether it has expired. The actual timeout value is expected to come from
`config/auth.yaml` (see `docs/security.md` for the requirement); this module
does not read the config file itself, it just exposes the primitives that
`src/auth/middleware.py` needs to enforce whatever timeout policy is
configured.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Session:
    """A single authenticated session."""

    session_id: str
    user_id: str
    created_at: float
    last_seen_at: float
    data: dict = field(default_factory=dict)


class SessionStore:
    """In-memory store of active sessions.

    A real deployment would back this with Redis or a database; for this
    fixture an in-memory dict is enough to demonstrate the shape of the
    session lifecycle and where expiration is checked.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create_session(self, user_id: str) -> Session:
        """Start a new session for `user_id` and return it."""
        now = time.time()
        session = Session(
            session_id=uuid.uuid4().hex,
            user_id=user_id,
            created_at=now,
            last_seen_at=now,
        )
        self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Look up a session by id, or None if it does not exist."""
        return self._sessions.get(session_id)

    def touch_session(self, session_id: str) -> None:
        """Record activity on a session, resetting its idle clock."""
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_seen_at = time.time()

    def end_session(self, session_id: str) -> None:
        """Explicitly end a session (logout)."""
        self._sessions.pop(session_id, None)

    def is_session_expired(self, session_id: str, timeout_minutes: float) -> bool:
        """Return True if the session has been idle longer than
        `timeout_minutes`.

        The caller supplies the timeout rather than this module hardcoding
        one, because the timeout is a configuration value (see
        config/auth.yaml and docs/security.md) and this module should not
        need to know where that value came from.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return True
        idle_seconds = time.time() - session.last_seen_at
        return idle_seconds > timeout_minutes * 60
