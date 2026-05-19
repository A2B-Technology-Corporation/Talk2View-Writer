"""Unit tests for the Talk2View sidebar panel.

Covers chat-event handling and slash-command parsing.

These do not stand up a real LibreOffice — they instantiate
:class:`Talk2ViewPanel` with mocks for ``ctx``, ``frame``, and
``parent_window``, manually bind in-memory widgets via
``_bind_test_widgets``, and verify the resulting widget state for each
SDK ``ChatEvent`` and each slash command.

The integration test ``test_sidebar_dock.py`` covers the
``createUIElement`` / ``XSidebarPanel`` path that builds these widgets
from a real ``.xdl`` against a live soffice.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeModel:
    """In-memory replacement for a UnoControl model.

    Mirrors only the two methods the panel uses on every widget model —
    ``getPropertyValue`` and ``setPropertyValue`` — backed by a dict.
    """

    def __init__(self, **initial: Any) -> None:
        self._props: dict[str, Any] = dict(initial)

    def getPropertyValue(self, name: str) -> Any:  # noqa: N802 — UNO IDL naming
        return self._props.get(name)

    def setPropertyValue(self, name: str, value: Any) -> None:  # noqa: N802
        self._props[name] = value


class _FakeControl:
    """In-memory replacement for an XControl. Holds a fake model."""

    def __init__(self, **initial: Any) -> None:
        self._model = _FakeModel(**initial)
        self.action_listeners: list[Any] = []

    def getModel(self) -> _FakeModel:  # noqa: N802
        return self._model

    def addActionListener(self, listener: Any) -> None:  # noqa: N802
        self.action_listeners.append(listener)


def _make_panel(
    monkeypatch: pytest.MonkeyPatch,
    *,
    auth: bool = False,
) -> Any:
    """Build a ``Talk2ViewPanel`` with in-memory widgets + a stub extension.

    The fixture skips ``getRealInterface`` (which would call out to
    ``ContainerWindowProvider``) and instead binds ``_FakeControl``
    instances directly. Run-sync is replaced with an inline executor
    so cross-thread updates apply synchronously inside the test.
    """
    from talk2view_writer.ui.sidebar_panel import Talk2ViewPanel

    ctx = MagicMock(name="ctx")
    parent_window = MagicMock(name="parent_window")
    frame = MagicMock(name="frame")
    panel = Talk2ViewPanel(
        ctx=ctx,
        frame=frame,
        parent_window=parent_window,
        resource_url="private:resource/toolpanel/com.talk2view.writer.Deck/talk2view",
    )

    panel._status_label = _FakeControl(Label="Talk2View — not logged in")
    panel._login_button = _FakeControl(EnableVisible=True)
    panel._history_field = _FakeControl(Text="")
    panel._composer_field = _FakeControl(Text="", Enabled=False)
    panel._send_button = _FakeControl(Enabled=False)

    # Mark "panel built" so `_apply_auth_state` runs in callbacks like
    # `on_auth_changed`. Mirrors the real lifecycle after
    # `getRealInterface()` returned.
    panel._tool_panel = SimpleNamespace()

    # Run UI-thread marshalling inline so test assertions can read
    # widget state without scheduling round-trips.
    fake_ext = SimpleNamespace(
        ui_thread=SimpleNamespace(run_sync=lambda fn, *a, **kw: fn(*a, **kw)),
        sdk=SimpleNamespace(),
        logout=MagicMock(name="logout"),
        show_settings_dialog=MagicMock(name="show_settings_dialog"),
    )

    def _get_ext(_ctx: Any) -> Any:
        return fake_ext

    monkeypatch.setattr(
        "talk2view_writer.extension.get_extension", _get_ext
    )

    if auth:
        from talk2view.types import User

        panel._user = User(id="u1", email="user@example.com")  # type: ignore[call-arg]
        panel._apply_auth_state()

    panel._fake_ext = fake_ext  # type: ignore[attr-defined]
    return panel


# ---------------------------------------------------------------------------
# Chat-event handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleChatEvent:
    def _event(self, **fields: Any) -> SimpleNamespace:
        defaults: dict[str, Any] = {
            "type": None,
            "content": None,
            "tool_name": None,
            "tool_call_id": None,
            "arguments": None,
            "message": None,
            "status": None,
            "todos": None,
            "thread_id": None,
        }
        defaults.update(fields)
        return SimpleNamespace(**defaults)

    def test_text_event_appends_content_to_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(self._event(type="text", content="hello "))
        panel._handle_chat_event(self._event(type="text", content="world"))
        assert panel._history_field.getModel().getPropertyValue("Text") == (
            "hello world"
        )

    def test_text_event_with_empty_content_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(self._event(type="text", content=""))
        panel._handle_chat_event(self._event(type="text", content=None))
        assert panel._history_field.getModel().getPropertyValue("Text") == ""

    def test_status_event_updates_status_label(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(
            self._event(type="status", message="Thinking", status="running")
        )
        assert (
            panel._status_label.getModel().getPropertyValue("Label") == "Thinking"
        )

    def test_status_event_falls_back_to_status_field_when_no_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(self._event(type="status", status="planning"))
        assert (
            panel._status_label.getModel().getPropertyValue("Label") == "planning"
        )

    def test_todos_event_renders_plan_in_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(
            self._event(type="todos", todos="- step 1\n- step 2")
        )
        text = panel._history_field.getModel().getPropertyValue("Text")
        assert "Plan:" in text
        assert "step 1" in text
        assert "step 2" in text

    def test_todos_event_with_empty_string_is_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(self._event(type="todos", todos=""))
        assert panel._history_field.getModel().getPropertyValue("Text") == ""

    def test_tool_call_event_renders_name_and_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(
            self._event(
                type="tool_call",
                tool_name="format_text",
                arguments={"query": "hello", "bold": True},
            )
        )
        text = panel._history_field.getModel().getPropertyValue("Text")
        assert "format_text" in text
        assert "query=hello" in text
        assert "bold=True" in text
        # The "(Phase C)" placeholder must be gone.
        assert "Phase C" not in text

    def test_tool_call_event_truncates_long_string_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        long_text = "a" * 80
        panel._handle_chat_event(
            self._event(
                type="tool_call",
                tool_name="search_document",
                arguments={"query": long_text},
            )
        )
        text = panel._history_field.getModel().getPropertyValue("Text")
        assert "..." in text
        # Truncated to 37 chars + "..." per _short() — the full 80-char
        # original must not appear in full.
        assert long_text not in text

    def test_tool_call_event_summarises_list_and_dict_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(
            self._event(
                type="tool_call",
                tool_name="format_paragraph",
                arguments={
                    "paragraph_indices": [1, 2, 3, 4, 5],
                    "format": {"bold": True, "italic": False},
                },
            )
        )
        text = panel._history_field.getModel().getPropertyValue("Text")
        assert "list(5)" in text
        assert "dict(2)" in text

    def test_tool_call_event_with_no_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(
            self._event(type="tool_call", tool_name="get_document", arguments={})
        )
        text = panel._history_field.getModel().getPropertyValue("Text")
        assert "get_document" in text

    def test_tool_call_event_with_missing_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(self._event(type="tool_call"))
        text = panel._history_field.getModel().getPropertyValue("Text")
        assert "?" in text

    def test_error_event_appends_error_to_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(
            self._event(type="error", message="token expired")
        )
        text = panel._history_field.getModel().getPropertyValue("Text")
        assert "[error]" in text
        assert "token expired" in text

    def test_done_event_does_not_mutate_widgets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        before = panel._history_field.getModel().getPropertyValue("Text")
        panel._handle_chat_event(self._event(type="done", thread_id="t-1"))
        assert (
            panel._history_field.getModel().getPropertyValue("Text") == before
        )

    def test_unknown_event_type_is_silently_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._handle_chat_event(self._event(type="future_event_type"))
        assert panel._history_field.getModel().getPropertyValue("Text") == ""


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSlashCommands:
    def test_help_lists_supported_commands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        assert panel._handle_slash_command("/help") is True
        text = panel._history_field.getModel().getPropertyValue("Text")
        for cmd in ["/help", "/clear", "/logout", "/settings", "/tools"]:
            assert cmd in text

    def test_clear_blanks_history_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._history_field.getModel().setPropertyValue(
            "Text", "previous chat here"
        )
        assert panel._handle_slash_command("/clear") is True
        assert panel._history_field.getModel().getPropertyValue("Text") == ""

    def test_logout_calls_extension_logout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        assert panel._handle_slash_command("/logout") is True
        panel._fake_ext.logout.assert_called_once()
        text = panel._history_field.getModel().getPropertyValue("Text")
        assert "Logged out" in text

    def test_settings_opens_settings_dialog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        assert panel._handle_slash_command("/settings") is True
        panel._fake_ext.show_settings_dialog.assert_called_once()

    def test_tools_lists_every_registered_tool_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        assert panel._handle_slash_command("/tools") is True
        text = panel._history_field.getModel().getPropertyValue("Text")
        for name in (
            "get_document",
            "insert_content",
            "format_text",
            "search_document",
            "insert_break",
            "get_comments",
        ):
            assert name in text

    def test_unknown_slash_command_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        assert panel._handle_slash_command("/unknowncmd") is False

    def test_slash_command_dispatch_is_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        assert panel._handle_slash_command("/HELP") is True

    def test_path_like_message_falls_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real path "/etc/passwd" must not be silently swallowed."""
        panel = _make_panel(monkeypatch, auth=True)
        # We don't recognise `/etc` as a command; the panel should pass
        # it through to the engine (return False).
        assert panel._handle_slash_command("/etc/passwd") is False


