"""Tests for the authentication middleware.

Covers login (successful authentication) and logout (session end). There is
deliberately no test here for session expiration -- that gap is what
OpenContextually's test_reference_gap check is meant to surface.
"""

from src.auth.middleware import AuthMiddleware, AuthenticationError
from src.users.session import SessionStore


def test_login_with_valid_session_succeeds():
    store = SessionStore()
    session = store.create_session(user_id="alice")
    middleware = AuthMiddleware(store)

    user_id = middleware.authenticate(session.session_id)

    assert user_id == "alice"


def test_login_with_unknown_session_fails():
    store = SessionStore()
    middleware = AuthMiddleware(store)

    try:
        middleware.authenticate("does-not-exist")
        assert False, "expected AuthenticationError"
    except AuthenticationError:
        pass


def test_logout_ends_session():
    store = SessionStore()
    session = store.create_session(user_id="bob")
    middleware = AuthMiddleware(store)

    store.end_session(session.session_id)

    try:
        middleware.authenticate(session.session_id)
        assert False, "expected AuthenticationError after logout"
    except AuthenticationError:
        pass
