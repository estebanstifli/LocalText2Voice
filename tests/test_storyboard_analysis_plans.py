import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
import json
import threading
from unittest.mock import patch
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from app.core.storyboard_analysis_review import choices, load_review, save_review, AnalysisReviewPaused
from app.ui.storyboard_review_dialog import StoryboardReviewDialog
from app.ui.video_storyboard_analysis_dialog import VideoStoryboardAnalysisDialog
from app.workers.video_storyboard_planner_worker import VideoStoryboardPlannerWorker

SOURCE = {"text": "A river flows between grassy banks.", "duration_seconds": 5}
VISUAL = "A river flows between grassy banks beneath the clear blue sky, with small ripples moving around smooth stones and reeds standing near the water in the foreground."


def fake_request(calls):
    def request(settings, schema, system, user, **kwargs):
        calls.append((kwargs.get("request_label"), system, user))
        if schema is None:
            return "A river with grassy banks. No recurring characters."
        keys = set(schema["properties"])
        if keys == {"scenes"}:
            return {"scenes": [{"visual": VISUAL, "character_state_ids": [], "location_state_ids": [], "era_state_id": ""}]}
        if keys == {"unit_bindings"}:
            return {"unit_bindings": [{"unit_id": 0, "character_ids": [], "location_ids": [], "era_id": ""}]}
        return {key: [] for key in keys}
    return request


def tr(key, default, **values):
    return default.format(**values)


def test_ui_choices_and_editable_review():
    app = QApplication.instance() or QApplication([])
    dialog = VideoStoryboardAnalysisDialog(tr, analysis_choices={"plan": "basic", "review": True})
    assert len(dialog.plan_buttons) == 3
    assert dialog.plan_buttons["basic"].isChecked() and dialog.review_checkbox.isChecked()
    draft = {"original": {"characters": "wrong"}, "edited": {"characters": "wrong"}}
    review = StoryboardReviewDialog(draft, tr)
    results = []
    review.submitted.connect(results.append)
    review.editors["characters"].setPlainText("corrected")
    review.submit("pause")
    assert results[0]["draft"]["edited"]["characters"] == "corrected"
    assert results[0]["draft"]["original"]["characters"] == "wrong"
    assert results[0]["draft"]["status"] == "pending"
    dialog.close()


def test_worker_review_waits_for_explicit_response_and_cancels():
    worker = VideoStoryboardPlannerWorker({}, {})
    result = []
    ready = threading.Event()
    worker.reviewRequested.connect(lambda draft: ready.set(), Qt.ConnectionType.DirectConnection)
    thread = threading.Thread(target=lambda: result.append(worker._review({"edited": {}})))
    thread.start()
    assert ready.wait(2)
    # The request has no provider work after it until submit_review wakes the event.
    worker.submit_review({"action": "continue", "draft": {"edited": {"directions": "ok"}}})
    thread.join(2)
    assert not thread.is_alive() and result[0]["edited"]["directions"] == "ok"


def test_worker_cancel_interrupts_review_wait():
    from app.core.video_storyboard_planner import VideoStoryboardPlanningError
    worker = VideoStoryboardPlannerWorker({}, {})
    ready = threading.Event()
    worker.reviewRequested.connect(lambda draft: ready.set(), Qt.ConnectionType.DirectConnection)
    errors = []
    def wait():
        try:
            worker._review({"edited": {}})
        except VideoStoryboardPlanningError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=wait)
    thread.start()
    assert ready.wait(2)
    worker.cancel()
    thread.join(2)
    assert not thread.is_alive() and errors
