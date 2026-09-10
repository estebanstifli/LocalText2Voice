"""Explicit appearance changes, anchored to narration rather than frame numbers."""
from copy import deepcopy


def build_character_states(records, reports, sections, text, units, duration, request, warn, check):
    from app.core.storyboard_conversation import _object, _array, STRING, quote_offsets, source_chunks
    from app.core.storyboard_entity_names import resolve

    schema = _object({"changes": _array(_object({
        "character": STRING, "start_quote": STRING, "visual_change": STRING,
        "kind": {"type": "string", "enum": ["clothing", "age", "hair", "physical"]},
    }))})
    portrait_schema = _object({"visual_description": STRING})
    events = []
    # Keep the extraction small and focused; no scene planning, locations or eras.
    original_summary = "\n\n".join(r.get("characters", "") for r in reports)
    original_appearance = "\n\n".join(r.get("appearance", "") for r in reports)
    edited = (sections.get("characters", "") != original_summary
              or sections.get("appearance", "") != original_appearance)
    for report in reports:
        summary = "\n\n".join(str(sections.get(k, "") if edited else report.get(k, ""))
                              for k in ("characters", "appearance"))
        for passage in source_chunks(report["text"], 6000):
            check()
            result = request(schema,
                "Find only actual changes to a named character's clothing, age, hair or lasting physical appearance. "
                "The summary is guidance; verify each change in the original passage. No moods, actions, possessions, "
                "newly revealed unchanged traits, hypothetical changes or initial descriptions. "
                "Copy a unique sentence from the passage where the changed appearance starts. "
                "Describe only the changed visual traits. Use supplied character names. Return changes: [] if none.",
                {"characters": [r["name"] for r in records], "summary": summary[:6000], "passage": passage},
                "conversation: explicit character changes")
            rows = result.get("changes") if isinstance(result, dict) else None
            if not isinstance(rows, list):
                warn("Character changes: invalid response; keeping existing appearances for manual review.")
                continue
            for row in rows:
                if not isinstance(row, dict):
                    warn("Character changes: ignored an invalid change.")
                    continue
                name, quote, change = (row.get(k) for k in ("character", "start_quote", "visual_change"))
                if not all(isinstance(v, str) and v.strip() for v in (name, quote, change)):
                    warn("Character changes: incomplete change; review manually.")
                    continue
                record = resolve(name, records)
                matches = quote_offsets(text, quote)
                if (record is None or len(matches) != 1 or not quote_offsets(passage, quote)
                        or row.get("kind") not in {"clothing", "age", "hair", "physical"}):
                    warn(f"Character change for {name}: ambiguous identity or source sentence; review manually.")
                    continue
                offset = matches[0]
                unit = next((u for u in units if u["text_start"] <= offset < u["text_end"]), None)
                if unit is None or not 0 < float(unit["start_seconds"]) < duration:
                    warn(f"Character change for {name}: no usable narration timing; review manually.")
                    continue
                events.append((offset, record, row, unit))
    # Apply in source order, regardless of LLM ordering. Group simultaneous changes
    # into one portrait to avoid overlapping/zero-length states.
    grouped = {}
    for offset, record, row, unit in sorted(events, key=lambda e: e[0]):
        key = (record["id"], float(unit["start_seconds"]))
        group = grouped.setdefault(key, (record, unit, []))
        if row not in group[2]:
            group[2].append(row)
    for (_, at), (record, unit, rows) in grouped.items():
        check()
        previous = record["states"][-1]
        if at <= previous["from_seconds"]:
            warn(f"{record['name']}: overlapping change left for manual review.")
            continue
        result = request(portrait_schema,
            "Update this storyboard portrait using ONLY the explicit visual changes supplied. "
            "Keep all unaffected traits exactly, including human hair length/color or baldness. "
            "Replace superseded traits; do not concatenate conflicting outfits or ages. "
            "Return one concise complete visual description, no actions, emotions or alternatives.",
            {"character": record["name"], "previous_portrait": previous["description"],
             "changes": [r["visual_change"] for r in rows]},
            "conversation: character state portrait")
        description = result.get("visual_description") if isinstance(result, dict) else None
        if not isinstance(description, str) or not description.strip():
            warn(f"{record['name']}: missing updated portrait; keeping previous appearance.")
            continue
        description = description.strip()
        if description.casefold() == previous["description"].strip().casefold():
            continue
        state = deepcopy(previous)
        state.update(id=f"{record['id']}_state_{len(record['states']) + 1}",
                     description=description, from_seconds=at, to_seconds=duration,
                     source_unit_start=unit["id"], change_reason="explicit_change",
                     evidence="\n".join(r["start_quote"] for r in rows),
                     origin="conversation_explicit_change")
        previous["to_seconds"] = at
        record["states"].append(state)
        if any(quote_offsets(text, r["start_quote"])[0] != unit["text_start"] for r in rows):
            warn(f"{record['name']}: change aligned to narration cue start ({at:.2f}s), not word-level timing.")
