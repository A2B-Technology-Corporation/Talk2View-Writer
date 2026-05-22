"""Tests for ``talk2view_writer.extension``.

After ADR-0030 the extension singleton holds just two pieces of
state: a UI-thread dispatcher (lazy) and a WebWindow handle (lazy).
We test that both stay lazy until accessed, that the chat-window
``show()`` is called on every menu invocation, and that the
process-wide singleton is shared across calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestExtensionLazy:
    """Sub-systems initialise on first access, never at construction."""

    def _fresh_extension(self) -> Any:
        # Reset the module-level singleton so each test starts clean.
        import talk2view_writer.extension as mod

        mod._INSTANCE = None
        return mod

    def test_constructor_does_not_create_ui_thread(self) -> None:
        from talk2view_writer.extension import Talk2ViewWriterExtension

        ext = Talk2ViewWriterExtension(ctx=MagicMock(name="ctx"))
        assert ext._ui_thread is None
        assert ext._chat_window is None

    def test_ui_thread_property_lazy_instantiates(self) -> None:
        from talk2view_writer.extension import Talk2ViewWriterExtension

        ext = Talk2ViewWriterExtension(ctx=MagicMock(name="ctx"))
        with patch("talk2view_writer.ui_thread.UIThreadDispatcher") as cls:
            cls.return_value = MagicMock(name="dispatcher")
            disp = ext.ui_thread
            disp2 = ext.ui_thread  # second access reuses
        assert disp is disp2
        cls.assert_called_once_with(ext.ctx)

    def test_show_chat_window_creates_then_shows(self) -> None:
        from talk2view_writer.extension import Talk2ViewWriterExtension

        ext = Talk2ViewWriterExtension(ctx=MagicMock(name="ctx"))
        fake_window = MagicMock(name="WebWindow instance")
        with patch("talk2view_writer.ui.web_window.WebWindow") as cls:
            cls.return_value = fake_window
            ext.show_chat_window()
            ext.show_chat_window()  # second call reuses window
        # Constructor called once, show() called twice.
        cls.assert_called_once_with(ext.ctx)
        assert fake_window.show.call_count == 2


@pytest.mark.unit
class TestExtensionSingleton:
    """``get_extension`` returns the same instance across calls."""

    def test_returns_same_instance_across_calls(self) -> None:
        import talk2view_writer.extension as mod

        mod._INSTANCE = None  # reset

        ctx = MagicMock(name="ctx")
        a = mod.get_extension(ctx)
        b = mod.get_extension(ctx)
        assert a is b

    def test_get_extension_or_raise_raises_before_init(self) -> None:
        import talk2view_writer.extension as mod

        mod._INSTANCE = None
        with pytest.raises(RuntimeError, match="has not been initialised"):
            mod.get_extension_or_raise()

    def test_get_extension_or_raise_returns_after_init(self) -> None:
        import talk2view_writer.extension as mod

        mod._INSTANCE = None
        ctx = MagicMock(name="ctx")
        ext = mod.get_extension(ctx)
        assert mod.get_extension_or_raise() is ext
