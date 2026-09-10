"""Collective references are membership timelines, not extra visual identities."""
from __future__ import annotations

from typing import Any, Callable


def group_schema() -> dict[str, Any]:
    return {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "name", "aliases", "member_ids", "effective_unit_id"],
        "properties": {
            "id": {"type": "string", "maxLength": 80},
            "name": {"type": "string", "maxLength": 100},
            "aliases": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 100}},
            "member_ids": {"type": "array", "items": {"type": "string"}},
            "effective_unit_id": {"type": "integer"},
        },
    }}


def merge_groups(continuity: dict, events: object, units: dict,
                 resolve: Callable, normalize: Callable) -> None:
    if not isinstance(events, list):
        return
    groups = continuity.setdefault("groups", [])
    characters = continuity.get("characters", [])
    for event in sorted((e for e in events if isinstance(e, dict)),
                        key=lambda e: e.get("effective_unit_id") if isinstance(e.get("effective_unit_id"), int) else -1):
        unit_id = event.get("effective_unit_id")
        if not isinstance(unit_id, int) or unit_id not in units:
            continue
        raw_members = event.get("member_ids")
        if not isinstance(raw_members, list):
            continue
        members = []
        for raw in raw_members:
            member = resolve(characters, raw) or resolve(characters, normalize(raw, "char"))
            if member and member["id"] not in members:
                members.append(member["id"])
        if len(members) != len(set(str(m) for m in raw_members)):
            continuity.setdefault("warnings", []).append(
                f"Group {event.get('name') or event.get('id')}: unknown members; membership update ignored."
            )
            continue  # Never silently change a group into only some members.
        group = resolve(groups, event.get("id"), event.get("name"))
        if group is None:
            if not members or not str(event.get("name") or "").strip():
                continue
            group = {"id": normalize(event.get("id") or event["name"], "group"),
                     "name": event["name"], "aliases": [], "states": []}
            groups.append(group)
        group["aliases"] = list(dict.fromkeys([
            *group.get("aliases", []), str(event.get("id") or ""),
            *[a for a in event.get("aliases", []) if isinstance(a, str)],
        ]))
        start = float(units[unit_id].get("start_seconds") or 0.0)
        states = group["states"]
        if not states or states[-1]["member_ids"] != members:
            state = {"from_seconds": start, "to_seconds": None,
                     "source_unit_start": unit_id, "member_ids": members}
            if states and states[-1]["from_seconds"] == start:
                states[-1] = state
            else:
                states.append(state)
        # A collective introduction may precede individual names. Only the
        # members of THAT membership state are backdated, not later joiners.
        for character in characters:
            char_states = character.get("states", [])
            if character["id"] in members and char_states and not character.get("user_authored"):
                first = char_states[0]
                if start < float(first.get("from_seconds") or 0.0):
                    first.update(from_seconds=start, source_unit_start=unit_id)
        # Some small models redundantly return the same group as a character.
        # Remove only exact duplicate collective records, never a real member
        # or a user's definition. An anonymous crowd without known individuals
        # remains a legitimate collective visual identity.
        duplicate = resolve(characters, event.get("id"), event.get("name"))
        if duplicate and duplicate["id"] not in members and not duplicate.get("user_authored"):
            group["aliases"] = list(dict.fromkeys([*group["aliases"], duplicate["id"], *duplicate.get("aliases", [])]))
            characters.remove(duplicate)


def expand_group_reference(groups: list, raw: object, unit_id: int, resolve: Callable) -> list[str] | None:
    group = resolve(groups, raw)
    if group is None:
        return None
    active = [s for s in group.get("states", []) if isinstance(s.get("source_unit_start"), int)
              and s["source_unit_start"] <= unit_id]
    return list(max(active, key=lambda s: s["source_unit_start"]).get("member_ids", [])) if active else []
