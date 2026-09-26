"""Fragment-local scene conversion with bounded recovery and no global interpolation."""
from copy import deepcopy
import math
import re


ENTRY = re.compile(r"(?m)^[ \t]*(?:[>#*\-]+[ \t]*)*(\d+)-(\d+)\b")
MINIMUM_BEAT_SECONDS = 3.0


def summary_chunks(summary, limit=6000):
    """Keep every numbered scene (heading, description and quote) atomic."""
    starts = [m.start() for m in ENTRY.finditer(summary)]
    if not starts or len(summary) <= limit:
        return [summary] if summary.strip() else []
    starts[0] = 0
    entries = [summary[a:b] for a, b in zip(starts, starts[1:] + [len(summary)])]
    batches, current = [], ""
    for entry in entries:
        if current and len(current) + len(entry) > limit:
            batches.append(current)
            current = ""
        current += entry
    if current:
        batches.append(current)
    return batches


def reviewed_excerpts(reports, edited, original):
    from app.core.video_storyboard_planner import VideoStoryboardPlanningError
    excerpts = deepcopy([e for r in reports for e in r["scene_excerpts"]])
    if edited == original:
        return excerpts
    if len(excerpts) == 1:
        excerpts[0]["summary"] = edited
        return excerpts
    matches = list(ENTRY.finditer(edited))
    known = {str(e["number"]): e for e in excerpts}
    if not matches or edited[:matches[0].start()].strip():
        raise VideoStoryboardPlanningError(
            "Keep the fragment-scene labels (for example 2-3) when editing a long scene summary so each scene retains its source passage.")
    for e in excerpts:
        e["summary"] = ""
    previous = 0
    for index, match in enumerate(matches):
        number = match.group(1)
        if number not in known or int(number) < previous:
            raise VideoStoryboardPlanningError("The reviewed scene labels refer to an unknown or out-of-order source fragment.")
        previous = int(number)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(edited)
        known[number]["summary"] += edited[match.start():end]
    return excerpts


def local_units(excerpt, units):
    """Clip a cue only when a text-budget boundary falls inside it."""
    left, right = excerpt["start"], excerpt["start"] + len(excerpt["text"])
    result = []
    for unit in units:
        a, b = max(left, unit["text_start"]), min(right, unit["text_end"])
        if a >= b:
            continue
        row = dict(unit)
        span = unit["text_end"] - unit["text_start"]
        duration = unit["end_seconds"] - unit["start_seconds"]
        row.update(text_start=a, text_end=b, text=excerpt["text"][a-left:b-left],
                   start_seconds=unit["start_seconds"] + duration * (a-unit["text_start"]) / span,
                   end_seconds=unit["start_seconds"] + duration * (b-unit["text_start"]) / span,
                   timing_approximate=unit.get("timing_approximate", False) or a != unit["text_start"] or b != unit["text_end"])
        result.append(row)
    return result


def summary_fields(summary):
    """Read explicit fields and older reports whose heading was the source quote."""
    matches = list(ENTRY.finditer(summary))
    result = {}
    for index, match in enumerate(matches):
        body = summary[match.end():matches[index+1].start() if index+1 < len(matches) else len(summary)]
        values = {}
        for key, label in (("title", "Title"), ("quote", "Source quote|Opening sentence")):
            field = re.search(rf"(?im)(?:^|\n)[ \t*]*(?:{label})[ *]*:[ *]*(.+)", body)
            if field:
                values[key] = field.group(1).strip(' *\t\r\n"“”«»')
        if "quote" not in values:
            heading = body.splitlines()[0] if body.splitlines() else ""
            quoted = re.search(r'[“«"](.+?)[”»"]', heading)
            if quoted:
                values["quote"] = quoted.group(1)
        result[f"{match.group(1)}-{match.group(2)}"] = values
    return result


