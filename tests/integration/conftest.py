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
from collections.abc import Iterator
from typing import Any

import pytest

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 2002


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
    try:
        import uno  # type: ignore[import-not-found]
    except ImportError as exc:
        pytest.skip(f"PyUNO not importable: {exc}. Is LibreOffice installed?")

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

    The .oxt registers two UNO services:

      - ``com.talk2view.writer.Talk2ViewJob``      (XJobExecutor)
      - ``com.talk2view.writer.ChatPanelFactory``  (XUIElementFactory)

    If either fails to instantiate, the install failed — fail fast
    with a clear message that tells the operator to re-run
    ``unopkg add --force``. This is the canary the rest of the suite
    relies on.
    """
    service_mgr = uno_context.ServiceManager
    expected = (
        "com.talk2view.writer.Talk2ViewJob",
        "com.talk2view.writer.ChatPanelFactory",
    )
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
        # Best-effort close. If a test left the doc in a bad state,
        # swallowing the close error avoids masking the real assertion
        # failure with a teardown exception.
        with contextlib.suppress(Exception):
            doc.close(False)
