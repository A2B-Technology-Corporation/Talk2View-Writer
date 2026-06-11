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


def _fake_uno_struct(_service_name: str) -> types.SimpleNamespace:
    """Return a FRESH attribute-bag per call, like real ``createUnoStruct``.

    A plain ``MagicMock`` would return one shared instance for every call, so
    code that builds N distinct ``PropertyValue`` structs (e.g.
    ``_build_numbering_rules``) would collapse to a single object and mask
    bugs. A new ``SimpleNamespace`` each call mirrors real PyUNO, where every
    struct is distinct.
    """
    return types.SimpleNamespace()


_uno.createUnoStruct = MagicMock(  # type: ignore[attr-defined]
    name="createUnoStruct", side_effect=_fake_uno_struct
)


class _FakeAny:
    """Stand-in for ``uno.Any`` — records the explicit UNO type + value.

    PyUNO requires ``uno.Any("[]com.sun.star.beans.PropertyValue", seq)`` when
    passing a typed sequence into an ``any`` parameter (e.g.
    ``XIndexReplace.replaceByIndex``): a bare Python tuple is marshalled as
    ``Sequence<Any>`` and the receiving C++ method's ``>>=`` extraction throws
    a message-less ``IllegalArgumentException``. Modelling the wrapper lets the
    synthetic ``FakeNumberingRules`` enforce that contract instead of silently
    accepting anything — the lenient-fake gap that let investigation #50's
    earlier fixes pass tests yet still crash on real soffice.
    """

    def __init__(self, type_name: str, value: object) -> None:
        self.typeName = type_name  # mirrors PyUNO's attribute name
        self.value = value
        #: Set True only when delivered through ``uno.invoke`` — lets the
        #: synthetic rig reject a positional ``uno.Any`` the way the real
        #: PyUNO bridge does (investigation #50).
        self.delivered_via_invoke = False


_uno.Any = _FakeAny  # type: ignore[attr-defined]
_uno.Enum = MagicMock(name="Enum")  # type: ignore[attr-defined]
_uno.getComponentContext = MagicMock(name="getComponentContext")  # type: ignore[attr-defined]
# getTypeByName + invoke are used historically by sidebar_panel code
# (now removed per ADR-0029). Kept for the few legacy call sites that
# still need them; safe to remove once those clean up.
_uno.getTypeByName = MagicMock(name="getTypeByName")  # type: ignore[attr-defined]


def _fake_uno_invoke(obj: object, method_name: str, arg_tuple: tuple) -> object:
    """Mirror ``uno.invoke``: call ``obj.method_name(*arg_tuple)``.

    Real PyUNO requires ``uno.invoke`` to pass an explicitly-typed
    ``uno.Any`` argument — a positional ``uno.Any`` is rejected at the bridge
    with "uno.Any instance not accepted during method call, use uno.invoke
    instead". The shim performs the call so synthetic UNO objects receive the
    args (the ``uno.Any`` wrapper included) exactly as production passes them,
    letting the fake enforce the typed-sequence contract (investigation #50).
    """
    for arg in arg_tuple:
        if isinstance(arg, _FakeAny):
            arg.delivered_via_invoke = True
    return getattr(obj, method_name)(*arg_tuple)


_uno.invoke = _fake_uno_invoke  # type: ignore[attr-defined]


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
# UNO throws this when an ``any`` argument can't be extracted as the expected
# type (e.g. NumberingRules.replaceByIndex given a Sequence<Any> instead of
# Sequence<PropertyValue>). A real Exception subclass so the synthetic UNO rig
# can model that strictness and production ``except`` clauses still work.
_lang.IllegalArgumentException = type(  # type: ignore[attr-defined]
    "IllegalArgumentException", (Exception,), {}
)

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
# Production code catches these when setting paragraph flow properties
# (formatting.py::format_paragraph). Expose real Exception subclasses so the
# narrow ``except (...)`` resolves against the stubs.
_beans.UnknownPropertyException = type(  # type: ignore[attr-defined]
    "UnknownPropertyException", (Exception,), {}
)
_beans.PropertyVetoException = type(  # type: ignore[attr-defined]
    "PropertyVetoException", (Exception,), {}
)
_uno_pkg = _make_module("com.sun.star.uno")
# Production code catches the UNO RuntimeException (e.g. writing.py's
# ParaStyleName-under-redline guard). Expose a real Exception subclass so
# ``from com.sun.star.uno import RuntimeException`` + ``except`` work in tests.
_uno_pkg.RuntimeException = type("RuntimeException", (Exception,), {})  # type: ignore[attr-defined]
