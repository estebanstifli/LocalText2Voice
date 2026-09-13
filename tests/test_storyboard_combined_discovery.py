import math

import pytest

from app.core.storyboard_combined_discovery import balanced_passages, split_report, new_character_text
from app.core.storyboard_analysis_settings import defaults, conversation_input_limit


@pytest.mark.parametrize("text", ["A short story.", "Hola.\n\n" * 6259, "x" * 50066, "Una frase. " * 5000], ids=["short", "paragraphs", "unbroken", "sentences"])
def test_balanced_blocks_preserve_text_and_respect_limit(text):
    parts = balanced_passages(text, 17000)
    assert "".join(parts) == text
    assert len(parts) == math.ceil(len(text) / 17000)
    assert all(0 < len(part) <= 17000 for part in parts)


def test_default_17000_and_paragraph_boundaries():
    assert conversation_input_limit(defaults()) == 17000
    text = "A paragraph about a girl walking beside a river.\n\n" * 1000
    parts = balanced_passages(text, 17000)
    assert len(parts) == 3
    assert all(part.endswith("\n\n") for part in parts)


def test_report_keeps_initial_appearance_separate_from_changes_and_summary():
    warnings = []
    result = split_report("**Characters (Name: Appearance)**\nAna: blue coat.\n\n**Changes**\nAna changes to red.\n\n**Summary**\nAna travels.", warnings.append)
    assert result == {"characters": "Ana: blue coat.", "changes": "Ana changes to red.", "summary": "Ana travels."}
    assert not warnings


def test_no_silent_loss_when_free_report_has_unexpected_format():
    warnings = []
    answer = "Ana is a girl in a blue coat. She travels."
    assert split_report(answer, warnings.append)["characters"] == answer
    assert warnings


def test_empty_additions_and_notes_are_not_character_profiles():
    assert new_character_text("None of the characters are new.") == ""
    assert new_character_text("No new characters.") == ""
    assert new_character_text("Luis: red cap.\n\n**Note:** unrelated explanation") == "Luis: red cap."
