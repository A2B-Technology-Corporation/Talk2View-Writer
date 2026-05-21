"""Pytest fixtures + UNO module stubs.

Production code imports ``uno``, ``unohelper``, and several
``com.sun.star.*`` modules at module load time. These only exist inside
LibreOffice's bundled Python; running ``pytest`` under a regular system
or ``uv``-managed Python would otherwise fail with ``ModuleNotFoundError``
the first time a test imports e.g. ``talk2view_writer.ui_thread``.

We stub the modules here so the production code imports cleanly. Tests
that need to assert on UNO calls then construct their own
``MagicMock`` instances and pass them in (see ``test_ui_thread.py``).
The stubs deliberately expose **only** the symbols production code
references at import time — class bases, enum values, factory
functions. Anything beyond that should come from the test's own mock
setup, so the boundary between "stub for import" and "mock for assertion"
stays explicit.

If you add a new import from a UNO module in production code and a
test fails with ``AttributeError`` or ``ImportError`` from this stub
layer, add the missing symbol below — do not work around it by adding
``# noqa`` to the production import.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _make_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules.setdefault(name, mod)
    return mod


# ── Top-level UNO modules ────────────────────────────────────────────

_uno = _make_module("uno")
_uno.createUnoStruct = MagicMock(name="createUnoStruct")  # type: ignore[attr-defined]
_uno.Enum = MagicMock(name="Enum")  # type: ignore[attr-defined]
_uno.getComponentContext = MagicMock(name="getComponentContext")  # type: ignore[attr-defined]
# getTypeByName + invoke are used historically by sidebar_panel code
# (now removed per ADR-0029). Kept for the few legacy call sites that
# still need them; safe to remove once those clean up.
_uno.getTypeByName = MagicMock(name="getTypeByName")  # type: ignore[attr-defined]
_uno.invoke = MagicMock(name="invoke")  # type: ignore[attr-defined]


_unohelper = _make_module("unohelper")


class _UNOBase:
    """Stub for ``unohelper.Base`` — a permissive multiple-inheritance base.

    Must be a distinct class (not ``object``) so production code can do
    ``class X(unohelper.Base, X<Interface>):`` without the C3 linearization
    failing on duplicate ``object`` bases.
    """


_unohelper.Base = _UNOBase  # type: ignore[attr-defined]


class _UnoImplementationHelper:
    """Stub for ``unohelper.ImplementationHelper``."""

    def addImplementation(self, *args: object, **kwargs: object) -> None:  # noqa: N802 — UNO interface naming
        pass


_unohelper.ImplementationHelper = _UnoImplementationHelper  # type: ignore[attr-defined]


# ── com.sun.star package tree ────────────────────────────────────────

_make_module("com")
_make_module("com.sun")
_make_module("com.sun.star")


class _StubInterface:
    """Permissive base class standing in for any UNO interface.

    Production code uses these as the second/third base of
    ``unohelper.Base``-derived classes; subclassing must succeed and
    the class must accept arbitrary keyword args.
    """


# Each UNO module we import production-side gets the symbols it exposes.
def _stub_interface(name: str) -> type:
    """Build a distinct stub class for each UNO interface.

    Multiple interface bases can't share a single class because
    Python forbids duplicate bases in a class definition.
    """
    return type(name, (), {})


_awt = _make_module("com.sun.star.awt")
_awt.XCallback = _stub_interface("XCallback")  # type: ignore[attr-defined]
_awt.XActionListener = _stub_interface("XActionListener")  # type: ignore[attr-defined]
_awt.XWindowListener = _stub_interface("XWindowListener")  # type: ignore[attr-defined]

_awt_possize = _make_module("com.sun.star.awt.PosSize")
_awt_possize.POSSIZE = 15  # type: ignore[attr-defined]

_awt_windowclass = _make_module("com.sun.star.awt.WindowClass")
_awt_windowclass.SIMPLE = 0  # type: ignore[attr-defined]

_task = _make_module("com.sun.star.task")
_task.XJobExecutor = _stub_interface("XJobExecutor")  # type: ignore[attr-defined]

# ProtocolHandler interfaces used by extension/talk2view_writer.py.
_frame = _make_module("com.sun.star.frame")
_frame.XDispatch = _stub_interface("XDispatch")  # type: ignore[attr-defined]
_frame.XDispatchProvider = _stub_interface("XDispatchProvider")  # type: ignore[attr-defined]

_lang = _make_module("com.sun.star.lang")
_lang.XComponent = _stub_interface("XComponent")  # type: ignore[attr-defined]

_ui = _make_module("com.sun.star.ui")
_ui.XUIElement = _stub_interface("XUIElement")  # type: ignore[attr-defined]
_ui.XUIElementFactory = _stub_interface("XUIElementFactory")  # type: ignore[attr-defined]
_ui.XToolPanel = _stub_interface("XToolPanel")  # type: ignore[attr-defined]


class _UIElementType:
    TOOLPANEL = 7
    MENUBAR = 1
    POPUPMENU = 2
    TOOLBAR = 3
    STATUSBAR = 4
    FLOATINGWINDOW = 5
    PROGRESSBAR = 6


_ui.UIElementType = _UIElementType  # type: ignore[attr-defined]

_text = _make_module("com.sun.star.text")
_frame = _make_module("com.sun.star.frame")
_beans = _make_module("com.sun.star.beans")
_beans.PropertyValue = MagicMock(name="PropertyValue")  # type: ignore[attr-defined]
_uno_pkg = _make_module("com.sun.star.uno")
