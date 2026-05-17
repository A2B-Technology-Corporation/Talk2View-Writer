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
from typing import TYPE_CHECKING, Any, Callable, Iterator, List, Optional

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
        storage: Optional[FileTokenStorage] = None,
    ) -> None:
        self._partner_key = partner_key
        self._base_url = base_url
        self._storage = storage or FileTokenStorage()
        self._client: Optional["Talk2View"] = None
        self._user: Optional["User"] = None
        self._listeners: List[AuthListener] = []
        self._lock = threading.Lock()

    # ----- lifecycle ---------------------------------------------------

    def _ensure_client(self) -> "Talk2View":
        with self._lock:
            if self._client is None:
                from talk2view import Talk2View

                self._client = Talk2View(
                    partner_key=self._partner_key,
                    base_url=self._base_url,
                    storage=self._storage,
                )
                logger.info(
                    "Talk2View SDK instantiated (base_url=%s)", self._base_url
                )
                # Restore cached user (no network call) if we have one.
                cached_user = self._client.auth.get_user()
                if cached_user is not None:
                    self._user = cached_user
                    logger.info("Restored cached user: %s", cached_user.email)
            return self._client

    # ----- auth --------------------------------------------------------

    def login(self, email: str, password: str) -> "User":
        """Authenticate and persist tokens.

        Raises:
            talk2view.errors.AuthenticationError: bad credentials.
            talk2view.errors.NetworkError: cannot reach the engine.
        """
        client = self._ensure_client()
        user = client.auth.login(email, password)
        with self._lock:
            self._user = user
        self._notify_listeners(user)
        logger.info("Logged in as %s", user.email)
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
            return True
        # Possibly persisted but not yet loaded.
        client = self._ensure_client()
        cached = client.auth.get_user()
        if cached is not None:
            with self._lock:
                self._user = cached
            return True
        return False

    @property
    def current_user(self) -> Optional["User"]:
        """The currently logged-in user, or ``None``."""
        return self._user

    def add_auth_listener(self, listener: AuthListener) -> None:
        """Subscribe to ``login`` / ``logout`` transitions."""
        with self._lock:
            self._listeners.append(listener)

    def remove_auth_listener(self, listener: AuthListener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _notify_listeners(self, user: Optional["User"]) -> None:
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
        system_prompt: Optional[str] = None,
    ) -> Iterator["ChatEvent"]:
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
            raise SdkClientError("Not authenticated — call login() first")
        client = self._ensure_client()
        yield from client.chat(message, system_prompt=system_prompt)

    # ----- tools (Phase C entry point) ---------------------------------

    def register_tools(self, tool_functions: List[Any]) -> None:
        """Register ``@tool``-decorated functions with the SDK.

        Empty in Phase B; Phase C will start populating ``tool_functions``.
        """
        client = self._ensure_client()
        client.tools.register_from_functions(tool_functions)
        logger.info("Registered %d tools with SDK", len(tool_functions))
