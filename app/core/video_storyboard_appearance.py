"""Typed, language-independent appearance deltas for continuity analysis.

The model interprets narration; code only accepts slots for the right entity
type and replaces their values. Legacy free-text support is conservative and
never admits arbitrary actions merely because they quote the source.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.core.video_storyboard_visual_traits import visual_clauses, _key, _source_supports

CHARACTER_APPEARANCE = {
    "age": "Life stage or explicit age; never invent a precise age.",
    "wardrobe": "The current outfit, not an action or carried object.",
    "grooming": "A lasting hairstyle, facial hair or grooming change.",
    "body": "An explicitly described lasting visible bodily change or injury; not hunger, mood, exertion, temporary redness or an inferred injury.",
    "equipment": "Recurring worn/assistive equipment such as glasses or a wheelchair; not an absence of disability or a temporary prop.",
    "form": "Only a complete bodily transformation (e.g. a new species/form). Replaces baseline physical identity while active; otherwise null.",
}
LOCATION_APPEARANCE = {
    "structure": "Current layout/materials or permanent structural modification.",
    "condition": "Current physical condition, e.g. under construction, finished, damaged, restored. Replace the previous condition.",
    "decoration": "Lasting furnishings, decoration or vegetation; not occupants, events or passing weather.",
}


def appearance_schema(kind: str) -> dict[str, Any]:
    fields = CHARACTER_APPEARANCE if kind == "character" else LOCATION_APPEARANCE
    return {"type": "object", "additionalProperties": False, "required": list(fields),
            "properties": {key: {"type": ["string", "null"], "maxLength": 180,
                                 "description": description} for key, description in fields.items()}}


def appearance_values(value: object, kind: str) -> dict[str, str]:
    fields = CHARACTER_APPEARANCE if kind == "character" else LOCATION_APPEARANCE
    if not isinstance(value, dict):
        return {}
    return {key: item.strip(" .") for key, item in value.items()
            if key in fields and isinstance(item, str)}


def appearance_text(slots: dict[str, str]) -> str:
    return ", ".join(value for value in slots.values() if value)


def legacy_appearance(text: str, kind: str) -> dict[str, str]:
    """Compatibility for saved replies; output is English regardless of source.

    Unknown text is not evidence of a durable visual change. New models use
    explicit slots, so they do not depend on this vocabulary or on source language.
    """
    slots: dict[str, list[str]] = {}
    for clause in visual_clauses(text):
        key = _key(clause)
        slot = ""
        if kind == "character":
            if key == "age" or clause.casefold() in {"girl", "boy", "woman", "man", "puppy", "kitten"}: slot = "age"
            elif key in {"neckwear", "footwear", "bottom", "outfit", "outerwear", "top", "headwear"}: slot = "wardrobe"
            elif key == "hair": slot = "grooming"
            elif key in {"aid", "glasses"}: slot = "equipment"
        else:
            if re.search(r"\b(?:under construction|unfinished|newly built|finished|completed|intact|destroyed|collapsed|ruined|rebuilt|restored|flooded|burned)\b", clause, re.I):
                slot = "condition"
            elif key in {"roof", "door", "windows", "architecture"}:
                slot = "structure"
            elif key == "decoration": slot = "decoration"
        # Clause mentions an entity but describes its activity/occupancy, not
        # appearance. Do not promote these legacy sentences to physical slots.
        if re.search(r"^(?:has |have |had |living\b|lives\b|sheltering\b|occupied\b|the .*? (?:travel|work|live)\b)", clause, re.I):
            continue
        if slot:
            slots.setdefault(slot, []).append(clause)
    return {key: ", ".join(values) for key, values in slots.items()}


def normalize_event_appearance(event: dict[str, Any], kind: str) -> None:
    if isinstance(event.get("appearance"), dict):
        event["state_description"] = appearance_text(appearance_values(event["appearance"], kind))
    else:
        event["state_description"] = appearance_text(legacy_appearance(str(event.get("state_description") or ""), kind))


def merge_appearance(current: str, metadata: object, delta: object, *, kind: str,
                     initial: bool, changed: bool, source_backed: bool = False,
                     evidence: str = "") -> tuple[str, dict[str, Any]]:
    meta = metadata if isinstance(metadata, dict) else {}
    if isinstance(meta.get("slots"), dict) and meta.get("text") == current:
        slots = deepcopy(meta["slots"])
    elif "slots" in meta:
        # A UI edit wins over an automatic update. Do not silently reparse it.
        return current, {**deepcopy(meta), "text": current, "manual": True}
    else:
        slots = legacy_appearance(current, kind)
    if meta.get("manual"):
        return current, deepcopy(meta)
    origins = deepcopy(meta.get("origins", {}))
    for key, value in appearance_values(delta, kind).items():
        supported = source_backed or bool(value and any(
            _source_supports(_key(clause), clause, evidence) for clause in visual_clauses(value)
        ))
        if initial or (supported and (changed or not slots.get(key) or origins.get(key) == "inferred")):
            if value:
                slots[key] = value
            else:
                slots.pop(key, None)
            origins[key] = "source" if supported else "inferred"
    text = appearance_text(slots)
    return text, {"text": text, "slots": slots, "origins": origins}


def selected_appearance(record: dict[str, Any], state: dict[str, Any]) -> tuple[str, str]:
    identity = str(record.get("identity_description") or "").strip()
    description = str(state.get("description") or "").strip()
    meta = state.get("visual_traits", {})
    if isinstance(meta, dict) and meta.get("profile_override"):
        return description, ""
    if isinstance(meta, dict) and meta.get("text") == description and isinstance(meta.get("slots"), dict):
        slots = dict(meta["slots"])
        if slots.get("form"):
            identity = str(slots.pop("form"))
            description = appearance_text(slots)
    return identity, description
