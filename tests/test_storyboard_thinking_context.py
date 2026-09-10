from unittest.mock import patch
import json
from app.core.video_storyboard_planner import _request_plan
from app.core.storyboard_analysis_settings import story_synopsis


def test_synopsis_extraction_keeps_original_report_independent():
    report = '**Protagonists:** Three pigs.\n\n**Summary:**\nA wolf threatens their houses.'
    assert story_synopsis(report) == 'A wolf threatens their houses.'
    assert story_synopsis('A plain answer without headings.') == 'A plain answer without headings.'


def test_discovery_is_a_single_conversational_message_with_thinking():
    settings = {"llm_provider": "ollama", "ollama": {"model": "qwen3:8b"}}
    with patch("app.core.video_storyboard_planner._http_json", return_value={"message": {"content": "A short synopsis."}}) as http:
        result = _request_plan(settings, None, "Which characters appear?", "The original story.",
                               request_label="continuity discovery block 1/1")
    payload = http.call_args.args[1]
    assert payload["think"] is True
    assert payload["messages"] == [{"role": "user", "content": "Which characters appear?\n\nThe original story."}]
    assert "format" not in payload
    assert result == "A short synopsis."


def test_context_is_injected_without_changing_structured_user_payload():
    settings = {"llm_provider": "ollama", "ollama": {"model": "qwen3:8b"}, "story_context": "A wolf threatens three pigs."}
    schema = {"type": "object", "properties": {"era_events": {"type": "array"}}, "required": ["era_events"]}
    user = '{"narration_units": []}'
    with patch("app.core.video_storyboard_planner._http_json", return_value={"message": {"content": '{"era_events": []}'}}) as http:
        _request_plan(settings, schema, "Return era events.", user)
    payload = http.call_args.args[1]
    assert payload["think"] is True
    assert settings["story_context"] in payload["messages"][0]["content"]
    assert "not evidence" in payload["messages"][0]["content"]
    assert payload["messages"][1]["content"] == user


def test_context_is_also_sent_to_plain_text_later_stages():
    settings = {"llm_provider": "ollama", "ollama": {"model": "qwen3:8b"}, "story_context": "A wolf threatens three pigs."}
    with patch("app.core.video_storyboard_planner._http_json", return_value={"message": {"content": "Three houses."}}) as http:
        _request_plan(settings, None, "Describe the places.", "Narration", request_label="locations")
    assert settings["story_context"] in http.call_args.args[1]["messages"][0]["content"]
