"""Historical periods and their recurring positions in a conversational storyboard."""
from copy import deepcopy
import re


MODES = ("manual", "auto_single", "auto_multiple")
DISCOVERY = (
    "In which historical period do the events take place? Give a short English period name only "
    "(e.g. Mesolithic, Neolithic, Bronze Age, Iron Age, Middle Ages, Victorian era, 1920s). "
    "Do not include regions, locations, environments, plot summaries or activities in the name. "
    "Separately give optional visual context limited to period-specific clothing, architecture and technology; "
    "do not describe geography, scenery or events. Mark present-day passages as Present day, without visual context, "
    "including transitions back to the present. Do not mistake a historical Modern era for present day. Quote the exact opening "
    "sentence of every change, including returns to earlier periods. Distinguish stated facts from broad "
    "inferences; never invent precise dates. A date merely mentioned is not a change of setting. "
    "Reuse known period names. Mark an unknown setting explicitly. Write a short plain-text report."
)
STRUCTURE = (
    "Convert the reviewed period report into chronological era_events. Each event starts at an exact "
    "source sentence, copied into start_quote. Describe the period actually illustrated, not dates merely "
    "mentioned. Reuse known_id for the same period, even on a return or flashback; otherwise leave it empty. "
    "name is a short English historical period name only (e.g. Neolithic, Bronze Age, Victorian era, 1920s), "
    "without regions, locations, environments or narrative summaries. Set description equal to name. "
    "visual_context separately lists only period-specific clothing, architecture and technology, without geography or scenery. "
    "For present-day events set name and description to Present day, is_current to true and visual_context empty; "
    "keep transitions to the present so a previous historical period stops applying. Otherwise is_current is false. "
    "evidence quotes the source; basis is explicit, inferred or unknown. For an unknown setting leave "
    "name, description, visual_context and known_id empty. Preserve reviewed corrections. "
    "Do not invent source quotes, timestamps, precise years or additional events."
)


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


_STRING = {"type": "string"}
ERA_SCHEMA = _object({"era_events": {"type": "array", "items": _object({
    **{k: _STRING for k in ("known_id", "name", "description", "visual_context", "evidence", "start_quote")},
    "is_current": {"type": "boolean"},
    "basis": {"type": "string", "enum": ["explicit", "inferred", "unknown"]},
})}})


def era_mode(value):
    return value if value in MODES else "manual"


def period_name(record):
    return str(record.get("name") or record.get("description") or "").strip()


def is_current_period(record):
    name = period_name(record).casefold()
    return (record.get("is_current") is True or name in {
        "present day", "present-day", "today", "current era", "contemporary", "actualidad", "época actual"}
        or name.startswith(("present-day ", "present day ")))


def period_context(record, *, include_visual_context=True):
    if is_current_period(record):
        return ""
    name = period_name(record)
    visual = str(record.get("material_culture") or "").strip() if include_visual_context else ""
    return "; ".join(part for part in (name, visual) if part)


def scene_era_visual_context(plan, scene):
    if not (plan.get("narrative_context") or {}).get("era_visual_context", False):
        return ""
    overrides = scene.get("generation_overrides") or {}
    narrative = overrides.get("narrative") or {}
    if "era" in narrative:
        return ""
    identifier = str(scene.get("era_state_id") or "").strip().casefold()
    for record in (plan.get("continuity") or {}).get("eras", []):
        if str(record.get("id") or "").strip().casefold() == identifier and identifier:
            return "" if is_current_period(record) else str(record.get("material_culture") or "").strip()
    return ""


def scene_era(plan, scene, *, include_visual_context=False):
    """Resolve a scene's period without treating an automatic summary as a setting."""
    overrides = scene.get("generation_overrides") or {}
    override = overrides.get("narrative") if isinstance(overrides, dict) else None
    if isinstance(override, dict) and "era" in override:
        # An explicit blank also overrides the inherited period.
        return period_context({"name": override["era"]}, include_visual_context=False)
    continuity = plan.get("continuity")
    if not isinstance(continuity, dict):
        continuity = {}
    identifier = str(scene.get("era_state_id") or "").strip().casefold()
    if identifier:
        for record in continuity.get("eras", []):
            if isinstance(record, dict) and str(record.get("id") or "").strip().casefold() == identifier:
                if is_current_period(record):
                    return ""
                context = period_context(record, include_visual_context=include_visual_context)
                if context:
                    return context
                break
    if scene.get("era"):
        return period_context({"name": scene["era"]}, include_visual_context=False)
    narrative = plan.get("narrative_context")
    if not isinstance(narrative, dict):
        narrative = {}
    mode = narrative.get("era_mode") or continuity.get("era_mode")
    if str(mode).startswith("auto_") or narrative.get("era_source") == "detected":
        return ""
    return period_context({"name": narrative.get("era")}, include_visual_context=False)


