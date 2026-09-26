from copy import deepcopy
import itertools
import json
import random
import pytest

from app.core import storyboard_conversation as c
from app.core import video_storyboard_planner as p
from app.core.storyboard_scene_alignment import (
    local_units, normalize_rows, ordered_quote_candidates, plan_excerpt, validate_rows)
from app.core.storyboard_analysis_review import load_review, load_recovery_plan, save_review
from app.core.storyboard_analysis_review import fingerprint, resume_maximum_seconds


def fixture(sentences):
    text = "\n".join(sentences)
    source = {"text": text, "duration_seconds": len(sentences)*10, "narration_cues": []}
    units, cursor = [], 0
    for i, sentence in enumerate(sentences):
        source["narration_cues"].append({"text": sentence, "start_seconds": i*10, "end_seconds": (i+1)*10})
        units.append({"id": i, "text": sentence, "text_start": cursor, "text_end": cursor+len(sentence),
                      "start_seconds": i*10, "end_seconds": (i+1)*10})
        cursor += len(sentence)+1
    return source, {"number": 1, "start": 0, "text": text, "summary": "Scenes"}, units


def rows(*quotes):
    return [{"title": f"Scene {i+1}", "start_quote": quote, "source_proposal_id": f"1-{i+1}"} for i, quote in enumerate(quotes)]


def test_repeated_greeting_uses_next_anchor_not_first_match_rule():
    _, excerpt, units = fixture(["Buenas noches.", "Y volvió a encenderla.", "Y apagó la farola.", "Buenas noches.", "El niño lo miró."])
    aligned, invalid = validate_rows(rows("Buenas noches.", "Y apagó la farola.", "El niño lo miró."), excerpt, units)
    assert not invalid and aligned[0]["quote_offset"] == 0
    assert aligned[0]["anchor_resolution"] == "ordered_context"
    aligned, invalid = validate_rows(rows("Y apagó la farola.", "Buenas noches.", "El niño lo miró."), excerpt, units)
    assert not invalid and aligned[1]["aligned_start_seconds"] == 30


@pytest.mark.parametrize("phrase", ["Sí.", "Yes.", "Oui.", "نعم.", "はい。", "是的。"])
def test_repeated_dialogue_is_language_independent(phrase):
    _, excerpt, units = fixture([phrase, "A unique intervening action.", phrase])
    aligned, invalid = validate_rows(rows(phrase, "A unique intervening action.", phrase), excerpt, units)
    assert not invalid and [r["aligned_start_seconds"] for r in aligned] == [0, 10, 20]


def test_ambiguous_chorus_is_not_assigned_to_an_arbitrary_occurrence():
    _, excerpt, units = fixture(["Refrain.", "First verse.", "Refrain.", "Second verse."])
    assert validate_rows(rows("Refrain."), excerpt, units)[1] == [0]
    aligned, invalid = validate_rows(rows("Refrain. Second verse."), excerpt, units)
    assert not invalid and aligned[0]["aligned_start_seconds"] == 20


def test_constraint_pruning_matches_all_possible_ordered_paths():
    rng = random.Random(4)
    for _ in range(200):
        candidates = [sorted(rng.sample(range(8), rng.randint(1, 4))) for _ in range(4)]
        paths = [path for path in itertools.product(*candidates) if all(a < b for a, b in zip(path, path[1:]))]
        actual = ordered_quote_candidates(candidates)
        expected = [[h for h in hits if any(path[i] == h for path in paths)] for i, hits in enumerate(candidates)] if paths else candidates
        assert actual == expected


def test_field_swap_and_empty_title_are_recovered_without_network():
    _, excerpt, units = fixture(["The pilot flies.", "The plane lands."])
    excerpt["summary"] = '1-1. **“The pilot flies.”**\nAn airplane above mountains.\n\n1-2.\nTitle: Landing\nImage: A landed airplane.\nSource quote: “The plane lands.”'
    bad = [{"title": "", "start_quote": "An airplane above mountains.", "source_proposal_id": "1-1"},
           {"title": "", "start_quote": "A landed airplane.", "source_proposal_id": "1-2"}]
    calls = []
    def request(*args):
        calls.append(args)
        return {"scenes": deepcopy(bad)}
    result = plan_excerpt(excerpt, units, request, "", lambda _: None, lambda: None, [])
    assert len(calls) == 1
    assert [r["start_quote"] for r in result] == ["The pilot flies.", "The plane lands."]
    assert result[1]["title"] == "Landing"
    assert bad[0]["title"] == ""  # caller-owned data is not changed


def test_missing_label_cannot_borrow_quote_from_another_summary_entry():
    _, excerpt, units = fixture(["The pilot flies.", "The plane lands."])
    excerpt["summary"] = '1-1. **“The pilot flies.”**\nFlying.'
    assert normalize_rows([{"title": "Another subject", "start_quote": "Wrong quote"}], excerpt)[0]["start_quote"] == "Wrong quote"


