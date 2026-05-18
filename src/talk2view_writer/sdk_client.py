"""Thin wrapper around the Talk2View Python SDK.

Encapsulates SDK instantiation, auth state, and chat streaming. Phase B
exposes ``login``, ``logout``, ``is_authenticated``, ``current_user``,
and ``chat`` (an iterator over ``ChatEvent``). Phase C will add tool
registration; Phase F will add settings (model picker, etc.).

The SDK is imported lazily inside method bodies so the module imports
cleanly under a non-LibreOffice Python that has not yet bundled the
SDK into ``sys.path``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, Optional

from talk2view_writer import config
from talk2view_writer.storage import FileTokenStorage

if TYPE_CHECKING:
    from talk2view import Talk2View
    from talk2view.types import ChatEvent, User

logger = logging.getLogger(__name__)

# Type alias for an auth-state change callback.
AuthListener = Callable[[Optional["User"]], None]


class SdkClientError(RuntimeError):
    """Raised when the SDK wrapper hits an unrecoverable state."""


class Talk2ViewSDKClient:
    """Long-lived wrapper around a :class:`talk2view.Talk2View` instance.

    Owned by :class:`talk2view_writer.extension.Talk2ViewWriterExtension`
    as a singleton. Holds the file-backed token storage so that login
    state survives LibreOffice restarts.

    Threading: ``login`` / ``logout`` should be called on the UI thread
    (they pop dialogs / mutate auth state). ``chat`` is intended to be
    called from a worker thread (it blocks on SSE reads).
    """

    def __init__(
        self,
        *,
        partner_key: str = config.PARTNER_KEY,
        base_url: str = config.BASE_URL,
        storage: FileTokenStorage | None = None,
    ) -> None:
        self._partner_key = partner_key
        self._base_url = base_url
        self._storage = storage or FileTokenStorage()
        self._client: Talk2View | None = None
        self._user: User | None = None
        self._listeners: list[AuthListener] = []
        self._lock = threading.Lock()

    # ----- lifecycle ---------------------------------------------------

    def _ensure_client(self) -> Talk2View:
        with self._lock:
            if self._client is None:
                from talk2view import Talk2View

                # Log the storage path so we can diagnose "session
                # expired" by comparing the path the SDK reads tokens
                # from against where login() wrote them.
                storage_path = getattr(self._storage, "path", None) or repr(self._storage)
                logger.info(
                    "Instantiating Talk2View SDK: base_url=%s storage=%s",
                    self._base_url,
                    storage_path,
                )
                self._client = Talk2View(
                    partner_key=self._partner_key,
                    base_url=self._base_url,
                    storage=self._storage,
                )
                logger.info("Talk2View SDK instantiated successfully")
                # Restore cached user (no network call) if we have one.
                cached_user = self._client.auth.get_user()
                if cached_user is not None:
                    self._user = cached_user
                    logger.info(
                        "Restored cached user from token storage: %s",
                        cached_user.email,
                    )
                else:
                    logger.info(
                        "No cached user in token storage at %s — "
                        "user will need to log in",
                        storage_path,
                    )
            return self._client

    # ----- auth --------------------------------------------------------

    def login(self, email: str, password: str) -> User:
        """Authenticate and persist tokens.

        Raises:
            talk2view.errors.AuthenticationError: bad credentials.
            talk2view.errors.NetworkError: cannot reach the engine.
        """
        client = self._ensure_client()
        logger.info("login: calling client.auth.login(email=%s)", email)
        try:
            user = client.auth.login(email, password)
        except Exception as exc:
            logger.exception(
                "login: client.auth.login raised %s: %s",
                type(exc).__name__,
                exc,
            )
            raise
        with self._lock:
            self._user = user
        self._notify_listeners(user)
        # Verify the token actually landed in storage. Helps diagnose
        # "session expired on restart" — if storage shows no token
        # immediately after a successful login(), the writer path is
        # broken.
        try:
            persisted = self._storage.get("access_token") if hasattr(self._storage, "get") else None
            persisted_str = "present" if persisted else "MISSING"
        except Exception:
            persisted_str = "<storage.get raised>"
        logger.info(
            "login: success for %s. Access-token persisted to storage: %s",
            user.email,
            persisted_str,
        )
        return user

    def logout(self) -> None:
        """Clear server-side session and local tokens."""
        client = self._ensure_client()
        client.auth.logout()
        with self._lock:
            self._user = None
        # Clear the in-process session too so the next chat starts fresh.
        try:
            client.clear_session()
        except Exception:
            logger.exception("clear_session raised during logout")
        self._notify_listeners(None)
        logger.info("Logged out")

    def is_authenticated(self) -> bool:
        """``True`` if we hold a valid auth state."""
        if self._user is not None:
            logger.debug("is_authenticated: in-memory user present (%s)", self._user.email)
            return True
        # Possibly persisted but not yet loaded.
        client = self._ensure_client()
        cached = client.auth.get_user()
        if cached is not None:
            with self._lock:
                self._user = cached
            logger.info(
                "is_authenticated: restored user from storage on-demand (%s)",
                cached.email,
            )
            return True
        logger.debug("is_authenticated: no in-memory user, none in storage either")
        return False

    @property
    def current_user(self) -> User | None:
        """The currently logged-in user, or ``None``."""
        return self._user

    def add_auth_listener(self, listener: AuthListener) -> None:
        """Subscribe to ``login`` / ``logout`` transitions."""
        with self._lock:
            self._listeners.append(listener)

    def remove_auth_listener(self, listener: AuthListener) -> None:
        """Unsubscribe a previously-added auth-state listener."""
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _notify_listeners(self, user: User | None) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(user)
            except Exception:
                logger.exception("Auth listener raised")

    # ----- chat --------------------------------------------------------

    def chat(
        self,
        message: str,
        *,
        system_prompt: str | None = None,
    ) -> Iterator[ChatEvent]:
        """Send a chat message and yield streamed :class:`ChatEvent` items.

        Blocking iterator — call from a worker thread.

        Args:
            message: User message text.
            system_prompt: Optional system-prompt override. Phase E will
                wire this up to the bundled Writer-edited system prompt.

        Raises:
            SdkClientError: If not authenticated.
            talk2view.errors.T2VError: Engine error.
            talk2view.errors.NetworkError: Connection failure.
        """
        if not self.is_authenticated():
            logger.warning("chat() called before login — raising SdkClientError")
            raise SdkClientError("Not authenticated — call login() first")
        client = self._ensure_client()
        logger.info(
            "chat: sending message len=%d (system_prompt %s)",
            len(message),
            "supplied" if system_prompt is not None else "engine default",
        )
        event_count = 0
        try:
            for event in client.chat(message, system_prompt=system_prompt):
                event_count += 1
                # Log a compact summary at DEBUG so we don't spam INFO,
                # but still get the wire-level event trail when
                # T2V_WRITER_DEBUG=1.
                logger.debug("chat event #%d: %s", event_count, type(event).__name__)
                yield event
        except Exception as exc:
            logger.exception(
                "chat: stream raised after %d events: %s: %s",
                event_count,
                type(exc).__name__,
                exc,
            )
            raise
        logger.info("chat: stream complete after %d events", event_count)

    # ----- tools (Phase C entry point) ---------------------------------

    def register_tools(self, tool_functions: list[Any]) -> None:
        """Register ``@tool``-decorated functions with the SDK.

        Empty in Phase B; Phase C will start populating ``tool_functions``.
        """
        client = self._ensure_client()
        client.tools.register_from_functions(tool_functions)
        logger.info("Registered %d tools with SDK", len(tool_functions))
