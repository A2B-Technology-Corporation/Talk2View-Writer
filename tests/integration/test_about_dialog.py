"""Integration: the About + License dialog models construct against real UNO.

The pure unit tests (tests/unit/test_about.py) cover content + wiring, but
can't validate the UNO service + property names (FixedHyperlink,
PushButtonType, multi-line read-only Edit, ...). Building the dialog models
on a live soffice catches those. The modal ``execute()`` can't run
headlessly, so we stop at model construction — the part most likely to break
on a UNO API mismatch.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.integration
def test_about_model_builds(uno_context: Any) -> None:
    from talk2view_writer.about import _build_about_model

    _smgr, model = _build_about_model(uno_context)
    names = set(model.getElementNames())
    assert {"title", "body", "more", "ok"} <= names, names
    assert any(n.startswith("link") for n in names), names


@pytest.mark.integration
def test_license_model_builds(uno_context: Any) -> None:
    from talk2view_writer.about import _build_license_model

    _smgr, model = _build_license_model(
        uno_context, "Mozilla Public License Version 2.0\n"
    )
    names = set(model.getElementNames())
    assert {"summary", "license", "ok"} <= names, names
