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
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

import uno  # type: ignore[import-not-found]
import unohelper  # type: ignore[import-not-found]
from com.sun.star.awt import XActionListener  # type: ignore[import-not-found]
from com.sun.star.ui import (  # type: ignore[import-not-found]
    UIElementType,
    XToolPanel,
    XUIElement,
)

from talk2view_writer._logging import flush_logs

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


# ---------------------------------------------------------------------------
# Diagnostic helpers
#
# These walk UNO objects (XWindow, XControl, ConfigurationProvider) and
# log their state. They are used both for pre-flight diagnostics
# (before risky native calls) and post-call diagnostics (after
# createContainerWindow returns) so we can tell whether a returned
# window is sized / visible / has children.
#
# **The single intentional non-rethrow pattern in this codebase lives
# here.** A diagnostic walk touches many UNO properties; some of them
# legitimately aren't supported by every impl (e.g. ``isVisible`` is
# only on XWindow2). To keep walking past those, ``_safe_call`` catches
# the per-attribute exception and routes the **full traceback** into
# the log via ``logger.exception(...)``, then continues. This is NOT
# silent failure — the trace IS in the log. See the approved plan
# ``~/.claude/plans/add-complete-logging-so-steady-finch.md`` for the
# rationale. All application-level code paths re-raise after
# ``logger.exception``; only these diagnostic walkers continue.
# ---------------------------------------------------------------------------


def _safe_call(label: str, fn: Callable[[], Any]) -> Any | None:
    """Run a diagnostic attribute lookup; on failure log + return ``None``.

    Diagnostic-only helper. The exception's **full traceback** is
    captured via ``logger.exception(...)``. See the module-level note
    above for the rationale.
    """
    try:
        return fn()
    except Exception:
        logger.exception("diag %s: lookup raised — continuing", label)
        return None


def _log_window_state(label: str, window: Any) -> None:
    """Walk an XWindow and log size / peer / visibility / children.

    Best-effort: any single attribute that fails is logged with full
    traceback (via :func:`_safe_call`) and we continue to the next.

    Method accesses are wrapped in lambdas so that a missing-method
    ``AttributeError`` is caught inside ``_safe_call`` (and the full
    traceback ends up in the log) instead of escaping uncaught from
    the bound-method lookup.
    """
    if window is None:
        logger.info("diag %s: window is None", label)
        return
    logger.info("diag %s: window=%s", label, _ru(window))

    rect = _safe_call(f"{label}.getPosSize", lambda: window.getPosSize())
    if rect is not None:
        logger.info(
            "diag %s.getPosSize: X=%s Y=%s Width=%s Height=%s",
            label,
            getattr(rect, "X", "?"),
            getattr(rect, "Y", "?"),
            getattr(rect, "Width", "?"),
            getattr(rect, "Height", "?"),
        )

    out = _safe_call(f"{label}.getOutputSize", lambda: window.getOutputSize())
    if out is not None:
        logger.info(
            "diag %s.getOutputSize: Width=%s Height=%s",
            label,
            getattr(out, "Width", "?"),
            getattr(out, "Height", "?"),
        )

    visible = _safe_call(f"{label}.isVisible", lambda: window.isVisible())
    logger.info("diag %s.isVisible=%s", label, visible)

    peer = _safe_call(f"{label}.getPeer", lambda: window.getPeer())
    if peer is not None:
        logger.info("diag %s.peer=%s", label, _ru(peer))
        peer_rect = _safe_call(
            f"{label}.peer.getPosSize", lambda: peer.getPosSize()
        )
        if peer_rect is not None:
            logger.info(
                "diag %s.peer.getPosSize: X=%s Y=%s Width=%s Height=%s",
                label,
                getattr(peer_rect, "X", "?"),
                getattr(peer_rect, "Y", "?"),
                getattr(peer_rect, "Width", "?"),
                getattr(peer_rect, "Height", "?"),
            )

    for service in (
        "com.sun.star.awt.UnoControlContainer",
        "com.sun.star.awt.UnoControlDialog",
    ):
        supported = _safe_call(
            f"{label}.supportsService({service})",
            lambda s=service: window.supportsService(s),
        )
        logger.info("diag %s.supportsService(%s)=%s", label, service, supported)

    controls = _safe_call(f"{label}.getControls", lambda: window.getControls())
    if controls is not None:
        count = _safe_call(f"{label}.getControls.len", lambda c=controls: len(c))
        logger.info("diag %s.getControls count=%s", label, count)
        try:
            for i, child in enumerate(controls):
                child_name = _safe_call(
                    f"{label}.child[{i}].getModel.Name",
                    lambda c=child: c.getModel().getPropertyValue("Name"),
                )
                child_rect = _safe_call(
                    f"{label}.child[{i}].getPosSize",
                    lambda c=child: c.getPosSize(),
                )
                if child_rect is not None:
                    logger.info(
                        "diag %s.child[%d] name=%s X=%s Y=%s W=%s H=%s",
                        label,
                        i,
                        child_name,
                        getattr(child_rect, "X", "?"),
                        getattr(child_rect, "Y", "?"),
                        getattr(child_rect, "Width", "?"),
                        getattr(child_rect, "Height", "?"),
                    )
                else:
                    logger.info(
                        "diag %s.child[%d] name=%s (no rect)", label, i, child_name
                    )
        except Exception:
            logger.exception("diag %s: iterating children raised — continuing", label)


