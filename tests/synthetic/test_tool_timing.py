"""Every tool call emits a greppable ``timing op=tool`` line (task #12).

The ``@ui_thread_tool`` wrapper is the local half of a tool call — it
wraps the UNO marshal + (for mutating tools) the track-changes
envelope. Its timing line is what tells us, after a slow run, whether
wall-clock went to the engine or to the local document operation. These
run against the synthetic doc so the real tool body executes.
"""

from __future__ import annotations

import json
import logging

import pytest

from tests.synthetic.synthetic_uno import FakeParagraph, FakeTextDocument

pytestmark = pytest.mark.synthetic

_LOG = "talk2view_writer.tools._base"


def _attach(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger(_LOG)
    log.addHandler(caplog.handler)
    log.setLevel(logging.INFO)


def test_read_tool_emits_tool_timing(
    patched_extension: object,
    synthetic_doc: FakeTextDocument,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from talk2view_writer.tools.reading import get_selection

    _attach(caplog)
    get_selection()
    assert "timing op=tool" in caplog.text
    assert "name=get_selection" in caplog.text


def test_mutating_tool_timing_notes_track_changes(
    patched_extension: object,
    synthetic_doc: FakeTextDocument,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from talk2view_writer.tools.formatting import format_text

    synthetic_doc._text._paragraphs.append(FakeParagraph("time me"))
    _attach(caplog)
    json.loads(format_text(query="time me", bold=True))
    assert "timing op=tool" in caplog.text
    assert "name=format_text" in caplog.text
    # format_text is a mutating tool -> the track-changes flag is recorded.
    assert "track_changes=" in caplog.text
