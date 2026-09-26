"""Scene-first chat, followed by deterministic source alignment and persistence.

The discovery conversation deliberately has no JSON, timestamps or system role.
Only its final answers (never private thinking) are carried into subsequent turns.
"""
from copy import deepcopy
import hashlib
import json
import re
from difflib import SequenceMatcher

from app.core.storyboard_analysis_review import choices, fingerprint
from app.core.storyboard_analysis_settings import PROMPTS, normalize, conversation_input_limit
from app.core.storyboard_entity_names import resolve, clean_names, resolve_references
from app.core.storyboard_eras import active_era, build_eras, era_boundaries, period_context, period_name
from app.core.storyboard_scene_resume import restored_scene

CHARACTERS = PROMPTS["conversation_report"][1]
SCENES = PROMPTS["conversation_scenes"][1]
LOCATIONS = "Which physical places appear in this scene summary? Briefly describe each place using only the information given."
OBJECTS = "Identify physical objects important to the plot or recurring in this passage. Give each distinct object a stable, specific name and briefly describe stated shape, material and color. Exclude people, places, clothing and incidental props. Do not invent objects or details. If none, say none."
PROFILE_INSTRUCTIONS = {
    "objects": "Convert this object summary to JSON: object and visual_description. Keep canonical names and stated visual facts; merge repeated mentions of the same object. Exclude incidental props. Return an empty objects list if none. Do not invent details.",
    "characters": 'Convert the summary to JSON, one entry per named person; exclude groups. "character" is their name, never their species. "visual_description" must describe species, approximate age, hair length and color, and clothing with a distinct color. Preserve stated traits; invent missing hair and clothing as a consistent storyboard design. Write neutral portraits in 15-25 words, without actions, relationships, burial details or props.',
    "locations": "Convert this place summary to JSON: location and visual_description for a storyboard. Keep the names and visual facts. Be concise; do not invent details, events or changes.",
}


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _array(item):
    return {"type": "array", "items": item}


STRING = {"type": "string"}
SCENE_SCHEMA = _object({"scenes": _array(_object({"title": STRING, "start_quote": STRING, "source_proposal_id": STRING}))})
CHARACTER_SCHEMA = _object({"characters": _array(_object({"character": STRING, "visual_description": STRING}))})
LOCATION_SCHEMA = _object({"locations": _array(_object({"location": STRING, "visual_description": STRING}))})
OBJECT_SCHEMA = _object({"objects": _array(_object({"object": STRING, "visual_description": STRING}))})
VISUAL_SCHEMA = _object({"scenes": _array(_object({
    "visual": STRING, "characters": _array(STRING), "locations": _array(STRING),
}))})


def visual_schema(count, include_objects=False):
    schema = deepcopy(VISUAL_SCHEMA)
    if include_objects:
        item = schema["properties"]["scenes"]["items"]
        item["properties"]["objects"] = _array(STRING)
        item["required"].append("objects")
    schema["properties"]["scenes"].update(minItems=count, maxItems=count)
    schema["properties"]["scenes"]["items"]["properties"]["visual"]["minLength"] = 1
    return schema


def _valid_visual(row, include_objects=False):
    return (isinstance(row, dict) and isinstance(row.get("visual"), str) and bool(row["visual"].strip())
            and all(isinstance(row.get(key), list) and all(isinstance(n, str) for n in row[key])
                    for key in (("characters", "locations", "objects") if include_objects else ("characters", "locations"))))


def request_visuals(request, data, instruction, label, warn, check):
    """Request at most two intervals at a time, preserving positional alignment."""
    intervals = data["intervals"]
    rows = []
    for start in range(0, len(intervals), 2):
        check()
        batch = deepcopy(data)
        batch["intervals"] = intervals[start:start + 2]
        count = len(batch["intervals"])
        batch_label = (f"{label} / frames {start + 1}-{start + count}/{len(intervals)}"
                       if len(intervals) > 2 else label)
        batch_instruction = (f"Describe exactly {count} still images, one per supplied narration interval in order. "
                             + instruction)
        rows.extend(_request_visual_batch(request, batch, batch_instruction, batch_label, warn, check))
    return rows


