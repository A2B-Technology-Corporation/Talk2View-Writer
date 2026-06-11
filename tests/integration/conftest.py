"""Integration-test fixtures: connect to a real headless LibreOffice.

This conftest is **completely separate** from the top-level
``tests/conftest.py``: that one stubs UNO so unit tests can run
without LibreOffice; this one assumes LibreOffice is installed and
already listening on a UNO socket. Pytest's per-directory conftest
precedence means importing ``uno`` from this file uses the real
module, not the stub.

Workflow expectations:

  1. CI starts ``soffice --headless --accept=socket:host=127.0.0.1,port=2002;urp;``
     in the background (Linux: under ``xvfb-run``; macOS/Windows:
     bare).
  2. CI runs ``scripts/wait_for_soffice.py`` to block until the
     port accepts a connection.
  3. CI runs ``pytest -m integration`` (this file's fixtures fire).
  4. CI tears soffice down at the end.

For local development the contract is the same — see
``tests/integration/README.md`` for the soffice invocation.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from typing import Any

import pytest

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 2002


def _is_uno_module_name(name: str) -> bool:
    """True for the module names the unit-test conftest stubs."""
    return name in ("uno", "unohelper") or name.startswith("com.sun.star.")


def _snapshot_uno_stubs() -> dict[str, Any]:
    """Capture the currently-installed UNO stub modules for later restore."""
    return {
        name: mod
        for name, mod in sys.modules.items()
        if _is_uno_module_name(name)
    }


def _restore_uno_stubs(saved: dict[str, Any]) -> None:
    """Put the unit-test UNO stubs back, dropping any real PyUNO modules.

    Reverses :func:`_evict_unit_uno_stubs`: removes whatever UNO-named
    modules are currently loaded (the real PyUNO bridge, if eviction led
    to a real ``import uno``) and reinstates the snapshotted stubs so the
    rest of the pytest session — unit / synthetic tests that lazily import
    production code — still resolves the stubs and doesn't blow up with
    ``ModuleNotFoundError``. See investigation #30.
    """
    for name in list(sys.modules):
        if _is_uno_module_name(name):
            del sys.modules[name]
    sys.modules.update(saved)


def _evict_unit_uno_stubs() -> None:
    """Drop the top-level conftest's UNO stubs from ``sys.modules``.

    The unit-test conftest installs stub modules in ``sys.modules`` for
    every UNO package the production code imports (``uno``, ``unohelper``,
    every ``com.sun.star.*``) so unit tests can run without LibreOffice.
    Those stubs persist for the whole pytest session — and a naive
    ``import uno`` from inside this file would return the stub instead
    of the real PyUNO bridge. ``uno.getComponentContext()`` then
    returns a ``MagicMock``, ``loadComponentFromURL`` returns a
    ``MagicMock``, and the smoke test's ``while enum.hasMoreElements():``
    loop hangs forever because ``MagicMock`` is always truthy.
    See investigation #28; pytest-timeout stack in PR #1 run
    26104536192 has the smoking gun.

    Called from the ``uno_context`` fixture (NOT at module import) so
    the eviction doesn't run when unit / synthetic / mock_chat tests
    are the only thing being collected — those rely on the stubs.

    IMPORTANT: snapshot via :func:`_snapshot_uno_stubs` first and restore
    via :func:`_restore_uno_stubs` on every exit path. A bare eviction
    that then skips (no soffice) leaves the stubs gone for the rest of the
    session, breaking every later unit test — investigation #30.
    """
    for name in list(sys.modules):
        if _is_uno_module_name(name):
            del sys.modules[name]


# ---------------------------------------------------------------------------
# Test ordering — fundamentals first, sidebar stress last
# ---------------------------------------------------------------------------
#
# Pytest collects tests in alphabetical filename order by default, which
# would run ``test_sidebar_dock.py`` (the dock-stress test) BEFORE
# ``test_smoke.py``. That's the wrong order: the dock test dispatches
# ``.uno:SidebarDeck`` against a Writer doc and even with paranoid
# teardown the deck's references can linger, slowing the next doc-load
# (investigation #27). Reorder so the simple "can soffice open a doc"
# checks run first — if that fails we already know something is wrong
# at the install/UNO level, not the panel layer.
#
# Sort key: ``test_smoke.py`` → 0, ``test_sidebar_dock.py`` → 1, anything
# else (live, dogtail, future) → 9. Stable within each bucket.


_FILE_ORDER = {
    "test_smoke.py": 0,
    "test_sidebar_dock.py": 1,
}


def pytest_collection_modifyitems(items: list[Any]) -> None:
    """Bucket integration test files so smoke runs before sidebar-dock."""
    items.sort(
        key=lambda it: _FILE_ORDER.get(it.path.name, 9)
    )


def _connection_url() -> str:
    """Build the URP connection URL from env or defaults."""
    host = os.environ.get("T2V_SOFFICE_HOST", _DEFAULT_HOST)
    port = os.environ.get("T2V_SOFFICE_PORT", str(_DEFAULT_PORT))
    return (
        f"uno:socket,host={host},port={port};urp;"
        "StarOffice.ComponentContext"
    )


@pytest.fixture(scope="session")
def uno_context() -> Iterator[Any]:
    """Resolve a remote UNO ``XComponentContext`` against the running soffice.

    Session-scoped because tearing down + re-establishing the bridge
    between tests would balloon runtime by ~10s per test.

    Skips the entire integration suite (with a clear message) if no
    soffice is listening — that's the expected behaviour when
    ``pytest -m integration`` is invoked outside CI without first
    starting headless soffice.

    Snapshots the unit-test UNO stubs before evicting them and restores
    them on EVERY exit path (skip, error, or normal teardown) so a mixed
    ``pytest`` session (``make test``) that skips integration doesn't
    leave the stubs gone and break every later unit test — investigation
    #30.
    """
    # Snapshot, then drop the unit-test conftest's UNO stubs so
    # ``import uno`` resolves to the real PyUNO package. See
    # ``_evict_unit_uno_stubs`` / ``_restore_uno_stubs``.
    saved_stubs = _snapshot_uno_stubs()
    _evict_unit_uno_stubs()

    try:
        import uno  # type: ignore[import-not-found]
    except ImportError as exc:
        _restore_uno_stubs(saved_stubs)
        pytest.skip(f"PyUNO not importable: {exc}. Is LibreOffice installed?")

    # Sanity: catch the case where ``uno`` is still the unit-test stub
    # rather than the real python3-uno package. The stub is a
    # ``ModuleType`` we constructed by hand; the real one ships with
    # LibreOffice and lives under ``/usr/lib/python3/dist-packages``
    # (or the platform equivalent). If the eviction didn't fire, fail
    # loudly — never silently mock.
    uno_file = getattr(uno, "__file__", None) or ""
    if "python3" not in uno_file and "site-packages" not in uno_file:
        _restore_uno_stubs(saved_stubs)
        raise RuntimeError(
            f"uno module is not the real PyUNO (__file__={uno_file!r}). "
            "The unit-test conftest's UNO stub leaked into the "
            "integration session. See investigation #28."
        )

    local_ctx = uno.getComponentContext()
    resolver = local_ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local_ctx
    )
    try:
        remote_ctx = resolver.resolve(_connection_url())
    except Exception as exc:
        _restore_uno_stubs(saved_stubs)
        pytest.skip(
            f"Cannot connect to headless soffice at {_connection_url()}: "
            f"{exc}.\nStart it with:\n"
            f"  soffice --headless "
            f'--accept="socket,host={_DEFAULT_HOST},port={_DEFAULT_PORT};urp;" &\n'
            f"then re-run pytest."
        )
    try:
        yield remote_ctx
    finally:
        # Restore the stubs at session teardown so any unit/synthetic
        # tests collected after the integration suite still see them.
        _restore_uno_stubs(saved_stubs)


@pytest.fixture(scope="session")
def desktop(uno_context: Any) -> Any:
    """Return the remote ``com.sun.star.frame.Desktop`` singleton."""
    return uno_context.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", uno_context
    )


@pytest.fixture(scope="session")
def oxt_installed(uno_context: Any) -> Any:
    """Assert the Talk2View-Writer .oxt is installed by querying its services.

    Post-ADR-0029, the .oxt registers one UNO component:

      - ``com.talk2view.writer.ProtocolHandler`` (XDispatchProvider +
        XDispatch) — handles menu commands via the
        ``vnd.com.talk2view.writer:`` URL scheme.

    (The previous ``ChatPanelFactory`` UIElementFactory for the
    sidebar deck was removed; the chat window now opens via a
    pywebview subprocess driven by the protocol handler. See
    ``extension/talk2view_writer.py``.)

    If the protocol handler fails to instantiate, the install failed
    — fail fast with a clear message that tells the operator to
    re-run ``unopkg add --force``. This is the canary the rest of
    the suite relies on.
    """
    service_mgr = uno_context.ServiceManager
    expected = ("com.talk2view.writer.ProtocolHandler",)
    missing = []
    for service in expected:
        instance = service_mgr.createInstanceWithContext(service, uno_context)
        if instance is None:
            missing.append(service)
    if missing:
        pytest.fail(
            f"Talk2View-Writer services not registered: {missing}. "
            "The .oxt is not installed (or not into this LibreOffice "
            "profile). Run:\n"
            "  bash scripts/install_oxt.sh dist/Talk2ViewWriter.oxt"
        )
    return service_mgr


@pytest.fixture
def blank_document(desktop: Any, oxt_installed: Any) -> Iterator[Any]:
    """Yield a freshly-opened, blank Writer document; close it on teardown.

    Use this for any test that mutates document state — the fresh
    document ensures isolation between tests.

    Teardown calls ``doc.close(False)`` and swallows any exception so
    a teardown failure doesn't mask the real test assertion.
    """
    from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]

    hidden = PropertyValue()
    hidden.Name = "Hidden"
    hidden.Value = True
    doc = desktop.loadComponentFromURL(
        "private:factory/swriter", "_blank", 0, (hidden,)
    )
    try:
        yield doc
    finally:
        # Best-effort close. Earlier attempts to be cleverer here
        # (explicit ``.uno:Sidebar`` dispatch, ``doc.close(True)``)
        # introduced hangs on hidden frames in CI — both calls can
        # block indefinitely and ``contextlib.suppress`` only catches
        # exceptions, not hangs. Keep teardown minimal; integration
        # failures from sidebar lifecycle issues should be diagnosed
        # via the per-test pytest-timeout traceback in pyproject.
        with contextlib.suppress(Exception):
            doc.close(False)


@pytest.fixture
def tool_doc(desktop: Any, uno_context: Any, monkeypatch: Any) -> Iterator[Any]:
    """A live Writer doc wired so the REAL ``@tool`` functions run on it.

    This is the harness for end-to-end tool tests: it lets a test call the
    actual tool (e.g. ``insert_content(text=...)``) and assert the resulting
    real-LibreOffice document state — the coverage gap that hid the
    commenting bugs (Investigations #38, #66).

    Wiring:
      * loads a fresh document — it becomes the desktop's current
        component, so the production ``get_writer_document(ctx)`` resolves
        to it (verified: a loaded doc is returned by ``getCurrentComponent``);
      * stubs the extension singleton with the live ``ctx`` and a
        SYNCHRONOUS ``ui_thread.run_sync`` so ``@ui_thread_tool`` runs the
        body inline on this thread (UNO is single-threaded here);
      * forces the AI-track-changes preference OFF so the mutating envelope
        is a plain undo-grouped call (track-changes has its own tests).

    Does NOT require the .oxt to be installed — it exercises pure tool +
    UNO behaviour independent of the extension package.
    """
    import types
    from unittest.mock import MagicMock

    import talk2view_writer.extension as ext_mod
    import talk2view_writer.preferences as prefs_mod

    doc = desktop.loadComponentFromURL(
        "private:factory/swriter", "_blank", 0, ()
    )

    stub = types.SimpleNamespace(
        ctx=uno_context,
        ui_thread=types.SimpleNamespace(run_sync=lambda fn, *a, **k: fn(*a, **k)),
        sdk=MagicMock(name="sdk"),
    )
    monkeypatch.setattr(ext_mod, "_INSTANCE", stub)
    monkeypatch.setattr(
        prefs_mod,
        "get_preferences",
        lambda: {prefs_mod.PREF_AI_TRACK_CHANGES: False},
    )

    # Pin get_writer_document to THIS doc everywhere it is used. The
    # production resolver goes through desktop.getCurrentComponent(), which
    # does NOT reliably return a freshly-loaded doc across environments (it
    # returned None on the ubuntu CI legs where soffice starts with --writer)
    # and can drift to another open doc between tests. Pinning it makes every
    # tool operate on — and every test assert against — the SAME document.
    # The tool body still runs all its real UNO logic; only the trivial doc
    # lookup is bypassed. Patch each tool module's local binding plus _base's
    # own (used by the mutating undo / track-changes envelope).
    import talk2view_writer.tools._base as base_mod

    def _resolve(_ctx: Any) -> Any:
        return doc

    monkeypatch.setattr(base_mod, "get_writer_document", _resolve)
    for _mod_name in (
        "writing",
        "formatting",
        "structure",
        "reading",
        "search",
        "commenting",
    ):
        _mod = __import__(
            f"talk2view_writer.tools.{_mod_name}",
            fromlist=["get_writer_document"],
        )
        if hasattr(_mod, "get_writer_document"):
            monkeypatch.setattr(_mod, "get_writer_document", _resolve)
    try:
        yield doc
    finally:
        with contextlib.suppress(Exception):
            doc.close(False)
