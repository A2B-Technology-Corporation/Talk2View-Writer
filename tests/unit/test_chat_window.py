"""Unit tests for the Talk2View floating chat window (ADR-0029).

Covers chat-event handling, slash-command parsing, auth state, and
the construction error paths.

These do not stand up a real LibreOffice — they instantiate
:class:`ChatWindow` directly with a mock ``ctx``, manually bind
in-memory widgets via ``_FakeControl`` instances, and verify the
resulting widget state for each SDK ``ChatEvent`` and each slash
command.

The integration test ``test_sidebar_dock.py`` covers the
construction path against a live soffice (will be renamed in the
ADR-0029 follow-up; the integration test file itself still passes
since it skips without a running soffice).
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _ensure_caplog_sees_package_records() -> Any:
    """Restore ``propagate=True`` on the package logger so caplog works.

    :func:`setup_logging` sets ``propagate=False`` to prevent
    double-logging in production. test_logging.py exercises that path
    and the change persists across test files, leaving caplog blind
    to ``talk2view_writer.*`` records. This fixture restores
    propagation for every test in this file, then puts the original
    value back.
    """
    pkg_logger = logging.getLogger("talk2view_writer")
    saved = pkg_logger.propagate
    pkg_logger.propagate = True
    yield
    pkg_logger.propagate = saved


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeModel:
    """In-memory replacement for a UnoControl model."""

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


def _make_window(
    monkeypatch: pytest.MonkeyPatch,
    *,
    auth: bool = False,
) -> Any:
    """Build a :class:`ChatWindow` with in-memory widgets + a stub extension.

    The fixture skips :meth:`ChatWindow.show` (which would call out to
    ``DialogProvider2.createDialog``) and instead binds ``_FakeControl``
    instances directly. ``run_sync`` is replaced with an inline executor
    so cross-thread updates apply synchronously inside the test.
    """
    from talk2view_writer.ui.chat_window import ChatWindow

    ctx = MagicMock(name="ctx")
    window = ChatWindow(ctx=ctx)

    window._status_label = _FakeControl(Label="Talk2View — not logged in")
    window._login_button = _FakeControl(EnableVisible=True)
    window._history_field = _FakeControl(Text="")
    window._composer_field = _FakeControl(Text="", Enabled=False)
    window._send_button = _FakeControl(Enabled=False)

    # Mark "dialog built" so ``_apply_auth_state`` runs in callbacks
    # like ``on_auth_changed``.
    window._dialog = SimpleNamespace()

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

        window._user = User(id="u1", email="user@example.com")  # type: ignore[call-arg]
        window._apply_auth_state()

    window._fake_ext = fake_ext  # type: ignore[attr-defined]
    return window


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
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(self._event(type="text", content="hello "))
        window._handle_chat_event(self._event(type="text", content="world"))
        assert window._history_field.getModel().getPropertyValue("Text") == (
            "hello world"
        )

    def test_text_event_with_empty_content_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(self._event(type="text", content=""))
        window._handle_chat_event(self._event(type="text", content=None))
        assert window._history_field.getModel().getPropertyValue("Text") == ""

    def test_status_event_updates_status_label(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(
            self._event(type="status", message="Thinking", status="running")
        )
        assert (
            window._status_label.getModel().getPropertyValue("Label") == "Thinking"
        )

    def test_status_event_falls_back_to_status_field_when_no_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(self._event(type="status", status="planning"))
        assert (
            window._status_label.getModel().getPropertyValue("Label") == "planning"
        )

    def test_todos_event_renders_plan_in_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(
            self._event(type="todos", todos="- step 1\n- step 2")
        )
        text = window._history_field.getModel().getPropertyValue("Text")
        assert "Plan:" in text
        assert "step 1" in text
        assert "step 2" in text

    def test_todos_event_with_empty_string_is_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(self._event(type="todos", todos=""))
        assert window._history_field.getModel().getPropertyValue("Text") == ""

    def test_tool_call_event_renders_name_and_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(
            self._event(
                type="tool_call",
                tool_name="format_text",
                arguments={"query": "hello", "bold": True},
            )
        )
        text = window._history_field.getModel().getPropertyValue("Text")
        assert "format_text" in text
        assert "query=hello" in text
        assert "bold=True" in text

    def test_tool_call_event_truncates_long_string_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        long_text = "a" * 80
        window._handle_chat_event(
            self._event(
                type="tool_call",
                tool_name="search_document",
                arguments={"query": long_text},
            )
        )
        text = window._history_field.getModel().getPropertyValue("Text")
        assert "..." in text
        assert long_text not in text

    def test_tool_call_event_summarises_list_and_dict_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(
            self._event(
                type="tool_call",
                tool_name="format_paragraph",
                arguments={
                    "paragraph_indices": [1, 2, 3, 4, 5],
                    "format": {"bold": True, "italic": False},
                },
            )
        )
        text = window._history_field.getModel().getPropertyValue("Text")
        assert "list(5)" in text
        assert "dict(2)" in text

    def test_tool_call_event_with_no_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(
            self._event(type="tool_call", tool_name="get_document", arguments={})
        )
        text = window._history_field.getModel().getPropertyValue("Text")
        assert "get_document" in text

    def test_tool_call_event_with_missing_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(self._event(type="tool_call"))
        text = window._history_field.getModel().getPropertyValue("Text")
        assert "?" in text

    def test_error_event_appends_error_to_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(
            self._event(type="error", message="token expired")
        )
        text = window._history_field.getModel().getPropertyValue("Text")
        assert "[error]" in text
        assert "token expired" in text

    def test_done_event_does_not_mutate_widgets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        before = window._history_field.getModel().getPropertyValue("Text")
        window._handle_chat_event(self._event(type="done", thread_id="t-1"))
        assert (
            window._history_field.getModel().getPropertyValue("Text") == before
        )

    def test_unknown_event_type_is_silently_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._handle_chat_event(self._event(type="future_event_type"))
        assert window._history_field.getModel().getPropertyValue("Text") == ""


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSlashCommands:
    def test_help_lists_supported_commands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        assert window._handle_slash_command("/help") is True
        text = window._history_field.getModel().getPropertyValue("Text")
        for cmd in ["/help", "/clear", "/logout", "/settings", "/tools"]:
            assert cmd in text

    def test_clear_blanks_history_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._history_field.getModel().setPropertyValue(
            "Text", "previous chat here"
        )
        assert window._handle_slash_command("/clear") is True
        assert window._history_field.getModel().getPropertyValue("Text") == ""

    def test_logout_calls_extension_logout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        assert window._handle_slash_command("/logout") is True
        window._fake_ext.logout.assert_called_once()
        text = window._history_field.getModel().getPropertyValue("Text")
        assert "Logged out" in text

    def test_settings_opens_settings_dialog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        assert window._handle_slash_command("/settings") is True
        window._fake_ext.show_settings_dialog.assert_called_once()

    def test_tools_lists_every_registered_tool_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        assert window._handle_slash_command("/tools") is True
        text = window._history_field.getModel().getPropertyValue("Text")
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
        window = _make_window(monkeypatch, auth=True)
        assert window._handle_slash_command("/unknowncmd") is False

    def test_slash_command_dispatch_is_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        assert window._handle_slash_command("/HELP") is True

    def test_path_like_message_falls_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real path "/etc/passwd" must not be silently swallowed."""
        window = _make_window(monkeypatch, auth=True)
        assert window._handle_slash_command("/etc/passwd") is False


