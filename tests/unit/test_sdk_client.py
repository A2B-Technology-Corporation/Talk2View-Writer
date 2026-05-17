"""Tests for ``talk2view_writer.sdk_client.Talk2ViewSDKClient``.

The real Talk2View SDK is mocked — these are unit tests, not integration
tests. A separate ``tests/integration/`` suite (Phase F) will exercise
the real engine.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_talk2view(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Install a fake ``talk2view`` module in ``sys.modules`` for the test."""
    fake_module = types.ModuleType("talk2view")
    fake_types_module = types.ModuleType("talk2view.types")
    fake_types_module.User = MagicMock(name="User")  # type: ignore[attr-defined]
    fake_types_module.ChatEvent = MagicMock(name="ChatEvent")  # type: ignore[attr-defined]

    talk2view_cls = MagicMock(name="Talk2View")
    fake_module.Talk2View = talk2view_cls  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "talk2view", fake_module)
    monkeypatch.setitem(sys.modules, "talk2view.types", fake_types_module)
    return talk2view_cls


@pytest.mark.unit
def test_lazy_client_instantiation(mock_talk2view: MagicMock, tmp_path: Path) -> None:
    """The SDK is not created until first use."""
    from talk2view_writer.sdk_client import Talk2ViewSDKClient
    from talk2view_writer.storage import FileTokenStorage

    client = Talk2ViewSDKClient(storage=FileTokenStorage(tmp_path / "t.json"))
    assert mock_talk2view.call_count == 0  # not instantiated yet
    _ = client.is_authenticated()
    assert mock_talk2view.call_count == 1  # first access triggered it


@pytest.mark.unit
def test_login_calls_sdk_and_sets_user(
    mock_talk2view: MagicMock, tmp_path: Path
) -> None:
    from talk2view_writer.sdk_client import Talk2ViewSDKClient
    from talk2view_writer.storage import FileTokenStorage

    fake_user = MagicMock(email="x@y.z")
    sdk_instance = MagicMock()
    sdk_instance.auth.login.return_value = fake_user
    sdk_instance.auth.get_user.return_value = None
    mock_talk2view.return_value = sdk_instance

    client = Talk2ViewSDKClient(storage=FileTokenStorage(tmp_path / "t.json"))
    result = client.login("x@y.z", "pw")
    assert result is fake_user
    sdk_instance.auth.login.assert_called_once_with("x@y.z", "pw")
    assert client.current_user is fake_user
    assert client.is_authenticated()


@pytest.mark.unit
def test_logout_clears_user_and_notifies(
    mock_talk2view: MagicMock, tmp_path: Path
) -> None:
    from talk2view_writer.sdk_client import Talk2ViewSDKClient
    from talk2view_writer.storage import FileTokenStorage

    sdk_instance = MagicMock()
    sdk_instance.auth.login.return_value = MagicMock(email="x@y.z")
    sdk_instance.auth.get_user.return_value = None
    mock_talk2view.return_value = sdk_instance

    client = Talk2ViewSDKClient(storage=FileTokenStorage(tmp_path / "t.json"))

    seen: list[object | None] = []
    client.add_auth_listener(lambda user: seen.append(user))

    client.login("x@y.z", "pw")
    client.logout()

    sdk_instance.auth.logout.assert_called_once()
    assert client.current_user is None
    assert not client.is_authenticated()
    assert len(seen) == 2  # login + logout
    assert seen[1] is None  # logout notified with None


@pytest.mark.unit
def test_chat_requires_authentication(
    mock_talk2view: MagicMock, tmp_path: Path
) -> None:
    from talk2view_writer.sdk_client import SdkClientError, Talk2ViewSDKClient
    from talk2view_writer.storage import FileTokenStorage

    sdk_instance = MagicMock()
    sdk_instance.auth.get_user.return_value = None
    mock_talk2view.return_value = sdk_instance

    client = Talk2ViewSDKClient(storage=FileTokenStorage(tmp_path / "t.json"))

    with pytest.raises(SdkClientError):
        # Must materialise the generator to trigger the auth check.
        list(client.chat("hello"))


@pytest.mark.unit
def test_chat_yields_events_from_sdk(mock_talk2view: MagicMock, tmp_path: Path) -> None:
    from talk2view_writer.sdk_client import Talk2ViewSDKClient
    from talk2view_writer.storage import FileTokenStorage

    sdk_instance = MagicMock()
    sdk_instance.auth.login.return_value = MagicMock(email="x@y.z")
    sdk_instance.auth.get_user.return_value = None
    sdk_instance.chat.return_value = iter(["evt1", "evt2", "evt3"])
    mock_talk2view.return_value = sdk_instance

    client = Talk2ViewSDKClient(storage=FileTokenStorage(tmp_path / "t.json"))
    client.login("x@y.z", "pw")
    events = list(client.chat("hi"))
    assert events == ["evt1", "evt2", "evt3"]
    sdk_instance.chat.assert_called_once_with("hi", system_prompt=None)


@pytest.mark.unit
def test_cached_user_restored_on_first_access(
    mock_talk2view: MagicMock, tmp_path: Path
) -> None:
    from talk2view_writer.sdk_client import Talk2ViewSDKClient
    from talk2view_writer.storage import FileTokenStorage

    cached = MagicMock(email="returning@example.com")
    sdk_instance = MagicMock()
    sdk_instance.auth.get_user.return_value = cached
    mock_talk2view.return_value = sdk_instance

    client = Talk2ViewSDKClient(storage=FileTokenStorage(tmp_path / "t.json"))
    assert client.is_authenticated()
    assert client.current_user is cached
