import json
from copy import deepcopy

import pytest

from app.core import storyboard_conversation as c, video_storyboard_planner as p
from app.core.storyboard_eras import ERA_SCHEMA, active_era, build_eras, revise_period
from app.core.storyboard_analysis_review import fingerprint, AnalysisReviewPaused
from app.core.video_storyboard_comfyui import compile_scene_prompt, compile_effective_scene_prompt


SENTENCES = ["Roman soldiers guard the road.", "In Victorian London, a train arrives.",
             "Today, cars fill the road.", "Back in Roman Britain, the guards return.",
             "Nobody knows when the final tale takes place."]
SOURCE = {"text": " ".join(SENTENCES), "duration_seconds": 60,
          "narration_cues": [{"text": s, "start_seconds": i * 12, "end_seconds": (i + 1) * 12}
                             for i, s in enumerate(SENTENCES)]}


@pytest.mark.parametrize("narrative", [
    {"era": "Multiple periods: Rome; London", "era_source": "detected"},
    {"era": "Multiple periods: Rome; London", "era_mode": "auto_multiple"},
    {"era": "Rome", "era_mode": "auto_single"},
])
def test_unassigned_scene_never_inherits_an_automatic_project_summary(narrative):
    assert "ERA:" not in compile_effective_scene_prompt(
        {"narrative_context": narrative}, {"prompt": "A road", "era": "", "era_state_id": ""})


@pytest.mark.parametrize("era_override", [None, "", "Medieval England"])
def test_manual_project_period_and_explicit_scene_override(era_override):
    plan = {"narrative_context": {"era": "Roman Britain", "era_mode": "manual"}}
    scene = {"prompt": "A road"}
    if era_override is not None:
        scene["generation_overrides"] = {"narrative": {"era": era_override}}
    prompt = compile_effective_scene_prompt(plan, scene)
    if era_override == "":
        assert "ERA:" not in prompt
    else:
        assert f"ERA: {era_override or 'Roman Britain'}." in prompt


@pytest.mark.parametrize("visual", [False, True])
def test_period_name_and_visual_context_are_separate_optional_instructions(visual):
    plan = {"narrative_context": {"era_visual_context": visual}, "continuity": {"eras": [
        {"id": "bronze", "name": "Bronze Age", "description": "Agricultural transition in early Britain",
         "material_culture": "Wool tunics, bronze tools"}]}}
    scene = {"prompt": "A worker", "era_state_id": "bronze"}
    prompt = compile_effective_scene_prompt(plan, scene)
    assert "ERA: Bronze Age." in prompt
    assert "Agricultural transition" not in prompt and "Britain" not in prompt
    assert ("ERA VISUAL CONTEXT: Wool tunics, bronze tools." in prompt) == visual
    plan["continuity"]["eras"][0].update(name="Present day", is_current=True)
    assert "ERA:" not in compile_effective_scene_prompt(plan, scene)
    assert "ERA VISUAL CONTEXT:" not in compile_effective_scene_prompt(plan, scene)


def test_detailed_context_reaches_analysis_only_when_selected(monkeypatch):
    calls = backend(monkeypatch)
    config = settings()
    config["narrative_context"] = {"era_visual_context": True}
    result = p.plan_video_storyboard(SOURCE, config)
    assert result["narrative_context"]["era_visual_context"]
    inputs = [json.loads(payload[1]) for label, payload in calls if "image prompts" in label]
    assert inputs[0]["intervals"][0]["ERA"] == "Roman Britain; stone roads"
    assert "ERA VISUAL CONTEXT: stone roads." in compile_scene_prompt(result, result["scenes"][0])
    assert "ERA:" not in compile_scene_prompt(result, result["scenes"][2])


def events():
    periods = [("Roman Britain", "stone roads", "explicit"), ("Victorian London", "steam trains", "explicit"),
               ("Present-day England", "modern cars", "explicit"), ("Roman Britain", "stone roads", "explicit"),
               ("", "", "unknown")]
    return [{"known_id": "", "name": name, "description": name, "visual_context": context,
             "basis": basis, "evidence": quote, "start_quote": quote}
            for (name, context, basis), quote in zip(periods, SENTENCES)]


def backend(monkeypatch, era_events=None):
    calls = []

    def free(settings, system, user, **kwargs):
        calls.append((kwargs["request_label"], user))
        if "eras and visual context" in kwargs["request_label"]:
            return "Roman Britain; Victorian London; present day; return to Roman Britain; unknown."
        if "characters, appearance" in kwargs["request_label"]:
            return "Characters\nNone.\nChanges\nNone.\nSummary\nA history of England."
        return "History: " + SENTENCES[0]

    def structured(settings, schema, system, user, **kwargs):
        calls.append((kwargs["request_label"], (system, user)))
        if schema == ERA_SCHEMA:
            return {"era_events": deepcopy(events() if era_events is None else era_events)}
        if schema == c.SCENE_SCHEMA:
            return {"scenes": [{"title": "History", "start_quote": SENTENCES[0], "source_proposal_id": "1-1"}]}
        if schema == c.CHARACTER_SCHEMA:
            return {"characters": []}
        if schema == c.LOCATION_SCHEMA:
            return {"locations": []}
        return {"scenes": [{"visual": "A road through the represented historical setting.", "characters": [], "locations": []}
                           for _ in json.loads(user)["intervals"]]}

    monkeypatch.setattr(p, "_request_free_text", free)
    monkeypatch.setattr(p, "_request_plan", structured)
    return calls