# ---------------------------------------------------------------------------
# Auth state application
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyAuthState:
    def test_logged_in_enables_composer_and_send(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        assert panel._composer_field.getModel().getPropertyValue("Enabled") is True
        assert panel._send_button.getModel().getPropertyValue("Enabled") is True
        # Login button hides once authenticated.
        assert (
            panel._login_button.getModel().getPropertyValue("EnableVisible") is False
        )
        # Status reflects the user's email.
        assert (
            "user@example.com"
            in panel._status_label.getModel().getPropertyValue("Label")
        )

    def test_logged_out_disables_composer_and_send(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=False)
        assert panel._composer_field.getModel().getPropertyValue("Enabled") in (
            False,
            None,
        )
        assert panel._send_button.getModel().getPropertyValue("Enabled") in (
            False,
            None,
        )
        assert (
            panel._login_button.getModel().getPropertyValue("EnableVisible") is True
        )

    def test_on_auth_changed_pushes_state_to_widgets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=False)
        from talk2view.types import User

        user = User(id="u2", email="alice@example.com")  # type: ignore[call-arg]
        panel.on_auth_changed(user)
        assert (
            "alice@example.com"
            in panel._status_label.getModel().getPropertyValue("Label")
        )
        # Composer is enabled, send button is enabled.
        assert panel._composer_field.getModel().getPropertyValue("Enabled") is True