def _request_visual_batch(request, data, instruction, label, warn, check):
    """Recover malformed/empty successful replies without restarting analysis.

    Wrong-count replies have ambiguous positional correspondence: never attach
    their rows to arbitrary intervals. Retry individual intervals instead.
    Provider/connection/cancellation errors propagate, rather than being hidden.
    """
    from app.core.video_storyboard_planner import VideoStoryboardPlanningError
    count = len(data["intervals"])
    include_objects = "objects" in data.get("profiles", {})
    check()
    response = request(visual_schema(count, include_objects), instruction, data, label)
    rows = response.get("scenes") if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) != count:
        found = len(rows) if isinstance(rows, list) else 0
        warn(f"{label}: received {found}/{count} descriptions. Recovering this block one frame at a time.")
        rows = [None] * count
    else:
        rows = list(rows)
    for index, row in enumerate(rows):
        if _valid_visual(row, include_objects):
            continue
        for attempt in range(1, 3):
            check()
            warn(f"{label}: retry frame {index + 1}/{count}, attempt {attempt}/2.")
            single = deepcopy(data)
            single["intervals"] = [data["intervals"][index]]
            result = request(visual_schema(1, include_objects),
                "Write one English still-image description of this narration. Describe visible subjects and action. "
                "Use the supplied canonical names; list only characters, places and requested objects visible in this fragment. "
                "Story context is orientation, not a reason to show people who appear later. No camera motion or style instructions.",
                single, f"{label} / frame {index + 1}/{count} retry {attempt}")
            candidate = result.get("scenes") if isinstance(result, dict) else None
            if isinstance(candidate, list) and len(candidate) == 1 and _valid_visual(candidate[0], include_objects):
                rows[index] = candidate[0]
                break
        else:
            raise VideoStoryboardPlanningError(
                f"Could not obtain a valid image description for {label}, frame {index + 1}/{count}, "
                "after two individual retries. Previously completed scenes remain saved."
            )
    return rows


def scene_excerpt_prompt(prompt, number):
    return (f"{prompt} Number them {number}-1, {number}-2, etc. "
            "Use ONLY SOURCE_EXCERPT below. Cover its opening and main visual changes in reading order. "
            "Copy literal opening sentences from this excerpt, without translating or paraphrasing them. "
            "Group related sentences into a few visual beats; do not illustrate the rest of the book. "
            "For each numbered scene, use three separately labeled lines: Title: (short title), "
            "Image: (visual description), Source quote: (exact opening sentence). "
            "If the sentence repeats, include consecutive following sentences in Source quote to distinguish its occurrence.")


def scene_passages(text, units, start, end, limit, seconds=120):
    """Keep scene discovery local, using narration boundaries rather than words/time estimates."""
    passages = []
    cursor = start
    while cursor < end:
        active = [u for u in units if u["text_end"] > cursor and u["text_start"] < end]
        boundary = end
        if active:
            first_time = active[0]["start_seconds"]
            for unit in active[1:]:
                if unit["end_seconds"] - first_time > seconds:
                    boundary = max(cursor + 1, unit["text_start"])
                    break
        boundary = min(end, boundary)
        # Unusually long cues still respect the configured text budget.
        for passage in source_chunks(text[cursor:boundary], limit):
            passages.append(passage)
        cursor = boundary
    return passages


def _tokens(text):
    return [(m.group().casefold(), m.start(), m.end()) for m in re.finditer(r"\w+", text)]


def quote_offsets(text, quote):
    """Literal word matching, punctuation/case insensitive, retaining source offsets."""
    words = _tokens(text)
    needle = [w[0] for w in _tokens(quote)]
    if not needle:
        return []
    return [words[i][1] for i in range(len(words) - len(needle) + 1)
            if [w[0] for w in words[i:i + len(needle)]] == needle]


def source_chunks(text, limit):
    """Slice, never rewrite the original text; split at a sentence/word boundary."""
    result, start = [], 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            boundaries = list(re.finditer(r"(?<=[.!?])\s+|\n+", text[start:end]))
            if boundaries:
                end = start + boundaries[-1].end()
            else:
                space = text.rfind(" ", start + 1, end)
                if space > start:
                    end = space + 1
        result.append(text[start:end])
        start = end
    return result


def add_simple_profiles(records, entries, name_key, duration, warnings):
    """Adapt two-field LLM rows to the existing editable project ledger."""
    from app.core import video_storyboard_planner as p
    if not isinstance(entries, list):
        raise p.VideoStoryboardPlanningError(f"Missing {name_key} list in the converted summary.")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get(name_key), str) or not isinstance(entry.get("visual_description"), str):
            raise p.VideoStoryboardPlanningError(f"Invalid {name_key} row: expected a name and visual description.")
        name = entry[name_key].strip()
        description = entry["visual_description"].strip()
        if not name:
            warnings.append(f"Ignored an unnamed {name_key} in the converted summary.")
            continue
        canonical = " ".join(w[0] for w in _tokens(name))
        known = next((r for r in records if " ".join(w[0] for w in _tokens(r["name"])) == canonical), None)
        if known:
            if not known["states"][0]["description"]:
                known["states"][0]["description"] = description
            elif description and description != known["states"][0]["description"]:
                warnings.append(f"{name}: kept the first visual profile; a repeated summary does not create a new state.")
            continue
        identifier = p._unique_entity_id(records, name, name, name_key)
        # A baseline is a reusable design, not an assertion of presence. The
        # scene bindings still decide who/what is actually visible.
        records.append({"id": identifier, "name": name, "aliases": [], "identity_description": "",
            "context_description": "", "states": [{"id": f"{identifier}_state_1",
                "description": description, "from_seconds": 0.0, "to_seconds": duration,
                "source_unit_start": 0, "change_reason": "baseline_profile", "evidence": "",
                "visual_traits": {"profile_override": True}, "origin": "summary_conversion"}]})