def settings(mode="auto_multiple", plan="scenes"):
    return {"analysis_choices": {"plan": plan, "era_mode": mode, "characters": False, "locations": False},
            "scene": {"maximum_seconds": 60}}


@pytest.mark.parametrize("plan", ["scenes", "basic", "full"])
def test_periods_split_scenes_recur_and_leave_unknown_unrestricted(monkeypatch, plan):
    calls = backend(monkeypatch)
    result = p.plan_video_storyboard(SOURCE, settings(plan=plan))
    periods = result["continuity"]["eras"]
    assert len(periods) == 3
    assert len(periods[0]["occurrences"]) == 2
    assert [s["start_seconds"] for s in result["scenes"]] == [0, 12, 24, 36, 48]
    assert result["scenes"][0]["era_state_id"] == result["scenes"][3]["era_state_id"]
    assert result["scenes"][-1]["era_state_id"] == ""
    assert "ERA: Roman Britain." in compile_scene_prompt(result, result["scenes"][0])
    assert "stone roads" not in compile_scene_prompt(result, result["scenes"][0])
    assert "ERA:" not in compile_scene_prompt(result, result["scenes"][2])
    assert "ERA:" not in compile_scene_prompt(result, result["scenes"][-1])
    inputs = [json.loads(payload[1]) for label, payload in calls if "image prompts" in label]
    intervals = [interval for data in inputs for interval in data["intervals"]]
    assert [i.get("ERA", "") for i in intervals] == ["Roman Britain", "Victorian London", "", "Roman Britain", ""]
    assert active_era(result["continuity"], 35)["name"] == "Present-day England"
    assert active_era(result["continuity"], 50) == {}


def test_manual_skips_detection_and_informs_discovery_profiles_and_visuals(monkeypatch):
    calls = backend(monkeypatch)
    config = settings("manual", "basic")
    config["analysis_choices"]["characters"] = True
    config["narrative_context"] = {"era": "Victorian England"}
    result = p.plan_video_storyboard(SOURCE, config)
    assert not any("eras" in label for label, _ in calls)
    assert result["continuity"]["era_locked"]
    assert all(s["era"] == "Victorian England" for s in result["scenes"])
    assert "ERA: Victorian England" in next(payload for label, payload in calls if "characters, appearance" in label)
    assert "ERA: Victorian England" in next(payload[0] for label, payload in calls if "convert characters" in label)


def test_automatic_ignores_preserved_manual_input(monkeypatch):
    calls = backend(monkeypatch)
    config = settings()
    config["narrative_context"] = {"era": "Old manual entry"}
    result = p.plan_video_storyboard(SOURCE, config)
    assert "Old manual entry" not in json.dumps(calls)
    assert not result["continuity"]["era_locked"]


def test_single_period_covers_whole_book_and_conflicts_are_reported(monkeypatch):
    backend(monkeypatch, [events()[1]])
    result = p.plan_video_storyboard(SOURCE, settings("auto_single"))
    assert result["continuity"]["eras"][0]["from_seconds"] == 0
    assert result["continuity"]["eras"][0]["to_seconds"] == 60
    assert all(s["era"] == "Victorian London" for s in result["scenes"])
    backend(monkeypatch)
    result = p.plan_video_storyboard(SOURCE, settings("auto_single"))
    assert not result["continuity"]["eras"]
    assert any("Several historical periods" in w for w in result["alignment_debug"]["warnings"])


def test_invalid_quote_not_assigned_and_unreviewed_resume_invalidates(monkeypatch):
    event = events()[0]
    event["start_quote"] = "A sentence not in the book."
    backend(monkeypatch, [event])
    result = p.plan_video_storyboard(SOURCE, settings())
    assert result["continuity"]["eras"] == []
    assert any("missing source anchor" in w for w in result["alignment_debug"]["warnings"])
    assert fingerprint(SOURCE, settings()) != fingerprint(SOURCE, settings("manual"))


def test_review_pauses_before_structure_and_resumes_with_edited_periods(monkeypatch):
    calls = backend(monkeypatch)
    config = settings()
    config["analysis_choices"]["review"] = True
    captured = []

    def pause(draft):
        captured.append(deepcopy(draft))
        raise AnalysisReviewPaused()

    with pytest.raises(AnalysisReviewPaused):
        p.plan_video_storyboard(SOURCE, config, review=pause)
    assert "eras" in captured[0]["edited"] and "era" not in captured[0]["edited"]
    assert not any("convert eras" in label for label, _ in calls)
    draft = captured[0]
    draft["edited"]["eras"] = "User-reviewed historical periods. " + SENTENCES[0]
    config["review_checkpoint"] = draft
    calls.clear()
    p.plan_video_storyboard(SOURCE, config, review=lambda d: d)
    assert not any("eras and visual context" in label for label, _ in calls)
    assert "User-reviewed historical periods" in next(v[1] for label, v in calls if "convert eras" in label)


