import json
from pathlib import Path

from tools.test_character_discovery import narration_from_log


def test_log_fixture_extracts_only_original_text(tmp_path):
    path = tmp_path / 'requests.txt'
    text = ''
    for n, rows in enumerate([[{'unit': 0, 'time': '2-8s', 'text': 'Three pigs lived together.'}],
                              [{'unit': 1, 'time': '8-12s', 'text': 'Their mother wore blue.'}]], 1):
        user = 'Produce a compact continuity report\n\n' + json.dumps(rows) + '\nPrevious character report: ignored'
        text += f'REQUEST {n}: continuity discovery block {n}/2\n' + json.dumps({'messages': [{'role': 'user', 'content': user}]}) + '\n'
    path.write_text(text, encoding='utf-8')
    assert narration_from_log(path) == 'Three pigs lived together.\n\nTheir mother wore blue.'