def anchor_candidates(scene, units, maximum=8, *, after=-1, before=None):
    """Offer a small source excerpt for a failed anchor, never the whole book."""
    quote = " ".join(w[0] for w in _tokens(str(scene.get("start_quote") or "")))
    title_words = {w[0] for w in _tokens(str(scene.get("title") or ""))}
    def score(unit):
        normalized = " ".join(w[0] for w in _tokens(unit["text"]))
        return (1.0 if quote and quote in normalized else SequenceMatcher(None, quote, normalized).ratio()) + 0.1 * len(title_words & set(normalized.split()))
    eligible = [u for u in units if u.get("text_start", u["id"]) > after and (before is None or u.get("text_start", u["id"]) < before)]
    ranked = sorted(eligible, key=score, reverse=True)[:maximum]
    return [u["text"] for u in sorted(ranked, key=lambda u: u["id"])]


def align_proposals(proposals, text, units):
    """Never sort an invalid LLM sequence or guess a missing quote's timestamp."""
    aligned, issues = [], []
    previous = -1
    previous_time = -1.0
    for number, scene in enumerate(proposals, 1):
        quote = str(scene.get("start_quote") or "")
        matches = quote_offsets(text, quote)
        if len(matches) != 1:
            issues.append(f"Scene {number}: {'ambiguous' if matches else 'missing'} source quote: {quote}")
            continue
        offset = matches[0]
        unit = next((u for u in units if u["text_start"] <= offset < u["text_end"]), None)
        if unit is None or offset <= previous or float(unit["start_seconds"]) <= previous_time:
            issues.append(f"Scene {number}: quote is out of order or shares an existing timing cue: {quote}")
            continue
        previous, previous_time = offset, float(unit["start_seconds"])
        aligned.append({**scene, "unit": unit, "quote_offset": offset,
                        "aligned_start_seconds": previous_time})
    return aligned, issues


def _names(names, records, at):
    result = []
    for name in names:
        record = resolve(name, records)
        if record:
            state = next((s for s in reversed(record["states"]) if s["from_seconds"] <= at < s["to_seconds"]), None)
            if state and state["id"] not in result:
                result.append(state["id"])
    return result


def bind_visible_names(names, records, at, visual, source_unit, warnings):
    """An explicit scene binding can reveal an earlier *initial* appearance.

    Group introductions are often dated at each member's first solo action by
    the registry converter. The scene interpreter has the actual narration and
    can bind those individuals earlier. Never move a later appearance state.
    """
    for record in records:
        if not any(resolve(n, records) is record for n in names):
            continue
        states = record.get("states", [])
        if states and at < states[0]["from_seconds"] and quote_offsets(visual, record["name"]):
            state = states[0]
            state.setdefault("reported_first_appearance_seconds", state["from_seconds"])
            state["from_seconds"] = at
            state["source_unit_start"] = source_unit["id"]
            state["evidence"] = source_unit["text"]
            state["first_appearance_resolution"] = "explicit_scene_binding"
            warnings.append(f"{record['name']}: initial appearance moved earlier using the scene's explicit character binding; review if unexpected.")
    return _names(names, records, at)


