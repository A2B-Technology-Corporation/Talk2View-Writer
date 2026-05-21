"""Floating non-modal chat window for Talk2View.

Per ADR-0029: the LibreOffice sidebar panel pattern is fundamentally
broken on LO 26.x — the framework-supplied ParentWindow is a
deliberately-restricted facade that doesn't implement XWindowPeer,
doesn't expose ``getToolkit``, and even ``getPosSize`` raises
"not implemented". After four ADRs (0025/0026/0027/0028) of failed
Python workarounds, this module replaces the sidebar with a floating
non-modal window opened via the **Talk2View → Open Chat** menu.

Construction: :class:`ChatWindow` calls
``com.sun.star.awt.DialogProvider2.createDialog`` on the same XDL
layout we already ship (``panels/chat_panel.xdl``). DialogProvider2
takes a URL string — no XWindowPeer parent needed. Single code path
that works on every LibreOffice build (TDF, Flathub, Snap, AppImage,
Debian apt) on every platform.

UX: a separate OS window with a title bar that the user can move,
close, or drag-to-dock against a screen edge via the desktop
environment's window manager. For users who want it docked to the
side of the Writer window, dragging to the screen edge gives them
exactly that on Linux (KDE, GNOME), macOS, and Windows.

Threading: the send-button handler spawns a worker thread that
iterates ``sdk.chat(text)``. Every UNO call from the worker is
marshalled to the UI thread via :class:`UIThreadDispatcher`
(see ADR-0018).
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

import uno  # type: ignore[import-not-found]
import unohelper  # type: ignore[import-not-found]
from com.sun.star.awt import XActionListener  # type: ignore[import-not-found]

from talk2view_writer._logging import flush_logs

if TYPE_CHECKING:
    from com.sun.star.awt import ActionEvent
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
    fast path.
    """
    try:
        return repr(obj)
    except Exception as exc:
        return f"<repr failed: {type(exc).__name__}>"


_EXTENSION_ID = "com.talk2view.writer"
_XDL_PATH = "panels/chat_panel.xdl"


# ---------------------------------------------------------------------------
# Diagnostic helpers (kept from the sidebar era — useful for any UNO
# debugging future maintainers might need).
#
# The single intentional non-rethrow pattern in this module lives in
# ``_safe_call``: per-attribute lookups in a diagnostic walk are logged
# with full traceback via ``logger.exception`` then we continue to the
# next attribute. Not silent failure — the full trace is in the log.
# All application-level code paths re-raise after logger.exception.
# ---------------------------------------------------------------------------


def _safe_call(label: str, fn: Callable[[], Any]) -> Any | None:
    """Run a diagnostic attribute lookup; on failure log + return ``None``."""
    try:
        return fn()
    except Exception:
        logger.exception("diag %s: lookup raised — continuing", label)
        return None


def _log_dialog_state(label: str, dialog: Any) -> None:
    """Walk a constructed XDialog and log size / peer / children.

    Best-effort: any single attribute that fails is logged via
    :func:`_safe_call` and we continue. The constructed dialog is
    expected to be a fully-featured XDialog (not the restricted
    sidebar stub) so most of these should succeed.
    """
    if dialog is None:
        logger.info("diag %s: dialog is None", label)
        return
    logger.info("diag %s: dialog=%s", label, _ru(dialog))

    rect = _safe_call(f"{label}.getPosSize", lambda: dialog.getPosSize())
    if rect is not None:
        logger.info(
            "diag %s.getPosSize: X=%s Y=%s Width=%s Height=%s",
            label,
            getattr(rect, "X", "?"),
            getattr(rect, "Y", "?"),
            getattr(rect, "Width", "?"),
            getattr(rect, "Height", "?"),
        )

    title = _safe_call(f"{label}.getTitle", lambda: dialog.getTitle())
    logger.info("diag %s.getTitle=%r", label, title)

    visible = _safe_call(f"{label}.isVisible", lambda: dialog.isVisible())
    logger.info("diag %s.isVisible=%s", label, visible)

    peer = _safe_call(f"{label}.getPeer", lambda: dialog.getPeer())
    if peer is not None:
        logger.info("diag %s.peer=%s", label, _ru(peer))


