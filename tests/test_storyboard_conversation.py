from copy import deepcopy
import pytest

from app.core import storyboard_conversation as c
from app.core import video_storyboard_planner as p


TEXT = "Ana enters the house. Ana sits beside the window. Ana leaves the house."
SOURCE = {"text": TEXT, "duration_seconds": 32, "voice_start_offset_seconds": 2,
          "narration_cues": [
              {"text": "Ana enters the house.", "start_seconds": 2, "end_seconds": 10},
              {"text": "Ana sits beside the window.", "start_seconds": 10, "end_seconds": 24},
              {"text": "Ana leaves the house.", "start_seconds": 24, "end_seconds": 32}]}


def fake_backend(monkeypatch):
    chats, requests = [], []
    def free(*args, **kwargs):
        chats.append(deepcopy(kwargs["messages"]))
        return ["Ana. A visit to a house.", "Arrival, sitting, departure.", "Ana wears a blue coat.", "House: a small white house."][(len(chats)-1) % 4]
    def structured(settings, schema, system, user, **kwargs):
        requests.append((schema, system, user))
        if "changes" in schema.get("properties", {}):
            return {"changes": []}
        if schema == c.CHARACTER_SCHEMA:
            return {"characters": [{"character": "Ana", "visual_description": "Brown-haired woman in a blue coat"}]}
        if schema == c.LOCATION_SCHEMA:
            return {"locations": [{"location": "House", "visual_description": "Small white house"}]}
        if schema == c.SCENE_SCHEMA:
            return {"scenes": [{"title": "Visit", "start_quote": "Ana enters the house."},
                               {"title": "Departure", "start_quote": "Ana leaves the house."}]}
        import json
        return {"scenes": [{"visual": "Ana stands beside the house.", "characters": ["Ana"], "locations": []}
                           for _ in json.loads(user)["intervals"]]}
    monkeypatch.setattr(p, "_request_free_text", free)
    monkeypatch.setattr(p, "_request_plan", structured)
    return chats, requests


def test_new_public_flow_shared_history_and_offset(monkeypatch):
    chats, requests = fake_backend(monkeypatch)
    partials = []
    plan = p.plan_video_storyboard(SOURCE, {"seed_override": 123}, partial=partials.append)
    assert len(chats) == 4
    assert chats[0] == [{"role": "user", "content": c.CHARACTERS + "\n\n" + TEXT}]
    assert chats[1][-1]["content"] == c.SCENES
    assert len(chats[2]) == 5
    assert all(m["role"] != "system" for m in chats[2])
    assert len(chats[3]) == 1
    assert chats[3][0]["content"] == c.LOCATIONS + "\n\nArrival, sitting, departure."
    assert plan["base_seed"] == 123
    assert [s["start_seconds"] for s in plan["scenes"]] == [0, 12, 24]
    assert sum(s["duration"] for s in plan["scenes"]) == 32
    assert plan["scenes"][0]["characters"] == ["character_ana_state_1"]
    assert all(s["image_path"] == "" for s in plan["scenes"])
    assert partials[-1]["scenes"] == plan["scenes"]


def test_quote_matching_is_literal_and_rejects_ambiguity():
    assert c.quote_offsets("Hello, Ana!", "hello ana") == [0]
    assert c.quote_offsets("Ana comes. Ana comes.", "Ana comes") == [0, 11]
    units = [{"text_start": 0, "text_end": 10, "start_seconds": 2},
             {"text_start": 11, "text_end": 21, "start_seconds": 10}]
    aligned, issues = c.align_proposals([{"start_quote": "Ana comes"}], "Ana comes. Ana comes.", units)
    assert not aligned and issues


def test_out_of_order_not_silently_sorted():
    units = [{"text_start": 0, "text_end": 6, "start_seconds": 2},
             {"text_start": 7, "text_end": 14, "start_seconds": 10}]
    _, issues = c.align_proposals([{"start_quote": "Second"}, {"start_quote": "First"}], "First. Second.", units)
    assert issues


def test_cancel_before_network(monkeypatch):
    chats, _ = fake_backend(monkeypatch)
    with pytest.raises(p.VideoStoryboardPlanningError, match="cancelled"):
        p.plan_video_storyboard(SOURCE, {}, cancelled=lambda: True)
    assert not chats


