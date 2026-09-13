"""Scene-first chat, followed by deterministic source alignment and persistence.

The discovery conversation deliberately has no JSON, timestamps or system role.
Only its final answers (never private thinking) are carried into subsequent turns.
"""
from copy import deepcopy
import hashlib
import json
import math
import re
from difflib import SequenceMatcher

from app.core.storyboard_analysis_review import choices, fingerprint
from app.core.storyboard_analysis_settings import PROMPTS, normalize, conversation_input_limit
from app.core.storyboard_entity_names import resolve, clean_names, resolve_references

CHARACTERS = PROMPTS["conversation_report"][1]
SCENES = PROMPTS["conversation_scenes"][1]
APPEARANCE = PROMPTS["conversation_appearance"][1]
LOCATIONS = "Which physical places appear in this scene summary? Briefly describe each place using only the information given."
PROFILE_INSTRUCTIONS = {
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
VISUAL_SCHEMA = _object({"scenes": _array(_object({
    "visual": STRING, "characters": _array(STRING), "locations": _array(STRING),
}))})


def visual_schema(count):
    schema = deepcopy(VISUAL_SCHEMA)
    schema["properties"]["scenes"].update(minItems=count, maxItems=count)
    schema["properties"]["scenes"]["items"]["properties"]["visual"]["minLength"] = 1
    return schema


def _valid_visual(row):
    return (isinstance(row, dict) and isinstance(row.get("visual"), str) and bool(row["visual"].strip())
            and all(isinstance(row.get(key), list) and all(isinstance(n, str) for n in row[key])
                    for key in ("characters", "locations")))


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
    check()
    response = request(visual_schema(count), instruction, data, label)
    rows = response.get("scenes") if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) != count:
        found = len(rows) if isinstance(rows, list) else 0
        warn(f"{label}: received {found}/{count} descriptions. Recovering this block one frame at a time.")
        rows = [None] * count
    else:
        rows = list(rows)
    for index, row in enumerate(rows):
        if _valid_visual(row):
            continue
        for attempt in range(1, 3):
            check()
            warn(f"{label}: retry frame {index + 1}/{count}, attempt {attempt}/2.")
            single = deepcopy(data)
            single["intervals"] = [data["intervals"][index]]
            result = request(visual_schema(1),
                "Write one English still-image description of this narration. Describe visible subjects and action. "
                "Use the supplied canonical names; list only characters and places visible in this fragment. "
                "Story context is orientation, not a reason to show people who appear later. No camera motion or style instructions.",
                single, f"{label} / frame {index + 1}/{count} retry {attempt}")
            candidate = result.get("scenes") if isinstance(result, dict) else None
            if isinstance(candidate, list) and len(candidate) == 1 and _valid_visual(candidate[0]):
                rows[index] = candidate[0]
                break
        else:
            raise VideoStoryboardPlanningError(
                f"Could not obtain a valid image description for {label}, frame {index + 1}/{count}, "
                "after two individual retries. Previously completed scenes remain saved."
            )
    return rows


def scene_excerpt_prompt(prompt, number):
    return f"{prompt} Number them {number}-1, {number}-2, etc."


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


