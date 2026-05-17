"""GUI smoke test — drives the Talk2View sidebar via AT-SPI on Linux.

This is the only test that exercises the rendered UI layer (not the
underlying Python code). It catches problems that pure UNO tests
miss:

  - ``Sidebar.xcu`` malformed → deck doesn't register.
  - ``Talk2ViewPanelFactory`` raises during ``createUIElement`` →
    panel slot stays empty.
  - Widget positions/sizes wrong → composer / submit button
    unreachable to a user.

Linux only because:

  - dogtail wraps Linux's AT-SPI accessibility bridge.
  - macOS has no AT-SPI (Apple uses NSAccessibility).
  - Windows has UI Automation but pywinauto/Inspect.exe is a
    different code path entirely.

Strategy on macOS / Windows: trust that the UNO-driven tests in
test_smoke.py + the upstream LibreOffice rendering produces a
working UI; rely on manual QA + user reports for visual regressions.

Required-pass on Linux. If dogtail proves flaky in practice, drop to
``continue-on-error`` in the workflow and surface as warnings —
**don't** silently disable the test by changing the marker.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.gui_smoke,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="dogtail/AT-SPI is Linux-only; macOS/Windows GUI smoke deferred",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _have_xvfb_display() -> bool:
    """Return True if $DISPLAY is set and points at a working X server."""
    if not os.environ.get("DISPLAY"):
        return False
    try:
        subprocess.run(
            ["xdpyinfo"],
            check=True,
            capture_output=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True


@pytest.fixture(scope="module")
def writer_window() -> Iterator[object]:
    """Launch ``soffice --writer`` under the current X display, yield its accessible root.

    Module-scoped because launching Writer takes ~5s; sharing the
    window between tests within this file is fine. If a test mutates
    UI state in a way that affects the next test, split it into its
    own file.
    """
    if not _have_xvfb_display():
        pytest.skip(
            "No X display available. Run under `xvfb-run pytest -m gui_smoke` "
            "or start an Xvfb session and export $DISPLAY."
        )

    try:
        from dogtail import tree
        from dogtail.config import config as dogtail_config
        from dogtail.utils import run as dogtail_run
    except ImportError as exc:
        pytest.skip(
            f"dogtail not importable: {exc}. "
            "Install with: uv sync --group gui-smoke"
        )

    # AT-SPI conventions: be patient + don't auto-dump tree on every
    # search failure (the explicit retries below give better debug
    # output).
    dogtail_config.searchCutoffCount = 20
    dogtail_config.searchBackoffDuration = 0.5
    dogtail_config.defaultDelay = 0.2
    dogtail_config.logDebugToFile = False

    # Launch Writer and wait until the main window's accessible root
    # registers with AT-SPI. ``dogtail.utils.run`` returns the PID.
    dogtail_run("soffice --writer --norestore --nologo --nodefault")

    deadline = time.monotonic() + 30
    app = None
    while time.monotonic() < deadline:
        try:
            app = tree.root.application("soffice")
            break
        except Exception:
            time.sleep(0.5)
    if app is None:
        pytest.fail("LibreOffice Writer did not register with AT-SPI within 30s")

    try:
        yield app
    finally:
        # Best-effort teardown — let CI's job-cleanup kill any
        # leftover soffice processes if our close() hangs.
        subprocess.run(["pkill", "-f", "soffice.*--writer"], check=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.gui_smoke
def test_talk2view_sidebar_deck_exists(writer_window: object) -> None:
    """The Talk2View deck must appear in Writer's sidebar tab strip.

    Catches: ``extension/Sidebar.xcu`` not registered, deck context
    mismatch (e.g. registered for Calc instead of Writer), permissions
    on the .oxt install directory.
    """
    # The deck title we ship comes from Sidebar.xcu's
    # com.talk2view.writer.Deck Title property.
    deck_title = "Talk2View"

    # AT-SPI exposes sidebar tabs as ``radio button`` role under the
    # main Writer document window. Search by name.
    try:
        deck_tab = writer_window.child(name=deck_title, roleName="radio button")
    except Exception as exc:  # dogtail.tree.SearchError or similar
        # Dump the AT-SPI tree to stderr to make debugging in CI logs
        # actionable. The tree dump is big but only fires on failure.
        from dogtail import tree as _tree

        _tree.root.application("soffice").dump()
        pytest.fail(
            f"Sidebar deck '{deck_title}' not found in Writer window: {exc}. "
            "Check extension/Sidebar.xcu deck registration."
        )
    assert deck_tab is not None
    assert deck_tab.showing or deck_tab.visible, "Talk2View deck tab is hidden"


@pytest.mark.gui_smoke
def test_talk2view_panel_renders_when_deck_clicked(writer_window: object) -> None:
    """Clicking the Talk2View tab must reveal the chat panel with composer + history.

    Catches: ``ChatPanelFactory.createUIElement`` raises, programmatic
    widget construction throws, panel registers but renders empty.
    """
    deck_tab = writer_window.child(name="Talk2View", roleName="radio button")
    deck_tab.click()

    # Give the panel a moment to materialise.
    time.sleep(1.0)

    # The composer is built programmatically as a UnoControlEdit;
    # AT-SPI exposes it with role ``text``. The submit button is
    # ``push button`` labelled "Send".
    composer = _retry_find(writer_window, role_name="text", description_contains="composer")
    assert composer is not None, "Chat composer not found after clicking deck tab"

    send_button = writer_window.child(name="Send", roleName="push button")
    assert send_button is not None, "Send button not found"
    assert send_button.sensitive, "Send button is disabled (extension not initialised?)"


def _retry_find(
    root: object,
    *,
    role_name: str,
    description_contains: str | None = None,
    attempts: int = 10,
    interval: float = 0.5,
) -> object | None:
    """Repeatedly poll the AT-SPI tree for a matching child.

    dogtail's built-in ``searchCutoffCount`` retries on
    ``not-found`` but doesn't filter by ``description``, so this
    helper supplements it.
    """
    import contextlib

    for _ in range(attempts):
        with contextlib.suppress(Exception):
            for candidate in root.findChildren(lambda n: n.roleName == role_name):
                desc = getattr(candidate, "description", "") or ""
                if description_contains is None or description_contains in desc:
                    return candidate
        time.sleep(interval)
    return None


# Save the writer_window fixture for later, more elaborate tests:
# typing into the composer + hitting send + asserting that the
# busy-state spinner appears would be added here once the basic
# render is proven stable in CI. Skipping that for now to keep the
# first CI run minimal-surface.
_ = Path  # silence unused-import warning when Path becomes needed