def plan_conversation(source, settings, *, progress=None, partial=None, cancelled=None, trace=None, review=None):
    from app.core import video_storyboard_planner as p
    from app.core.storyboard_source_alignment import analysis_source
    from app.core.storyboard_scene_alignment import (
        reviewed_excerpts, plan_excerpt, consolidate_beats, frame_intervals)
    original_source = source
    source, source_alignment = analysis_source(source)
    settings = deepcopy(settings)
    text = str(source.get("text") or "").strip()
    if not text:
        raise p.VideoStoryboardPlanningError("The audiobook text is empty.")
    selection = choices(settings.get("analysis_choices"))
    config = normalize(settings.get("continuity_analysis"))
    prompts = {k: config["prompts"].get(k) or PROMPTS[k][1] for k in PROMPTS}
    duration = float(source.get("duration_seconds") or max(4, len(text.split()) / 2.6))
    offset = max(0.0, float(source.get("voice_start_offset_seconds") or 0))
    warnings, conversations, scenes = [], [], []
    saved_scenes = (settings.get("analysis_checkpoint") or {}).get("scenes") or []
    # Until the saved prefix is verified, keep the last durable checkpoint intact.
    # Revalidation itself can fail or be cancelled before any new prompt is made.
    protect_saved_scenes = bool(saved_scenes and settings.get("review_checkpoint"))
    scene_alignment = []
    if source_alignment["coordinate_basis"] == "timed_narration":
        warnings.append("Analysis uses the timed narration text because TTS normalization differs from the original. Source offsets refer to the saved analysis text.")
    units = []
    for batch in p._planning_batches(text, source.get("narration_cues", []), duration, settings):
        for unit in p._semantic_units(batch, 1):
            units.append({**unit, "id": len(units)})
    # Cue times already include voice offset. Never add it a second time.
    if not source.get("narration_cues"):
        for unit in units:
            unit["timing_approximate"] = True
            for key in ("start_seconds", "end_seconds"):
                unit[key] = offset + unit[key] * max(0, duration - offset) / duration
        warnings.append("No narration cues: timings are estimated from text length.")
    continuity = p._empty_continuity()
    if selection["objects"]:
        continuity["objects"] = []
    from app.core.storyboard_analysis_settings import snapshot as analysis_snapshot
    continuity["analysis_configuration"] = {**analysis_snapshot(settings), "revision": 3,
                                             "profile_mode": "simple_baseline",
                                             "content_plan": selection["plan"], "era_mode": selection["era_mode"],
                                             "prompts": {**prompts, "conversation_eras": config["prompts"].get("conversation_eras") or PROMPTS["conversation_eras"][1]}}
    image = settings.get("image", {})
    mode = p.normalize_storyboard_style_id(image.get("style_mode") or p.DEFAULT_STORYBOARD_STYLE_ID)
    style = p._application_style_lock(mode, custom_style=str(image.get("style_prompt") or ""),
                                      overrides=settings.get("style_override", {}))
    era_mode = selection["era_mode"]
    automatic_eras = era_mode != "manual"
    era_visual_context = bool(settings.get("narrative_context", {}).get("era_visual_context", False))
    era = "" if automatic_eras else str(settings.get("narrative_context", {}).get("era") or "")
    continuity["era_mode"] = era_mode
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:6], "big")
    try:
        supplied_seed = int(settings.get("seed_override"))
        if supplied_seed >= 0:
            seed = min(supplied_seed, 2**63 - 1)
    except (TypeError, ValueError):
        pass
    stage = 0
    emitted_warnings = set()

    def warn(message):
        warnings.append(message)
        if trace:
            trace({"kind": "warning", "message": message})
            emitted_warnings.add(message)

    def check():
        if cancelled and cancelled():
            raise p.VideoStoryboardPlanningError("Storyboard analysis was cancelled.")

    def announce(label):
        nonlocal stage
        check()
        stage += 1
        if trace:
            trace({"kind": "status", "message": label})
        if progress:
            progress(stage, stage + 1)

    def snapshot(phase):
        style["characters"] = list(p._legacy_character_registry_from_continuity(continuity).values())
        return {"title": str(source.get("title") or "Storyboard"), "base_seed": seed,
                "style_mode": mode, "style": deepcopy(style), "source_duration_seconds": duration,
                "voice_start_offset_seconds": offset, "narrative_context": {
                    **p._resolved_narrative_context(era, scenes), "era_mode": era_mode,
                    "era_visual_context": era_visual_context,
                    "era_source": "detected" if automatic_eras else "user"},
                "continuity": deepcopy(continuity), "scenes": deepcopy(scenes), "analysis_phase": phase,
                "completed_blocks": stage, "total_blocks": stage + 1,
                "alignment_debug": {"version": 4, "units": units, "warnings": list(warnings),
                                    "source": source_alignment, "fragments": deepcopy(scene_alignment),
                                    "approximate_frames": sum(bool(s.get("alignment_approximate")) for s in scenes)}}

    def publish(phase):
        if protect_saved_scenes:
            return
        if trace:
            for warning in warnings:
                if warning not in emitted_warnings:
                    trace({"kind": "warning", "message": warning})
                    emitted_warnings.add(warning)
        if partial:
            partial(snapshot(phase))

    def ask(history, question, label):
        announce(label)
        history.append({"role": "user", "content": question})
        answer = p._request_free_text(settings, "", question, messages=history,
                                     trace=trace, cancelled=cancelled, request_label=label)
        history.append({"role": "assistant", "content": answer})
        check()
        return answer

    def structured(schema, instruction, data, label):
        announce(label)
        request_settings = deepcopy(settings)
        request_settings.pop("story_context", None)
        user_text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        return p._request_plan(request_settings, schema, instruction, user_text,
                               trace=trace, cancelled=cancelled, request_label=label)

    # Keep short books whole. Long books use consecutive, plain-text passages;
    # completed reports remain available for review and global identity reuse.
    limit = max(1000, int(settings.get("analysis", {}).get("max_block_characters") or conversation_input_limit(config)))
    from app.core.storyboard_combined_discovery import balanced_passages, split_report, new_character_text
    chunks = balanced_passages(text, limit)
    key = fingerprint(original_source, settings)
    checkpoint = settings.get("review_checkpoint", {})
    resumable = (checkpoint.get("fingerprint") == key and checkpoint.get("pipeline") == "conversational-v2"
                 and checkpoint.get("status") in {"pending", "approved"})
    if checkpoint and not resumable:
        raise p.VideoStoryboardPlanningError(
            "The saved analysis does not match this text or analysis settings. Restore the previous settings, "
            "or uncheck Resume saved analysis to start a new analysis.")
    if resumable:
        conversations = deepcopy(checkpoint.get("reports", []))
        unified_characters = str(checkpoint.get("unified_characters") or "")
        unified_objects = str(checkpoint.get("unified_objects") or "")
        unified_locations = str(checkpoint.get("unified_locations") or "")
    else:
        from app.core.storyboard_summary_files import SummaryFiles
        summary_files = SummaryFiles(settings.get("review_project_dir"))
        first_answers = []
        unified_characters = ""
        unified_objects = ""
        unified_locations = ""

        def save_summary(number, answer):
            try:
                target = summary_files.save(number, answer)
                if target is not None and trace:
                    trace({"kind": "status", "message": f"Saved character summary: {target}"})
            except OSError as exc:
                warn(f"Could not save character summary file: {exc}")

        cursor = 0
        for number, chunk in enumerate(chunks, 1):
            check()
            start = text.find(chunk, cursor)
            if start < 0:
                raise p.VideoStoryboardPlanningError("Cannot locate conversation passage in the audiobook.")
            cursor = start + len(chunk)
            history = []
            era_report = ""
            if automatic_eras:
                from app.core.storyboard_eras import DISCOVERY
                previous = "\n\n".join(r.get("era_report", "") for r in conversations)
                era_report = ask([], (config["prompts"].get("conversation_eras") or DISCOVERY)
                    + ("\nFind the common setting for this audiobook; flag genuinely different periods." if era_mode == "auto_single" else "\nFollow every change of the represented period.")
                    + ("\n\nKnown period reports (context only):\n" + previous[-6000:] if previous else "")
                    + "\n\nCURRENT PASSAGE:\n" + chunk,
                    f"conversation {number}/{len(chunks)}: eras and visual context")
            historical_context = "\n\nERA: " + (era_report or era) if era_report or era else ""
            first = "" if not selection["characters"] else ask(history, prompts["conversation_report"] + historical_context + "\n\n" + chunk,
                                                       f"conversation {number}/{len(chunks)}: characters, appearance and summary")
            if selection["characters"]:
                first_answers.append(first)
                save_summary(number, first)
                continuity["first_phase_summaries"] = list(first_answers)
                publish("discovery")
            report = split_report(first, warn) if first else {"characters": "", "changes": "", "summary": ""}
            conversations.append({"start": start, "text": chunk, "characters": report["characters"],
                                  "scenes": "", "appearance": report["changes"], "locations": "", "messages": history,
                                  "raw_report": first, "story_summary": report["summary"], "era_report": era_report})
            continuity["discovery_reports"] = deepcopy(conversations)
            publish("discovery")
        if first_answers:
            unified_characters = conversations[0]["characters"]
            for number, report in enumerate(conversations[1:], 2):
                answer = ask([], prompts["conversation_additions"] + "\n\nFIRST TEXT:\n" + unified_characters +
                             "\n\nSECOND TEXT:\n" + report["characters"], f"conversation: new characters from block {number}/{len(chunks)}")
                try:
                    summary_files.save_named(f"novedades{number}.txt", answer)
                except OSError as exc:
                    warn(f"Could not save character additions: {exc}")
                addition = new_character_text(answer)
                if addition:
                    unified_characters += "\n\n" + addition
                continuity["unified_character_summary"] = unified_characters
                publish("discovery")
            save_summary(None, unified_characters)
            for field, filename in (("story_summary", "historia_concatenada.txt"), ("appearance", "cambios_por_tramo.txt")):
                try:
                    summary_files.save_named(filename, "\n\n".join(f"TRAMO {i}\n{r[field]}" for i, r in enumerate(conversations, 1)))
                except OSError as exc:
                    warn(f"Could not save {filename}: {exc}")
        # Scene proposals get their own bounded conversation: don't carry the
        # full B answer into another already large source-text request.
        excerpt_number = 0
        for number, report in enumerate(conversations, 1):
            excerpts = scene_passages(text, units, report["start"],
                                      report["start"] + len(report["text"]), min(limit, 4000))
            answers = []
            report["scene_excerpts"] = []
            excerpt_start = report["start"]
            for part, excerpt in enumerate(excerpts, 1):
                excerpt_number += 1
                answer = ask([], scene_excerpt_prompt(prompts["conversation_scenes"], excerpt_number)
                             + "\n\nSOURCE_EXCERPT:\n" + excerpt + "\nEND_SOURCE_EXCERPT",
                             f"conversation {number}/{len(chunks)}: scenes and start sentences / global excerpt {excerpt_number} (part {part}/{len(excerpts)})")
                answers.append(answer)
                report["scene_excerpts"].append({"number": excerpt_number, "start": excerpt_start,
                                                 "text": excerpt, "summary": answer})
                excerpt_start += len(excerpt)
            report["scenes"] = "\n\n".join(answers)
            if selection["locations"]:
                report["locations"] = ask([], prompts["conversation_locations"] + "\n\n" + report["scenes"],
                                          f"conversation {number}/{len(chunks)}: place summary")
            if selection["objects"]:
                report["objects"] = ask([], prompts["conversation_objects"] + "\n\n" + report["text"],
                                        f"conversation {number}/{len(chunks)}: important objects summary")
            continuity["discovery_reports"] = deepcopy(conversations)
            publish("discovery")
    if selection["objects"] and not (resumable and "unified_objects" in checkpoint):
        from app.core.storyboard_object_discovery import unify_object_reports

        def publish_objects(summary):
            continuity["unified_object_summary"] = summary
            publish("discovery")

        unified_objects = unify_object_reports(conversations, ask, publish_objects, instruction=prompts["object_additions"])
    if selection["objects"]:
        continuity["unified_object_summary"] = unified_objects
    if selection["locations"] and not (resumable and "unified_locations" in checkpoint):
        from app.core.storyboard_object_discovery import unify_location_reports

        def publish_locations(summary):
            continuity["unified_location_summary"] = summary
            publish("discovery")

        unified_locations = unify_location_reports(conversations, ask, publish_locations, instruction=prompts["location_additions"])
    if selection["locations"]:
        continuity["unified_location_summary"] = unified_locations
    continuity["first_phase_summaries"] = [c.get("raw_report", c["characters"]) for c in conversations if c["characters"]]
    continuity["unified_character_summary"] = unified_characters
    sections = {"characters": unified_characters or "\n\n".join(c["characters"] for c in conversations),
                "scenes": "\n\n".join(c["scenes"] for c in conversations),
                "appearance": "", "era": era,
                "locations": unified_locations,
                "directions": ""}
    if automatic_eras:
        sections.pop("era", None)
        sections["eras"] = "\n\n".join(f"PASSAGE {i}\n{r.get('era_report', '')}" for i, r in enumerate(conversations, 1))
    if selection["objects"]:
        sections["objects"] = unified_objects
    checkpoint = deepcopy(checkpoint) if resumable else {
        "fingerprint": key, "pipeline": "conversational-v2", "status": "pending",
        "choices": selection, "reports": conversations, "unified_characters": unified_characters,
        "unified_objects": unified_objects, "unified_locations": unified_locations,
        "original": deepcopy(sections), "edited": sections}
    if resumable and selection["objects"] and "unified_objects" not in checkpoint:
        original_objects = checkpoint.get("original", {}).get("objects", "")
        if checkpoint.get("edited", {}).get("objects", "") == original_objects:
            checkpoint.setdefault("edited", {})["objects"] = unified_objects
        checkpoint.setdefault("original", {})["objects"] = unified_objects
        checkpoint["unified_objects"] = unified_objects
    if resumable and selection["locations"] and "unified_locations" not in checkpoint:
        original_locations = checkpoint.get("original", {}).get("locations", "")
        if checkpoint.get("edited", {}).get("locations", "") == original_locations:
            checkpoint.setdefault("edited", {})["locations"] = unified_locations
        checkpoint.setdefault("original", {})["locations"] = unified_locations
        checkpoint["unified_locations"] = unified_locations
    if selection["review"] and review:
        checkpoint = review(deepcopy(checkpoint))
    sections = checkpoint.get("edited", sections)
    recovery = settings.get("analysis_checkpoint") or {}
    recovered_continuity = recovery.get("continuity") or {}
    recovered_review = recovered_continuity.get("analysis_review") or {}
    reuse_continuity = (resumable and recovered_review.get("fingerprint") == key
        and recovered_review.get("edited") == sections
        and recovery.get("analysis_phase") in {"alignment", "alignment_failed", "scenes"})
    if not reuse_continuity or recovered_review.get("reports") != checkpoint.get("reports"):
        saved_scenes = []
        protect_saved_scenes = False
    if reuse_continuity:
        configuration_snapshot = continuity["analysis_configuration"]
        continuity = deepcopy(recovered_continuity)
        continuity["analysis_configuration"] = configuration_snapshot
        warn("Resuming saved analysis: reusing completed entity and period profiles.")
    if not automatic_eras:
        era = str(sections.get("era") or "")
    continuity["analysis_review"] = checkpoint
    continuity["discovery_reports"] = conversations
    publish("discovery")
    if not reuse_continuity:
        if automatic_eras:
            continuity["eras"], continuity["era_assignments"] = build_eras(
                conversations, str(sections.get("eras") or ""), str(checkpoint.get("original", {}).get("eras") or ""),
                era_mode, text, units, duration, structured, warn, check, instructions=prompts)
        else:
            era = str(sections.get("era") or "")
            p._seed_requested_era(continuity, era, duration)
    continuity["analysis_review"] = checkpoint
    continuity["discovery_reports"] = conversations
    continuity["story_context"] = "\n\n".join(c.get("story_summary", "") for c in conversations)
    publish("discovery")

    if not reuse_continuity:
        if any(selection[k] for k in ("characters", "locations", "objects")):
            for collection, name_key, schema in (
                ("characters", "character", CHARACTER_SCHEMA),
                ("locations", "location", LOCATION_SCHEMA),
                ("objects", "object", OBJECT_SCHEMA),
            ):
                if not selection[collection]:
                    continue
                if collection == "characters":
                    fields = ("characters", "appearance")
                else:
                    fields = (collection,)
                edited = any(sections.get(k, "") != checkpoint.get("original", {}).get(k, "") for k in fields)
                summaries = (["\n\n".join(str(sections.get(k, "")) for k in fields if sections.get(k))] if edited or collection in {"objects", "locations"} or (collection == "characters" and unified_characters) else
                             ["\n\n".join(str(report.get(k, "")) for k in fields) for report in conversations])
                for summary in summaries:
                    for summary_part in source_chunks(summary, min(limit, 6000)):
                        if not summary_part.strip():
                            continue
                        historical_context = "\n".join(period_context(r) for r in continuity["eras"])
                        instruction = prompts[collection + "_profiles"]
                        if historical_context:
                            instruction += "\nERA: " + historical_context + "\nRespect the applicable period. For a recurring entity use its initial appearance; do not mix technologies or clothing across periods."
                        result = structured(schema, instruction, summary_part,
                                            f"conversation: convert {collection} summary to JSON")
                        add_simple_profiles(continuity[collection], result.get(collection), name_key,
                                            duration, warnings)
                        publish("continuity")

        for collection in ("characters", "locations", "objects"):
            clean_names(continuity.get(collection, []))
        if selection["plan"] == "full" and continuity["characters"]:
            from app.core.storyboard_character_changes import build_character_states
            state_sections = dict(sections)
            if unified_characters and sections.get("characters") == checkpoint.get("original", {}).get("characters"):
                # Unification is not a user edit: keep passage-local change evidence.
                state_sections["characters"] = "\n\n".join(c["characters"] for c in conversations)
                if sections.get("appearance") == checkpoint.get("original", {}).get("appearance"):
                    state_sections["appearance"] = "\n\n".join(c["appearance"] for c in conversations)
            build_character_states(continuity["characters"], conversations, state_sections,
                                   text, units, duration, structured, warn, check, instructions=prompts)
    publish("continuity")
    aligned = []
    reuse_anchors = (resumable and recovered_review.get("fingerprint") == key
        and recovered_review.get("edited", {}).get("scenes") == sections.get("scenes")
        and recovered_review.get("reports") == checkpoint.get("reports"))
    cached_fragments = {f.get("excerpt"): f for f in recovery.get("alignment_debug", {}).get("fragments", [])} if reuse_anchors else {}
    for excerpt in reviewed_excerpts(conversations, str(sections.get("scenes") or ""),
                                     str(checkpoint.get("original", {}).get("scenes") or "")):
        try:
            aligned.extend(plan_excerpt(excerpt, units, structured, prompts["scene_structure"], warn, check, scene_alignment,
                                        cached=cached_fragments.get(excerpt["number"])))
        except p.VideoStoryboardPlanningError as exc:
            warn(str(exc))
            publish("alignment_failed")
            raise
        publish("alignment")
    if not aligned:
        raise p.VideoStoryboardPlanningError("No validated scenes were obtained from the source passages.")
    for proposal in aligned:
        if not proposal.get("alignment_approximate") and proposal["quote_offset"] != proposal["unit"]["text_start"]:
            warnings.append(f"{proposal['title']}: start quote is inside a narration cue; using its start time (not word-level alignment).")
    # Preserve an omitted opening as its own semantic block instead of pulling
    # a later subject (for example childhood) back over the introduction.
    if aligned and units and aligned[0]["unit"]["id"] > units[0]["id"]:
        aligned.insert(0, {"title": "Opening narration", "start_quote": units[0]["text"],
                           "unit": units[0], "quote_offset": 0, "aligned_start_seconds": 0.0})
        warn("The proposed scenes omit the opening narration; preserving it as a separate scene.")
    # First picture covers the introduction/silence; subsequent pictures start
    # at their cited source cue. Quote-to-cue alignment is not word-level ASR.
    aligned[0]["aligned_start_seconds"] = 0.0
    aligned = consolidate_beats(aligned, duration, warn)
    for i, proposal in enumerate(aligned):
        check()
        start = proposal["aligned_start_seconds"]
        end = aligned[i + 1]["aligned_start_seconds"] if i + 1 < len(aligned) else duration
        # Split AI-proposed scenes using the user's maximum shot duration. Each prompt receives
        # only the narration currently spoken, so later actions cannot be pulled forward.
        maximum = max(4, min(60, float(settings.get("scene", {}).get("maximum_seconds") or 8)))
        boundaries = set()
        for collection in ("characters", "locations"):
            for record in continuity[collection]:
                boundaries.update(s["from_seconds"] for s in record["states"][1:]
                                  if start < s["from_seconds"] < end)
        boundaries.update(t for t in era_boundaries(continuity) if start < t < end)
        intervals = frame_intervals(start, end, maximum, boundaries)
        count = len(intervals)
        assignments = []
        for a, b in intervals:
            active_units = [u for u in units if u["start_seconds"] < b and u["end_seconds"] > a]
            hold = not active_units
            if hold:
                # Silence holds its preceding picture; leading silence uses the opening.
                active_units = [next((u for u in reversed(units) if u["end_seconds"] <= a), units[0])]
            assignments.append({"narration": " ".join(u["text"] for u in active_units), "start": a, "end": b,
                                "units": active_units, "hold": hold,
                                "era": period_context(active_era(continuity, a), include_visual_context=era_visual_context)})
        reused = 0
        for j, assignment in enumerate(assignments):
            if len(scenes) >= len(saved_scenes):
                break
            current_era = active_era(continuity, assignment["start"])
            restored = restored_scene(saved_scenes[len(scenes)], assignment, proposal,
                index=len(scenes) + 1, semantic_index=i + 1,
                coverage_index=j + 1, coverage_count=count,
                era=period_name(current_era) if current_era else era,
                era_state_id=current_era.get("id", ""),
                coordinate_basis=source_alignment["coordinate_basis"])
            if restored is None:
                raise p.VideoStoryboardPlanningError(
                    f"Saved scene {len(scenes) + 1} does not match the reconstructed narration, timing or context. "
                    "Saved prompts were preserved. Restore the previous settings or uncheck Resume saved analysis "
                    "to start a new analysis.")
            scenes.append(restored)
            reused += 1
        if protect_saved_scenes and len(scenes) == len(saved_scenes):
            protect_saved_scenes = False
            warn(f"Resuming saved analysis: reused {len(saved_scenes)} image prompts without new model requests.")
            publish("scenes")
        if reused == count:
            continue
        pending_assignments = assignments[reused:]
        scene_context = " ".join(u["text"] for u in [u for u in units if u["end_seconds"] <= start][-2:])
        visual_instruction = (prompts["scene_visuals"] +
            " Write the visual field in English even when narration is in another language. "
            "Illustrate only the supplied interval narration as a single still image. "
            "Context is preceding narration for pronouns only; do not depict its events again. "
            "Use empty reference arrays for any empty profile collection.")
        visual_input = {"intervals": [
            {"narration": a["narration"], **({"hold_previous_action": True} if a["hold"] else {}),
             **({"ERA": a["era"]} if a["era"] else {})} for a in pending_assignments],
             "context": scene_context, "directions": sections.get("directions", ""),
             "profiles": {k: [r["name"] for r in continuity[k]] for k in ("characters", "locations")}}
        if selection["objects"]:
            visual_input["profiles"]["objects"] = [r["name"] for r in continuity["objects"]]
            visual_instruction += " List only supplied important objects actually visible in this interval in objects; otherwise use an empty list."
        results = request_visuals(structured, visual_input, visual_instruction,
                                 f"conversation: image prompts {i + 1}/{len(aligned)}", warn, check)
        resolve_references(results, pending_assignments, continuity, structured, warn, check,
                           enabled=[k for k in ("characters", "locations") if selection[k]])
        for j, (assignment, visual) in enumerate(zip(pending_assignments, results), start=reused):
            a, b = assignment["start"], assignment["end"]
            selected_units = assignment["units"]
            current_era = active_era(continuity, a)
            scenes.append({"id": f"{len(scenes) + 1:03d}", "duration": round(round(b, 3) - round(a, 3), 3),
                "start_seconds": round(a, 3), "aligned_start_seconds": round(a, 3),
                "narration": assignment["narration"], "prompt": visual["visual"].strip(),
                "characters": bind_visible_names(visual.get("characters", []), continuity["characters"],
                    max(a, selected_units[0]["start_seconds"]), visual["visual"], selected_units[0], warnings),
                "locations": _names(visual.get("locations", []), continuity["locations"], max(a, selected_units[0]["start_seconds"])),
                "era": period_name(current_era) if current_era else era, "era_state_id": current_era.get("id", ""),
                "shot": "", "motion": "zoom_in" if len(scenes) % 2 == 0 else "zoom_out",
                "transition": "fade", "image_path": "", "status": "planned",
                "semantic_scene_id": f"{i + 1:03d}", "semantic_title": proposal["title"],
                "source_proposal_id": str(proposal.get("source_proposal_id") or ""),
                "source_excerpt_id": str(proposal.get("source_excerpt_id") or ""),
                "source_coordinate_basis": source_alignment["coordinate_basis"],
                "consolidated_proposal_ids": proposal.get("consolidated_proposal_ids", []),
                "coverage_index": j + 1, "coverage_count": count, "coverage_prompt_version": 2,
                "source_unit_start": selected_units[0]["id"], "source_unit_end": selected_units[-1]["id"],
                "source_text_start": selected_units[0]["text_start"], "source_text_end": selected_units[-1]["text_end"],
                "start_quote": proposal["start_quote"],
                "alignment_method": proposal.get("alignment_method", "literal_quote_to_narration_cue"),
                "alignment_approximate": proposal.get("alignment_approximate", False)})
            if selection["objects"]:
                scenes[-1]["generation_overrides"] = {"object_state_ids": _names(
                    visual.get("objects", []), continuity["objects"], max(a, selected_units[0]["start_seconds"]))}
        publish("scenes")
    if protect_saved_scenes:
        raise p.VideoStoryboardPlanningError(
            "Saved scenes extend beyond the reconstructed analysis. Saved prompts were preserved. "
            "Restore the previous settings or uncheck Resume saved analysis to start a new analysis.")
    check()
    if progress:
        progress(stage, stage)
    result = snapshot("complete")
    result["completed_blocks"] = result["total_blocks"] = stage
    return result