def active_era(continuity, seconds):
    """Assignments allow the same period to recur, and explicit unknown gaps."""
    records = continuity.get("eras", [])
    if "era_assignments" in continuity:
        binding = next((a for a in reversed(continuity["era_assignments"])
                        if a["from_seconds"] <= seconds < a["to_seconds"]), {})
        return next((r for r in records if r["id"] == binding.get("era_id")), {})
    return next((r for r in reversed(records) if r.get("from_seconds", 0) <= seconds < r.get("to_seconds", 0)), {})


def era_boundaries(continuity):
    return {float(a["from_seconds"]) for a in continuity.get("era_assignments", continuity.get("eras", []))}


def revise_period(plan, identifier, description, visual_context, scene_ids, merge_into=""):
    """Apply an explicit user edit; keep frames and hand-edited prompts intact."""
    result = deepcopy(plan)
    continuity = result["continuity"]
    record = next(r for r in continuity["eras"] if r["id"] == identifier)
    target = next((r for r in continuity["eras"] if r["id"] == merge_into), None)
    record["description"] = description.strip()
    record["name"] = description.strip()
    record["is_current"] = is_current_period({"name": description.strip()})
    record["material_culture"] = visual_context.strip()
    record["reason"] = "user_review"
    selected = set(scene_ids)
    for scene in result.get("scenes", []):
        scene_id = str(scene.get("id") or scene.get("scene_id") or "")
        if scene_id in selected:
            scene["era_state_id"] = identifier
            scene["era"] = record["description"]
        elif scene.get("era_state_id") == identifier:
            scene["era_state_id"] = ""
            scene["era"] = ""
        if target and scene.get("era_state_id") == identifier:
            scene["era_state_id"] = target["id"]
            scene["era"] = period_name(target)
    if target:
        continuity["eras"].remove(record)
    bindings = []
    for scene in result.get("scenes", []):
        start = float(scene.get("start_seconds") or 0)
        end = start + float(scene.get("duration", scene.get("duration_seconds", 0)) or 0)
        era_id = scene.get("era_state_id", "")
        if bindings and bindings[-1]["era_id"] == era_id and abs(bindings[-1]["to_seconds"] - start) < .01:
            bindings[-1]["to_seconds"] = end
        else:
            bindings.append({"era_id": era_id, "from_seconds": start, "to_seconds": end})
    continuity["era_assignments"] = bindings
    continuity["era_mode"] = "auto_multiple"
    continuity["era_locked"] = False
    for record in continuity["eras"]:
        record["occurrences"] = [deepcopy(a) for a in bindings if a["era_id"] == record["id"]]
        record["from_seconds"] = min((a["from_seconds"] for a in record["occurrences"]), default=0)
        record["to_seconds"] = max((a["to_seconds"] for a in record["occurrences"]), default=0)
    from app.core.video_storyboard_planner import _resolved_narrative_context
    result["narrative_context"] = {**result.get("narrative_context", {}),
        **_resolved_narrative_context("", result.get("scenes", [])), "era_mode": "auto_multiple"}
    return result


