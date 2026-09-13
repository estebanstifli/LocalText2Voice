import json
import os
from copy import deepcopy
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit

from app.core.storyboard_analysis_settings import normalize, request_continuity, snapshot, CONTRACT
from app.core.video_storyboard_planner import _continuity_analyzer_schema, _plan_video_storyboard_legacy as plan_video_storyboard
from app.ui.storyboard_analysis_settings import StoryboardAnalysisSettingsWidget


def test_simple_calls_have_separate_schemas_and_context():
    calls = []
    def request(settings, schema, system, user, **kwargs):
        calls.append((schema, system, json.loads(user), kwargs))
        if schema is None:
            return "No places."
        return {k: [] for k in schema["properties"]}
    ledger = {"characters": [{"id": "char_ana", "name": "Ana", "identity_description": "brown eyes"}],
              "locations": [{"id": "loc_house", "name": "house", "identity_description": "white house"}]}
    request_continuity(request, {"continuity_analysis": {"process": "simple"}}, _continuity_analyzer_schema(), "", "",
                       context=([{"id": 0, "text": "Ana arrives."}], ledger, "Ana is a person.", ""), request_label="block 1")
    assert [set(c[0]["properties"]) if c[0] else None for c in calls] == [{"entities", "groups"}, None, {"entities"}, {"era_events"}]
    assert "char_ana" not in json.dumps(calls[1][2])
    assert "loc_house" not in json.dumps(calls[0][2])
    assert "known_id" in calls[0][1] and "plain-text" in calls[1][1]


def test_provided_era_skips_separate_request_and_custom_instruction_is_used():
    calls = []
    def request(settings, schema, system, user, **kwargs):
        calls.append(system)
        if schema is None:
            return "No places."
        return {k: [] for k in schema["properties"]}
    settings = {"continuity_analysis": {"process": "simple", "era_mode": "provided",
                                        "prompts": {"locations": "My location instructions"}}}
    request_continuity(request, settings, _continuity_analyzer_schema(), "", "",
                       context=([], {}, "", ""), request_label="test")
    assert len(calls) == 3 and calls[1].startswith("My location instructions\n")


def test_snapshot_is_independent_and_excludes_credentials():
    settings = {"continuity_analysis": {"process": "simple"}, "llm_provider": "litellm",
                "litellm": {"model": "test", "api_key": "secret", "max_output_tokens": 5000}}
    saved = snapshot(settings)
    settings["continuity_analysis"]["process"] = "compact"
    assert saved["process"] == "simple" and "secret" not in json.dumps(saved)
    assert saved["protected_contract"] == CONTRACT and "characters" in saved["effective_prompts"]


def test_simple_pipeline_saves_configuration_in_partial_and_final_results():
    calls = []
    def request(settings, schema, system, user, **kwargs):
        calls.append(kwargs["request_label"])
        if schema is None:
            if kwargs["request_label"].startswith("continuity discovery"):
                assert user == "The river flows."
                assert "timestamp" not in system.lower()
                assert "unit IDs" not in system
            return "No characters."
        keys = set(schema["properties"])
        if keys == {"unit_bindings"}:
            return {"unit_bindings": [{"unit_id": 0, "character_ids": [], "location_ids": [], "era_id": ""}]}
        if keys == {"scenes"}:
            return {"scenes": [{"character_state_ids": [], "location_state_ids": [], "era_state_id": "",
                               "visual": "A river flows between grassy banks beneath the clear sky, with small ripples moving around smooth stones and reeds standing near the water in the foreground."}]}
        return {k: [] for k in keys}
    settings = {"llm_provider": "ollama", "ollama": {"model": "test"},
                "continuity_analysis": {"process": "simple", "block_size": "small", "era_mode": "provided"}}
    before = deepcopy(settings)
    partial = []
    with patch("app.core.video_storyboard_planner._request_plan", side_effect=request):
        result = plan_video_storyboard({"text": "The river flows.", "duration_seconds": 5}, settings, partial=partial.append)
    assert settings == before
    assert any("/ character_registry" in c for c in calls) and any("/ locations" in c for c in calls)
    assert not any("/ character_appearance" in c for c in calls)  # No identities: no design request.
    assert not any(c.endswith("/ era") for c in calls)
    assert all(p["continuity"]["analysis_configuration"]["process"] == "simple" for p in partial)
    assert result["continuity"]["analysis_configuration"]["limits"]["max_block_characters"] == 2000


def test_settings_widget_roundtrip_and_editor_cancel_save():
    app = QApplication.instance() or QApplication([])
    widget = StoryboardAnalysisSettingsWidget(lambda key, default, **values: default.format(**values))
    config = normalize({"process": "conversational", "block_size": "custom", "custom_characters": 7000,
                        "prompts": {"conversation_report": "Original custom text"}})
    widget.set_configuration(config)
    assert widget.configuration() == config and widget.characters.isEnabled()

    def edit_and_return(dialog, result):
        dialog.findChild(QPlainTextEdit, "continuityInstructionEditor").setPlainText("Changed custom text")
        return result
    with patch.object(QDialog, "exec", lambda d: edit_and_return(d, QDialog.DialogCode.Rejected)):
        widget._edit()
    assert widget.configuration() == config
    with patch.object(QDialog, "exec", lambda d: edit_and_return(d, QDialog.DialogCode.Accepted)):
        widget._edit()
    assert widget.configuration()["prompts"]["conversation_report"] == "Changed custom text"
    widget.deleteLater()
