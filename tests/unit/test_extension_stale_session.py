"""``Talk2ViewWriterExtension.sdk`` must self-recover from a stale token.

The cached-user flow in ``sdk_client.py`` restores a ``User`` from the
on-disk token storage without re-validating the access token against
the engine. So ``is_authenticated()`` can return True even when the
token is expired. The extension used to react to that by eagerly
calling ``register_tools()``, hitting a 401, and propagating
``AuthenticationError`` out of the property — which dumped a 500-style
traceback on every menu dispatch the user touched (Settings, Sidebar,
Login) until they noticed and clicked Login manually.

Reproduced live on 2026-05-19 against LibreOffice 26.2.3.2 (Debian
backports). See ``talk2view.log`` excerpt from that session for the
exact stack.

The fix: catch ``AuthenticationError`` on the eager register call,
trigger a normal logout (clearing local tokens, notifying panels),
and return the SDK in a clean "logged out" state. Next dispatch sees
``is_authenticated() == False`` and the panel renders the login
prompt instead of crashing.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_talk2view_errors(monkeypatch: pytest.MonkeyPatch) -> type[Exception]:
    """Install fake ``talk2view`` + ``talk2view.errors`` modules.

    The production ``extension.sdk`` property does
    ``from talk2view.errors import AuthenticationError`` and
    indirectly imports ``talk2view_writer.tools`` (which uses
    ``from talk2view import tool``). Both must be importable for the
    test to reach the eager-register path under test.
    """

    class _AuthenticationError(Exception):
        pass

    fake_pkg = types.ModuleType("talk2view")
    fake_pkg.tool = lambda fn: fn  # type: ignore[attr-defined]
    fake_errors = types.ModuleType("talk2view.errors")
    fake_errors.AuthenticationError = _AuthenticationError  # type: ignore[attr-defined]
    fake_types = types.ModuleType("talk2view.types")
    fake_types.User = MagicMock(name="User")  # type: ignore[attr-defined]
    fake_types.ChatEvent = MagicMock(name="ChatEvent")  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "talk2view", fake_pkg)
    monkeypatch.setitem(sys.modules, "talk2view.errors", fake_errors)
    monkeypatch.setitem(sys.modules, "talk2view.types", fake_types)

    # Force re-import of any tool modules already loaded under a real
    # ``talk2view``; their module-level ``from talk2view import tool``
    # captured the old reference.
    for mod_name in list(sys.modules):
        if mod_name.startswith("talk2view_writer.tools"):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)
    return _AuthenticationError


@pytest.fixture
def stub_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
    fake_talk2view_errors: type[Exception],
) -> MagicMock:
    """Replace ``Talk2ViewSDKClient`` and the wheel loader with stubs.

    Returns the mock SDK instance so the test can configure
    ``is_authenticated`` / ``register_tools`` behaviour and assert on
    ``logout`` calls.
    """
    import talk2view_writer._wheel_loader as wheel_loader

    monkeypatch.setattr(
        wheel_loader, "ensure_vendored_pydantic_core", lambda: None
    )

    import talk2view_writer.sdk_client as sdk_module

    sdk_instance = MagicMock(name="Talk2ViewSDKClient")
    sdk_instance.is_authenticated.return_value = True

    def _factory(*_a: Any, **_kw: Any) -> MagicMock:
        return sdk_instance

    monkeypatch.setattr(sdk_module, "Talk2ViewSDKClient", _factory)
    return sdk_instance


@pytest.mark.unit
def test_sdk_property_self_recovers_from_stale_cached_session(
    stub_sdk_client: MagicMock,
    fake_talk2view_errors: type[Exception],
) -> None:
    """Stale 401 on register_tools triggers a clean logout, not a crash."""
    stub_sdk_client.register_tools.side_effect = fake_talk2view_errors(
        "Session expired. Please log in again."
    )

    from talk2view_writer.extension import Talk2ViewWriterExtension

    ext = Talk2ViewWriterExtension(MagicMock(name="ctx"))

    sdk = ext.sdk

    assert sdk is stub_sdk_client
    stub_sdk_client.register_tools.assert_called_once()
    stub_sdk_client.logout.assert_called_once()


@pytest.mark.unit
def test_sdk_property_logout_failure_is_swallowed(
    stub_sdk_client: MagicMock,
    fake_talk2view_errors: type[Exception],
) -> None:
    """If the cleanup logout itself raises, ``sdk`` still returns the client.

    A user with no network connectivity (or a server that's down) can
    still recover into a clean logged-out state — the cleanup attempt
    is best-effort.
    """
    stub_sdk_client.register_tools.side_effect = fake_talk2view_errors(
        "Session expired."
    )
    stub_sdk_client.logout.side_effect = RuntimeError("offline")

    from talk2view_writer.extension import Talk2ViewWriterExtension

    ext = Talk2ViewWriterExtension(MagicMock(name="ctx"))

    sdk = ext.sdk  # must NOT raise

    assert sdk is stub_sdk_client
    stub_sdk_client.logout.assert_called_once()


@pytest.mark.unit
def test_sdk_property_does_not_logout_when_register_succeeds(
    stub_sdk_client: MagicMock,
    fake_talk2view_errors: type[Exception],
) -> None:
    """Happy path: a valid cached session registers tools and does NOT logout."""
    stub_sdk_client.register_tools.return_value = None  # success

    from talk2view_writer.extension import Talk2ViewWriterExtension

    ext = Talk2ViewWriterExtension(MagicMock(name="ctx"))
    sdk = ext.sdk

    assert sdk is stub_sdk_client
    stub_sdk_client.register_tools.assert_called_once()
    stub_sdk_client.logout.assert_not_called()


@pytest.mark.unit
def test_sdk_property_does_not_logout_when_no_cached_session(
    stub_sdk_client: MagicMock,
    fake_talk2view_errors: type[Exception],
) -> None:
    """No cached session → no eager register, no logout."""
    stub_sdk_client.is_authenticated.return_value = False

    from talk2view_writer.extension import Talk2ViewWriterExtension

    ext = Talk2ViewWriterExtension(MagicMock(name="ctx"))
    sdk = ext.sdk

    assert sdk is stub_sdk_client
    stub_sdk_client.register_tools.assert_not_called()
    stub_sdk_client.logout.assert_not_called()
