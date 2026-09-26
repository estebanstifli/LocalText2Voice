from copy import deepcopy
import json
import pytest

from app.core import video_storyboard_planner as p
from app.core import storyboard_conversation as c
from app.core.storyboard_source_alignment import analysis_source
from app.core.storyboard_scene_alignment import (
    validate_rows, plan_excerpt, summary_chunks, consolidate_beats, frame_intervals, reviewed_excerpts)


def timed(texts, seconds=10):
    text = " ".join(texts)
    units, cursor = [], 0
    for i, sentence in enumerate(texts):
        units.append({"id": i, "text": sentence, "text_start": cursor, "text_end": cursor+len(sentence),
                      "start_seconds": i*seconds, "end_seconds": (i+1)*seconds})
        cursor += len(sentence)+1
    return text, units


def test_normalized_numbers_cannot_accumulate_source_offset_drift():
    cues = [{"text": f"En mil novecientos noventa ocurrió el evento {i}.",
             "start_seconds": i*10, "end_seconds": (i+1)*10} for i in range(163)]
    source = {"text": " ".join(f"En 1990 ocurrió el evento {i}." for i in range(163)),
              "duration_seconds": 1630, "narration_cues": cues}
    original = deepcopy(source)
    prepared, metadata = analysis_source(source)
    units = [u for b in p._planning_batches(prepared["text"], prepared["narration_cues"], 1630)
             for u in p._semantic_units(b, 1)]
    assert source == original
    assert metadata["coordinate_basis"] == "timed_narration"
    assert all(prepared["text"][u["text_start"]:u["text_end"]] == u["text"] for u in units)
    assert units[-1]["start_seconds"] == 1620
    assert units[-1]["text_end"] == len(prepared["text"])
    assert "".join(c.scene_passages(prepared["text"], units, 0, len(prepared["text"]), 4000)) == prepared["text"]
    with pytest.raises(p.VideoStoryboardPlanningError, match="could not be located"):
        p._find_text_offset(source["text"], cues[0]["text"], 0)


@pytest.mark.parametrize("cues", [
    [{"text": "A", "start_seconds": 0, "end_seconds": 0}],
    [{"text": "A", "start_seconds": 0, "end_seconds": 10}, {"text": "B", "start_seconds": 5, "end_seconds": 12}],
    [{"text": "A", "start_seconds": 0, "end_seconds": float("inf")}],
])
def test_missing_or_invalid_audio_timings_stop_before_the_llm(cues):
    with pytest.raises(p.VideoStoryboardPlanningError):
        analysis_source({"text": "A B", "narration_cues": cues, "duration_seconds": 20})


def test_repeated_book_quote_is_valid_only_in_its_own_fragment():
    text, units = timed(["The storm returns.", "A clear day.", "The storm returns."])
    excerpt = {"number": 2, "start": units[2]["text_start"], "text": units[2]["text"]}
    rows = [{"title": "Storm", "start_quote": "The storm returns."}]
    aligned, invalid = validate_rows(rows, excerpt, units)
    assert not invalid and aligned[0]["aligned_start_seconds"] == 20
    assert validate_rows([{"title": "Day", "start_quote": "A clear day."}], excerpt, units)[1]


def test_conversion_keeps_scene_heading_description_and_quote_together():
    entries = [f"1-{i}. **Scene {i}**\nImage: " + "detail "*70 + f"\nQuote: sentence {i}.\n\n" for i in range(1, 16)]
    chunks = summary_chunks("".join(entries), 1000)
    assert "".join(chunks) == "".join(entries)
    assert all(any(entry in chunk for chunk in chunks) for entry in entries)


def test_failed_local_repair_preserves_valid_quote_and_replans_once():
    text, units = timed(["Ancient farmers cultivate maize.", "The villagers build houses."])
    excerpt = {"number": 1, "start": 0, "text": text, "summary": "Farming; an unrelated football match."}
    old = [{"title": "Farming", "start_quote": units[0]["text"], "source_proposal_id": "1-1"},
           {"title": "Football", "start_quote": "The players win.", "source_proposal_id": "1-2"}]
    calls, audit = [], []
    def request(schema, instruction, data, label):
        calls.append((data, label))
        if "convert" in label:
            return {"scenes": deepcopy(old)}
        assert data["SOURCE_EXCERPT"] == text
        if "repair" in label:
            assert data["verified_scenes"] == old[:1]
            assert data["invalid_scenes"] == old[1:]
            return {"scenes": [{**old[1], "start_quote": ""}]}
        return {"scenes": [old[0], {"title": "Houses", "start_quote": units[1]["text"]}]}
    result = plan_excerpt(excerpt, units, request, "convert", lambda _: None, lambda: None, audit)
    assert [r["title"] for r in result] == ["Farming", "Houses"]
    assert result[0]["start_quote"] == old[0]["start_quote"]
    assert len(calls) == 3 and audit[0]["status"] == "validated"
    assert audit[0]["attempts"][0]["rows"] == old


