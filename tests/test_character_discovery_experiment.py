import json
from pathlib import Path

from app.core.storyboard_analysis_settings import character_discovery_messages
from tools.test_character_discovery import narration_from_log


def test_default_is_conversational_question_and_short_summary():
    system, _ = character_discovery_messages('Story')
    assert system == "Which characters appear in this story? Also add a brief 2–3-line summary of what the story is about."


def test_plain_narration_is_preserved_without_metadata():
    text = 'Clara arrived in 1980.\n\nHer coat was blue. At 12:00 she left.'
    system, user = character_discovery_messages(text)
    assert user == text  # Preserve numbers that actually belong to the story.
    assert 'unit' not in system.lower()
    assert 'timestamp' not in system.lower()
    assert 'JSON' not in system
    assert 'summary' in system


def test_custom_instruction_is_preserved():
    assert character_discovery_messages('Story', {'prompts': {'characters': 'List characters.'}}) == ('List characters.', 'Story')


def test_log_fixture_extracts_only_original_text(tmp_path):
    path = tmp_path / 'requests.txt'
    text = ''
    for n, rows in enumerate([[{'unit': 0, 'time': '2-8s', 'text': 'Three pigs lived together.'}],
                              [{'unit': 1, 'time': '8-12s', 'text': 'Their mother wore blue.'}]], 1):
        user = 'Produce a compact continuity report\n\n' + json.dumps(rows) + '\nPrevious character report: ignored'
        text += f'REQUEST {n}: continuity discovery block {n}/2\n' + json.dumps({'messages': [{'role': 'user', 'content': user}]}) + '\n'
    path.write_text(text, encoding='utf-8')
    assert narration_from_log(path) == 'Three pigs lived together.\n\nTheir mother wore blue.'