def _log_control(group: str, name: str, control: Any) -> None:
    """Walk a control + its model and log diagnostic state."""
    if control is None:
        logger.info("diag %s/%s: control is None", group, name)
        return
    logger.info("diag %s/%s: control=%s", group, name, _ru(control))

    model = _safe_call(f"{group}/{name}.getModel", lambda: control.getModel())
    if model is not None:
        for prop in ("Name", "Label", "Text", "Enabled", "EnableVisible", "Hidden"):
            value = _safe_call(
                f"{group}/{name}.model.{prop}",
                lambda p=prop: model.getPropertyValue(p),
            )
            logger.info("diag %s/%s.model.%s=%r", group, name, prop, value)


_PLATFORM_INFO_LOGGED = False


def _log_platform_info(ctx: Any) -> None:
    """One-shot dump of LibreOffice product info."""
    global _PLATFORM_INFO_LOGGED
    if _PLATFORM_INFO_LOGGED:
        return
    _PLATFORM_INFO_LOGGED = True

    try:
        from com.sun.star.beans import PropertyValue

        nodepath = PropertyValue()
        nodepath.Name = "nodepath"
        nodepath.Value = "/org.openoffice.Setup/Product"

        cfg_provider = ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.configuration.ConfigurationProvider", ctx
        )
        access = cfg_provider.createInstanceWithArguments(
            "com.sun.star.configuration.ConfigurationAccess",
            (nodepath,),
        )
        for prop in ("ooName", "ooSetupVersion", "ooSetupVersionAboutBox"):
            value = access.getByName(prop)
            logger.info("diag LibreOffice.Setup.Product.%s=%s", prop, _ru(value))
    except Exception:
        logger.exception(
            "diag _log_platform_info: failed reading LibreOffice product info "
            "(best-effort diagnostic — continuing)"
        )


def _assert_dialog_file_exists(dialog_url: str) -> None:
    """Verify ``dialog_url`` resolves to a real file on disk.

    Raises :class:`FileNotFoundError` if missing, with a clear message
    the caller logs via ``logger.exception`` before re-raising.
    """
    parsed = urlparse(dialog_url)
    if parsed.scheme != "file":
        logger.info(
            "diag dialog_url has non-file scheme %r — skipping existence check",
            parsed.scheme,
        )
        return
    local_path = Path(unquote(parsed.path))
    if not local_path.exists():
        raise FileNotFoundError(
            f"Talk2View dialog file missing: {local_path} "
            f"(resolved from {dialog_url})"
        )
    logger.info(
        "diag dialog_url file exists: path=%s size=%d bytes",
        local_path,
        local_path.stat().st_size,
    )


# ---------------------------------------------------------------------------
# ChatWindow
# ---------------------------------------------------------------------------