def test_unresolved_repetition_requests_longer_quote_and_preserves_other_rows():
    _, excerpt, units = fixture(["Yes.", "First action.", "Yes.", "Second action.", "Departure."])
    calls = []
    def request(schema, instruction, data, label):
        calls.append(label)
        if "convert" in label:
            return {"scenes": rows("Yes.", "Departure.")}
        assert "following" in instruction and len(data["invalid_scenes"]) == 1
        return {"scenes": [{**data["invalid_scenes"][0], "start_quote": "Yes. Second action."}]}
    result = plan_excerpt(excerpt, units, request, "", lambda _: None, lambda: None, [])
    assert len(calls) == 2
    assert result[-2]["aligned_start_seconds"] == 20
    assert result[-1]["start_quote"] == "Departure."


def test_resume_reuses_profiles_and_failed_fragment_without_new_discovery(monkeypatch, tmp_path):
    source, excerpt, units = fixture(["Arrival.", "Departure."])
    settings = {"analysis_choices": {"plan": "scenes"}}
    monkeypatch.setattr(p, "_request_free_text", lambda *a, **k: "A scene report")
    def broken(settings, schema, system, user, **kwargs):
        return {"scenes": rows("Absent quotation")}
    monkeypatch.setattr(p, "_request_plan", broken)
    partials = []
    with pytest.raises(p.VideoStoryboardPlanningError):
        p.plan_video_storyboard(source, settings, partial=partials.append)
    failed = partials[-1]
    # Saved conversion from an older validator: empty title is recoverable locally.
    failed["alignment_debug"]["fragments"][0]["attempts"][0]["rows"] = [{"title": "", "start_quote": "Arrival.", "source_proposal_id": "1-1"}]
    directory = tmp_path / "storyboard"
    directory.mkdir()
    (directory / "storyboard.json").write_text(json.dumps({"schema": "localtext2voice.video-storyboard", "version": 1,
        "analysis_status": "failed", "source": source, "scenes": [], "plan": failed}), encoding="utf-8")
    draft = load_review(tmp_path)["draft"]
    recovered = load_recovery_plan(tmp_path)
    assert recovered["analysis_phase"] == "alignment_failed"
    calls = []
    def forbidden(*a, **k):
        pytest.fail("Completed discovery must not be repeated")
    def visuals(settings, schema, instruction, user, **kwargs):
        assert schema != c.SCENE_SCHEMA
        calls.append(kwargs["request_label"])
        return {"scenes": [{"visual": "A still image.", "characters": [], "locations": []} for _ in json.loads(user)["intervals"]]}
    monkeypatch.setattr(p, "_request_free_text", forbidden)
    monkeypatch.setattr(p, "_request_plan", visuals)
    result = p.plan_video_storyboard(source, {**settings, "review_checkpoint": draft, "analysis_checkpoint": recovered})
    assert calls and result["analysis_phase"] == "complete"
    assert result["alignment_debug"]["fragments"][0]["attempts"][0]["stage"] == "resume"
    with pytest.raises(p.VideoStoryboardPlanningError, match="does not match"):
        p.plan_video_storyboard({**source, "text": source["text"] + " Changed."}, {**settings, "review_checkpoint": draft})
    # Finished projects must not offer stale saved work as a new analysis.
    document = json.loads((directory / "storyboard.json").read_text(encoding="utf-8"))
    document["analysis_status"] = "ready"
    (directory / "storyboard.json").write_text(json.dumps(document), encoding="utf-8")
    assert not load_recovery_plan(tmp_path)
    assert not load_review(tmp_path).get("draft")


def test_legacy_resume_recovers_exact_scene_limit_without_reusing_changed_source():
    source, _, _ = fixture(["A short narrative."])
    settings = {"analysis_choices": {"plan": "basic"}, "scene": {"maximum_seconds": 10},
                "analysis": {"max_block_characters": 17000, "max_block_seconds": 170., "max_output_tokens": 16000},
                "narrative_context": {"era": "", "era_visual_context": False}}
    saved = {"choices": settings["analysis_choices"], "draft": {"fingerprint": fingerprint(source, settings)},
             "limits": {"max_input_characters": 17000, "max_output_tokens": 16000}}
    current = deepcopy(settings)
    current["scene"]["maximum_seconds"] = 20
    assert resume_maximum_seconds(source, current, saved) == 10
    assert current["scene"]["maximum_seconds"] == 20
    assert resume_maximum_seconds({**source, "text": "Different book"}, current, saved) == 20


def test_reviewer_corrections_take_precedence_over_saved_proposals(monkeypatch):
    source, _, _ = fixture(["Arrival.", "Departure."])
    settings = {"analysis_choices": {"plan": "scenes"}}
    monkeypatch.setattr(p, "_request_free_text", lambda *a, **k: "Original scene summary")
    requests = []
    def request(settings, schema, instruction, user, **kwargs):
        requests.append((schema, user))
        if schema == c.SCENE_SCHEMA:
            return {"scenes": rows("Arrival.")}
        return {"scenes": [{"visual": "An image.", "characters": [], "locations": []} for _ in json.loads(user)["intervals"]]}
    monkeypatch.setattr(p, "_request_plan", request)
    original = p.plan_video_storyboard(source, settings)
    original["analysis_phase"] = "alignment_failed"
    draft = deepcopy(original["continuity"]["analysis_review"])
    draft["edited"]["scenes"] = "A different reviewed scene summary"
    requests.clear()
    p.plan_video_storyboard(source, {**settings, "analysis_checkpoint": original, "review_checkpoint": draft})
    assert any(schema == c.SCENE_SCHEMA and text == draft["edited"]["scenes"] for schema, text in requests)
