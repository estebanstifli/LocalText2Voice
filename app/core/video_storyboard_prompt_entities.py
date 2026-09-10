from __future__ import annotations

import re
from typing import Any


def storyboard_prompt_entities(
    plan: dict[str, Any],
    collection: str,
) -> list[dict[str, Any]]:
    """Return named continuity entities suitable for prompt markup."""
    continuity = (
        plan.get("continuity", {})
        if isinstance(plan.get("continuity"), dict)
        else {}
    )
    return [
        value
        for value in continuity.get(collection, [])
        if isinstance(value, dict) and str(value.get("name") or "").strip()
    ]


def decorate_storyboard_prompt(text: str, plan: dict[str, Any]) -> str:
    """Prefix known canonical character and location names with ``@``."""
    result = str(text or "")
    names = {
        str(value.get("name") or "").strip()
        for collection in ("characters", "locations")
        for value in storyboard_prompt_entities(plan, collection)
        if str(value.get("name") or "").strip()
    }
    for name in sorted(names, key=len, reverse=True):
        result = re.sub(
            rf"(?<![\w@])({re.escape(name)})(?!\w)",
            r"@\1",
            result,
            flags=re.IGNORECASE,
        )
    return result


def strip_storyboard_prompt_markers(text: str, plan: dict[str, Any]) -> str:
    """Remove UI-only ``@`` markers from known continuity entities."""
    result = str(text or "")
    names = {
        str(value.get("name") or "").strip()
        for collection in ("characters", "locations")
        for value in storyboard_prompt_entities(plan, collection)
        if str(value.get("name") or "").strip()
    }
    for name in sorted(names, key=len, reverse=True):
        result = re.sub(
            rf"(?<!\w)@(?={re.escape(name)}(?!\w))",
            "",
            result,
            flags=re.IGNORECASE,
        )
    return result


def active_entity_state_id(
    entity: dict[str, Any],
    reference_time: float,
) -> str:
    active: list[dict[str, Any]] = []
    for state in entity.get("states", []):
        if not isinstance(state, dict) or not str(state.get("id") or "").strip():
            continue
        try:
            start = float(state.get("from_seconds", float("-inf")))
        except (TypeError, ValueError):
            start = float("-inf")
        try:
            end = float(state.get("to_seconds", float("inf")))
        except (TypeError, ValueError):
            end = float("inf")
        if start <= reference_time <= end:
            active.append(state)
    if not active:
        return ""
    selected = max(
        active,
        key=lambda value: _float_value(value.get("from_seconds"), 0.0),
    )
    return str(selected.get("id") or "").strip()


def canonical_location_state_ids_for_prompt(
    plan: dict[str, Any],
    scene: dict[str, Any],
    prompt: str,
) -> list[str]:
    """Resolve mentioned canonical location names to their active state IDs."""
    try:
        start = float(scene.get("start_seconds") or 0.0)
    except (TypeError, ValueError):
        start = 0.0
    try:
        duration = max(0.0, float(scene.get("duration_seconds") or 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    reference_time = start + duration / 2.0
    plain_prompt = strip_storyboard_prompt_markers(prompt, plan)
    result: list[str] = []
    occupied: list[tuple[int, int]] = []
    entities = sorted(
        storyboard_prompt_entities(plan, "locations"),
        key=lambda value: len(str(value.get("name") or "")),
        reverse=True,
    )
    for entity in entities:
        name = str(entity.get("name") or "").strip()
        match = re.search(
            rf"(?<!\w){re.escape(name)}(?!\w)",
            plain_prompt,
            flags=re.IGNORECASE,
        )
        if match is None or any(
            match.start() < end and match.end() > begin
            for begin, end in occupied
        ):
            continue
        occupied.append(match.span())
        state_id = active_entity_state_id(entity, reference_time)
        if state_id and state_id not in result:
            result.append(state_id)
    return result


def _float_value(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