class ChatWindow:
    """Singleton floating non-modal chat window for Talk2View.

    Constructed once per process. ``show()`` raises and refocuses the
    existing window on subsequent invocations.

    The construction call (``DialogProvider2.createDialog``) takes a
    URL string and needs no XWindowPeer parent — this sidesteps every
    sidebar-parent constraint that the historical sidebar attempts
    (ADRs 0025/0026/0027/0028) ran into.
    """

    def __init__(self, ctx: XComponentContext) -> None:
        self.ctx = ctx

        # Lazy-built on first show().
        self._dialog: Any | None = None

        # Widget refs — bound after the dialog is created.
        self._status_label: Any | None = None
        self._login_button: Any | None = None
        self._history_field: Any | None = None
        self._composer_field: Any | None = None
        self._send_button: Any | None = None

        # Auth + chat state.
        self._user: User | None = None
        self._busy = threading.Event()

        logger.info("ChatWindow instantiated (ctx=%s)", _ru(ctx))

    # ----- Public API -----------------------------------------------------

    def show(self) -> None:
        """Open the chat window. Constructs lazily on first call.

        Re-invocations make the existing window visible + bring it to
        the front; they do NOT create a second window.
        """
        logger.info("ChatWindow.show: already_built=%s", self._dialog is not None)
        if self._dialog is None:
            _log_platform_info(self.ctx)
            try:
                self._dialog = self._create_dialog()
            except Exception:
                logger.exception(
                    "ChatWindow.show: _create_dialog raised — re-raising"
                )
                raise
            try:
                self._bind_controls(self._dialog)
            except Exception:
                logger.exception(
                    "ChatWindow.show: _bind_controls raised — re-raising"
                )
                raise
            try:
                self._apply_auth_state()
            except Exception:
                logger.exception(
                    "ChatWindow.show: _apply_auth_state raised — re-raising"
                )
                raise

        self._dialog.setVisible(True)
        try:
            # Bring the window to the front. Best-effort — some
            # window managers ignore programmatic raise.
            peer = _safe_call("ChatWindow.show.getPeer", lambda: self._dialog.getPeer())
            if peer is not None:
                _safe_call(
                    "ChatWindow.show.peer.toFront", lambda: peer.toFront()
                )
        except Exception:
            logger.exception(
                "ChatWindow.show: bring-to-front failed (best effort, continuing)"
            )
        logger.info("ChatWindow.show: dialog visible")

    def hide(self) -> None:
        """Hide the chat window without disposing it."""
        logger.info("ChatWindow.hide: built=%s", self._dialog is not None)
        if self._dialog is not None:
            self._dialog.setVisible(False)

    def is_visible(self) -> bool:
        """Return True if the dialog exists and is currently visible."""
        if self._dialog is None:
            return False
        result = _safe_call(
            "ChatWindow.is_visible", lambda: self._dialog.isVisible()
        )
        return bool(result)

    # ----- Dialog construction -------------------------------------------

    def _create_dialog(self) -> Any:
        """Load chat_panel.xdl via DialogProvider2.createDialog.

        DialogProvider2's API takes a URL string — no XWindowPeer
        parent. This sidesteps the sidebar-parent constraints that
        broke ADRs 0027/0028 on LO 26.x.
        """
        logger.info(
            "_create_dialog: enter sys.platform=%s sys.version=%s",
            sys.platform,
            sys.version.split()[0],
        )
        logger.info("_create_dialog: resolving PIP singleton")
        pip = self.ctx.getValueByName(
            "/singletons/com.sun.star.deployment.PackageInformationProvider"
        )
        logger.info("_create_dialog: PIP %s", _ru(pip))

        extension_root = pip.getPackageLocation(_EXTENSION_ID)
        dialog_url = f"{extension_root}/{_XDL_PATH}"
        logger.info(
            "_create_dialog: extension_root=%s dialog_url=%s",
            extension_root,
            dialog_url,
        )

        _assert_dialog_file_exists(dialog_url)

        logger.info("_create_dialog: creating DialogProvider2")
        provider = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.awt.DialogProvider2", self.ctx
        )
        logger.info("_create_dialog: provider %s", _ru(provider))

        logger.info("_create_dialog: flushing logs before createDialog")
        flush_logs()
        logger.info("_create_dialog: calling createDialog(url=%s)", dialog_url)
        try:
            dialog = provider.createDialog(dialog_url)
        except Exception:
            logger.exception("_create_dialog: createDialog raised — re-raising")
            raise
        logger.info("_create_dialog: createDialog returned %s", _ru(dialog))
        flush_logs()
        _log_dialog_state("created_dialog", dialog)
        return dialog

    def _bind_controls(self, dialog: Any) -> None:
        """Resolve XDL control ids to control references + wire actions."""
        logger.info("_bind_controls: enter")

        for name in (
            "status_label",
            "login_button",
            "history_field",
            "composer_field",
            "send_button",
        ):
            logger.info("_bind_controls: looking up %s", name)
            control = dialog.getControl(name)
            setattr(self, f"_{name}", control)
            _log_control("bind", name, control)

        logger.info("_bind_controls: flushing before wiring action listeners")
        flush_logs()
        logger.info("_bind_controls: wiring action listeners")
        self._login_button.addActionListener(_ActionForwarder(self._on_login_clicked))
        self._send_button.addActionListener(_ActionForwarder(self._on_send_clicked))
        logger.info("_bind_controls: complete")

    # ----- Public: auth state callback ------------------------------------

    def on_auth_changed(self, user: User | None) -> None:
        """Called by the extension singleton on every login/logout."""
        self._user = user
        if self._dialog is not None:
            # Only update widgets once the dialog has been built.
            self._apply_auth_state()

    # ----- Auth state application -----------------------------------------

    def _apply_auth_state(self) -> None:
        """Update labels + enabled state to match ``self._user``."""
        is_auth = self._user is not None
        user_email = (
            getattr(self._user, "email", None) if self._user is not None else None
        )
        logger.info(
            "_apply_auth_state: enter is_auth=%s user_email=%s", is_auth, user_email
        )

        if self._status_label is not None:
            text = (
                f"Logged in as {self._user.email}"
                if self._user is not None
                else "Talk2View — not logged in"
            )
            old = self._status_label.getModel().getPropertyValue("Label")
            self._status_label.getModel().setPropertyValue("Label", text)
            logger.info(
                "_apply_auth_state: status_label.Label %r -> %r", old, text
            )

        if self._login_button is not None:
            new = not is_auth
            old = self._login_button.getModel().getPropertyValue("EnableVisible")
            self._login_button.getModel().setPropertyValue("EnableVisible", new)
            logger.info(
                "_apply_auth_state: login_button.EnableVisible %r -> %r", old, new
            )

        if self._composer_field is not None:
            old = self._composer_field.getModel().getPropertyValue("Enabled")
            self._composer_field.getModel().setPropertyValue("Enabled", is_auth)
            logger.info(
                "_apply_auth_state: composer_field.Enabled %r -> %r", old, is_auth
            )
        if self._send_button is not None:
            old = self._send_button.getModel().getPropertyValue("Enabled")
            self._send_button.getModel().setPropertyValue("Enabled", is_auth)
            logger.info(
                "_apply_auth_state: send_button.Enabled %r -> %r", old, is_auth
            )
        logger.info("_apply_auth_state: done")

    # ----- Event handlers -------------------------------------------------

    def _on_login_clicked(self) -> None:
        from talk2view_writer.extension import get_extension

        parent = self._dialog_peer()
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

        if message.startswith("/"):
            handled = self._handle_slash_command(message)
            if handled:
                return

        self._append_history(f"You: {message}\n")
        self._append_history("Talk2View: ")
        self._set_busy(True)

        thread = threading.Thread(target=self._chat_worker, args=(message,), daemon=True)
        thread.start()

    # ----- Slash commands -------------------------------------------------

    def _handle_slash_command(self, message: str) -> bool:
        """Try to handle ``message`` as a local slash command.

        Returns True if recognised and consumed; False if the caller
        should fall through to sending the message to the engine.
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

            parent = self._dialog_peer()
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
            # UI boundary: surface the failure into the chat history
            # field (visible UI) so the user sees it. Logging captures
            # the full traceback.
            logger.exception("chat_worker failed")
            self._append_history(f"\n[error] {exc}\n")
        finally:
            self._set_busy(False)

    def _handle_chat_event(self, event: Any) -> None:
        """Render a single :class:`talk2view.types.ChatEvent` into the window."""
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

    def _dialog_peer(self) -> Any | None:
        """Return the dialog's window peer for parenting child dialogs."""
        if self._dialog is None:
            return None
        return _safe_call("ChatWindow._dialog_peer", lambda: self._dialog.getPeer())

    def _show_message(self, title: str, message: str) -> None:
        """Show an ERRORBOX above the chat window."""
        logger.info("_show_message: title=%r message=%r", title, message)
        peer = self._dialog_peer()
        if peer is None:
            logger.warning("No dialog peer; cannot show message: %s", message)
            return
        toolkit = peer.getToolkit()
        msgbox = toolkit.createMessageBox(
            peer,
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