# ---------------------------------------------------------------------------
# Auth state application
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyAuthState:
    def test_logged_in_enables_composer_and_send(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        assert window._composer_field.getModel().getPropertyValue("Enabled") is True
        assert window._send_button.getModel().getPropertyValue("Enabled") is True
        assert (
            window._login_button.getModel().getPropertyValue("EnableVisible") is False
        )
        assert (
            "user@example.com"
            in window._status_label.getModel().getPropertyValue("Label")
        )

    def test_logged_out_disables_composer_and_send(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=False)
        assert window._composer_field.getModel().getPropertyValue("Enabled") in (
            False,
            None,
        )
        assert window._send_button.getModel().getPropertyValue("Enabled") in (
            False,
            None,
        )
        assert (
            window._login_button.getModel().getPropertyValue("EnableVisible") is True
        )

    def test_on_auth_changed_pushes_state_to_widgets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=False)
        from talk2view.types import User

        user = User(id="u2", email="alice@example.com")  # type: ignore[call-arg]
        window.on_auth_changed(user)
        assert (
            "alice@example.com"
            in window._status_label.getModel().getPropertyValue("Label")
        )
        assert window._composer_field.getModel().getPropertyValue("Enabled") is True


# ---------------------------------------------------------------------------
# _on_send_clicked: slash-command interception
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOnSendClickedSlashRouting:
    def test_slash_command_does_not_start_worker_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._composer_field.getModel().setPropertyValue("Text", "/help")
        thread_calls: list[Any] = []
        monkeypatch.setattr(
            "talk2view_writer.ui.chat_window.threading.Thread",
            lambda *a, **kw: thread_calls.append((a, kw))  # type: ignore[arg-type]
            or MagicMock(),
        )
        window._on_send_clicked()
        assert thread_calls == [], "slash command must not spawn a chat worker"

    def test_plain_message_spawns_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._composer_field.getModel().setPropertyValue("Text", "hello there")
        started: list[Any] = []
        thread_mock = MagicMock()
        thread_mock.start.side_effect = lambda: started.append(True)
        monkeypatch.setattr(
            "talk2view_writer.ui.chat_window.threading.Thread",
            lambda *a, **kw: thread_mock,
        )
        window._on_send_clicked()
        thread_mock.start.assert_called_once()
        assert started == [True]
        assert window._composer_field.getModel().getPropertyValue("Text") == ""

    def test_unknown_slash_command_falls_through_to_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._composer_field.getModel().setPropertyValue("Text", "/etc/passwd")
        thread_mock = MagicMock()
        monkeypatch.setattr(
            "talk2view_writer.ui.chat_window.threading.Thread",
            lambda *a, **kw: thread_mock,
        )
        window._on_send_clicked()
        thread_mock.start.assert_called_once()

    def test_empty_message_does_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._composer_field.getModel().setPropertyValue("Text", "   ")
        thread_mock = MagicMock()
        monkeypatch.setattr(
            "talk2view_writer.ui.chat_window.threading.Thread",
            lambda *a, **kw: thread_mock,
        )
        window._on_send_clicked()
        thread_mock.start.assert_not_called()

    def test_send_while_busy_ignores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window = _make_window(monkeypatch, auth=True)
        window._composer_field.getModel().setPropertyValue("Text", "hello")
        window._busy.set()
        thread_mock = MagicMock()
        monkeypatch.setattr(
            "talk2view_writer.ui.chat_window.threading.Thread",
            lambda *a, **kw: thread_mock,
        )
        window._on_send_clicked()
        thread_mock.start.assert_not_called()


