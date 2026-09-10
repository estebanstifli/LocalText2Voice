import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
import json
import threading
from unittest.mock import patch
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from app.core.storyboard_analysis_review import choices, load_review, save_review, AnalysisReviewPaused, apply_basic_policy, freeze_basic_profiles
# Legacy contract coverage; the public conversational route has its own suite.
from app.core.video_storyboard_planner import _plan_video_storyboard_legacy as plan_video_storyboard
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


def test_scenes_only_skips_all_continuity_calls():
    calls = []
    with patch("app.core.video_storyboard_planner._request_plan", side_effect=fake_request(calls)):
        result = plan_video_storyboard(SOURCE, {"analysis_choices": {"plan": "scenes"}})
    assert [c[0] for c in calls] == ["scene block 1/1"]
    assert result["scenes"] and result["continuity"]["characters"] == []
    assert result["continuity"]["analysis_configuration"]["content_plan"] == "scenes"


def test_review_prevents_next_phase_and_resumes_from_saved_draft(tmp_path):
    settings = {"analysis_choices": {"plan": "scenes", "review": True}}
    calls = []
    def pause(draft):
        save_review(tmp_path, {"draft": draft})
        raise AnalysisReviewPaused()
    with patch("app.core.video_storyboard_planner._request_plan", side_effect=fake_request(calls)):
        with pytest.raises(AnalysisReviewPaused):
            plan_video_storyboard(SOURCE, settings, review=pause)
    assert [c[0] for c in calls] == ["visual approach review"]
    saved = load_review(tmp_path)["draft"]
    saved["edited"]["directions"] = "Show the river at dawn."
    settings["review_checkpoint"] = saved
    calls.clear()
    with patch("app.core.video_storyboard_planner._request_plan", side_effect=fake_request(calls)):
        result = plan_video_storyboard(SOURCE, settings, review=lambda draft: draft)
    assert [c[0] for c in calls] == ["scene block 1/1"]
    assert "Show the river at dawn." in calls[0][2]
    assert result["continuity"]["analysis_review"]["original"] != saved["edited"]


def test_changed_source_invalidates_resume():
    captured = []
    settings = {"analysis_choices": {"plan": "scenes", "review": True}}
    calls = []
    def approve(draft):
        captured.append(draft)
        return draft
    with patch("app.core.video_storyboard_planner._request_plan", side_effect=fake_request(calls)):
        plan_video_storyboard(SOURCE, settings, review=approve)
        settings["review_checkpoint"] = captured[0]
        plan_video_storyboard({**SOURCE, "text": "A river flows at dawn."}, settings, review=approve)
    assert sum(c[0] == "visual approach review" for c in calls) == 2


def test_basic_omits_temporal_era_task_and_filters_changes():
    calls = []
    settings = {"analysis_choices": {"plan": "basic"}, "continuity_analysis": {"process": "simple"}}
    with patch("app.core.video_storyboard_planner._request_plan", side_effect=fake_request(calls)):
        result = plan_video_storyboard(SOURCE, settings)
    assert not any(label.endswith(" / era") for label, _, _ in calls)
    assert any("unit binder" in label for label, _, _ in calls)
    assert result["continuity"]["eras"] == []
    raw = {"character_events": [{"event_type": "first_appearance"}, {"event_type": "explicit_change"}],
           "location_events": [], "era_events": [{"id": "old"}]}
    filtered = apply_basic_policy(raw)
    assert len(filtered["character_events"]) == 1 and filtered["era_events"] == []
    assert len(raw["character_events"]) == 2


def test_reviewed_locations_are_not_requested_again_after_pause():
    calls = []
    settings = {"analysis_choices": {"plan": "full", "review": True}, "continuity_analysis": {"process": "simple"}}
    with patch("app.core.video_storyboard_planner._request_plan", side_effect=fake_request(calls)):
        result = plan_video_storyboard(SOURCE, settings, review=lambda d: d)
    assert any(c[0] == "location summary for review" for c in calls)
    assert not any(c[0].endswith(" / locations") for c in calls)
    assert result["continuity"]["analysis_review"]["edited"]["characters"]


def tr(key, default, **values):
    return default.format(**values)


def test_ui_choices_and_editable_review():
    app = QApplication.instance() or QApplication([])
    dialog = VideoStoryboardAnalysisDialog(tr, analysis_choices={"plan": "basic", "review": True})
    assert dialog.content_plan_combo.count() == 3
    assert dialog.content_plan_combo.currentData() == "basic" and dialog.review_checkbox.isChecked()
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


def test_basic_profiles_do_not_drift_between_blocks():
    previous = {"characters": [{"id": "ana", "identity_description": "Blue eyes",
                               "states": [{"id": "ana_1", "description": "Red coat"}]}]}
    current = deepcopy(previous)
    current["characters"][0]["identity_description"] = "Green eyes"
    current["characters"][0]["states"].append({"id": "ana_2", "description": "Blue coat"})
    freeze_basic_profiles(current, previous)
    assert current == previous


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


def test_manual_era_edit_is_used_and_saved():
    calls = []
    settings = {"analysis_choices": {"plan": "basic", "review": True},
                "continuity_analysis": {"process": "simple"}}
    def edit(draft):
        draft["edited"]["era"] = "Victorian England"
        return draft
    with patch("app.core.video_storyboard_planner._request_plan", side_effect=fake_request(calls)):
        result = plan_video_storyboard(SOURCE, settings, review=edit)
    assert result["narrative_context"]["era"] == "Victorian England"
    assert result["continuity"]["eras"][0]["reason"] == "user_override"