def test_catastrophic_foreign_quotes_never_become_microframes():
    text, units = timed(["The match begins.", "The match ends."], seconds=38)
    excerpt = {"number": 19, "start": 0, "text": text, "summary": "Old corrupted whole-book report"}
    calls = []
    def request(*args):
        calls.append(1)
        return {"scenes": [{"title": f"Foreign event {i}", "start_quote": "Unrelated era report quote."} for i in range(52)]}
    with pytest.raises(p.VideoStoryboardPlanningError, match="Cannot align fragment 19"):
        plan_excerpt(excerpt, units, request, "", lambda _: None, lambda: None, [])
    assert len(calls) == 3


def test_dense_valid_boundaries_consolidate_without_moving_narration_times():
    rows = [{"title": str(i), "aligned_start_seconds": i*1.47249, "source_proposal_id": str(i)} for i in range(52)]
    result = consolidate_beats(rows, 76, lambda _: None)
    starts = [r["aligned_start_seconds"] for r in result]
    assert all(b-a >= 3 for a, b in zip(starts, starts[1:]+[76]))
    assert set(starts) <= {i*1.47249 for i in range(52)}
    assert len(result) < 52


def test_era_change_does_not_create_a_sliver_next_to_a_regular_split():
    intervals = frame_intervals(0, 30, 15, {14.8})
    assert intervals == [(0, 14.8), (14.8, 22.4), (22.4, 30)]
    assert all(3 < b-a <= 15 for a, b in intervals)


def test_review_edits_remain_local_and_cannot_reorder_source_fragments():
    reports = [{"scene_excerpts": [{"number": i, "text": str(i), "start": i, "summary": "old"} for i in (1, 2)]}]
    edited = "1-1. New scene one\n\n2-1. New scene two"
    values = reviewed_excerpts(reports, edited, "old")
    assert "two" not in values[0]["summary"] and "one" not in values[1]["summary"]
    with pytest.raises(p.VideoStoryboardPlanningError, match="out-of-order"):
        reviewed_excerpts(reports, "2-1. Two\n1-1. One", "old")


def test_long_pipeline_uses_local_source_and_never_empty_final_narration(monkeypatch):
    texts = [f"Narration passage number {i} describes a different event." for i in range(30)]
    text, units = timed(texts, 20)
    source = {"text": text, "duration_seconds": 645, "narration_cues": [
        {"text": u["text"], "start_seconds": u["start_seconds"], "end_seconds": u["end_seconds"]} for u in units]}
    latest, seen = {}, []
    def free(settings, system, user, **kwargs):
        assert "SOURCE_EXCERPT:" in user and "ERA:" not in user
        excerpt = user.split("SOURCE_EXCERPT:\n", 1)[1].split("\nEND_SOURCE_EXCERPT")[0]
        latest["text"] = excerpt
        latest["summary"] = excerpt  # deterministic, literal discovery stand-in
        return excerpt
    def request(settings, schema, instruction, user, **kwargs):
        if schema == c.SCENE_SCHEMA:
            return {"scenes": [{"title": "Local narration", "start_quote": user.split(". ")[0].strip()}]}
        data = json.loads(user)
        seen.extend(i["narration"] for i in data["intervals"])
        return {"scenes": [{"visual": "A single illustration of the narrated event.", "characters": ["Untracked person"], "locations": []} for _ in data["intervals"]]}
    monkeypatch.setattr(p, "_request_free_text", free)
    monkeypatch.setattr(p, "_request_plan", request)
    result = p.plan_video_storyboard(source, {"analysis_choices": {"plan": "scenes"}, "scene": {"maximum_seconds": 15}})
    assert all(seen) and all(s["narration"] for s in result["scenes"])
    assert sum(s["duration"] for s in result["scenes"]) == pytest.approx(645)
    assert all(3 <= s["duration"] <= 15 for s in result["scenes"])
    assert not any("Untracked person" in w for w in result["alignment_debug"]["warnings"])
    assert texts[-1] in seen[-1]
    assert all(f["status"] == "validated" for f in result["alignment_debug"]["fragments"])
