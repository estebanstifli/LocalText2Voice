from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from app.ui.video_storyboard_analysis_dialog import (
    VideoStoryboardAnalysisDialog,
)


def _translate(_key: str, default: str, **values: object) -> str:
    return default.format(**values)


def test_analysis_dialog_streams_progress_and_offers_both_running_actions(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    request_log = tmp_path / "analysis-requests.txt"
    request_log.write_text("request log", encoding="utf-8")
    dialog = VideoStoryboardAnalysisDialog(
        _translate,
        request_log_path=str(request_log),
    )
    cancelled: list[bool] = []
    dialog.cancelRequested.connect(lambda: cancelled.append(True))
    dialog.show()
    app.processEvents()
    dialog.set_progress(2, 4, "Planning block 2/4")
    dialog.append_trace({"kind": "thinking", "text": "Checking scene boundaries"})
    dialog.append_trace({"kind": "content", "text": '{"scenes": ['})
    dialog.append_trace(
        {"kind": "retry", "reason": "length", "label": "block 2/4"}
    )
    dialog.append_trace(
        {
            "kind": "warning",
            "message": "Possible character name was not added; analysis continued.",
        }
    )
    dialog.append_trace(
        {
            "kind": "request",
            "label": "block 2/4",
            "model": "qwen3:8b",
            "scene_count": 7,
            "output_tokens": 5400,
            "prompt": "FIXED SCENE ASSIGNMENTS: police arrival",
            "provider": "ollama",
            "endpoint": "http://127.0.0.1:11434/api/chat",
            "attempt": 1,
            "raw_request": {
                "model": "qwen3:8b",
                "think": False,
                "messages": [
                    {"role": "system", "content": "Return JSON."},
                    {
                        "role": "user",
                        "content": "FIXED SCENE ASSIGNMENTS: police arrival",
                    },
                ],
                "format": {"type": "object"},
            },
        }
    )
    dialog.append_trace(
        {
            "kind": "error",
            "message": "OpenAI rejected the response format",
            "raw_error": {
                "type": "BadRequestError",
                "status_code": 400,
            },
        }
    )

    assert dialog.close_continue_button.isEnabled()
    assert dialog.cancel_button.isEnabled()
    assert dialog.progress_bar.value() == 25
    assert dialog.windowTitle() == "Analyzing audiobook"
    assert dialog.tabs.count() == 3
    assert dialog.tabs.widget(0) is dialog.response_view
    assert dialog.tabs.currentWidget() is dialog.response_view
    assert not hasattr(dialog, "reasoning_view")
    assert '{"scenes": [' in dialog.response_view.toPlainText()
    assert "qwen3:8b" in dialog.activity_view.toPlainText()
    assert "Retrying automatically" in dialog.activity_view.toPlainText()
    assert "Warning: Possible character" in dialog.activity_view.toPlainText()
    assert "OpenAI rejected" in dialog.activity_view.toPlainText()
    assert "BadRequestError" in dialog.response_view.toPlainText()
    assert "police arrival" in dialog.request_view.toPlainText()
    assert '"messages"' in dialog.request_view.toPlainText()
    assert '"think": false' in dialog.request_view.toPlainText()
    assert "POST http://127.0.0.1:11434/api/chat" in (
        dialog.request_view.toPlainText()
    )
    assert str(request_log) in dialog.request_log_label.text()
    assert dialog.open_request_log_button.isVisible()
    assert "response" in dialog.open_request_log_button.text().lower()

    dialog.close_continue_button.click()
    assert not dialog.isVisible()
    dialog.show()
    dialog.cancel_button.click()
    assert cancelled == [True]
    assert not dialog.cancel_button.isEnabled()
    dialog.set_finished(False, "Cancelled")
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_analysis_summary_counts_states_once_and_recommends_recurring_characters():
    app = QApplication.instance() or QApplication([])
    dialog = VideoStoryboardAnalysisDialog(_translate, request_log_path="analysis.json")
    plan = {"continuity": {"characters": [
        {"id": "ana", "name": "Ana", "aliases": ["Anita"], "states": [{"id": "ana_young"}, {"id": "ana_old"}]},
        {"id": "bob", "name": "Bob", "states": []},
        {"id": "unused", "name": "Unseen", "states": []},
    ], "locations": [{"id": "town"}], "objects": [{"id": "key"}]},
        "scenes": [{"characters": ["ana_young", "ana_old", "Ana"]},
                   {"characters": ["Anita", "bob"]}]}
    dialog.append_trace({"kind": "status", "message": "conversation: convert characters summary to JSON"})
    assert dialog.sidebar.cards["characters"][0].text() == "Validating structured results"
    dialog.update_plan(plan)
    assert dialog.sidebar.cards["characters"][1].text() == "3 found"
    assert dialog.tabs.count() == 3
    assert dialog.recommendation_label.isHidden()
    dialog.update_plan(plan, final=True)
    dialog.set_finished(True, "Done")
    assert dialog.tabs.currentWidget() is dialog.response_view
    assert dialog.character_summary.rowCount() == 3
    assert [(dialog.character_summary.item(i, 0).text(), dialog.character_summary.item(i, 1).text())
            for i in range(3)] == [("Ana", "2"), ("Bob", "1"), ("Unseen", "0")]
    assert "first: Ana." in dialog.recommendation_label.text()
    assert "Bob" not in dialog.recommendation_label.text()
    assert dialog.sidebar.cards["scenes"][1].text() == "2 created"
    assert dialog.sidebar.cards["objects"][0].text() == "Not requested"
    dialog.update_plan(plan, final=True)
    assert dialog.tabs.count() == 6
    dialog.close()
    app.processEvents()


def test_analysis_failure_stops_active_stage_without_reference_recommendation():
    app = QApplication.instance() or QApplication([])
    dialog = VideoStoryboardAnalysisDialog(_translate, request_log_path="analysis.json")
    dialog.append_trace({"kind": "request", "label": "conversation: place summary"})
    dialog.set_finished(False, "Connection failed")
    assert dialog.sidebar.cards["locations"][0].text() == "Analysis stopped here"
    assert dialog.recommendation_label.isHidden()
    assert dialog.tabs.count() == 3
    dialog.close()
    app.processEvents()


def test_empty_discovery_does_not_claim_zero_found_and_active_style_matches_theme():
    from app.ui.theme import theme_palette
    app = QApplication.instance() or QApplication([])
    dialog = VideoStoryboardAnalysisDialog(_translate, request_log_path="analysis.json")
    styles = []
    for theme in ("light", "dark"):
        dialog.sidebar.setPalette(theme_palette(theme))
        dialog.append_trace({"kind": "request", "label": "characters and summary"})
        styles.append(dialog.sidebar.cards["characters"][0].styleSheet())
        dialog.update_plan({"continuity": {"characters": [], "locations": []}, "scenes": []})
        for key in ("characters", "locations", "scenes"):
            assert dialog.sidebar.cards[key][1].text() == "Pending structured results"
        assert dialog.sidebar.cards["objects"][1].text() == ""
    assert styles[0] != styles[1] and all("font-weight: bold" in style for style in styles)
    dialog.set_finished(False, "Cancelled")
    assert dialog.sidebar.cards["characters"][0].styleSheet() == ""
    dialog.close()


def test_cards_entity_choices_and_settings_scene_limit_are_sent_to_worker():
    app = QApplication.instance() or QApplication([])
    dialog = VideoStoryboardAnalysisDialog(_translate, maximum_scene_seconds=27)
    assert dialog.maximum_scene_spin.value() == 27
    assert not hasattr(dialog, "content_plan_combo")
    dialog.plan_buttons["scenes"].click()
    assert not dialog.entity_checks["characters"].isChecked()
    assert not dialog.entity_checks["locations"].isChecked()
    dialog.plan_buttons["basic"].click()
    dialog.entity_checks["characters"].setChecked(False)
    assert dialog.entity_checks["locations"].isChecked()
    assert dialog.entity_checks["objects"].isEnabled()
    dialog.maximum_scene_spin.setValue(15)
    results = []
    dialog.startRequested.connect(results.append)
    dialog.continue_button.click()
    assert results[0]["maximum_scene_seconds"] == 15
    assert results[0]["analysis_choices"] == {"plan": "basic", "review": False, "characters": False, "locations": True, "objects": False, "era_mode": "manual"}
    dialog.set_finished(False, "Done")
    dialog.close()


def test_analysis_dialog_confirms_model_and_per_run_limits_before_start() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = VideoStoryboardAnalysisDialog(
        _translate,
        provider="LiteLLM",
        model="openai/gpt-5.5",
        max_input_characters=12000,
        max_output_tokens=32000,
        replaces_existing=True,
    )
    starts: list[object] = []
    dialog.startRequested.connect(starts.append)
    dialog.show()
    app.processEvents()

    assert dialog.setup_group.isVisible()
    assert not dialog.tabs.isVisible()
    setup_text = " ".join(
        label.text() for label in dialog.setup_group.findChildren(QLabel)
    )
    assert "AI model:" not in setup_text
    assert "Analysis process:" not in setup_text
    assert "openai/gpt-5.5" not in setup_text
    dialog.era_edit.setText("1890, Victorian era")
    assert "replace" in setup_text.lower()
    assert dialog.max_input_characters_spin.value() == 12000
    assert dialog.max_output_tokens_spin.value() == 32000

    dialog.continue_button.click()
    app.processEvents()

    assert starts == [
        {"max_input_characters": 12000, "max_output_tokens": 32000,
         "analysis_choices": {"plan": "full", "review": False, "characters": True, "locations": True, "objects": False, "era_mode": "manual"},
         "maximum_scene_seconds": 8, "audiobook_era": "1890, Victorian era", "generate_character_references": False, "resume_review": False}
    ]
    assert not dialog.setup_group.isVisible()
    assert dialog.tabs.isVisible()
    assert dialog.cancel_button.isVisible()
    dialog.set_finished(True, "Done")
    dialog.close()
    dialog.deleteLater()
    app.processEvents()