def normalize_rows(rows, excerpt):
    """Repair mechanical field swaps without asking the model to rewrite scenes."""
    from app.core.storyboard_conversation import quote_offsets
    fields = summary_fields(excerpt.get("summary", ""))
    normalized = []
    for index, raw in enumerate(rows if isinstance(rows, list) else []):
        row = dict(raw) if isinstance(raw, dict) else {}
        identifier = str(row.get("source_proposal_id") or "")
        known = fields.get(identifier, {})
        quote = str(row.get("start_quote") or "").strip()
        if not quote_offsets(excerpt["text"], quote):
            literal = known.get("quote", "")
            if literal and quote_offsets(excerpt["text"], literal):
                row["start_quote"] = literal
                row["anchor_recovery"] = "literal_quote_from_summary"
        if not str(row.get("title") or "").strip():
            row["title"] = known.get("title") or str(row.get("start_quote") or "").strip(' \t\r\n“”«»"')[:100] or f"Scene {identifier or str(excerpt['number']) + '-' + str(index+1)}"
            row["title_recovered"] = True
        normalized.append(row)
    return normalized


def ordered_quote_candidates(candidates):
    """Prune only positions excluded by every chronological assignment.

    Both preceding and following quotes constrain repetitions. Missing quotes
    remain unresolved; contradictory order never causes scenes to be sorted.
    """
    nonempty = [i for i, hits in enumerate(candidates) if hits]
    if not nonempty:
        return candidates
    forward, backward = {}, {}
    previous = None
    for i in nonempty:
        forward[i] = [h for h in candidates[i] if previous is None or any(p < h for p in previous)]
        previous = forward[i]
    following = None
    for i in reversed(nonempty):
        backward[i] = [h for h in candidates[i] if following is None or any(h < n for n in following)]
        following = backward[i]
    if not forward[nonempty[-1]]:
        return candidates
    return [[h for h in hits if h in forward.get(i, []) and h in backward.get(i, [])]
            for i, hits in enumerate(candidates)]


def validate_rows(rows, excerpt, units):
    from app.core.storyboard_conversation import quote_offsets
    aligned, invalid = [], []
    previous = -1
    used_ids = set()
    if not isinstance(rows, list) or not rows:
        return [], ["No scene proposals returned."]
    rows = normalize_rows(rows, excerpt)
    all_hits = [quote_offsets(excerpt["text"], str(r.get("start_quote") or "")) for r in rows]
    candidates = ordered_quote_candidates(all_hits)
    for index, raw in enumerate(rows):
        row = raw if isinstance(raw, dict) else {}
        title = str(row.get("title") or "").strip()
        quote = str(row.get("start_quote") or "").strip()
        hits = candidates[index]
        at = excerpt["start"] + hits[0] if len(hits) == 1 else -1
        unit = next((u for u in units if u["text_start"] <= at < u["text_end"]), None)
        if not title or len(hits) != 1 or at <= previous or unit is None:
            invalid.append(index)
            continue
        previous = at
        approximate = bool(unit.get("timing_approximate"))
        identifier = str(row.get("source_proposal_id") or "")
        if not re.fullmatch(rf"{excerpt['number']}-\d+", identifier) or identifier in used_ids:
            suffix = index + 1
            while f"{excerpt['number']}-{suffix}" in used_ids:
                suffix += 1
            identifier = f"{excerpt['number']}-{suffix}"
        used_ids.add(identifier)
        aligned.append({**row, "title": title, "start_quote": quote, "source_proposal_id": identifier,
                        "source_excerpt_id": str(excerpt["number"]), "unit": unit, "quote_offset": at,
                        "aligned_start_seconds": unit["start_seconds"],
                        "alignment_method": "estimated_sentence_in_cue" if approximate else "literal_quote_to_narration_cue",
                        "anchor_resolution": "ordered_context" if len(all_hits[index]) > 1 else "literal_quote",
                        "alignment_approximate": approximate})
    return aligned, invalid


