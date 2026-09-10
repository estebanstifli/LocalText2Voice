"""Small-model pipeline: discovery -> identities -> short visual profiles.

Provider responses deliberately do not expose the application's temporal-state
slots. This adapter translates simple lists into the existing ledger protocol.
"""
from copy import deepcopy
import json
import re


def obj(properties):
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def array(items):
    return {"type": "array", "items": items}


def rows(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def description(value):
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if text.casefold().strip(" .") in {"none", "unchanged", "unknown", "not specified", "same as before", "no change"}:
        return ""
    return text


TEXT = {"type": "string"}
DESCRIPTION = {"type": "string", "maxLength": 320}
UNIT = {"type": "integer"}


def registry_schema(characters=True):
    fields = {"entities": array(obj({"name": TEXT, "known_id": TEXT, "unit": UNIT,
                                     "aliases": array(TEXT)}))}
    if characters:
        fields["groups"] = array(obj({"name": TEXT, "unit": UNIT, "members": array(TEXT)}))
    return obj(fields)


def profiles_schema():
    return obj({
        "definitions": array(obj({"name": TEXT, "description": DESCRIPTION})),
        "changes": array(obj({"name": TEXT, "unit": UNIT, "description": DESCRIPTION, "evidence": TEXT})),
        "revelations": array(obj({"name": TEXT, "unit": UNIT, "description": DESCRIPTION, "evidence": TEXT})),
    })


def evidence_quote(value):
    return re.sub(r"^(?:Narration\s+)?Unit\s+\d+\s*:\s*", "", str(value or ""),
                  flags=re.I).strip(" '\"“”‘’.")


def current_profile(record):
    states = record.get("states", [])
    state = states[-1] if states else {}
    meta = state.get("visual_traits", {})
    if meta.get("profile_override") and meta.get("text") == state.get("description"):
        return str(state.get("description") or "")
    return ", ".join(filter(None, [str(record.get("identity_description") or ""),
                                   str(state.get("description") or "")]))


def validated_era_events(response, units, warnings):
    """Optional era metadata must never invalidate otherwise useful continuity.

    Literal evidence is necessary, not proof that the model understood the era.
    Do not repair stale IDs by assigning an arbitrary current narration unit.
    """
    events = response.get("era_events") if isinstance(response, dict) else None
    if not isinstance(events, list):
        warnings.append("era: ignored malformed era_events; no new era assigned.")
        return []
    lookup = {u["id"]: str(u["text"]) for u in units}
    accepted = []
    seen = set()
    for event in events:
        reason = ""
        if not isinstance(event, dict):
            reason = "malformed event"
        elif type(event.get("effective_unit_id")) is not int or event["effective_unit_id"] not in lookup:
            reason = "unit ID outside the current block"
        elif not isinstance(event.get("id"), str) or not re.fullmatch(r"[a-z0-9_]{1,80}", event["id"]):
            reason = "invalid event ID"
        elif any(not isinstance(event.get(k), str) for k in ("description", "material_culture", "evidence")):
            reason = "missing or invalid text fields"
        elif not description(event["description"]):
            reason = "unspecified period"
        elif any(len(event[k]) > limit for k, limit in (("description", 200), ("material_culture", 260), ("evidence", 220))):
            reason = "overlong text fields"
        elif len(event["material_culture"].split()) > 38 or len(event["evidence"].split()) > 32:
            reason = "overlong evidence or material culture"
        else:
            quote = evidence_quote(event["evidence"])
            if not quote or quote not in lookup[event["effective_unit_id"]]:
                reason = "evidence is not a quote from the referenced narration"
        if reason:
            warnings.append(f"era: ignored event ({reason}); no new era assigned.")
            continue
        item = {key: event[key] for key in ("id", "effective_unit_id", "description", "material_culture", "evidence")}
        item["evidence"] = quote
        fingerprint = (item["effective_unit_id"], item["description"], quote)
        if fingerprint not in seen:
            accepted.append(item)
            seen.add(fingerprint)
    return accepted


def run_simple(request, settings, schema, *, context, config, kwargs, instruction, contract_for):
    from app.core.video_storyboard_planner import (
        _entity_by_reference, _normalized_entity_id, VideoStoryboardPlanningError,
    )
    units, ledger, character_report, era = context
    narration = [{"unit_id": u["id"], "narration": u["text"]} for u in units]
    valid_units = {u["id"]: u for u in units}
    result = {"character_events": [], "location_events": [], "era_events": [], "groups": [],
              "_simple_reports": {}, "_simple_warnings": []}

    def call(stage, response_schema, payload):
        if kwargs.get("cancelled") and kwargs["cancelled"]():
            raise VideoStoryboardPlanningError("Storyboard analysis was cancelled.")
        policy = instruction(config, stage) + "\n" + contract_for(stage)
        if settings.get("analysis_choices", {}).get("plan") == "basic":
            policy += "\nUse one stable initial appearance per identity; return no temporal changes."
        if settings.get("reviewed_summary") is not None:
            payload = {**payload, "user_approved_summary": settings["reviewed_summary"]}
            policy += "\nUser-approved summaries take precedence for identity and appearance. Narration supplies time anchors."
        return request(settings, response_schema,
                       policy,
                       json.dumps(payload, ensure_ascii=False),
                       **{**kwargs, "request_label": kwargs.get("request_label", "continuity") + " / " + stage})

    for collection, report_stage, registry_stage, profile_stage in (
        ("characters", "characters", "character_registry", "character_appearance"),
        ("locations", "locations", "location_registry", "location_appearance"),
    ):
        character = collection == "characters"
        prefix = "char" if character else "loc"
        output = "character_events" if character else "location_events"
        known = [r for r in ledger.get(collection, []) if isinstance(r, dict)]
        known_names = [{"id": r["id"], "name": r["name"], "aliases": r.get("aliases", [])} for r in known]
        report = character_report if character else settings.get("reviewed_location_report")
        if report is None:
            report = call(report_stage, None, {
            "narration_units": narration, "known_places": known_names,
        })
        result["_simple_reports"][collection] = str(report)
        registry = call(registry_stage, registry_schema(character), {
            "report": str(report), "narration_units": narration, "known_entities": known_names,
            **({"known_groups": [{"name": g["name"], "states": g.get("states", [])} for g in ledger.get("groups", [])]} if character else {}),
        })
        selected = []
        for item in rows(registry.get("entities")):
            if not isinstance(item.get("unit"), int) or item["unit"] not in valid_units or not str(item.get("name") or "").strip():
                result["_simple_warnings"].append(f"{collection}: ignored an invalid identity reference.")
                continue
            name = str(item["name"]).strip()
            raw_id = str(item.get("known_id") or "").strip()
            existing = _entity_by_reference(known, raw_id, name)
            if raw_id and existing is None:
                result["_simple_warnings"].append(f"{collection}: unknown existing ID for {name}; ignored.")
                continue
            identifier = existing["id"] if existing else _normalized_entity_id(name, prefix)
            if any(s["id"] == identifier for s in selected):
                continue
            selected.append({"id": identifier, "name": existing["name"] if existing else name,
                             "aliases": list(dict.fromkeys([*(existing or {}).get("aliases", []),
                                 *[a for a in (item.get("aliases") or []) if isinstance(a, str)]])),
                             "unit": item["unit"], "is_new": existing is None,
                             "current_appearance": current_profile(existing) if existing else ""})
        if character:
            for group in rows(registry.get("groups")):
                if not isinstance(group.get("unit"), int) or group["unit"] not in valid_units or not isinstance(group.get("members"), list):
                    continue
                members = []
                raw_members = [name for name in group["members"] if isinstance(name, str)]
                for name in raw_members:
                    candidates = list({r["id"]: r for r in [*known, *selected]}.values())
                    record = _entity_by_reference(candidates, name)
                    if record and record["id"] not in members:
                        members.append(record["id"])
                if not members or len(members) != len(set(raw_members)) or len(raw_members) != len(group["members"]):
                    result["_simple_warnings"].append("Ignored a group with unresolved members.")
                    continue
                result["groups"].append({"id": _normalized_entity_id(group.get("name"), "group"),
                    "name": group.get("name", ""), "aliases": [], "member_ids": members,
                    "effective_unit_id": group["unit"]})
        if not selected:
            continue
        # Several entities per request, not one call for every character or line.
        for start in range(0, len(selected), 5):
            batch = selected[start:start + 5]
            profiles = call(profile_stage, profiles_schema(), {
                "entities": batch, "report": str(report), "narration_units": narration,
                "project_era": era,
            })
            definitions = {}
            for definition in rows(profiles.get("definitions")):
                if isinstance(definition, dict):
                    match = _entity_by_reference(batch, definition.get("name"))
                    if match and match["is_new"]:
                        definitions.setdefault(match["id"], description(definition.get("description")))
            for entity in batch:
                # Preserve aliases of existing characters without redefining them.
                result[output].append({
                    "id": entity["id"], "name": entity["name"], "aliases": entity["aliases"],
                    "effective_unit_id": entity["unit"],
                    "event_type": "first_appearance" if entity["is_new"] else "stable_revelation",
                    "identity_description": definitions.get(entity["id"], "") if entity["is_new"] else "",
                    "appearance": {}, "evidence": "",
                    "_simple_profile": definitions.get(entity["id"], "") if entity["is_new"] else "",
                })
            for field, event_type in (("changes", "explicit_change"), ("revelations", "stable_revelation")):
                for change in rows(profiles.get(field)):
                    if not isinstance(change, dict):
                        continue
                    entity = _entity_by_reference(batch, change.get("name"))
                    unit = valid_units.get(change["unit"]) if isinstance(change.get("unit"), int) else None
                    profile_text = description(change.get("description"))
                    quote = evidence_quote(change.get("evidence"))
                    if not entity or not unit or not profile_text or not quote or quote.casefold() not in unit["text"].casefold():
                        result["_simple_warnings"].append(f"{collection}: ignored a {field} entry with invalid source evidence.")
                        continue
                    if change["unit"] < entity["unit"] and entity["is_new"]:
                        result["_simple_warnings"].append(f"{collection}: ignored a change before the entity's introduction.")
                        continue
                    result[output].append({
                        "id": entity["id"], "name": entity["name"], "aliases": [],
                        "effective_unit_id": change["unit"], "event_type": event_type,
                        "identity_description": "", "appearance": {}, "evidence": quote,
                        "_simple_profile": profile_text,
                    })
    if config["era_mode"] == "detect" and not era and units and settings.get("analysis_choices", {}).get("plan") != "basic":
        era_schema = obj({"era_events": deepcopy(schema["properties"]["era_events"])})
        era_schema["properties"]["era_events"]["items"]["properties"]["effective_unit_id"] = {
            "type": "integer", "enum": list(valid_units)}
        response = call("era", era_schema, {
            "narration_units": narration,
            "known_eras": [{"id": row.get("id"), "description": row.get("description")}
                           for row in rows(ledger.get("eras"))],
        })
        result["era_events"] = validated_era_events(response, units, result["_simple_warnings"])
    return result


def merge_simple_profile(record, event, unit):
    """Apply whole short profiles without lexical slot guessing or concatenation."""
    profile = str(event.get("_simple_profile") or "").strip()
    states = record.setdefault("states", [])
    if record.get("user_authored"):
        return
    if not states:
        record["identity_description"] = profile or str(event.get("identity_description") or "")
        record["_simple_identity_snapshot"] = record["identity_description"]
        states.append({"id": f"{record['id']}_state_1", "description": "",
                       "from_seconds": float(unit.get("start_seconds") or 0), "to_seconds": None,
                       "source_unit_start": int(unit["id"]), "change_reason": "first_appearance",
                       "evidence": str(event.get("evidence") or "")})
        return
    if not profile or event.get("event_type") == "first_appearance":
        return
    current = states[-1]
    meta = current.get("visual_traits", {})
    if record.get("_simple_identity_snapshot") != record.get("identity_description"):
        return  # A user edited the identity.
    if current.get("description") and (not meta.get("profile_override") or meta.get("text") != current["description"]):
        return  # A user edited the state.
    quote = evidence_quote(event.get("evidence"))
    if not quote or quote.casefold() not in str(unit.get("text") or "").casefold():
        return
    if " ".join(profile.casefold().split()) == " ".join(current_profile(record).casefold().split()):
        return
    metadata = {"profile_override": True, "text": profile}
    if event.get("event_type") == "stable_revelation":
        if len(states) == 1 and not current.get("description"):
            record["identity_description"] = profile
            record["_simple_identity_snapshot"] = profile
        else:
            current.update(description=profile, visual_traits=metadata)
    elif float(unit.get("start_seconds") or 0) > float(current.get("from_seconds") or 0):
        states.append({"id": f"{record['id']}_state_{len(states) + 1}",
                       "description": profile, "visual_traits": metadata,
                       "from_seconds": float(unit["start_seconds"]), "to_seconds": None,
                       "source_unit_start": int(unit["id"]), "change_reason": "explicit_change", "evidence": quote})
    else:
        current.update(description=profile, visual_traits=metadata)