def _log_control(group: str, name: str, control: Any) -> None:
    """Walk a control + its model and log diagnostic state.

    Best-effort, same pattern as :func:`_log_window_state`.
    """
    if control is None:
        logger.info("diag %s/%s: control is None", group, name)
        return
    logger.info("diag %s/%s: control=%s", group, name, _ru(control))

    model = _safe_call(f"{group}/{name}.getModel", lambda: control.getModel())
    if model is not None:
        logger.info("diag %s/%s: model=%s", group, name, _ru(model))
        for prop in ("Name", "Label", "Text", "Enabled", "EnableVisible", "Hidden"):
            value = _safe_call(
                f"{group}/{name}.model.{prop}",
                lambda p=prop: model.getPropertyValue(p),
            )
            logger.info("diag %s/%s.model.%s=%r", group, name, prop, value)

    rect = _safe_call(f"{group}/{name}.getPosSize", lambda: control.getPosSize())
    if rect is not None:
        logger.info(
            "diag %s/%s.getPosSize: X=%s Y=%s Width=%s Height=%s",
            group,
            name,
            getattr(rect, "X", "?"),
            getattr(rect, "Y", "?"),
            getattr(rect, "Width", "?"),
            getattr(rect, "Height", "?"),
        )


_PLATFORM_INFO_LOGGED = False


def _log_platform_info(ctx: Any) -> None:
    """One-shot dump of LibreOffice product info at panel-build time.

    Reads ``/org.openoffice.Setup/Product`` via the configuration
    provider. Best-effort: a single try-except covers the whole walk —
    if any step fails (no UNO ctx in tests, missing config node, etc.)
    we log the full traceback and continue. See the module-level note
    on diagnostic helpers.
    """
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

    The URL is of the form ``file:///path/to/file``. If the file is
    missing we raise :class:`FileNotFoundError` — the caller will log
    the full traceback via ``logger.exception`` before re-raising,
    surfacing the actionable root cause instead of letting the C++
    XML parser crash on an empty input deep inside
    ``createContainerWindow``.
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


# Must match the identifier in extension/description.xml — looked up
# at runtime via the deployment singleton to find the extension's
# install path on disk.
_EXTENSION_ID = "com.talk2view.writer"
_XDL_PATH = "panels/chat_panel.xdl"