@pytest.mark.parametrize("maximum,starts", [(6, [0, 6, 12, 18, 24, 28]), (12, [0, 12, 24]), (30, [0, 24])])
def test_maximum_scene_duration_controls_visual_splits(monkeypatch, maximum, starts):
    fake_backend(monkeypatch)
    plan = p.plan_video_storyboard(SOURCE, {"scene": {"maximum_seconds": maximum}})
    assert [s["start_seconds"] for s in plan["scenes"]] == starts
    assert all(s["duration"] <= maximum for s in plan["scenes"])
    assert sum(s["duration"] for s in plan["scenes"]) == 32


def test_review_resume_skips_chat_and_uses_edits(monkeypatch):
    chats, _ = fake_backend(monkeypatch)
    captured = []
    settings = {"analysis_choices": {"review": True, "plan": "full"}}
    def review(draft):
        draft["edited"]["directions"] = "Evening light"
        draft["status"] = "approved"
        captured.append(deepcopy(draft))
        return draft
    p.plan_video_storyboard(SOURCE, settings, review=review)
    settings["review_checkpoint"] = captured[0]
    chats.clear()
    plan = p.plan_video_storyboard(SOURCE, settings, review=review)
    assert not chats
    assert plan["continuity"]["analysis_review"]["edited"]["directions"] == "Evening light"


def test_scene_only_skips_character_profiles(monkeypatch):
    chats, requests = fake_backend(monkeypatch)
    plan = p.plan_video_storyboard(SOURCE, {"analysis_choices": {"plan": "scenes"}})
    assert len(chats) == 1
    assert chats[0][0]["content"] == c.SCENES + "\n\n" + TEXT
    assert not plan["continuity"]["characters"]
    assert all(r[0] not in (c.CHARACTER_SCHEMA, c.LOCATION_SCHEMA) for r in requests)


def test_transport_ollama_retains_messages_and_model_defaults(monkeypatch):
    sent = []
    def http(endpoint, payload, timeout):
        sent.append(payload)
        return {"message": {"content": "Done"}, "done_reason": "stop"}
    monkeypatch.setattr(p, "_http_json", http)
    messages = [{"role": "user", "content": "Question"}, {"role": "assistant", "content": "Answer"},
                {"role": "user", "content": "Next"}]
    p._request_free_text({"ollama": {"model": "qwen3:8b"}}, "", "Next", messages=messages)
    assert sent[0]["messages"] == messages
    assert sent[0]["think"] is True
    assert "temperature" not in sent[0]["options"]


def test_litellm_responses_uses_real_history(monkeypatch):
    sent = []
    def responses(payload, **kwargs):
        sent.append(payload)
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Done"}]}]}
    monkeypatch.setattr(p, "_litellm_direct_responses", responses)
    history = [{"role": "user", "content": "Question"}, {"role": "assistant", "content": "Answer"}]
    p._request_free_text({"llm_provider": "litellm", "litellm": {"model": "openai/test"}}, "", "", messages=history)
    assert sent[0]["input"] == history
    assert "instructions" not in sent[0]


def test_group_member_binding_can_correct_initial_but_never_later_state():
    record = {"id": "ana", "name": "Ana", "aliases": [], "states": [
        {"id": "ana_1", "from_seconds": 20, "to_seconds": 30},
        {"id": "ana_2", "from_seconds": 30, "to_seconds": 50}]}
    warnings = []
    assert c.bind_visible_names(["Ana"], [record], 2, "Ana and Luis stand together.",
                               {"id": 0, "text": "The two siblings lived together."}, warnings) == ["ana_1"]
    assert record["states"][0]["from_seconds"] == 2
    assert record["states"][1]["from_seconds"] == 30
    assert warnings


def test_missing_anchor_approximates_after_one_repair(monkeypatch):
    fake_backend(monkeypatch)
    original = p._request_plan
    count = []
    def request(settings, schema, *args, **kwargs):
        if schema == c.SCENE_SCHEMA:
            count.append(1)
            return {"scenes": [{"title": "Unknown", "start_quote": "Not in this book"}]}
        return original(settings, schema, *args, **kwargs)
    monkeypatch.setattr(p, "_request_plan", request)
    result = p.plan_video_storyboard(SOURCE, {})
    assert result["scenes"] and result["scenes"][0]["alignment_approximate"]
    assert result["alignment_debug"]["warnings"]
    assert len(count) == 2


def test_source_chunks_do_not_rewrite_original():
    text = "First.\n\nSecond with   spaces.\nThird."
    chunks = c.source_chunks(text, 20)
    assert "".join(chunks) == text


