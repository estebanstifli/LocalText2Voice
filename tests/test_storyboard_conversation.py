from copy import deepcopy
import pytest

from app.core import storyboard_conversation as c
from app.core import video_storyboard_planner as p


TEXT = "Ana enters the house. Ana sits beside the window. Ana leaves the house."
REPORT = "Characters\nAna: Brown-haired woman in a blue coat.\nChanges\nNone.\nSummary\nA visit to a house."
SOURCE = {"text": TEXT, "duration_seconds": 32, "voice_start_offset_seconds": 2,
          "narration_cues": [
              {"text": "Ana enters the house.", "start_seconds": 2, "end_seconds": 10},
              {"text": "Ana sits beside the window.", "start_seconds": 10, "end_seconds": 24},
              {"text": "Ana leaves the house.", "start_seconds": 24, "end_seconds": 32}]}


def test_scene_passages_follow_cues_and_preserve_every_character():
    text = "One. Two. Three. Four. Five."
    units = [{"text_start": text.index(word), "text_end": text.index(word) + len(word),
              "start_seconds": i * 50 + 2, "end_seconds": (i + 1) * 50 + 2}
             for i, word in enumerate(["One.", "Two.", "Three.", "Four.", "Five."])]
    parts = c.scene_passages(text, units, 0, len(text), 4000)
    assert parts == ["One. Two. ", "Three. Four. ", "Five."]
    assert "".join(parts) == text
    limited = c.scene_passages(text, units, 0, len(text), 10)
    assert "".join(limited) == text
    assert all(len(part) <= 10 for part in limited)


def test_omitted_opening_is_not_merged_into_later_scene(monkeypatch):
    fake_backend(monkeypatch)
    original = p._request_plan
    def request(settings, schema, system, user, **kwargs):
        if schema == c.SCENE_SCHEMA:
            return {"scenes": [{"title": "Departure", "start_quote": "Ana leaves the house."}]}
        return original(settings, schema, system, user, **kwargs)
    monkeypatch.setattr(p, "_request_plan", request)
    plan = p.plan_video_storyboard(SOURCE, {})
    assert [s["start_seconds"] for s in plan["scenes"]] == [0, 12, 24]
    assert plan["scenes"][0]["semantic_title"] == "Opening narration"
    assert plan["scenes"][-1]["semantic_title"] == "Departure"


def fake_backend(monkeypatch):
    chats, requests = [], []
    def free(*args, **kwargs):
        chats.append(deepcopy(kwargs["messages"]))
        label = kwargs["request_label"]
        if "characters, appearance and summary" in label:
            return REPORT
        if "new characters from block" in label:
            return "None."
        if "scenes and start sentences" in label:
            return "Arrival, sitting, departure."
        return "House: a small white house."
    def structured(settings, schema, system, user, **kwargs):
        requests.append((schema, system, user))
        if "changes" in schema.get("properties", {}):
            return {"changes": []}
        if schema == c.CHARACTER_SCHEMA:
            return {"characters": [{"character": "Ana", "visual_description": "Brown-haired woman in a blue coat"}]}
        if schema == c.LOCATION_SCHEMA:
            return {"locations": [{"location": "House", "visual_description": "Small white house"}]}
        if schema == c.SCENE_SCHEMA:
            return {"scenes": [{"title": "Visit", "start_quote": "Ana enters the house.", "source_proposal_id": "1-1"},
                               {"title": "Departure", "start_quote": "Ana leaves the house.", "source_proposal_id": "1-2"}]}
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
    assert len(chats) == 3
    assert chats[0] == [{"role": "user", "content": c.CHARACTERS + "\n\n" + TEXT}]
    assert chats[1][-1]["content"] == c.scene_excerpt_prompt(c.SCENES, 1) + "\n\n" + TEXT
    assert len(chats[2]) == 1
    assert all(m["role"] != "system" for m in chats[2])
    assert chats[2][0]["content"] == c.LOCATIONS + "\n\nArrival, sitting, departure."
    assert plan["base_seed"] == 123
    assert [s["start_seconds"] for s in plan["scenes"]] == [0, 12, 24]
    assert sum(s["duration"] for s in plan["scenes"]) == 32
    assert [s["source_proposal_id"] for s in plan["scenes"]] == ["1-1", "1-1", "1-2"]
    assert plan["scenes"][0]["characters"] == ["character_ana_state_1"]
    character_instruction = next(system for schema, system, _ in requests if schema == c.CHARACTER_SCHEMA)
    assert character_instruction == c.PROFILE_INSTRUCTIONS["characters"]
    assert '"character" is their name, never their species' in character_instruction
    assert "hair length and color" in character_instruction
    assert "clothing with a distinct color" in character_instruction
    assert "Preserve stated traits" in character_instruction
    assert "4–5-line summary" in chats[0][0]["content"]
    assert "including unnamed relatives" in chats[0][0]["content"]
    assert "without actions" in character_instruction
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
    assert chats[0][0]["content"] == c.scene_excerpt_prompt(c.SCENES, 1) + "\n\n" + TEXT
    assert not plan["continuity"]["characters"]
    assert all(r[0] not in (c.CHARACTER_SCHEMA, c.LOCATION_SCHEMA) for r in requests)


