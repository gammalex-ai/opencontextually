"""Authentication middleware.

Wraps request handling to require a valid, non-expired session before
letting a request through. This is the entry point most requests hit, and
it is the file a developer investigating "the authentication bug" is most
likely to open first.
"""

from __future__ import annotations

from src.users.session import SessionStore

# The session timeout is expected to be supplied by the caller (loaded from
# config/auth.yaml at application startup). A conservative fallback is used
# only if no configured value is available.
FALLBACK_TIMEOUT_MINUTES = 30


class AuthenticationError(Exception):
    """Raised when a request cannot be authenticated."""


class AuthMiddleware:
    """Rejects requests that lack a valid, unexpired session cookie."""

    def __init__(self, session_store: SessionStore, timeout_minutes: float | None = None):
        self._sessions = session_store
        self._timeout_minutes = timeout_minutes or FALLBACK_TIMEOUT_MINUTES

    def authenticate(self, session_id: str | None) -> str:
        """Return the authenticated user_id for `session_id`, or raise
        AuthenticationError.
        """
        if not session_id:
            raise AuthenticationError("missing session cookie")

        session = self._sessions.get_session(session_id)
        if session is None:
            raise AuthenticationError("unknown session")

        if self._sessions.is_session_expired(session_id, self._timeout_minutes):
            self._sessions.end_session(session_id)
            raise AuthenticationError("session expired")

        self._sessions.touch_session(session_id)
        return session.user_id

    def handle_request(self, session_id: str | None, request):
        """Authenticate the request, then dispatch it to the handler."""
        user_id = self.authenticate(session_id)
        request.user_id = user_id
        return request