def test_revision_merges_periods_and_preserves_media_and_custom_prompts(monkeypatch):
    backend(monkeypatch)
    plan = p.plan_video_storyboard(SOURCE, settings())
    periods = plan["continuity"]["eras"]
    plan["scenes"][0].update(image_path="saved.png", generation_overrides={"raw_prompt": "My custom prompt"})
    result = revise_period(plan, periods[0]["id"], "Reviewed Roman Britain", "Stone roads", ["001", "004", "005"])
    assert result["scenes"][-1]["era"] == "Reviewed Roman Britain"
    assert result["scenes"][0]["image_path"] == "saved.png"
    assert result["scenes"][0]["generation_overrides"]["raw_prompt"] == "My custom prompt"
    assert plan["scenes"][-1]["era"] == ""
    merged = revise_period(result, periods[0]["id"], "Reviewed Roman Britain", "Stone roads", ["001", "004", "005"], periods[1]["id"])
    assert len(merged["continuity"]["eras"]) == 2
    assert merged["scenes"][0]["era_state_id"] == periods[1]["id"]
    assert len(merged["continuity"]["eras"][0]["occurrences"]) == 2


def test_periods_survive_project_roundtrip_and_legacy_manual_periods_still_work(monkeypatch, tmp_path):
    from app.core.video_storyboard_project import save_storyboard_state, load_storyboard_state
    backend(monkeypatch)
    plan = p.plan_video_storyboard(SOURCE, settings())
    metadata = {k: v for k, v in plan.items() if k != "scenes"}
    save_storyboard_state(tmp_path, {"source": SOURCE, "plan": metadata, "scenes": plan["scenes"]})
    restored = load_storyboard_state(tmp_path)
    assert restored["plan"]["continuity"]["era_assignments"] == metadata["continuity"]["era_assignments"]
    assert restored["plan"]["continuity"]["eras"] == metadata["continuity"]["eras"]
    assert [s["era_state_id"] for s in restored["scenes"]] == [s["era_state_id"] for s in plan["scenes"]]
    legacy = {"eras": [{"id": "era_old", "description": "Old manually supplied period", "from_seconds": 0, "to_seconds": 60}]}
    assert active_era(legacy, 30)["id"] == "era_old"


def test_unverifiable_explicit_evidence_is_marked_for_review(monkeypatch):
    event = events()[0]
    event["evidence"] = "An invented evidence quote."
    backend(monkeypatch, [event])
    result = p.plan_video_storyboard(SOURCE, settings())
    assert result["continuity"]["eras"][0]["reason"] == "inferred"
    assert any("evidence could not be verified" in w for w in result["alignment_debug"]["warnings"])


def test_period_schema_uses_closed_objects_with_required_properties():
    def check(schema):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
            for value in schema["properties"].values():
                check(value)
        if schema.get("type") == "array":
            check(schema["items"])
    check(ERA_SCHEMA)


def test_review_keeps_fragment_anchors_and_reuses_known_period_between_fragments():
    text = "A town appears. A town appears."
    units = [{"id": 0, "text_start": 0, "text_end": 15, "start_seconds": 0},
             {"id": 1, "text_start": 16, "text_end": len(text), "start_seconds": 12}]
    reports = [{"text": "A town appears.", "start": start, "era_report": "Roman Britain"} for start in (0, 16)]
    warnings, calls = [], []

    def request(schema, instruction, data, label):
        calls.append(data)
        event = {"known_id": "" if len(calls) == 1 else data["known_periods"][0]["id"],
                 "name": "Roman Britain" if len(calls) == 1 else "Britain under Rome",
                 "description": "Roman Britain", "visual_context": "Stone streets", "basis": "inferred",
                 "evidence": "A town appears.", "start_quote": "A town appears."}
        return {"era_events": [event]}

    periods, assignments = build_eras(reports, "PASSAGE 1\nReviewed Roman Britain\n\nPASSAGE 2\nSame period",
        "old report", "auto_multiple", text, units, 24, request, warnings.append, lambda: None)
    assert len(periods) == 1 and len(assignments) == 1
    assert assignments[0]["to_seconds"] == 24
    assert calls[1]["known_periods"][0]["id"] == periods[0]["id"]
    assert not warnings


def test_later_first_period_does_not_fill_unknown_opening(monkeypatch):
    backend(monkeypatch, [events()[1]])
    result = p.plan_video_storyboard(SOURCE, settings())
    assert result["scenes"][0]["era"] == "" and result["scenes"][0]["era_state_id"] == ""
    assert result["scenes"][1]["start_seconds"] == 12
    assert "ERA:" not in compile_scene_prompt(result, result["scenes"][0])
