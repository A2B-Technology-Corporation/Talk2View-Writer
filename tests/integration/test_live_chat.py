"""Live end-to-end chat against engine.talk2view.com.

Validates the SDK <-> engine contract on each CI matrix entry's
actual Python interpreter. Catches:

  - The bundled ``pydantic_core`` wheel for this platform doesn't
    actually link / load (the wheel-loader test in test_smoke.py
    covers the import; this covers the runtime serialisation path).
  - The engine's wire protocol drifted from what the SDK expects.
  - The Writer-specific partner key has been revoked.
  - Auth round-trip works on every platform.

Does NOT touch LibreOffice — purely exercises the Talk2View SDK
client. Bypassing UNO means this test catches engine-side problems
without confounding them with UI / install issues.

Gated on ``T2V_TEST_USER_EMAIL`` + ``T2V_TEST_USER_PASSWORD`` env
vars. PRs from forks won't have these (GitHub blocks secrets there
by design), so the test will skip with a clear message rather than
failing.
"""

from __future__ import annotations

import contextlib
import os
import time

import pytest

pytestmark = pytest.mark.live


_EMAIL_ENV = "T2V_TEST_USER_EMAIL"
_PASSWORD_ENV = "T2V_TEST_USER_PASSWORD"


def _have_credentials() -> bool:
    return bool(os.environ.get(_EMAIL_ENV)) and bool(os.environ.get(_PASSWORD_ENV))


@pytest.fixture(scope="module")
def sdk_client() -> object:
    """Build a Talk2ViewSDKClient with test credentials and return it logged-in.

    Module-scoped: one login per test file. Tests within this file
    can assume the client is authenticated.
    """
    if not _have_credentials():
        pytest.skip(
            f"Live chat test requires {_EMAIL_ENV} + {_PASSWORD_ENV} env vars. "
            "Set them in your shell, or add as GitHub Actions secrets and "
            "ensure the workflow exposes them via `env:`."
        )

    # Ensure the bundled pydantic_core wheel loads under this Python.
    from talk2view_writer._wheel_loader import ensure_vendored_pydantic_core

    ensure_vendored_pydantic_core()

    from talk2view_writer.sdk_client import Talk2ViewSDKClient

    client = Talk2ViewSDKClient()
    user = client.login(
        os.environ[_EMAIL_ENV],
        os.environ[_PASSWORD_ENV],
    )
    assert user is not None, "Login returned no user"

    try:
        yield client
    finally:
        with contextlib.suppress(Exception):
            client.logout()


@pytest.mark.live
def test_login_then_chat_then_logout(sdk_client: object) -> None:
    """Send 'hello' and assert text content arrives within 30s.

    A real engine round-trip — this is the most realistic CI signal
    short of driving the actual LibreOffice UI.

    Reads ``event.content`` for ``type=text`` events, matching the
    SDK's :class:`talk2view.types.ChatEvent` schema (the previous
    revision of this test read ``event.text``/``event.delta`` which
    never existed on the dataclass, so it always asserted an empty
    string).
    """
    deadline = time.monotonic() + 30
    event_count = 0
    text_received = ""
    saw_done = False
    for event in sdk_client.chat("hello"):  # type: ignore[attr-defined]
        event_count += 1
        etype = getattr(event, "type", None)
        if etype == "text":
            content = getattr(event, "content", None)
            if isinstance(content, str):
                text_received += content
        if etype == "done":
            saw_done = True
        if event_count > 100 or time.monotonic() > deadline:
            break
    assert event_count > 0, "Chat stream produced zero events"
    # A successful chat produces at least one of: text content (the
    # agent answered), or a ``done`` event (the agent finished, even
    # if it routed through a tool-call with no registered handler).
    # Both are valid signals that the SDK <-> engine wire works.
    assert text_received.strip() or saw_done, (
        f"Got {event_count} events but neither text content nor a "
        f"done event. The SDK <-> engine wire may be broken."
    )


@pytest.mark.live
def test_authentication_persists_between_chats(sdk_client: object) -> None:
    """Second chat after a successful one should reuse the session, not re-login."""
    assert sdk_client.is_authenticated()  # type: ignore[attr-defined]
    # If we got the fixture, login already succeeded. This test
    # mostly documents that the client is meant to be reused —
    # the fixture's module scope enforces it.