class UnsupportedLibreOfficeBuildError(RuntimeError):
    """The running LibreOffice's PyUNO bridge can't host this panel.

    Raised when ``ContainerWindowProvider.createContainerWindow``
    fails on the strict-PyUNO XWindowPeer rejection (see ADR-0027 /
    investigation #29). The exception ``args[0]`` is the
    user-facing message; the underlying UNO exception is the
    ``__cause__``.
    """


def _is_strict_pyuno_xwindowpeer_failure(exc: BaseException) -> bool:
    """Detect the ADR-0027 strict-PyUNO XWindowPeer rejection.

    The known failure mode is a UNO ``CannotConvertException`` (or
    ``IllegalArgumentException`` on some builds) thrown out of
    ``createContainerWindow`` because the bridge can't marshal the
    bare ``XWindow`` ParentWindow at the ``XWindowPeer`` slot. We
    fingerprint by class name (avoids importing the UNO exception
    types at module load — they aren't available in stub-only test
    environments) and a tolerant substring check on the message.
    """
    cls = type(exc).__name__
    if cls not in {"CannotConvertException", "IllegalArgumentException"}:
        return False
    msg = (str(exc) or "").lower()
    return "xwindowpeer" in msg or "windowpeer" in msg


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
        "build_chat_panel: enter resource_url=%s frame=%s parent_window=%s",
        resource_url,
        "present" if frame is not None else "None",
        _ru(parent_window),
    )
    _log_platform_info(ctx)
    panel = Talk2ViewPanel(ctx, frame, parent_window, resource_url)

    from talk2view_writer.extension import get_extension

    get_extension(ctx).register_panel(panel)
    logger.info("build_chat_panel: returning XUIElement %s", _ru(panel))
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
        logger.info(
            "Talk2ViewToolPanel constructed panel_window=%s", _ru(panel_window)
        )

    def createAccessible(self, parent_accessible: object) -> Any:  # noqa: N802
        """XToolPanel: return our panel window as its own accessible root."""
        logger.info("Talk2ViewToolPanel.createAccessible: returning PanelWindow")
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
        logger.info(
            "getRealInterface: enter already_built=%s",
            self._tool_panel is not None,
        )
        if self._tool_panel is not None:
            return self._tool_panel

        try:
            window = self._create_panel_window()
        except UnsupportedLibreOfficeBuildError as exc:
            logger.exception(
                "getRealInterface: unsupported LibreOffice build — "
                "showing message and re-raising"
            )
            self._show_message(
                "Talk2View — unsupported LibreOffice build", str(exc)
            )
            raise
        except Exception:
            logger.exception(
                "getRealInterface: _create_panel_window raised — "
                "showing message and re-raising"
            )
            self._show_message(
                "Talk2View — panel build failed",
                "Panel construction raised an exception. See talk2view.log "
                "for the full traceback.",
            )
            raise

        try:
            self._bind_controls(window)
        except Exception:
            logger.exception(
                "getRealInterface: _bind_controls raised — re-raising"
            )
            raise

        try:
            self._apply_auth_state()
        except Exception:
            logger.exception(
                "getRealInterface: _apply_auth_state raised — re-raising"
            )
            raise

        self._tool_panel = Talk2ViewToolPanel(window, self.ctx)
        _log_window_state("final_tool_panel.window", window)
        logger.info("getRealInterface: Talk2View panel window created and bound")
        return self._tool_panel

    def setSettings(self, settings: object) -> None:  # noqa: N802
        """XUIElement: no-op — tool panels don't carry settings."""

    def getSettings(self, write: bool) -> object | None:  # noqa: N802
        """XUIElement: no-op — tool panels don't carry settings."""
        return None

    # ----- Window construction --------------------------------------------

    def _create_panel_window(self) -> Any:
        """Load chat_panel.xdl via ContainerWindowProvider.

        Canonical Python sidebar-panel pattern, matching
        odk/examples/python/toolpanel/toolpanel.py in the
        LibreOffice SDK. Three calls:

          1. Resolve the extension's install location via the
             PackageInformationProvider singleton (so the dialog
             URL is portable across user-profile / shared / bundled
             install modes).
          2. Instantiate the
             com.sun.star.awt.ContainerWindowProvider service.
          3. Call createContainerWindow(URL, "", ParentWindow,
             EventHandler=None) with the bare ParentWindow
             XWindow from the sidebar framework's
             createUIElement arguments. The container-window
             provider's C++ implementation does its own
             UNO_QUERY to obtain an XWindowPeer from the
             underlying VCL window.

        Raises ``UnsupportedLibreOfficeBuildError`` on the known
        strict-PyUNO failure mode (see ADR-0027 / investigation #29)
        with an actionable message; any other UNO exception
        propagates verbatim so the rotating log captures the trace.
        """
        logger.info(
            "_create_panel_window: enter sys.platform=%s sys.version=%s",
            sys.platform,
            sys.version.split()[0],
        )
        logger.info("_create_panel_window: resolving PIP singleton")
        pip = self.ctx.getValueByName(
            "/singletons/com.sun.star.deployment.PackageInformationProvider"
        )
        logger.info("_create_panel_window: PIP %s", _ru(pip))

        extension_root = pip.getPackageLocation(_EXTENSION_ID)
        dialog_url = f"{extension_root}/{_XDL_PATH}"
        logger.info(
            "_create_panel_window: extension_root=%s dialog_url=%s",
            extension_root,
            dialog_url,
        )

        # Verify the XDL file is actually on disk. If it isn't, the
        # underlying createContainerWindow would crash in the C++ XML
        # parser; surface a clean Python error here instead.
        _assert_dialog_file_exists(dialog_url)

        logger.info("_create_panel_window: creating ContainerWindowProvider")
        provider = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.awt.ContainerWindowProvider", self.ctx
        )
        logger.info("_create_panel_window: provider %s", _ru(provider))

        _log_window_state("parent_window", self._parent_window)

        # Flush before the native call — soffice has crashed inside
        # createContainerWindow on Debian without leaving the most
        # recent log lines on disk. We want every diagnostic above to
        # survive the segfault.
        logger.info(
            "_create_panel_window: flushing logs before createContainerWindow"
        )
        flush_logs()
        logger.info("_create_panel_window: calling createContainerWindow")
        try:
            window = provider.createContainerWindow(
                dialog_url, "", self._parent_window, None
            )
        except Exception as exc:
            logger.exception(
                "_create_panel_window: createContainerWindow raised %s",
                type(exc).__name__,
            )
            if _is_strict_pyuno_xwindowpeer_failure(exc):
                raise UnsupportedLibreOfficeBuildError(
                    "This LibreOffice build has a known incompatibility with "
                    "the canonical Python sidebar pattern (the PyUNO bridge "
                    "rejects the sidebar's ParentWindow at the XWindowPeer "
                    "slot of ContainerWindowProvider.createContainerWindow). "
                    "Please install LibreOffice from documentfoundation.org, "
                    "Flathub, or the Snap Store — those builds ship a stock "
                    "PyUNO bridge that works."
                ) from exc
            raise
        logger.info(
            "_create_panel_window: createContainerWindow returned %s", _ru(window)
        )
        flush_logs()
        _log_window_state("returned_window", window)
        self._panel_window = window
        return window

    def _bind_controls(self, window: Any) -> None:
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
            control = window.getControl(name)
            setattr(self, f"_{name}", control)
            _log_control("bind", name, control)

        logger.info(
            "_bind_controls: walking window children for full state dump"
        )
        _log_window_state("post_bind_window", window)

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
        if self._tool_panel is not None:
            # Only update widgets once the panel has actually been built.
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
        logger.info("_show_message: title=%r message=%r", title, message)
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