def plan_excerpt(excerpt, units, request, instruction, warn, check, audit, cached=None):
    from app.core.storyboard_conversation import SCENE_SCHEMA
    from app.core.video_storyboard_planner import VideoStoryboardPlanningError
    units = local_units(excerpt, units)
    if not units:
        raise VideoStoryboardPlanningError(f"Fragment {excerpt['number']} has no narration timings.")
    rows = []
    entry = {"excerpt": excerpt["number"], "text_start": excerpt["start"],
             "text_end": excerpt["start"] + len(excerpt["text"]), "attempts": []}
    audit.append(entry)
    # Always revalidate cached anchors against the current source and timings.
    cached_rows = cached_candidates(cached) if cached else []
    rows = next((candidate for candidate in cached_rows if not validate_rows(candidate, excerpt, units)[1]), None)
    if rows is not None:
        entry["attempts"].append({"stage": "resume", "rows": deepcopy(rows)})
        warn(f"Fragment {excerpt['number']}: reused saved proposals after source validation.")
    else:
        rows = []
    for part in ([] if entry["attempts"] else summary_chunks(excerpt["summary"])):
        check()
        result = request(SCENE_SCHEMA, instruction +
            " The source quotation (Source quote, Opening sentence, or a quoted heading) belongs in start_quote. "
            "Image descriptions are NOT source quotes. If no title is provided, create a brief title from the image description; "
            "never leave title empty. Preserve source quotations in their original language.",
            part, "conversation: convert scene summary to JSON")
        values = result.get("scenes") if isinstance(result, dict) else None
        rows.extend(values if isinstance(values, list) else [{}])
    rows = normalize_rows(rows, excerpt)
    aligned, invalid = validate_rows(rows, excerpt, units)
    entry["attempts"].append({"stage": "conversion", "rows": deepcopy(rows), "invalid": invalid})
    if invalid:
        warn(f"Fragment {excerpt['number']}: invalid scene anchors; repairing only against this source passage.")
        positions = [i for i in invalid if isinstance(i, int)]
        bad = [{**(rows[i] if isinstance(rows[i], dict) else {}), "source_proposal_id": f"{excerpt['number']}-{i+1}"} for i in positions]
        if bad:
            check()
            result = request(SCENE_SCHEMA,
                "Repair only these invalid scene anchors using SOURCE_EXCERPT. Keep titles, labels and order unchanged. "
                "Copy a literal opening sentence for the same subject. If a quote is repeated, include the following "
                "source sentence(s) to identify the intended occurrence uniquely, keeping its starting position. "
                "If the subject is absent, leave its quote empty. "
                "Never borrow from another part of the book. Verified scenes are context only; do not return or modify them.",
                {"invalid_scenes": bad, "verified_scenes": [r for i, r in enumerate(rows) if i not in positions],
                 "SOURCE_EXCERPT": excerpt["text"]}, f"conversation: repair fragment {excerpt['number']} anchors")
            repaired = result.get("scenes", []) if isinstance(result, dict) else []
            candidate = deepcopy(rows)
            if isinstance(repaired, list) and len(repaired) == len(bad):
                for i, old, new in zip(positions, bad, repaired):
                    if (isinstance(new, dict) and new.get("title") == old.get("title")
                            and new.get("source_proposal_id") == old["source_proposal_id"] and new.get("start_quote")):
                            from app.core.storyboard_conversation import quote_offsets
                            trial = {**old, "start_quote": new["start_quote"]}
                            if quote_offsets(excerpt["text"], new["start_quote"]):
                                candidate[i] = trial
            entry["attempts"].append({"stage": "repair", "response": deepcopy(repaired)})
            aligned, invalid = validate_rows(candidate, excerpt, units)
            if not invalid:
                rows = candidate
    if invalid:
        # One bounded replan replaces the contaminated local summary. There is
        # no interpolation over unknown subjects and no book-wide repair loop.
        check()
        warn(f"Fragment {excerpt['number']}: replanning scenes from its narration after failed anchor validation.")
        result = request(SCENE_SCHEMA,
            "Propose a few chronological visual beats ONLY from SOURCE_EXCERPT. Cover its opening and main changes. "
            "Use short titles; copy each opening sentence verbatim into start_quote. Do not create a scene per sentence "
            "or use outside knowledge. Each quote must be unique in this passage and follow the previous quote. "
            "For repeated dialogue, include consecutive following sentences to disambiguate the occurrence. "
            f"Use labels {excerpt['number']}-1, {excerpt['number']}-2, etc. Return at most "
            f"{max(1, int((units[-1]['end_seconds']-units[0]['start_seconds']) / MINIMUM_BEAT_SECONDS))} scenes.",
            {"SOURCE_EXCERPT": excerpt["text"]}, f"conversation: replan fragment {excerpt['number']} from source")
        rows = result.get("scenes", []) if isinstance(result, dict) else []
        aligned, invalid = validate_rows(rows, excerpt, units)
        entry["attempts"].append({"stage": "replan", "rows": deepcopy(rows), "invalid": invalid})
    if invalid or not aligned:
        entry["status"] = "failed"
        raise VideoStoryboardPlanningError(
            f"Cannot align fragment {excerpt['number']} to its narration after local repair and replanning. "
            "Review its scene summary and source quotes. No compressed or invented scene timings were accepted.",
            details={"scene_alignment": deepcopy(audit)})
    entry["status"] = "validated"
    entry["validated_rows"] = [{k: r[k] for k in ("title", "start_quote", "source_proposal_id")} for r in aligned]
    if aligned[0]["unit"]["text_start"] > units[0]["text_start"]:
        aligned.insert(0, {"title": "Opening narration", "start_quote": units[0]["text"],
                          "unit": units[0], "quote_offset": units[0]["text_start"],
                          "aligned_start_seconds": units[0]["start_seconds"],
                          "source_excerpt_id": str(excerpt["number"]),
                          "source_proposal_id": f"{excerpt['number']}-opening",
                          "alignment_method": "preserved_excerpt_opening",
                          "alignment_approximate": bool(units[0].get("timing_approximate"))})
        warn(f"Fragment {excerpt['number']}: preserved omitted opening narration as its own visual beat.")
    return aligned


