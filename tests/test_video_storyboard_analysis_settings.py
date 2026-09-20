import json
import os
from copy import deepcopy
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit

from app.core.storyboard_analysis_settings import normalize, snapshot, CONTRACT
from app.ui.storyboard_analysis_settings import StoryboardAnalysisSettingsWidget


def test_snapshot_is_independent_and_excludes_credentials():
    settings = {"continuity_analysis": {"process": "simple"}, "llm_provider": "litellm",
                "litellm": {"model": "test", "api_key": "secret", "max_output_tokens": 5000}}
    saved = snapshot(settings)
    settings["continuity_analysis"]["process"] = "compact"
    assert saved["process"] == "conversational" and "secret" not in json.dumps(saved)
    assert saved["protected_contract"] == CONTRACT and "conversation_eras" in saved["effective_prompts"]


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