# ---------------------------------------------------------------------------
# Construction: _create_dialog + show()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateDialog:
    def _window_with_provider(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        dialog_side_effect: Any = None,
    ) -> Any:
        """Build a ChatWindow whose DialogProvider2 is mocked.

        Stages a real XDL stub so the existence check passes.
        """
        pkg_root = tmp_path / "extension"
        (pkg_root / "panels").mkdir(parents=True, exist_ok=True)
        (pkg_root / "panels" / "chat_panel.xdl").write_text("<stub/>\n")

        window = _make_window(monkeypatch, auth=False)
        window._dialog = None  # reset so _create_dialog runs

        pip = MagicMock()
        pip.getPackageLocation.return_value = pkg_root.as_uri()
        provider = MagicMock()
        if dialog_side_effect is not None:
            provider.createDialog.side_effect = dialog_side_effect

        window.ctx = MagicMock()
        window.ctx.getValueByName.return_value = pip
        window.ctx.ServiceManager.createInstanceWithContext.return_value = provider
        return window, provider

    def test_calls_dialogprovider2_create_dialog_with_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        window, provider = self._window_with_provider(monkeypatch, tmp_path)
        result = MagicMock(name="dialog")
        provider.createDialog.return_value = result

        returned = window._create_dialog()
        provider.createDialog.assert_called_once()
        url_arg = provider.createDialog.call_args.args[0]
        assert url_arg.endswith("/panels/chat_panel.xdl"), (
            f"createDialog called with URL {url_arg!r}, "
            f"expected suffix /panels/chat_panel.xdl"
        )
        assert returned is result

    def test_create_dialog_failure_is_logged_and_re_raised(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        exc = RuntimeError("native createDialog crashed")
        window, _provider = self._window_with_provider(
            monkeypatch, tmp_path, dialog_side_effect=exc
        )
        with caplog.at_level(
            "ERROR", logger="talk2view_writer.ui.chat_window"
        ), pytest.raises(RuntimeError, match="native createDialog crashed"):
            window._create_dialog()
        assert "createDialog raised" in caplog.text

    def test_missing_dialog_file_raises_filenotfounderror(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        window = _make_window(monkeypatch, auth=False)
        window._dialog = None

        pkg_root = tmp_path / "does-not-exist"

        pip = MagicMock()
        pip.getPackageLocation.return_value = pkg_root.as_uri()
        provider = MagicMock()

        window.ctx = MagicMock()
        window.ctx.getValueByName.return_value = pip
        window.ctx.ServiceManager.createInstanceWithContext.return_value = provider

        with pytest.raises(FileNotFoundError, match=r"chat_panel\.xdl"):
            window._create_dialog()
        provider.createDialog.assert_not_called()


# ---------------------------------------------------------------------------
# show() error envelope
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestShowErrorPaths:
    def test_create_dialog_failure_logged_and_re_raised(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        window = _make_window(monkeypatch, auth=False)
        window._dialog = None
        monkeypatch.setattr(
            window,
            "_create_dialog",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with caplog.at_level(
            "ERROR", logger="talk2view_writer.ui.chat_window"
        ), pytest.raises(RuntimeError, match="boom"):
            window.show()
        assert "_create_dialog raised" in caplog.text


# ---------------------------------------------------------------------------
# Diagnostic helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSafeCall:
    def test_returns_value_on_success(self) -> None:
        from talk2view_writer.ui.chat_window import _safe_call

        assert _safe_call("label", lambda: 42) == 42

    def test_returns_none_and_logs_exception(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from talk2view_writer.ui.chat_window import _safe_call

        def boom() -> None:
            raise RuntimeError("nope")

        with caplog.at_level(
            "ERROR", logger="talk2view_writer.ui.chat_window"
        ):
            result = _safe_call("test_label", boom)
        assert result is None
        assert "test_label" in caplog.text
        assert "RuntimeError" in caplog.text


@pytest.mark.unit
class TestAssertDialogFileExists:
    def test_raises_when_file_missing(self, tmp_path: Path) -> None:
        from talk2view_writer.ui.chat_window import _assert_dialog_file_exists

        missing = tmp_path / "no_such_file.xdl"
        with pytest.raises(FileNotFoundError) as info:
            _assert_dialog_file_exists(missing.as_uri())
        assert "no_such_file.xdl" in str(info.value)

    def test_succeeds_when_file_exists(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from talk2view_writer.ui.chat_window import _assert_dialog_file_exists

        f = tmp_path / "stub.xdl"
        f.write_text("<stub/>\n", encoding="utf-8")
        with caplog.at_level(
            "INFO", logger="talk2view_writer.ui.chat_window"
        ):
            _assert_dialog_file_exists(f.as_uri())
        assert "size=" in caplog.text

    def test_non_file_scheme_is_skipped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from talk2view_writer.ui.chat_window import _assert_dialog_file_exists

        with caplog.at_level(
            "INFO", logger="talk2view_writer.ui.chat_window"
        ):
            _assert_dialog_file_exists("vnd.something:nonsense")
        assert "non-file scheme" in caplog.text