def test_conversion_inputs_are_only_their_own_summaries(monkeypatch):
    _, requests = fake_backend(monkeypatch)
    p.plan_video_storyboard(SOURCE, {"story_context": "SHOULD NOT LEAK"})
    characters = next(r for r in requests if r[0] == c.CHARACTER_SCHEMA)
    locations = next(r for r in requests if r[0] == c.LOCATION_SCHEMA)
    scenes = next(r for r in requests if r[0] == c.SCENE_SCHEMA)
    assert characters[2] == "Ana. A visit to a house.\n\nAna wears a blue coat."
    assert locations[2] == "House: a small white house."
    assert scenes[2] == "Arrival, sitting, departure."
    for schema in (c.CHARACTER_SCHEMA, c.LOCATION_SCHEMA):
        row = next(iter(schema["properties"].values()))["items"]
        assert len(row["properties"]) == 2
        assert row["additionalProperties"] is False


def test_temporal_states_split_frames_and_bind_the_new_portrait(monkeypatch):
    fake_backend(monkeypatch)
    original = p._request_plan
    def request(settings, schema, system, user, **kwargs):
        if "changes" in schema.get("properties", {}):
            return {"changes": [{"character": "Ana", "start_quote": "Ana sits beside the window.",
                                  "visual_change": "red coat", "kind": "clothing"}]}
        if "visual_description" in schema.get("properties", {}):
            return {"visual_description": "Brown-haired woman in a red coat"}
        return original(settings, schema, system, user, **kwargs)
    monkeypatch.setattr(p, "_request_plan", request)
    plan = p.plan_video_storyboard(SOURCE, {})
    states = plan["continuity"]["characters"][0]["states"]
    assert len(states) == 2
    assert states[0]["to_seconds"] == states[1]["from_seconds"] == 10
    assert 10 in [s["start_seconds"] for s in plan["scenes"]]
    for scene in plan["scenes"]:
        expected = states[0 if scene["start_seconds"] < 10 else 1]["id"]
        assert scene["characters"] == [expected]


def test_basic_plan_does_not_request_temporal_changes(monkeypatch):
    _, requests = fake_backend(monkeypatch)
    p.plan_video_storyboard(SOURCE, {"analysis_choices": {"plan": "basic"}})
    assert not any("changes" in schema.get("properties", {}) for schema, _, _ in requests)


def test_simple_profiles_are_editable_baselines_without_duplicate_descriptions():
    records, warnings = [], []
    c.add_simple_profiles(records, [{"character": "Ana", "visual_description": "Blue coat"},
                                   {"character": "ANA", "visual_description": "Red coat"}],
                          "character", 50, warnings)
    assert len(records) == 1
    assert records[0]["identity_description"] == ""
    assert len(records[0]["states"]) == 1
    assert records[0]["states"][0]["description"] == "Blue coat"
    assert records[0]["states"][0]["to_seconds"] == 50
    assert warnings
    assert c._names([], records, 5) == []


def test_review_changes_are_sent_only_to_the_right_converter(monkeypatch):
    _, requests = fake_backend(monkeypatch)
    def review(draft):
        draft["edited"]["characters"] = "Edited Ana"
        draft["edited"]["appearance"] = "Green coat"
        draft["edited"]["locations"] = "Edited house"
        draft["edited"]["scenes"] = "Edited scenes"
        return draft
    p.plan_video_storyboard(SOURCE, {"analysis_choices": {"review": True}}, review=review)
    assert next(r[2] for r in requests if r[0] == c.CHARACTER_SCHEMA) == "Edited Ana\n\nGreen coat"
    assert next(r[2] for r in requests if r[0] == c.LOCATION_SCHEMA) == "Edited house"
    assert next(r[2] for r in requests if r[0] == c.SCENE_SCHEMA) == "Edited scenes"


def test_anchor_candidates_are_bounded():
    units = [{"id": i, "text": f"Sentence {i}."} for i in range(100)]
    candidates = c.anchor_candidates({"title": "Scene", "start_quote": "Sentence 53."}, units)
    assert len(candidates) <= 8
    assert "Sentence 53." in candidates