# ---------------------------------------------------------------------------
# _on_send_clicked: slash-command interception
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOnSendClickedSlashRouting:
    def test_slash_command_does_not_start_worker_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._composer_field.getModel().setPropertyValue("Text", "/help")
        thread_calls: list[Any] = []
        monkeypatch.setattr(
            "talk2view_writer.ui.sidebar_panel.threading.Thread",
            lambda *a, **kw: thread_calls.append((a, kw))  # type: ignore[arg-type]
            or MagicMock(),
        )
        panel._on_send_clicked()
        assert thread_calls == [], "slash command must not spawn a chat worker"

    def test_plain_message_spawns_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._composer_field.getModel().setPropertyValue("Text", "hello there")
        started: list[Any] = []
        thread_mock = MagicMock()
        thread_mock.start.side_effect = lambda: started.append(True)
        monkeypatch.setattr(
            "talk2view_writer.ui.sidebar_panel.threading.Thread",
            lambda *a, **kw: thread_mock,
        )
        panel._on_send_clicked()
        thread_mock.start.assert_called_once()
        assert started == [True]
        # Composer is cleared on send.
        assert panel._composer_field.getModel().getPropertyValue("Text") == ""

    def test_unknown_slash_command_falls_through_to_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._composer_field.getModel().setPropertyValue("Text", "/etc/passwd")
        thread_mock = MagicMock()
        monkeypatch.setattr(
            "talk2view_writer.ui.sidebar_panel.threading.Thread",
            lambda *a, **kw: thread_mock,
        )
        panel._on_send_clicked()
        thread_mock.start.assert_called_once()

    def test_empty_message_does_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._composer_field.getModel().setPropertyValue("Text", "   ")
        thread_mock = MagicMock()
        monkeypatch.setattr(
            "talk2view_writer.ui.sidebar_panel.threading.Thread",
            lambda *a, **kw: thread_mock,
        )
        panel._on_send_clicked()
        thread_mock.start.assert_not_called()

    def test_send_while_busy_ignores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel = _make_panel(monkeypatch, auth=True)
        panel._composer_field.getModel().setPropertyValue("Text", "hello")
        panel._busy.set()
        thread_mock = MagicMock()
        monkeypatch.setattr(
            "talk2view_writer.ui.sidebar_panel.threading.Thread",
            lambda *a, **kw: thread_mock,
        )
        panel._on_send_clicked()
        thread_mock.start.assert_not_called()
