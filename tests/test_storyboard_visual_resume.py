from copy import deepcopy
import json

import pytest

from app.core import storyboard_conversation as c
from app.core import video_storyboard_planner as p
from app.core.storyboard_analysis_review import load_recovery_plan
from app.core.video_storyboard_project import save_storyboard_state


@pytest.fixture
def interrupted(monkeypatch, tmp_path):
    sentences = ["The traveler arrives.", "A storm approaches.", "The ship departs."]
    source = {"text": "\n".join(sentences), "duration_seconds": 30,
              "narration_cues": [{"text": text, "start_seconds": i*10, "end_seconds": (i+1)*10}
                                 for i, text in enumerate(sentences)]}
    settings = {"analysis_choices": {"plan": "scenes"}, "scene": {"maximum_seconds": 4}}
    monkeypatch.setattr(p, "_request_free_text", lambda *a, **k: "Three scenes.")
    calls = []
    def request(settings, schema, instruction, user, **kwargs):
        if schema == c.SCENE_SCHEMA:
            return {"scenes": [{"title": f"Scene {i+1}", "start_quote": text,
                                "source_proposal_id": f"1-{i+1}"} for i, text in enumerate(sentences)]}
        calls.append(kwargs["request_label"])
        if kwargs["request_label"].startswith("conversation: image prompts 3/3"):
            raise p.VideoStoryboardPlanningError("No credits remaining")
        return {"scenes": [{"visual": f"Original prompt {len(calls)}-{i}", "characters": [], "locations": []}
                           for i, _ in enumerate(json.loads(user)["intervals"])]}
    monkeypatch.setattr(p, "_request_plan", request)
    partials = []
    with pytest.raises(p.VideoStoryboardPlanningError, match="credits"):
        p.plan_video_storyboard(source, settings, partial=partials.append)
    last = deepcopy(partials[-1])
    records = last.pop("scenes")
    assert len(records) == 6
    # The UI uses different field names than the planner.
    for record in records:
        record["scene_id"] = record.pop("id")
        record["duration_seconds"] = record.pop("duration")
    records[0]["image_path"] = str(tmp_path / "frame.png")
    save_storyboard_state(tmp_path, {"source": source, "plan": last, "scenes": records}, analysis_status="failed")
    recovered = load_recovery_plan(tmp_path)
    assert len(recovered["scenes"]) == 6
    def forbidden(*a, **k):
        pytest.fail("Saved discovery must not be requested again")
    monkeypatch.setattr(p, "_request_free_text", forbidden)
    return source, {**settings, "review_checkpoint": recovered["continuity"]["analysis_review"],
                    "analysis_checkpoint": recovered}, records


def test_resume_skips_saved_visual_batches_and_preserves_prompts_and_media(interrupted, monkeypatch):
    source, settings, records = interrupted
    calls, partials = [], []
    def request(settings, schema, instruction, user, **kwargs):
        assert kwargs["request_label"].startswith("conversation: image prompts 3/3")
        calls.append(json.loads(user))
        return {"scenes": [{"visual": "New prompt", "characters": [], "locations": []}
                           for _ in calls[-1]["intervals"]]}
    monkeypatch.setattr(p, "_request_plan", request)
    result = p.plan_video_storyboard(source, settings, partial=partials.append)
    assert len(calls) == 2 and len(result["scenes"]) == 9
    assert [s["prompt"] for s in result["scenes"][:6]] == [s["prompt"] for s in records]
    assert result["scenes"][0]["image_path"] == records[0]["image_path"]
    assert all(len(partial["scenes"]) >= 6 for partial in partials)
    assert [s["id"] for s in result["scenes"]] == [f"{i:03d}" for i in range(1, 10)]
    assert sum(s["duration"] for s in result["scenes"]) == pytest.approx(30)


def test_second_provider_failure_keeps_saved_prefix_resumable(interrupted, monkeypatch):
    source, settings, _ = interrupted
    partials = []
    def fail(*a, **k):
        raise p.VideoStoryboardPlanningError("Provider unavailable")
    monkeypatch.setattr(p, "_request_plan", fail)
    for _ in range(2):
        with pytest.raises(p.VideoStoryboardPlanningError, match="Provider unavailable"):
            p.plan_video_storyboard(source, settings, partial=partials.append)
        assert len(partials[-1]["scenes"]) == 6
        assert partials[-1]["analysis_phase"] == "scenes"
        settings["analysis_checkpoint"] = partials[-1]


