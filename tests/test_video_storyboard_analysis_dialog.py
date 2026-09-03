from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

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
    assert not hasattr(dialog, "reasoning_view")
    assert '{"scenes": [' in dialog.response_view.toPlainText()
    assert "qwen3:8b" in dialog.activity_view.toPlainText()
    assert "Retrying automatically" in dialog.activity_view.toPlainText()
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