def cached_candidates(cached):
    """Recover both new checkpoints and legacy conversion/repair audit records."""
    if cached.get("validated_rows"):
        yield cached["validated_rows"]
    candidates, current = [], []
    for attempt in cached.get("attempts", []):
        if isinstance(attempt.get("rows"), list):
            current = deepcopy(attempt["rows"])
            candidates.append(deepcopy(current))
        elif attempt.get("stage") == "repair" and current:
            repairs = {r.get("source_proposal_id"): r for r in attempt.get("response", []) if isinstance(r, dict)}
            for row in current:
                if not isinstance(row, dict):
                    continue
                repair = repairs.get(row.get("source_proposal_id"), {})
                if repair.get("start_quote") and repair.get("title") == row.get("title"):
                    row["start_quote"] = repair["start_quote"]
            candidates.append(deepcopy(current))
    yield from (reversed(candidates) if cached.get("status") == "validated" else candidates)


def consolidate_beats(aligned, duration, warn):
    """Remove redundant boundaries, never move an anchor or extend the audiobook."""
    result = []
    for row in aligned:
        if result and row["aligned_start_seconds"] - result[-1]["aligned_start_seconds"] < MINIMUM_BEAT_SECONDS:
            result[-1].setdefault("consolidated_proposal_ids", []).append(row.get("source_proposal_id", ""))
        else:
            result.append(row)
    if len(result) > 1 and duration - result[-1]["aligned_start_seconds"] < MINIMUM_BEAT_SECONDS:
        last = result.pop()
        result[-1].setdefault("consolidated_proposal_ids", []).extend(
            [last.get("source_proposal_id", ""), *last.get("consolidated_proposal_ids", [])])
    removed = len(aligned) - len(result)
    if removed:
        warn(f"Consolidated {removed} scene boundaries less than {MINIMUM_BEAT_SECONDS:g}s apart; original narration times are unchanged.")
    return result


def frame_intervals(start, end, maximum, changes):
    """Split at actual continuity changes first, then evenly apply the time cap."""
    boundaries = sorted({start, end, *(t for t in changes if start < t < end)})
    intervals = []
    for a, b in zip(boundaries, boundaries[1:]):
        count = max(1, math.ceil((b-a) / maximum))
        intervals.extend((a+(b-a)*i/count, a+(b-a)*(i+1)/count) for i in range(count))
    return intervals