def test_first_phase_saves_one_summary_without_unification(monkeypatch, tmp_path):
    chats, _ = fake_backend(monkeypatch)
    result = p.plan_video_storyboard(SOURCE, {"review_project_dir": str(tmp_path)})
    files = list((tmp_path / "storyboard" / "analysis").glob("*/resumen*.txt"))
    assert len(chats) == 3
    assert {f.name for f in files} == {"resumen1.txt", "resumen_unificado.txt"}
    assert next(f for f in files if f.name == "resumen1.txt").read_text(encoding="utf-8") == REPORT
    assert result["continuity"]["unified_character_summary"] == "Ana: Brown-haired woman in a blue coat."


def test_multiple_first_phase_summaries_are_unified_once_and_resume(monkeypatch, tmp_path):
    chats, requests = fake_backend(monkeypatch)
    from app.core import storyboard_combined_discovery as combined
    original_free, original_chunks = p._request_free_text, combined.balanced_passages
    monkeypatch.setattr(combined, "balanced_passages", lambda text, limit:
                        ["Ana enters the house. ", TEXT[len("Ana enters the house. "):]]
                        if text == TEXT else original_chunks(text, limit))
    unifications = []
    def free(*args, **kwargs):
        if "new characters from block" in kwargs["request_label"]:
            unifications.append(deepcopy(kwargs["messages"]))
            return "Luis: Red hat."
        return original_free(*args, **kwargs)
    monkeypatch.setattr(p, "_request_free_text", free)
    drafts = []
    def review(draft):
        drafts.append(deepcopy(draft))
        return draft
    settings = {"review_project_dir": str(tmp_path), "analysis_choices": {"plan": "basic", "review": True}}
    result = p.plan_video_storyboard(SOURCE, settings, review=review)
    assert len(chats) == 6 and len(unifications) == 1
    scene_questions = [chat[0]["content"] for chat in chats if "Number them" in chat[0]["content"]]
    assert len(scene_questions) == 2
    assert "Number them 1-1, 1-2, etc." in scene_questions[0]
    assert "Number them 2-1, 2-2, etc." in scene_questions[1]
    assert len(unifications[0]) == 1
    prompt = unifications[0][0]["content"]
    assert prompt.count("Ana: Brown-haired woman in a blue coat.") == 2
    assert "FIRST TEXT:" in prompt and "SECOND TEXT:" in prompt
    assert "A visit to a house." not in prompt  # only character descriptions
    files = list((tmp_path / "storyboard" / "analysis").glob("*/resumen*.txt"))
    assert {f.name for f in files} == {"resumen1.txt", "resumen2.txt", "resumen_unificado.txt"}
    assert result["continuity"]["story_context"] == "A visit to a house.\n\nA visit to a house."
    assert drafts[0]["original"]["characters"] == "Ana: Brown-haired woman in a blue coat.\n\nLuis: Red hat."
    assert any("Luis: Red hat." in user for schema, _, user in requests if schema == c.CHARACTER_SCHEMA)
    settings["review_checkpoint"] = drafts[0]
    chats.clear()
    again = p.plan_video_storyboard(SOURCE, settings, review=review)
    assert not chats and len(unifications) == 1
    assert again["continuity"]["story_context"] == result["continuity"]["story_context"]


def test_summary_saved_before_a_later_phase_fails(monkeypatch, tmp_path):
    def free(*args, **kwargs):
        if "characters, appearance and summary" in kwargs["request_label"]:
            return "Ana: niña de pelo castaño."
        raise p.VideoStoryboardPlanningError("Later phase failed")
    monkeypatch.setattr(p, "_request_free_text", free)
    with pytest.raises(p.VideoStoryboardPlanningError, match="Later phase failed"):
        p.plan_video_storyboard(SOURCE, {"review_project_dir": str(tmp_path)})
    target = next((tmp_path / "storyboard" / "analysis").glob("*/resumen1.txt"))
    assert target.read_text(encoding="utf-8") == "Ana: niña de pelo castaño."


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
    assert characters[2] == "Ana: Brown-haired woman in a blue coat."
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


@pytest.mark.parametrize("count", [1, 2, 3, 11, 12])
def test_visual_requests_are_limited_to_two_intervals(count):
    data = {"scene": "Visit", "context": "Story context", "profiles": {"characters": ["Ana"]},
            "intervals": [{"narration": f"Fragment {i}"} for i in range(count)]}
    original = deepcopy(data)
    calls = []
    def request(schema, instruction, payload, label):
        size = len(payload["intervals"])
        assert 1 <= size <= 2
        assert schema["properties"]["scenes"]["minItems"] == size
        assert schema["properties"]["scenes"]["maxItems"] == size
        assert instruction.startswith(f"Describe exactly {size} still images")
        assert payload["profiles"] == data["profiles"]
        assert payload["context"] == data["context"]
        calls.append(label)
        return {"scenes": [{"visual": row["narration"], "characters": [], "locations": []}
                           for row in payload["intervals"]]}
    rows = c.request_visuals(request, data, "Use concrete descriptions.", "test", lambda _: None, lambda: None)
    assert [row["visual"] for row in rows] == [row["narration"] for row in data["intervals"]]
    assert len(calls) == (count + 1) // 2
    assert len(set(calls)) == len(calls)
    assert data == original


def test_visual_batches_stop_on_cancellation():
    calls = []
    def request(*args):
        calls.append(args)
        return {"scenes": [{"visual": "Valid", "characters": [], "locations": []}] * 2}
    def check():
        if calls:
            raise p.VideoStoryboardPlanningError("cancelled")
    with pytest.raises(p.VideoStoryboardPlanningError, match="cancelled"):
        c.request_visuals(request, {"intervals": [{}] * 11}, "", "test", lambda _: None, check)
    assert len(calls) == 1


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
