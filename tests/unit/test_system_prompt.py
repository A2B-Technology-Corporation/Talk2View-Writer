"""Tests for the system-prompt loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from talk2view_writer import system_prompt


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    """Ensure each test sees a fresh ``load_system_prompt`` result."""
    system_prompt.reset_cache()
    yield
    system_prompt.reset_cache()


@pytest.mark.unit
def test_finds_repo_root_prompt() -> None:
    """The bundled SYSTEM_PROMPT.md at the repo root must be discoverable."""
    text = system_prompt.load_system_prompt()
    assert text is not None, "SYSTEM_PROMPT.md should be present in the repo root"
    assert "LibreOffice Writer" in text, "Prompt must be the Writer-edited version"
    assert "Writer Deltas" in text, "Prompt must include the deltas section"


@pytest.mark.unit
def test_env_var_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``TALK2VIEW_WRITER_SYSTEM_PROMPT`` short-circuits the default search."""
    override = tmp_path / "custom-prompt.md"
    override.write_text("CUSTOM PROMPT CONTENT", encoding="utf-8")
    monkeypatch.setenv("TALK2VIEW_WRITER_SYSTEM_PROMPT", str(override))
    system_prompt.reset_cache()

    text = system_prompt.load_system_prompt()
    assert text == "CUSTOM PROMPT CONTENT"


@pytest.mark.unit
def test_returns_none_when_no_prompt_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing files yield ``None`` so the engine default applies."""
    fake_path = tmp_path / "does-not-exist.md"
    monkeypatch.setenv("TALK2VIEW_WRITER_SYSTEM_PROMPT", str(fake_path))
    monkeypatch.setattr(system_prompt, "_PACKAGE_DIR", tmp_path / "no-pkg")
    system_prompt.reset_cache()

    text = system_prompt.load_system_prompt()
    assert text is None
