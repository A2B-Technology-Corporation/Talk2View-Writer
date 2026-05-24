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
    """
    for name in list(sys.modules):
        if name in ("uno", "unohelper") or name.startswith("com.sun.star."):
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
def uno_context() -> Any:
    """Resolve a remote UNO ``XComponentContext`` against the running soffice.

    Session-scoped because tearing down + re-establishing the bridge
    between tests would balloon runtime by ~10s per test.

    Skips the entire integration suite (with a clear message) if no
    soffice is listening — that's the expected behaviour when
    ``pytest -m integration`` is invoked outside CI without first
    starting headless soffice.
    """
    # Drop the unit-test conftest's UNO stubs first so ``import uno``
    # resolves to the real PyUNO package. See ``_evict_unit_uno_stubs``.
    _evict_unit_uno_stubs()

    try:
        import uno  # type: ignore[import-not-found]
    except ImportError as exc:
        pytest.skip(f"PyUNO not importable: {exc}. Is LibreOffice installed?")

    # Sanity: catch the case where ``uno`` is still the unit-test stub
    # rather than the real python3-uno package. The stub is a
    # ``ModuleType`` we constructed by hand; the real one ships with
    # LibreOffice and lives under ``/usr/lib/python3/dist-packages``
    # (or the platform equivalent). If the eviction didn't fire, fail
    # loudly — never silently mock.
    uno_file = getattr(uno, "__file__", None) or ""
    if "python3" not in uno_file and "site-packages" not in uno_file:
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
        pytest.skip(
            f"Cannot connect to headless soffice at {_connection_url()}: "
            f"{exc}.\nStart it with:\n"
            f"  soffice --headless "
            f'--accept="socket,host={_DEFAULT_HOST},port={_DEFAULT_PORT};urp;" &\n'
            f"then re-run pytest."
        )
    return remote_ctx


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
