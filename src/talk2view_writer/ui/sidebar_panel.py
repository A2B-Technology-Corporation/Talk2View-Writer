"""LibreOffice Writer sidebar panel for Talk2View.

Implements the *canonical* Python sidebar pattern from LibreOffice's
SDK example ``odk/examples/python/toolpanel/toolpanel.py``:

  1. The sidebar deck calls ``ChatPanelFactory.createUIElement`` (in
     ``extension/talk2view_writer.py``) with a ``ParentWindow`` XWindow
     and an XFrame.
  2. We return a :class:`Talk2ViewPanel` (XUIElement) — at this point
     **no window is created**. ``Frame``, ``ResourceURL``, ``Type``
     are exposed as direct instance attributes per the PyUNO IDL-to-
     attribute mapping.
  3. LibreOffice calls ``getRealInterface()``. *Now* we lazily build the
     panel window via
     ``com.sun.star.awt.ContainerWindowProvider.createContainerWindow``,
     loading the layout from ``panels/chat_panel.xdl`` shipped in the
     ``.oxt``. The provider accepts the bare XWindow as the parent
     and handles all peer creation internally — this is the workaround
     for the sidebar's ParentWindow not exposing XWindowPeer.
  4. ``getRealInterface()`` returns a :class:`Talk2ViewToolPanel`
     (XToolPanel) whose ``.PanelWindow`` / ``.Window`` attributes
     point at the loaded container. The sidebar dock code uses those
     to slot the panel into the deck.

Why this pattern: the manual ``UnoControlContainer + createPeer``
approach we tried before segfaulted soffice on every dock attempt
(see git log 2026-05-18). The dock code expects a VCL-bridged window
from ``ContainerWindowProvider``; an unbridged UnoControlContainer
is for embedded dialogs, not sidebar panels.

Threading: the send-button handler spawns a worker thread that
iterates ``sdk.chat(text)``. Every UNO call from the worker is
marshalled to the UI thread via :class:`UIThreadDispatcher`
(see ADR-0018).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import uno  # type: ignore[import-not-found]
import unohelper  # type: ignore[import-not-found]
from com.sun.star.awt import XActionListener  # type: ignore[import-not-found]
from com.sun.star.ui import (  # type: ignore[import-not-found]
    UIElementType,
    XToolPanel,
    XUIElement,
)

if TYPE_CHECKING:
    from com.sun.star.awt import ActionEvent, XWindow
    from com.sun.star.frame import XFrame
    from com.sun.star.lang import EventObject
    from com.sun.star.uno import XComponentContext
    from talk2view.types import User

logger = logging.getLogger(__name__)


def _ru(obj: Any) -> str:
    """Render a UNO proxy for safe logging.

    Python's ``logging`` module has a single-positional-arg fast path
    that checks ``isinstance(args[0], Mapping)`` to decide whether to
    treat the value as ``%(name)s``-style kwargs. The isinstance call
    delegates to ABC's ``__subclasscheck__``, which dereferences
    ``args[0].__class__``. UNO proxies expose a synthetic ``__class__``
    that isn't a real Python class, so the check raises ``TypeError``
    — and because this happens inside Python's C-implemented logging
    fast path, the exception silently crashes soffice instead of
    surfacing as a Python traceback.

    Use ``_ru(obj)`` (UNO-safe repr) on every UNO value before passing
    it to a logger. Result is a plain ``str``, which is safe for the
    fast path. This is the third time this bug has bitten the
    codebase — make it impossible to write the broken form by always
    using this helper.
    """
    try:
        return repr(obj)
    except Exception as exc:
        # If repr itself raises (some UNO proxies don't implement it
        # cleanly), fall back to a type-name string.
        return f"<repr failed: {type(exc).__name__}>"


# Must match the identifier in extension/description.xml — looked up
# at runtime via the deployment singleton to find the extension's
# install path on disk.
_EXTENSION_ID = "com.talk2view.writer"
_XDL_PATH = "panels/chat_panel.xdl"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_chat_panel(
    ctx: XComponentContext,
    parent_window: XWindow,
    frame: XFrame | None,
    resource_url: str,
) -> XUIElement:
    """Construct the Talk2View XUIElement.

    Called from ``ChatPanelFactory.createUIElement``. Window creation
    is deferred to ``getRealInterface`` per the canonical pattern;
    this function just builds the XUIElement wrapper and registers
    it with the extension singleton.
    """
    logger.info(
        "build_chat_panel: resource_url=%s frame=%s",
        resource_url,
        "present" if frame is not None else "None",
    )
    panel = Talk2ViewPanel(ctx, frame, parent_window, resource_url)

    from talk2view_writer.extension import get_extension

    get_extension(ctx).register_panel(panel)
    return panel


# ---------------------------------------------------------------------------
# XToolPanel — wraps the loaded container window for the sidebar dock
# ---------------------------------------------------------------------------


class Talk2ViewToolPanel(unohelper.Base, XToolPanel):
    """The XToolPanel returned from XUIElement.getRealInterface.

    The sidebar dock code reads ``.Window`` / ``.PanelWindow`` to slot
    the panel into the deck, and ``createAccessible`` to wire it into
    the AT-SPI tree. Mirrors LibreOffice SDK's ``pocToolPanel``.
    """

    def __init__(self, panel_window: Any, ctx: XComponentContext) -> None:
        self.ctx = ctx
        self.PanelWindow = panel_window
        self.Window = panel_window

    def createAccessible(self, parent_accessible: object) -> Any:  # noqa: N802
        """XToolPanel: return our panel window as its own accessible root."""
        return self.PanelWindow


# ---------------------------------------------------------------------------
# XUIElement — the object returned from createUIElement
# ---------------------------------------------------------------------------


class Talk2ViewPanel(unohelper.Base, XUIElement):
    """Talk2View chat panel.

    XUIElement attributes (``Frame``, ``ResourceURL``, ``Type``) are
    set as direct Python attributes — PyUNO's attribute synthesis
    binds them to the IDL-declared read-only attributes. This is how
    LibreOffice's SDK toolpanel example does it.

    Deliberately does NOT inherit ``XComponent``. The sidebar framework
    treats panels that implement XComponent as owned, and immediately
    calls ``dispose()`` after ``getRealInterface()`` — tearing down
    the panel window 10ms after we create it. The SDK reference doesn't
    inherit XComponent either; lifecycle cleanup happens lazily when
    the underlying panel window itself is disposed.
    """

    def __init__(
        self,
        ctx: XComponentContext,
        frame: XFrame | None,
        parent_window: XWindow,
        resource_url: str,
    ) -> None:
        self.ctx = ctx
        self._frame_ref = frame  # used by handlers; XUIElement.Frame is set below
        self._parent_window = parent_window

        # XUIElement attributes (PyUNO maps these to the IDL attributes).
        self.Frame = frame
        self.ResourceURL = resource_url
        self.Type = int(UIElementType.TOOLPANEL)

        # Lazy-built on first getRealInterface() call.
        self._tool_panel: Talk2ViewToolPanel | None = None
        self._panel_window: Any | None = None  # ContainerWindowProvider result

        # Widget refs — bound after panel_window is created.
        self._status_label: Any | None = None
        self._login_button: Any | None = None
        self._history_field: Any | None = None
        self._composer_field: Any | None = None
        self._send_button: Any | None = None

        # Auth + chat state.
        self._user: User | None = None
        self._busy = threading.Event()

    # ----- XUIElement -----------------------------------------------------

    def getRealInterface(self) -> Any:  # noqa: N802
        """Lazily build the panel window + return an XToolPanel wrapping it."""
        if self._tool_panel is None:
            window = self._create_panel_window()
            self._bind_controls(window)
            self._apply_auth_state()
            self._tool_panel = Talk2ViewToolPanel(window, self.ctx)
            logger.info("Talk2View panel window created and bound")
        return self._tool_panel

    def setSettings(self, settings: object) -> None:  # noqa: N802
        """XUIElement: no-op — tool panels don't carry settings."""

    def getSettings(self, write: bool) -> object | None:  # noqa: N802
        """XUIElement: no-op — tool panels don't carry settings."""
        return None

    # ----- Window construction --------------------------------------------

    def _create_panel_window(self) -> Any:
        """Load chat_panel.xdl via ContainerWindowProvider.

        Granular logging between every UNO call: createContainerWindow
        can segfault soffice (silent exit, no Python exception). When
        that happens the last log line we see pinpoints the failing
        operation.

        NB: every UNO-proxy log uses _ru() because Python's logging
        single-arg fast path crashes on UNO proxies. See _ru().
        """
        logger.info("_create_panel_window: resolving PIP singleton")
        pip = self.ctx.getValueByName(
            "/singletons/com.sun.star.deployment.PackageInformationProvider"
        )
        logger.info("_create_panel_window: PIP %s", _ru(pip))

        extension_root = pip.getPackageLocation(_EXTENSION_ID)
        dialog_url = f"{extension_root}/{_XDL_PATH}"
        logger.info("_create_panel_window: dialog_url=%s", dialog_url)

        logger.info("_create_panel_window: creating ContainerWindowProvider")
        provider = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.awt.ContainerWindowProvider", self.ctx
        )
        logger.info("_create_panel_window: provider %s", _ru(provider))

        logger.info(
            "_create_panel_window: calling createContainerWindow (parent %s)",
            _ru(self._parent_window),
        )
        window = provider.createContainerWindow(
            dialog_url, "", self._parent_window, None
        )
        logger.info(
            "_create_panel_window: createContainerWindow returned %s", _ru(window)
        )

        self._panel_window = window
        return window

    def _bind_controls(self, window: Any) -> None:
        """Resolve XDL control ids to control references + wire actions."""
        logger.info("_bind_controls: looking up status_label")
        self._status_label = window.getControl("status_label")
        logger.info("_bind_controls: looking up login_button")
        self._login_button = window.getControl("login_button")
        logger.info("_bind_controls: looking up history_field")
        self._history_field = window.getControl("history_field")
        logger.info("_bind_controls: looking up composer_field")
        self._composer_field = window.getControl("composer_field")
        logger.info("_bind_controls: looking up send_button")
        self._send_button = window.getControl("send_button")

        logger.info("_bind_controls: wiring action listeners")
        self._login_button.addActionListener(_ActionForwarder(self._on_login_clicked))
        self._send_button.addActionListener(_ActionForwarder(self._on_send_clicked))
        logger.info("_bind_controls: complete")

    # ----- Public: auth state callback ------------------------------------

    def on_auth_changed(self, user: User | None) -> None:
        """Called by the extension singleton on every login/logout."""
        self._user = user
        if self._tool_panel is not None:
            # Only update widgets once the panel has actually been built.
            self._apply_auth_state()

    # ----- Auth state application -----------------------------------------

    def _apply_auth_state(self) -> None:
        """Update labels + enabled state to match ``self._user``."""
        is_auth = self._user is not None

        if self._status_label is not None:
            text = (
                f"Logged in as {self._user.email}"
                if self._user is not None
                else "Talk2View — not logged in"
            )
            self._status_label.getModel().setPropertyValue("Label", text)

        if self._login_button is not None:
            self._login_button.getModel().setPropertyValue("EnableVisible", not is_auth)

        if self._composer_field is not None:
            self._composer_field.getModel().setPropertyValue("Enabled", is_auth)
        if self._send_button is not None:
            self._send_button.getModel().setPropertyValue("Enabled", is_auth)

    # ----- Event handlers -------------------------------------------------

    def _on_login_clicked(self) -> None:
        from talk2view_writer.extension import get_extension

        parent = (
            self._frame_ref.getContainerWindow() if self._frame_ref is not None else None
        )
        try:
            get_extension(self.ctx).show_login_dialog(parent_window=parent)
        except Exception as exc:
            logger.exception("Login flow failed")
            self._show_message("Talk2View — login failed", str(exc))

    def _on_send_clicked(self) -> None:
        if self._busy.is_set():
            logger.info("Send pressed while busy — ignoring")
            return
        if self._composer_field is None or self._history_field is None:
            return
        message = str(
            self._composer_field.getModel().getPropertyValue("Text") or ""
        ).strip()
        if not message:
            return

        self._composer_field.getModel().setPropertyValue("Text", "")

        # Slash commands take priority: they don't hit the engine.
        # See _handle_slash_command for the supported set.
        if message.startswith("/"):
            handled = self._handle_slash_command(message)
            if handled:
                return
            # Not a recognised slash command — fall through and send to engine.

        self._append_history(f"You: {message}\n")
        self._append_history("Talk2View: ")
        self._set_busy(True)

        thread = threading.Thread(target=self._chat_worker, args=(message,), daemon=True)
        thread.start()

    # ----- Slash commands -------------------------------------------------

    def _handle_slash_command(self, message: str) -> bool:
        """Try to handle ``message`` as a local slash command.

        Returns True if the command was recognised and consumed; False if
        the caller should fall through to sending the message to the
        engine (allowing e.g. `/path/to/file` to reach a tool when no
        local command matches).

        Recognised commands:
            /help                 — list available slash commands.
            /clear                — clear the chat history field.
            /logout               — sign out of Talk2View.
            /settings             — show the read-only settings dialog.
            /tools                — list registered tool names.
        """
        parts = message.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        if cmd == "/help":
            self._append_history(
                "\nSlash commands:\n"
                "  /help      Show this help.\n"
                "  /clear     Clear chat history.\n"
                "  /logout    Sign out of Talk2View.\n"
                "  /settings  Open settings.\n"
                "  /tools     List registered Writer tools.\n"
            )
            return True
        if cmd == "/clear":
            if self._history_field is not None:
                self._history_field.getModel().setPropertyValue("Text", "")
            return True
        if cmd == "/logout":
            from talk2view_writer.extension import get_extension

            get_extension(self.ctx).logout()
            self._append_history("\nLogged out.\n")
            return True
        if cmd == "/settings":
            from talk2view_writer.extension import get_extension

            parent = (
                self._frame_ref.getContainerWindow()
                if self._frame_ref is not None
                else None
            )
            get_extension(self.ctx).show_settings_dialog(parent_window=parent)
            return True
        if cmd == "/tools":
            from talk2view_writer.tools import all_tools

            names = sorted(t.__name__ for t in all_tools())
            self._append_history(
                f"\nRegistered tools ({len(names)}):\n  "
                + "\n  ".join(names)
                + "\n"
            )
            return True
        return False

    # ----- Chat worker (background thread) --------------------------------

    def _chat_worker(self, message: str) -> None:
        from talk2view_writer.extension import get_extension
        from talk2view_writer.system_prompt import load_system_prompt

        logger.info(
            "chat_worker started in thread=%s for message len=%d",
            threading.current_thread().name,
            len(message),
        )
        try:
            sdk = get_extension(self.ctx).sdk
            system_prompt = load_system_prompt()
            event_count = 0
            for event in sdk.chat(message, system_prompt=system_prompt):
                event_count += 1
                self._handle_chat_event(event)
            self._append_history("\n")
            logger.info("chat_worker finished cleanly after %d events", event_count)
        except Exception as exc:
            # UI boundary: catching `Exception` here is justified because
            # the chat-worker thread has no other way to surface failure
            # to the user; we write the error into the history field
            # (visible UI) before exiting cleanly. Re-raising would
            # crash the thread silently.
            logger.exception("chat_worker failed")
            self._append_history(f"\n[error] {exc}\n")
        finally:
            self._set_busy(False)

    def _handle_chat_event(self, event: Any) -> None:
        """Render a single :class:`talk2view.types.ChatEvent` into the panel.

        Six event types are emitted by the SDK (see ``talk2view/types.py``
        ChatEvent docstring): ``text``, ``status``, ``todos``, ``tool_call``,
        ``error``, ``done``. Each maps to one or two widget updates; the
        unhandled fallback logs at DEBUG so any SDK additions surface
        before tests catch them.
        """
        etype = getattr(event, "type", None)
        if etype == "text":
            if event.content:
                self._append_history(event.content)
        elif etype == "status":
            self._set_status(event.message or event.status or "")
        elif etype == "todos":
            self._render_todos(event.todos or "")
        elif etype == "tool_call":
            self._render_tool_call(
                getattr(event, "tool_name", None) or "?",
                getattr(event, "arguments", None) or {},
            )
        elif etype == "error":
            self._append_history(f"\n[error] {event.message}\n")
        elif etype == "done":
            return
        else:
            logger.debug("Unhandled ChatEvent type: %s", etype)

    def _render_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Render a tool_call event as a one-line bullet in the history.

        The SDK auto-executes the tool on the worker thread; this is
        purely a visual breadcrumb so the user can see what the agent
        decided to do. The matching tool result lands in subsequent
        ``text`` events from the engine's resume response.

        ``arguments`` is summarised to keep the line short — strings
        get truncated, lists / dicts get a count. Full args live in
        ``talk2view.log`` at INFO via ``ui_thread_tool``.
        """
        def _short(v: Any) -> str:
            if isinstance(v, str):
                return v if len(v) <= 40 else f"{v[:37]}..."
            if isinstance(v, (list, tuple, dict)):
                return f"{type(v).__name__}({len(v)})"
            return repr(v)
        arg_str = ", ".join(f"{k}={_short(v)}" for k, v in arguments.items())
        suffix = f" {arg_str}" if arg_str else ""
        self._append_history(f"\n  → {tool_name}({suffix})\n")

    def _render_todos(self, todos_text: str) -> None:
        """Render a todos plan from the agent into the history field.

        ``todos`` is a freeform string (the agent renders its own
        checklist). Prefix each line with a blank line + a label so it
        visually separates from text content above.
        """
        if not todos_text:
            return
        self._append_history(f"\nPlan:\n{todos_text}\n")

    # ----- Cross-thread widget writers ------------------------------------

    def _dispatch_ui(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        from talk2view_writer.extension import get_extension

        return get_extension(self.ctx).ui_thread.run_sync(fn, *args, **kwargs)

    def _append_history(self, text: str) -> None:
        if self._history_field is None:
            return
        self._dispatch_ui(self._append_history_ui, text)

    def _append_history_ui(self, text: str) -> None:
        if self._history_field is None:
            return
        model = self._history_field.getModel()
        current = model.getPropertyValue("Text") or ""
        model.setPropertyValue("Text", current + text)

    def _set_status(self, text: str) -> None:
        if self._status_label is None:
            return
        self._dispatch_ui(self._set_status_ui, text)

    def _set_status_ui(self, text: str) -> None:
        if self._status_label is None:
            return
        self._status_label.getModel().setPropertyValue("Label", text)

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self._busy.set()
        else:
            self._busy.clear()
        self._dispatch_ui(self._set_busy_ui, busy)

    def _set_busy_ui(self, busy: bool) -> None:
        if self._composer_field is not None:
            self._composer_field.getModel().setPropertyValue(
                "Enabled", not busy and self._user is not None
            )
        if self._send_button is not None:
            self._send_button.getModel().setPropertyValue(
                "Enabled", not busy and self._user is not None
            )
        if busy and self._status_label is not None:
            self._status_label.getModel().setPropertyValue("Label", "Thinking…")
        elif not busy:
            self._apply_auth_state()

    # ----- Misc -----------------------------------------------------------

    def _show_message(self, title: str, message: str) -> None:
        if self._frame_ref is None:
            logger.warning("No frame; cannot show message: %s", message)
            return
        window = self._frame_ref.getContainerWindow()
        toolkit = window.getToolkit()
        msgbox = toolkit.createMessageBox(
            window,
            uno.Enum("com.sun.star.awt.MessageBoxType", "ERRORBOX"),
            1,
            title,
            message,
        )
        msgbox.execute()


# ---------------------------------------------------------------------------
# Helper: bridge between UNO XActionListener and a plain Python callable
# ---------------------------------------------------------------------------


class _ActionForwarder(unohelper.Base, XActionListener):
    """Forward UNO action events to a Python callable."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback

    def actionPerformed(self, event: ActionEvent) -> None:  # noqa: N802
        self._callback()

    def disposing(self, event: EventObject) -> None:
        pass
