"""Synthetic-UNO test fixtures.

These tests run the actual tool function bodies (``insert_content``,
``get_document``, ``search_document``, ...) against an in-process
:mod:`synthetic_uno` document model. There is no soffice / no UNO
bridge — the tools call ``ctx.ServiceManager.createInstanceWithContext``
with ``"com.sun.star.frame.Desktop"`` and our :class:`FakeServiceManager`
returns a :class:`FakeDesktop` whose ``getCurrentComponent()`` returns
the synthetic document.

The ``@ui_thread_tool`` decorator runs the body through
:meth:`UIThreadDispatcher.run_sync`. For these tests we substitute
an in-process executor (synchronous call) so assertions don't have
to dance around UNO's async callback service.

Each test gets a fresh document via the ``synthetic_doc`` fixture
so cases don't bleed state into each other.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.synthetic.synthetic_uno import FakeContext, FakeTextDocument


@pytest.fixture
def synthetic_doc() -> FakeTextDocument:
    """A fresh synthetic Writer document.

    Tests mutate this freely; the next test gets a new one. Override
    contents per-test by passing ``paragraphs=`` /  ``styles=`` to
    ``FakeTextDocument`` directly.
    """
    return FakeTextDocument(paragraphs=[""], styles=["Standard"])


@pytest.fixture
def synthetic_ctx(synthetic_doc: FakeTextDocument) -> FakeContext:
    """A :class:`FakeContext` whose service manager resolves to ``synthetic_doc``."""
    return FakeContext(synthetic_doc)


@pytest.fixture
def patched_extension(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_ctx: FakeContext,
    synthetic_doc: FakeTextDocument,
) -> Any:
    """Install a stub extension singleton wired to the synthetic doc.

    The stub's ``ctx`` points at the synthetic document and its
    ``ui_thread.run_sync`` is a synchronous executor (no threading).

    Returns the stub so individual tests can attach mocks (SDK calls,
    panel registrations) if needed. Most tool tests just rely on the
    fixture being active — the tool body calls
    ``get_extension_or_raise()`` and gets this stub back.
    """
    import talk2view_writer.extension as ext_mod

    class _StubExt:
        def __init__(self) -> None:
            self.ctx = synthetic_ctx
            self.ui_thread = types.SimpleNamespace(
                run_sync=lambda fn, *a, **kw: fn(*a, **kw)
            )
            self.sdk = MagicMock(name="sdk")

    stub = _StubExt()
    monkeypatch.setattr(ext_mod, "_INSTANCE", stub)
    return stub


@pytest.fixture(autouse=True)
def _talk2view_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a real ``talk2view`` (no test stubs).

    Ensures ``@tool`` decoration wraps each tool with the SDK's real
    schema introspector. The top-level ``tests/conftest.py`` only stubs
    UNO modules; the talk2view SDK is the real one from
    ``../Talk2View-Platform/packages/sdk-python``. Drop any stale stub
    that bled in from an earlier test fixture.
    """
    real_talk2view = sys.modules.get("talk2view")
    if real_talk2view is not None and not hasattr(real_talk2view, "Talk2View"):
        # A test stub. Re-import to get the real one.
        for mod in list(sys.modules):
            if mod.startswith("talk2view") and not mod.startswith("talk2view_writer"):
                monkeypatch.delitem(sys.modules, mod, raising=False)