@pytest.mark.parametrize("bad", [[], [{"visual": "wrong positional assignment"}], "invalid", None])
def test_empty_or_wrong_count_recovers_individual_frames(bad):
    calls, warnings = [], []
    data = {"intervals": [{"narration": "first"}, {"narration": "second"}]}
    def request(schema, instruction, payload, label):
        calls.append(deepcopy(payload))
        if len(calls) == 1:
            assert schema["properties"]["scenes"]["minItems"] == 2
            return {"scenes": bad}
        assert schema["properties"]["scenes"]["maxItems"] == 1
        return {"scenes": [{"visual": payload["intervals"][0]["narration"], "characters": [], "locations": []}]}
    rows = c.request_visuals(request, data, "", "block", warnings.append, lambda: None)
    assert [r["visual"] for r in rows] == ["first", "second"]
    assert len(calls) == 3 and warnings


def test_valid_rows_are_preserved_and_retries_are_bounded():
    good = {"visual": "Valid", "characters": [], "locations": []}
    calls = []
    def request(*args):
        calls.append(args)
        return {"scenes": [good, {"visual": "   "}]} if len(calls) == 1 else {"scenes": []}
    with pytest.raises(p.VideoStoryboardPlanningError, match="two individual retries"):
        c.request_visuals(request, {"intervals": [{"narration": "A"}, {"narration": "B"}]},
                          "", "block", lambda _: None, lambda: None)
    assert len(calls) == 3
    assert all(call[2]["intervals"] == [{"narration": "B"}] for call in calls[1:])


def test_recovery_checks_cancellation_before_retry():
    count = []
    def request(*args):
        count.append(1)
        return {"scenes": []}
    def check():
        if count:
            raise p.VideoStoryboardPlanningError("cancelled")
    with pytest.raises(p.VideoStoryboardPlanningError, match="cancelled"):
        c.request_visuals(request, {"intervals": [{}]}, "", "block", lambda _: None, check)
    assert len(count) == 1


def test_provider_error_is_not_hidden_as_an_empty_response():
    def request(*args):
        raise p.VideoStoryboardPlanningError("Cannot connect")
    with pytest.raises(p.VideoStoryboardPlanningError, match="Cannot connect"):
        c.request_visuals(request, {"intervals": [{}]}, "", "block", lambda _: None, lambda: None)


def test_approximation_keeps_scene_order_and_neighbouring_anchors():
    text = "Opening. Sunshine. Leaving. Friends. Walking. Sea."
    units = []
    for i, sentence in enumerate(text.split(". ")):
        start = text.index(sentence)
        units.append({"id": i, "text": sentence, "text_start": start, "text_end": start + len(sentence),
                      "start_seconds": 2 + i * 10, "end_seconds": 12 + i * 10})
    proposals = [{"title": title, "start_quote": quote} for title, quote in
                 [("Opening", "Opening"), ("Leaving", "Leaving"), ("Friends", "Friends"),
                  ("Gulls", "Sunshine"), ("Sea", "Sea")]]
    aligned, warnings = c.approximate_alignment(proposals, text, units)
    assert [a["title"] for a in aligned] == [p["title"] for p in proposals]
    assert [a["aligned_start_seconds"] for a in aligned] == [2, 22, 32, 42, 52]
    assert aligned[3]["alignment_approximate"] and warnings


def test_approximation_can_share_a_cue_without_zero_durations():
    text = "Ana and Peter walk along the beach."
    units = [{"id": 0, "text": text, "text_start": 0, "text_end": len(text), "start_seconds": 2, "end_seconds": 14}]
    proposals = [{"title": str(i), "start_quote": text} for i in range(3)]
    aligned, warnings = c.approximate_alignment(proposals, text, units)
    assert [a["aligned_start_seconds"] for a in aligned] == [2, 6, 10]
    assert len(warnings) == 2


def test_longest_ordered_chain_ignores_a_premature_far_future_quote():
    text = "A. B. C. D. E."
    units = [{"id": i, "text": ch, "text_start": text.index(ch), "text_end": text.index(ch) + 1,
              "start_seconds": i * 10, "end_seconds": (i + 1) * 10} for i, ch in enumerate("ABCDE")]
    proposals = [{"title": ch, "start_quote": ch} for ch in "AEBCD"]
    aligned, _ = c.approximate_alignment(proposals, text, units)
    assert [a["title"] for a in aligned] == list("AEBCD")
    assert [a["aligned_start_seconds"] for a in aligned] == [0, 5, 10, 20, 30]


def test_repair_candidates_exclude_backward_and_later_scenes():
    units = [{"id": i, "text_start": i * 10, "text": f"Sentence {i}"} for i in range(10)]
    candidates = c.anchor_candidates({"title": "Scene", "start_quote": "Sentence 1"}, units, after=30, before=60)
    assert candidates == ["Sentence 4", "Sentence 5"]