def approximate_alignment(proposals, text, units):
    """Preserve proposal order, retain the longest consistent chain of quotes.

    Missing/backward anchors are interpolated between reliable neighbours, never
    sorted into a new narrative order. Fractional cue positions are estimates,
    not word-level alignment. Their provenance remains visible in project data.
    """
    if not proposals or not units:
        return [], ["No scenes or narration timings available for approximate alignment."]
    candidates = []
    for index, scene in enumerate(proposals):
        hits = quote_offsets(text, str(scene.get("start_quote") or ""))
        if len(hits) == 1:
            unit = next((u for u in units if u["text_start"] <= hits[0] < u["text_end"]), None)
            if unit is not None:
                candidates.append((index, hits[0], float(unit["start_seconds"]), unit))
    # Force the opening image to the opening narration; do not let a mistaken
    # first quote near the end of the book constrain every following scene.
    opening = (0, int(units[0]["text_start"]), float(units[0]["start_seconds"]), units[0])
    candidates = [opening] + [c for c in candidates if c[0] > 0]
    chains = [[opening]]
    for k, candidate in enumerate(candidates[1:], 1):
        previous = [chains[j] for j in range(k) if chains[j]
                    and candidate[1] > candidates[j][1] and candidate[2] > candidates[j][2]]
        chains.append(max(previous, key=len) + [candidate] if previous else [])
    chain = max(chains, key=len)
    end = max(float(u.get("end_seconds", u["start_seconds"])) for u in units)
    if end <= opening[2]:
        return [], ["Narration duration is too short to align proposed scenes."]
    chain = [c for c in chain if c[2] < end]
    anchors = {c[0]: c for c in chain}
    anchors[len(proposals)] = (len(proposals), len(text), end, units[-1])
    aligned, warnings = [], []
    for index, scene in enumerate(proposals):
        if index in anchors:
            _, offset, at, unit = anchors[index]
            method = "literal_quote_to_narration_cue"
            if index == 0 and quote_offsets(text, str(scene.get("start_quote") or "")) != [offset]:
                method = "approximate_opening"
        else:
            left = anchors[max(k for k in anchors if k < index)]
            right = anchors[min(k for k in anchors if k > index)]
            fraction = (index - left[0]) / (right[0] - left[0])
            at = left[2] + fraction * (right[2] - left[2])
            original = next((u for u in units if float(u["start_seconds"]) <= at < float(u.get("end_seconds", u["start_seconds"]))), None)
            if original is None:
                original = min(units, key=lambda u: abs(float(u["start_seconds"]) - at))
            span = float(original.get("end_seconds", at)) - float(original["start_seconds"])
            local = max(0.0, min(1.0, (at - float(original["start_seconds"])) / span)) if span > 0 else 0.0
            offset = int(original["text_start"] + local * (original["text_end"] - original["text_start"]))
            # Snap estimated text position to a nearby word start within the cue.
            word_starts = [m.start() for m in re.finditer(r"\S+", text)
                           if original["text_start"] <= m.start() < original["text_end"]]
            if word_starts:
                offset = min(word_starts, key=lambda pos: abs(pos - offset))
            unit = {**original, "start_seconds": at, "text_start": offset}
            method = "approximate_between_neighbours"
        if method.startswith("approximate"):
            warnings.append(f"Scene {index + 1} ({scene.get('title', '')}): approximate start at {at:.2f}s; original scene order preserved.")
        aligned.append({**scene, "unit": unit, "quote_offset": offset,
                        "aligned_start_seconds": at, "alignment_method": method,
                        "alignment_approximate": method.startswith("approximate")})
    return aligned, warnings


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
    settings = deepcopy(settings)
    text = str(source.get("text") or "").strip()
    if not text:
        raise p.VideoStoryboardPlanningError("The audiobook text is empty.")
    selection = choices(settings.get("analysis_choices"))
    config = normalize(settings.get("continuity_analysis"))
    prompts = {k: config["prompts"].get(k) or PROMPTS[k][1] for k in (
        "conversation_report", "conversation_scenes", "conversation_additions")}
    duration = float(source.get("duration_seconds") or max(4, len(text.split()) / 2.6))
    offset = max(0.0, float(source.get("voice_start_offset_seconds") or 0))
    warnings, conversations, scenes = [], [], []
    units = []
    for batch in p._planning_batches(text, source.get("narration_cues", []), duration, settings):
        for unit in p._semantic_units(batch, 1):
            units.append({**unit, "id": len(units)})
    # Cue times already include voice offset. Never add it a second time.
    if not source.get("narration_cues"):
        for unit in units:
            for key in ("start_seconds", "end_seconds"):
                unit[key] = offset + unit[key] * max(0, duration - offset) / duration
        warnings.append("No narration cues: timings are estimated from text length.")
    continuity = p._empty_continuity()
    continuity["analysis_configuration"] = {"process": "conversational", "revision": 2,
                                             "profile_mode": "simple_baseline",
                                             "content_plan": selection["plan"], "prompts": prompts}
    image = settings.get("image", {})
    mode = p.normalize_storyboard_style_id(image.get("style_mode") or p.DEFAULT_STORYBOARD_STYLE_ID)
    style = p._application_style_lock(mode, custom_style=str(image.get("style_prompt") or ""),
                                      overrides=settings.get("style_override", {}))
    era = str(settings.get("narrative_context", {}).get("era") or "")
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
                "voice_start_offset_seconds": offset, "narrative_context": p._resolved_narrative_context(era, scenes),
                "continuity": deepcopy(continuity), "scenes": deepcopy(scenes), "analysis_phase": phase,
                "completed_blocks": stage, "total_blocks": stage + 1,
                "alignment_debug": {"version": 3, "units": units, "warnings": list(warnings)}}

    def publish(phase):
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
    key = fingerprint(source, settings)
    checkpoint = settings.get("review_checkpoint", {})
    resumable = (checkpoint.get("fingerprint") == key and checkpoint.get("pipeline") == "conversational-v2"
                 and checkpoint.get("status") in {"pending", "approved"})
    if resumable:
        conversations = deepcopy(checkpoint.get("reports", []))
        unified_characters = str(checkpoint.get("unified_characters") or "")
    else:
        from app.core.storyboard_summary_files import SummaryFiles
        summary_files = SummaryFiles(settings.get("review_project_dir"))
        first_answers = []
        unified_characters = ""

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
            first = "" if selection["plan"] == "scenes" else ask(history, prompts["conversation_report"] + "\n\n" + chunk,
                                                       f"conversation {number}/{len(chunks)}: characters, appearance and summary")
            if selection["plan"] != "scenes":
                first_answers.append(first)
                save_summary(number, first)
                continuity["first_phase_summaries"] = list(first_answers)
                publish("discovery")
            report = split_report(first, warn) if first else {"characters": "", "changes": "", "summary": ""}
            conversations.append({"start": start, "text": chunk, "characters": report["characters"],
                                  "scenes": "", "appearance": report["changes"], "locations": "", "messages": history,
                                  "raw_report": first, "story_summary": report["summary"]})
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
            for part, excerpt in enumerate(excerpts, 1):
                excerpt_number += 1
                answers.append(ask([], scene_excerpt_prompt(prompts["conversation_scenes"], excerpt_number) + "\n\n" + excerpt,
                                   f"conversation {number}/{len(chunks)}: scenes and start sentences / global excerpt {excerpt_number} (part {part}/{len(excerpts)})"))
            report["scenes"] = "\n\n".join(answers)
            if selection["plan"] != "scenes":
                report["locations"] = ask([], LOCATIONS + "\n\n" + report["scenes"],
                                          f"conversation {number}/{len(chunks)}: place summary")
            continuity["discovery_reports"] = deepcopy(conversations)
            publish("discovery")
    continuity["first_phase_summaries"] = [c.get("raw_report", c["characters"]) for c in conversations if c["characters"]]
    continuity["unified_character_summary"] = unified_characters
    sections = {"characters": unified_characters or "\n\n".join(c["characters"] for c in conversations),
                "scenes": "\n\n".join(c["scenes"] for c in conversations),
                "appearance": "", "era": era,
                "locations": "\n\n".join(c.get("locations", "") for c in conversations),
                "directions": ""}
    checkpoint = deepcopy(checkpoint) if resumable else {
        "fingerprint": key, "pipeline": "conversational-v2", "status": "pending",
        "choices": selection, "reports": conversations, "unified_characters": unified_characters,
        "original": deepcopy(sections), "edited": sections}
    if selection["review"] and review:
        checkpoint = review(deepcopy(checkpoint))
    sections = checkpoint.get("edited", sections)
    era = str(sections.get("era") or "")
    p._seed_requested_era(continuity, era, duration)
    continuity["analysis_review"] = checkpoint
    continuity["discovery_reports"] = conversations
    continuity["story_context"] = "\n\n".join(c.get("story_summary", "") for c in conversations)
    publish("discovery")

    if selection["plan"] != "scenes":
        for collection, name_key, schema in (
            ("characters", "character", CHARACTER_SCHEMA),
            ("locations", "location", LOCATION_SCHEMA),
        ):
            if collection == "characters":
                fields = ("characters", "appearance")
            else:
                fields = ("locations",)
            edited = any(sections.get(k, "") != checkpoint.get("original", {}).get(k, "") for k in fields)
            summaries = (["\n\n".join(str(sections.get(k, "")) for k in fields if sections.get(k))] if edited or (collection == "characters" and unified_characters) else
                         ["\n\n".join(str(report.get(k, "")) for k in fields) for report in conversations])
            for summary in summaries:
                for summary_part in source_chunks(summary, min(limit, 6000)):
                    if not summary_part.strip():
                        continue
                    result = structured(schema, PROFILE_INSTRUCTIONS[collection], summary_part,
                                        f"conversation: convert {collection} summary to JSON")
                    add_simple_profiles(continuity[collection], result.get(collection), name_key,
                                        duration, warnings)
                    publish("continuity")

    for collection in ("characters", "locations"):
        clean_names(continuity[collection])
    if selection["plan"] == "full" and continuity["characters"]:
        from app.core.storyboard_character_changes import build_character_states
        state_sections = dict(sections)
        if unified_characters and sections.get("characters") == checkpoint.get("original", {}).get("characters"):
            # Unification is not a user edit: keep passage-local change evidence.
            state_sections["characters"] = "\n\n".join(c["characters"] for c in conversations)
            if sections.get("appearance") == checkpoint.get("original", {}).get("appearance"):
                state_sections["appearance"] = "\n\n".join(c["appearance"] for c in conversations)
        build_character_states(continuity["characters"], conversations, state_sections,
                               text, units, duration, structured, warn, check)
    publish("continuity")
    proposals = {"scenes": []}
    proposal_summaries = ([sections.get("scenes", "")]
                          if sections.get("scenes") != checkpoint.get("original", {}).get("scenes") else
                          [r["scenes"] for r in conversations])
    for summary in proposal_summaries:
        for summary_part in source_chunks(summary, min(limit, 6000)):
            converted = structured(SCENE_SCHEMA,
                "Convert these proposed scenes to JSON, in the same order. Copy each title and start sentence exactly. "
                "Copy each fragment-scene label (e.g. 2-3) into source_proposal_id; use an empty string if absent. "
                "Do not invent or rewrite scenes or quotes.",
                summary_part, "conversation: convert scene summary to JSON")
            proposals["scenes"].extend(converted.get("scenes", []))
    aligned, issues = align_proposals(proposals.get("scenes", []), text, units)
    if issues or not aligned:
        invalid = {int(m.group(1)) - 1 for issue in issues if (m := re.match(r"Scene (\d+):", issue))}
        repaired_positions = set()
        for index in sorted(invalid):
            if index in repaired_positions:
                continue
            scene = proposals["scenes"][index]
            # Include the next neighbour: a bad quote can be shifted by one
            # scene (e.g. gulls followed by boats), even if that next quote is
            # literal. Fix their boundaries together without reordering titles.
            repair_scenes = proposals["scenes"][index:index + 2]
            previous = [quote_offsets(text, str(s.get("start_quote") or "")) for s in proposals["scenes"][:index]]
            after = next((hits[0] for hits in reversed(previous) if len(hits) == 1), -1)
            following = [quote_offsets(text, str(s.get("start_quote") or "")) for s in proposals["scenes"][index + len(repair_scenes):]]
            before = next((hits[0] for hits in following if len(hits) == 1 and hits[0] > after), None)
            candidates = anchor_candidates(scene, units, maximum=12, after=after, before=before)
            if not candidates:
                continue
            repaired = structured(SCENE_SCHEMA,
                f"Correct the start sentences for these {len(repair_scenes)} scenes using the source candidates. "
                "Keep their titles and order. Match what each scene shows; the old quotes may belong to other scenes. "
                "Copy the corresponding sentences exactly, in chronological order. Do not invent quotes.",
                {"scenes": repair_scenes, "source_candidates": candidates},
                f"conversation: repair scene anchor {index + 1}")
            rows = repaired.get("scenes", [])
            if len(rows) == len(repair_scenes):
                for local, row in enumerate(rows):
                    repair_scenes[local]["start_quote"] = row.get("start_quote", "")
                    repaired_positions.add(index + local)
        aligned, issues = align_proposals(proposals.get("scenes", []), text, units)
    if issues or not aligned:
        for issue in issues:
            warn(issue)
        aligned, approximations = approximate_alignment(proposals.get("scenes", []), text, units)
        for message in approximations:
            warn(message)
        if not aligned:
            raise p.VideoStoryboardPlanningError("Cannot align scenes without proposals and usable narration timings.")
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
    for i, proposal in enumerate(aligned):
        check()
        start = proposal["aligned_start_seconds"]
        end = aligned[i + 1]["aligned_start_seconds"] if i + 1 < len(aligned) else duration
        text_start = 0 if i == 0 else proposal["unit"]["text_start"]
        text_end = aligned[i + 1]["unit"]["text_start"] if i + 1 < len(aligned) else len(text)
        # Split AI-proposed scenes using the user's maximum shot duration. Each prompt receives
        # only the narration currently spoken, so later actions cannot be pulled forward.
        maximum = max(4, min(60, float(settings.get("scene", {}).get("maximum_seconds") or 20)))
        count = max(1, math.ceil((end - start) / maximum))
        intervals = [(start + (end - start) * j / count, start + (end - start) * (j + 1) / count)
                     for j in range(count)]
        boundaries = {start, end, *(a for a, _ in intervals)}
        for collection in ("characters", "locations"):
            for record in continuity[collection]:
                boundaries.update(s["from_seconds"] for s in record["states"][1:]
                                  if start < s["from_seconds"] < end)
        boundaries.update(e["from_seconds"] for e in continuity["eras"] if start < e["from_seconds"] < end)
        ordered = sorted(boundaries)
        intervals = list(zip(ordered, ordered[1:]))
        count = len(intervals)
        assignments = []
        for a, b in intervals:
            active_units = [u for u in units if u["start_seconds"] < b and u["end_seconds"] > a
                            and u["text_start"] < text_end and u["text_end"] > text_start]
            assignments.append({"narration": " ".join(u["text"] for u in active_units), "start": a, "end": b,
                                "units": active_units or [proposal["unit"]]})
        scene_context = next((r.get("story_summary", "") for r in conversations
                              if r["start"] <= proposal["quote_offset"] < r["start"] + len(r["text"])), "")
        if sections.get("characters") != checkpoint.get("original", {}).get("characters"):
            scene_context = sections.get("characters", "")
        visual_instruction = ("Use concrete English visual descriptions of 25-55 words. Use canonical names from profiles "
            "but do not repeat their appearance. List only visible characters and locations by canonical name. "
            "Resolve pronouns using story context, not by inserting everyone. No style or zoom/motion instructions. "
            "For repeated action vary framing or show an important existing detail; never anticipate later actions.")
        visual_input = {"scene": proposal["title"], "intervals": [{"narration": a["narration"]} for a in assignments],
             "context": scene_context, "directions": sections.get("directions", ""),
             "profiles": {k: [r["name"] for r in continuity[k]] for k in ("characters", "locations")}}
        results = request_visuals(structured, visual_input, visual_instruction,
                                 f"conversation: image prompts {i + 1}/{len(aligned)}", warn, check)
        resolve_references(results, assignments, continuity, structured, warn, check)
        for j, (assignment, visual) in enumerate(zip(assignments, results)):
            a, b = assignment["start"], assignment["end"]
            selected_units = assignment["units"]
            current_era = next((e for e in reversed(continuity["eras"])
                                if e["from_seconds"] <= max(a, selected_units[0]["start_seconds"]) < e["to_seconds"]), {})
            scenes.append({"id": f"{len(scenes) + 1:03d}", "duration": round(b - a, 3),
                "start_seconds": round(a, 3), "aligned_start_seconds": round(a, 3),
                "narration": assignment["narration"], "prompt": visual["visual"].strip(),
                "characters": bind_visible_names(visual.get("characters", []), continuity["characters"],
                    max(a, selected_units[0]["start_seconds"]), visual["visual"], selected_units[0], warnings),
                "locations": _names(visual.get("locations", []), continuity["locations"], max(a, selected_units[0]["start_seconds"])),
                "era": current_era.get("description", era), "era_state_id": current_era.get("id", ""),
                "shot": "", "motion": "zoom_in" if len(scenes) % 2 == 0 else "zoom_out",
                "transition": "fade", "image_path": "", "status": "planned",
                "semantic_scene_id": f"{i + 1:03d}", "semantic_title": proposal["title"],
                "source_proposal_id": str(proposal.get("source_proposal_id") or ""),
                "coverage_index": j + 1, "coverage_count": count, "coverage_prompt_version": 2,
                "source_unit_start": selected_units[0]["id"], "source_unit_end": selected_units[-1]["id"],
                "source_text_start": selected_units[0]["text_start"], "source_text_end": selected_units[-1]["text_end"],
                "start_quote": proposal["start_quote"],
                "alignment_method": proposal.get("alignment_method", "literal_quote_to_narration_cue"),
                "alignment_approximate": proposal.get("alignment_approximate", False)})
        publish("scenes")
    check()
    if progress:
        progress(stage, stage)
    result = snapshot("complete")
    result["completed_blocks"] = result["total_blocks"] = stage
    return result