def build_eras(reports, reviewed, original, mode, text, units, duration, request, warn, check):
    """Structure reviewed reports; only locally validated source anchors set timing."""
    from app.core.storyboard_conversation import quote_offsets, source_chunks
    from app.core.video_storyboard_planner import _normalized_entity_id
    records, events = [], []
    if reviewed != original:
        fragments = re.split(r"(?m)^PASSAGE (\d+)\s*\n", reviewed)
        numbered = list(zip(fragments[1::2], fragments[2::2]))
        if numbered and not fragments[0].strip() and all(1 <= int(i) <= len(reports) for i, _ in numbered):
            batches = [{**reports[int(i) - 1], "era_report": part}
                       for i, content in numbered for part in source_chunks(content, 6000) if part.strip()]
        else:
            batches = [{"era_report": part, "text": text, "start": 0}
                       for part in source_chunks(reviewed, 6000) if part.strip()]
    else:
        batches = [{**r, "era_report": part} for r in reports
                   for part in source_chunks(r.get("era_report", ""), 6000) if part.strip()]
    for index, report in enumerate(batches, 1):
        check()
        data = {"report": report["era_report"],
                "known_periods": [{k: r.get(k, "") for k in ("id", "name", "description")} for r in records]}
        # The report already contains literal source anchors. Validate them locally,
        # avoiding another full audiobook in every structuring request.
        instruction = STRUCTURE
        if mode == "auto_single":
            instruction += " Identify the common visual period. If the passage truly spans different periods, preserve them so the app can flag this conflict."
        result = request(ERA_SCHEMA, instruction, data, f"conversation: convert eras report {index}/{len(batches)} to JSON")
        for event in result.get("era_events", []):
            quote = str(event.get("start_quote") or "").strip()
            hits = quote_offsets(report["text"], quote) if quote else []
            if len(hits) != 1:
                warn(f"Historical period: skipped an ambiguous or missing source anchor: {quote[:100]}")
                continue
            position = report["start"] + hits[0]
            unit = next((u for u in units if u["text_start"] <= position < u["text_end"]), None)
            if not unit:
                warn(f"Historical period: no narration cue for {quote[:100]}")
                continue
            identifier = ""
            name = str(event.get("name") or "").strip()
            basis = str(event.get("basis") or "unknown")
            evidence = str(event.get("evidence") or "").strip()
            if basis == "explicit" and (not evidence or not quote_offsets(report["text"], evidence)):
                basis = "inferred"
                warn(f"Historical period {name}: source evidence could not be verified; marked as inferred for review.")
            if name and basis != "unknown":
                known = str(event.get("known_id") or "")
                record = next((r for r in records if (known and r["id"] == known)
                               or _normalized_entity_id(name, "era") == r["id"]
                               or name.casefold() == r["name"].casefold()), None)
                if not record:
                    record = {"id": _normalized_entity_id(name, "era"), "name": name,
                              "description": name,
                              "is_current": event.get("is_current") is True or is_current_period({"name": name}),
                              "material_culture": str(event.get("visual_context") or "").strip(),
                              "reason": basis, "evidence": evidence,
                              "occurrences": []}
                    records.append(record)
                identifier = record["id"]
            # The first period covers leading silence only when it belongs to the opening cue.
            seconds = 0.0 if unit is units[0] else float(unit["start_seconds"])
            events.append({"era_id": identifier, "from_seconds": seconds,
                           "source_unit_start": unit["id"], "start_quote": quote, "text_start": position})
    events.sort(key=lambda e: (e["from_seconds"], e["text_start"]))
    ordered = []
    for event in events:
        if ordered and event["from_seconds"] == ordered[-1]["from_seconds"]:
            if event["era_id"] != ordered[-1]["era_id"]:
                warn("Historical periods change inside one narration cue; using the later period at that cue boundary.")
            ordered[-1] = event
        elif not ordered or event["era_id"] != ordered[-1]["era_id"]:
            ordered.append(event)
    used = {e["era_id"] for e in ordered if e["era_id"]}
    records = [r for r in records if r["id"] in used]
    if mode == "auto_single":
        if len(records) > 1:
            warn("Several historical periods were identified. Choose automatic multiple periods; no single era has been forced onto the whole audiobook.")
            return [], []
        if records:
            ordered = [{**next(e for e in ordered if e["era_id"]), "from_seconds": 0.0}]
    for index, event in enumerate(ordered):
        event["to_seconds"] = min(duration, ordered[index + 1]["from_seconds"] if index + 1 < len(ordered) else duration)
        record = next((r for r in records if r["id"] == event["era_id"]), None)
        if record:
            record["occurrences"].append(deepcopy(event))
    for record in records:
        record["from_seconds"] = record["occurrences"][0]["from_seconds"]
        record["to_seconds"] = record["occurrences"][-1]["to_seconds"]
        record["source_unit_start"] = record["occurrences"][0]["source_unit_start"]
    if not records:
        warn("No historical period could be assigned confidently. Scenes without a period remain unrestricted.")
    return records, ordered
