"""Tests verifying the bundled skill catalog + system prompt invariants.

These tests guard against accidentally deleting / renaming skills or
losing the Writer Deltas section of the system prompt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"
SYSTEM_PROMPT = REPO_ROOT / "SYSTEM_PROMPT.md"


EXPECTED_SKILLS = {
    "comment-triage",
    "consistency-check",
    "content-extraction",
    "document-creation",
    "document-restructuring",
    "document-review",
    "formatting-standards",
    "guided-tour",
    "headers-footers-page-numbers",
    "page-layout-setup",
    "pre-send-review",
    "rewrite-in-place",
    "table-editing",
    "template-filling",
}


@pytest.mark.unit
class TestSkillCatalog:
    def test_skills_directory_exists(self) -> None:
        assert SKILLS_DIR.is_dir(), f"{SKILLS_DIR} should exist"

    def test_all_word_skills_present(self) -> None:
        found = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
        missing = EXPECTED_SKILLS - found
        assert not missing, f"Missing skills: {sorted(missing)}"

    def test_no_extra_unknown_skills(self) -> None:
        found = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
        extra = found - EXPECTED_SKILLS
        assert not extra, (
            f"Unexpected skill directories: {sorted(extra)}. "
            "Add them to EXPECTED_SKILLS if they're intentional."
        )

    @pytest.mark.parametrize("skill", sorted(EXPECTED_SKILLS))
    def test_each_skill_has_skill_md(self, skill: str) -> None:
        path = SKILLS_DIR / skill / "SKILL.md"
        assert path.is_file(), f"{path} must exist"
        content = path.read_text(encoding="utf-8")
        assert content.strip(), f"{path} should not be empty"


@pytest.mark.unit
class TestSystemPrompt:
    def test_system_prompt_exists(self) -> None:
        assert SYSTEM_PROMPT.is_file()

    def test_mentions_libreoffice_writer_not_word(self) -> None:
        text = SYSTEM_PROMPT.read_text(encoding="utf-8")
        first_line = text.splitlines()[0]
        assert "LibreOffice Writer" in first_line
        assert "Microsoft Word" not in first_line

    def test_has_writer_deltas_section(self) -> None:
        text = SYSTEM_PROMPT.read_text(encoding="utf-8")
        assert "## Writer Deltas" in text, (
            "SYSTEM_PROMPT.md must include a Writer Deltas section "
            "documenting LibreOffice-specific behaviours."
        )

    def test_references_each_expected_skill(self) -> None:
        text = SYSTEM_PROMPT.read_text(encoding="utf-8")
        missing = [s for s in EXPECTED_SKILLS if s not in text]
        assert not missing, f"Skills not referenced in prompt: {missing}"


@pytest.mark.unit
class TestGuidedTourSkill:
    """The interactive demo skill must keep its load-bearing properties.

    It's the onboarding experience ("show me what you can do"), so it has
    to (a) trigger on demo/tour phrasing, (b) stay strictly one-step-at-a-
    time, and (c) put Track Changes acceptance front and centre.
    """

    SKILL = SKILLS_DIR / "guided-tour" / "SKILL.md"

    def _text(self) -> str:
        return self.SKILL.read_text(encoding="utf-8").lower()

    def test_description_covers_demo_triggers(self) -> None:
        # The description is what the engine surfaces via list_skills, so
        # it must contain the phrases users actually type to ask for a demo.
        text = self._text()
        for phrase in ("show me what you can do", "demo", "tour"):
            assert phrase in text, f"guided-tour must trigger on {phrase!r}"

    def test_is_interactive_one_step_at_a_time(self) -> None:
        text = self._text()
        assert "one step per turn" in text
        assert "next" in text  # the user-driven advance cue

    def test_demonstrates_track_changes_acceptance(self) -> None:
        text = self._text()
        assert "track changes" in text
        assert "accept all" in text

    def test_covers_the_core_feature_set(self) -> None:
        text = self._text()
        for tool in (
            "insert_content",
            "format_text",
            "manage_list",
            "insert_table",
            "search_document",
            "add_comment",
        ):
            assert tool in text, f"tour should demonstrate {tool}"