@pytest.mark.parametrize("field,value", [("narration", "Different passage"), ("start_seconds", 12),
    ("duration_seconds", 0.1), ("source_excerpt_id", "88"), ("coverage_index", 9), ("prompt", "")])
def test_incompatible_saved_scene_stops_without_overwriting_checkpoint(interrupted, monkeypatch, field, value):
    source, settings, _ = interrupted
    settings["analysis_checkpoint"]["scenes"][1][field] = value
    partials = []
    monkeypatch.setattr(p, "_request_plan", lambda *a, **k: pytest.fail("No new paid work before verification"))
    with pytest.raises(p.VideoStoryboardPlanningError, match="Saved scene 2 does not match"):
        p.plan_video_storyboard(source, settings, partial=partials.append)
    assert not partials


def test_partial_visual_batch_requests_only_missing_intervals(interrupted, monkeypatch):
    source, settings, _ = interrupted
    settings["analysis_checkpoint"]["scenes"] = settings["analysis_checkpoint"]["scenes"][:4]
    calls = []
    def request(settings, schema, instruction, user, **kwargs):
        data = json.loads(user)
        calls.append((kwargs["request_label"], len(data["intervals"])))
        return {"scenes": [{"visual": "Missing prompt", "characters": [], "locations": []}
                           for _ in data["intervals"]]}
    monkeypatch.setattr(p, "_request_plan", request)
    result = p.plan_video_storyboard(source, settings)
    assert calls == [("conversation: image prompts 2/3", 2),
                     ("conversation: image prompts 3/3 / frames 1-2/3", 2),
                     ("conversation: image prompts 3/3 / frames 3-3/3", 1)]
    assert len(result["scenes"]) == 9
    assert [s["coverage_index"] for s in result["scenes"]] == [1, 2, 3]*3


def test_changed_review_does_not_reuse_old_visual_prompts(interrupted, monkeypatch):
    source, settings, _ = interrupted
    settings["review_checkpoint"] = deepcopy(settings["review_checkpoint"])
    settings["review_checkpoint"]["edited"]["directions"] = "Use overhead shots."
    calls = []
    def request(settings, schema, instruction, user, **kwargs):
        data = json.loads(user)
        assert data["directions"] == "Use overhead shots."
        calls.append(kwargs["request_label"])
        return {"scenes": [{"visual": "Revised prompt", "characters": [], "locations": []}
                           for _ in data["intervals"]]}
    monkeypatch.setattr(p, "_request_plan", request)
    result = p.plan_video_storyboard(source, settings)
    assert len(calls) == 6
    assert all(s["prompt"] == "Revised prompt" for s in result["scenes"])


def test_alignment_failure_during_resume_preserves_visual_checkpoint(interrupted, monkeypatch):
    source, settings, _ = interrupted
    partials = []
    def fail(*a, **k):
        raise p.VideoStoryboardPlanningError("Alignment interrupted")
    from app.core import storyboard_scene_alignment
    monkeypatch.setattr(storyboard_scene_alignment, "plan_excerpt", fail)
    with pytest.raises(p.VideoStoryboardPlanningError, match="Alignment interrupted"):
        p.plan_video_storyboard(source, settings, partial=partials.append)
    assert not partials


def test_fully_saved_visuals_finish_without_any_provider_requests(interrupted, monkeypatch):
    source, settings, _ = interrupted
    def request(settings, schema, instruction, user, **kwargs):
        return {"scenes": [{"visual": "Final prompt", "characters": [], "locations": []}
                           for _ in json.loads(user)["intervals"]]}
    monkeypatch.setattr(p, "_request_plan", request)
    complete = p.plan_video_storyboard(source, settings)
    complete["analysis_phase"] = "scenes"  # process stopped before final status persisted
    settings["analysis_checkpoint"] = complete
    monkeypatch.setattr(p, "_request_plan", lambda *a, **k: pytest.fail("All prompts already saved"))
    result = p.plan_video_storyboard(source, settings)
    assert result["analysis_phase"] == "complete"
    assert result["scenes"] == complete["scenes"]
