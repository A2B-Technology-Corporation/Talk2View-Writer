"""Process-wide singleton for the Talk2View-Writer extension.

LibreOffice instantiates a new UNO job/factory object per command, so any
state we want to persist (auth tokens, the SDK client, open sidebar panels)
lives at module level here and is fetched via :func:`get_extension`.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from com.sun.star.awt import XWindow
    from com.sun.star.uno import XComponentContext
    from talk2view.types import User

    from talk2view_writer.sdk_client import Talk2ViewSDKClient
    from talk2view_writer.ui.web_window import WebWindow
    from talk2view_writer.ui_thread import UIThreadDispatcher

logger = logging.getLogger(__name__)


class Talk2ViewWriterExtension:
    """Holds long-lived state for the extension across UNO invocations.

    Owns:

    - the :class:`Talk2ViewSDKClient` (lazy-init on first ``sdk`` access),
    - the singleton :class:`ChatWindow` (per ADR-0029 — one floating
      chat window per process),
    - the auth-state listener that broadcasts login/logout to it.
    """

    def __init__(self, ctx: XComponentContext) -> None:
        self.ctx = ctx
        self._lock = threading.Lock()
        self._sdk: Talk2ViewSDKClient | None = None
        self._ui_thread: UIThreadDispatcher | None = None
        self._tools_registered = False
        self._chat_window: WebWindow | None = None
        # NOTE: render ctx via repr() at the call site rather than passing
        # the UNO proxy through %r. Python logging's fast path does
        # `isinstance(args[0], Mapping)` when there's a single positional
        # arg, which crashes on UNO proxies whose synthetic __class__
        # isn't a real Python class. Always stringify UNO objects before
        # logging them.
        logger.info(
            "Talk2ViewWriterExtension singleton created (ctx=%s). "
            "Lazy sub-systems (UIThreadDispatcher, SDK client) will "
            "initialise on first access.",
            repr(ctx),
        )

    # ------------------------------------------------------------------
    # UI-thread dispatcher (lazy, owned at extension lifetime)
    # ------------------------------------------------------------------

    @property
    def ui_thread(self) -> UIThreadDispatcher:
        """Lazily-instantiated :class:`UIThreadDispatcher`.

        Used by every tool implementation (via the ``ui_thread_tool``
        decorator) and by the sidebar panel when streaming chat events
        from a worker thread.
        """
        with self._lock:
            if self._ui_thread is None:
                from talk2view_writer.ui_thread import UIThreadDispatcher

                self._ui_thread = UIThreadDispatcher(self.ctx)
                logger.info("UIThreadDispatcher instantiated")
            return self._ui_thread

    # ------------------------------------------------------------------
    # SDK lifecycle
    # ------------------------------------------------------------------

    @property
    def sdk(self) -> Talk2ViewSDKClient:
        """Lazily-instantiated Talk2View SDK wrapper.

        Side-effect-free beyond constructing the client. Tool
        registration deliberately happens elsewhere — see
        :meth:`_register_tools_if_needed`. That call hits an
        authenticated endpoint (``/v1/tools/register``); putting it
        here meant every menu action (login, logout, settings) tried
        to register tools *before* the user had a session, raising
        ``AuthenticationError`` and aborting the action. Worse, the
        login path itself read ``self.sdk.login(...)``, so the
        property fired before the credentials could be sent — a
        deadlock-by-property.
        """
        stale_cache = False
        with self._lock:
            if self._sdk is None:
                # Pre-flight: make the bundled pydantic_core wheel matching
                # the runtime Python + platform importable. See ADR-0023.
                from talk2view_writer._wheel_loader import (
                    ensure_vendored_pydantic_core,
                )

                ensure_vendored_pydantic_core()

                from talk2view_writer.sdk_client import Talk2ViewSDKClient

                self._sdk = Talk2ViewSDKClient()
                self._sdk.add_auth_listener(self._on_auth_changed)
                logger.info("SDK client instantiated")
                # If the user is already authenticated from a previous
                # session (token in storage), the auth listener will
                # never fire — register tools now while we hold the
                # lock.
                #
                # ``is_authenticated()`` only checks for a cached user;
                # the access token may have expired server-side. In
                # that case ``register_tools`` returns 401 →
                # ``AuthenticationError``. Catch it, defer the
                # cleanup to outside this lock — ``sdk.logout()``
                # fires ``_on_auth_changed`` which re-acquires
                # ``self._lock``, so calling it here would deadlock.
                if self._sdk.is_authenticated():
                    logger.info(
                        "SDK init: cached auth detected, registering "
                        "tools immediately"
                    )
                    from talk2view.errors import AuthenticationError

                    try:
                        self._register_tools_locked()
                    except AuthenticationError:
                        stale_cache = True
        if stale_cache:
            logger.warning(
                "Cached session is stale; engine rejected register_tools "
                "with 401. Clearing local credentials so the user is "
                "prompted to re-login on next action."
            )
            try:
                self._sdk.logout()
            except Exception:
                logger.exception(
                    "Stale-session cleanup via sdk.logout() failed; "
                    "continuing"
                )
        return self._sdk

    def _register_tools_locked(self) -> None:
        """Register every tool with the SDK. Caller must hold ``self._lock``.

        Hits ``/v1/tools/register`` — authentication required. Only
        invoke when ``self._sdk.is_authenticated()`` is True (i.e. from
        the auth-change listener on login, or from the ``sdk`` getter
        when a cached session was restored).
        """
        if self._tools_registered:
            return
        assert self._sdk is not None, "sdk must exist before tool registration"
        from talk2view_writer.tools import all_tools

        tools = all_tools()
        self._sdk.register_tools(tools)
        self._tools_registered = True
        logger.info(
            "Registered %d tool(s) with SDK: %s",
            len(tools),
            [getattr(t, "__name__", repr(t)) for t in tools],
        )

    # ------------------------------------------------------------------
    # Menu command handlers (called by Talk2ViewJob.trigger)
    # ------------------------------------------------------------------

    def show_chat_window(self) -> None:
        """Open (or refocus) the singleton Talk2View chat window.

        Per ADR-0030: the chat UI is a pywebview-backed window that
        renders the same React + Talk2View SDK stack the Word
        integration uses. The previous UNO-based dialog (ADR-0029) is
        retired because every text-stream chunk required a
        UNO-via-UI-thread round-trip and the worker deadlocked on the
        AsyncCallback marshal — the web stack does all rendering
        client-side and never touches UNO except when a tool runs.
        """
        logger.info("show_chat_window invoked (menu command)")
        with self._lock:
            if self._chat_window is None:
                from talk2view_writer.ui.web_window import WebWindow

                self._chat_window = WebWindow(self.ctx)
        self._chat_window.show()
        logger.info("show_chat_window: complete")

    def show_login_dialog(self, parent_window: XWindow | None = None) -> None:
        """Prompt for credentials and call ``sdk.login``."""
        logger.info(
            "show_login_dialog invoked (parent_window=%r is_authed_pre=%s)",
            parent_window,
            self._sdk.is_authenticated() if self._sdk is not None else "n/a",
        )
        from talk2view_writer.ui.login_dialog import show_login_dialog

        creds = show_login_dialog(self.ctx, parent_window=parent_window)
        if creds is None:
            logger.info("Login dialog cancelled by user")
            return
        email, _password = creds  # never log password
        logger.info("Login dialog submitted for email=%s — calling sdk.login()", email)
        # SDK errors propagate to the caller (Talk2ViewJob.trigger), which
        # surfaces them via an ERRORBOX. We deliberately do not catch
        # AuthenticationError / NetworkError here — see ADR-0014.
        user = self.sdk.login(email, _password)
        logger.info(
            "Login succeeded for %s — is_authenticated=%s",
            user.email,
            self.sdk.is_authenticated(),
        )

    def logout(self) -> None:
        """Clear local + server-side session."""
        logger.info(
            "logout invoked (was authed=%s)",
            self._sdk.is_authenticated() if self._sdk is not None else "n/a",
        )
        self.sdk.logout()
        logger.info("logout complete — local tokens cleared")

    def show_settings_dialog(self, parent_window: XWindow | None = None) -> None:
        """Open the (read-only) Talk2View settings status panel."""
        logger.info("show_settings_dialog")
        from talk2view_writer.ui.settings_dialog import show_settings_dialog

        show_settings_dialog(self.ctx, self.sdk, parent_window=parent_window)

    # ------------------------------------------------------------------
    # Internal: auth state fan-out
    # ------------------------------------------------------------------

    def _on_auth_changed(self, user: User | None) -> None:
        """SDK auth-state callback — register/unregister tools.

        Per ADR-0030 the chat UI lives in a webview and runs its own
        Talk2View SDK in the browser. The Python-side SDK kept here is
        used only for the legacy login-dialog flow + tool registration
        bookkeeping until the web shell takes over those too. There is
        no longer a Python-side chat window to fan auth events to.
        """
        with self._lock:
            if user is not None:
                self._register_tools_locked()
            else:
                # Logout: server-side session is gone, our registration
                # is invalid against the next session.
                self._tools_registered = False
        logger.info(
            "Auth changed: user=%s",
            user.email if user else None,
        )


_INSTANCE: Talk2ViewWriterExtension | None = None
_INSTANCE_LOCK = threading.Lock()


def get_extension(ctx: XComponentContext) -> Talk2ViewWriterExtension:
    """Return the process-wide :class:`Talk2ViewWriterExtension` singleton."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = Talk2ViewWriterExtension(ctx)
        return _INSTANCE


def get_extension_or_raise() -> Talk2ViewWriterExtension:
    """Return the singleton if it has been created; otherwise raise.

    Use this from contexts that cannot supply an :class:`XComponentContext`
    of their own — tool implementations and the UI-thread dispatcher
    callbacks. By the time a tool fires the singleton must already exist
    (the sidebar panel that triggered the chat created it), so a missing
    instance indicates a real bug rather than a recoverable state.

    Raises:
        RuntimeError: If :func:`get_extension` has not yet been called.
    """
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            raise RuntimeError(
                "Talk2ViewWriterExtension is not initialised — "
                "get_extension(ctx) must be called first"
            )
        return _INSTANCE
