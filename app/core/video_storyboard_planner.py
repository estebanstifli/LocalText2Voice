from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from app.core.video_storyboard_styles import (
    DEFAULT_STORYBOARD_STYLE_ID,
    normalize_storyboard_style_id,
    storyboard_style,
)
from app.core.video_storyboard_appearance import selected_appearance


class VideoStoryboardPlanningError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


CancelCallback = Callable[[], bool]
TraceCallback = Callable[[dict[str, Any]], None]

_MAX_PLANNING_BLOCK_SECONDS = 40.0
_MAX_PLANNING_BLOCK_CHARACTERS = 4000
_CONTINUITY_VERSION = 3

_DEFAULT_NEGATIVE = (
    "text, captions, subtitles, speech bubbles, logos, watermark, split screen, "
    "collage, comic panels, duplicate subject, multiple views, malformed hands, "
    "extra fingers, anatomical distortion"
)


def plan_video_storyboard(source, settings, *, progress=None, partial=None,
                          cancelled=None, trace=None, review=None):
    """Public entry point: scene-first conversational analysis."""
    from app.core.storyboard_conversation import plan_conversation
    return plan_conversation(source, settings, progress=progress, partial=partial,
                             cancelled=cancelled, trace=trace, review=review)


def _planning_batches(
    text: str,
    raw_cues: object,
    total_duration: float,
    settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    analysis = (
        settings.get("analysis", {})
        if isinstance(settings, dict)
        and isinstance(settings.get("analysis"), dict)
        else {}
    )
    try:
        max_block_seconds = max(
            10.0,
            min(1000.0, float(analysis.get("max_block_seconds") or _MAX_PLANNING_BLOCK_SECONDS)),
        )
    except (TypeError, ValueError):
        max_block_seconds = _MAX_PLANNING_BLOCK_SECONDS
    try:
        max_block_characters = max(
            1000,
            min(100000, int(analysis.get("max_block_characters") or _MAX_PLANNING_BLOCK_CHARACTERS)),
        )
    except (TypeError, ValueError):
        max_block_characters = _MAX_PLANNING_BLOCK_CHARACTERS
    cues = [
        cue
        for cue in raw_cues
        if isinstance(cue, dict) and str(cue.get("text") or "").strip()
    ] if isinstance(raw_cues, list) else []
    if not cues:
        chunks = _text_chunks(text, max_block_characters)
        total_characters = max(1, sum(len(chunk) for chunk in chunks))
        batches: list[dict[str, Any]] = []
        time_cursor = 0.0
        text_cursor = 0
        for chunk in chunks:
            duration = total_duration * len(chunk) / total_characters
            text_start = _find_text_offset(text, chunk, text_cursor)
            text_end = text_start + len(chunk)
            batches.append(
                {
                    "text": chunk,
                    "timings": [
                        {
                            "start": time_cursor,
                            "end": time_cursor + duration,
                            "text": chunk,
                            "text_start": text_start,
                            "text_end": text_end,
                        }
                    ],
                    "start_seconds": time_cursor,
                    "end_seconds": time_cursor + duration,
                    "duration_seconds": duration,
                }
            )
            time_cursor += duration
            text_cursor = text_end
        return batches

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    characters = 0
    group_start = 0.0
    for cue in cues:
        cue_start = max(0.0, float(cue.get("start_seconds") or 0.0))
        cue_end = max(
            cue_start,
            float(
                cue.get("end_seconds")
                or cue_start + float(cue.get("duration_seconds") or 0.0)
            ),
        )
        if not current:
            group_start = cue_start
        projected_duration = cue_end - group_start
        projected_characters = characters + len(str(cue.get("text") or ""))
        if current and (
            projected_duration > max_block_seconds
            or projected_characters > max_block_characters
        ):
            groups.append(current)
            current = []
            characters = 0
            group_start = cue_start
        current.append(cue)
        characters += len(str(cue.get("text") or ""))
    if current:
        groups.append(current)

    batches: list[dict[str, Any]] = []
    previous_end = 0.0
    text_cursor = 0
    for index, group in enumerate(groups):
        last = group[-1]
        last_start = max(0.0, float(last.get("start_seconds") or 0.0))
        batch_end = max(
            previous_end,
            float(
                last.get("end_seconds")
                or last_start + float(last.get("duration_seconds") or 0.0)
            ),
        )
        if index == len(groups) - 1:
            batch_end = max(batch_end, total_duration)
        timings: list[dict[str, Any]] = []
        for item in group:
            cue_text = str(item.get("text") or "")
            text_start = _find_text_offset(text, cue_text, text_cursor)
            text_end = text_start + len(cue_text)
            timings.append(
                {
                    "start": float(item.get("start_seconds") or 0.0),
                    "end": float(
                        item.get("end_seconds")
                        or float(item.get("start_seconds") or 0.0)
                        + float(item.get("duration_seconds") or 0.0)
                    ),
                    "text": cue_text,
                    "text_start": text_start,
                    "text_end": text_end,
                }
            )
            text_cursor = text_end
        batches.append(
            {
                "text": "\n".join(str(item.get("text") or "") for item in group),
                "timings": timings,
                "start_seconds": previous_end,
                "end_seconds": batch_end,
                "duration_seconds": max(1.0, batch_end - previous_end),
            }
        )
        previous_end = batch_end
    return batches


def _find_text_offset(text: str, fragment: str, cursor: int) -> int:
    if not fragment:
        return max(0, cursor)
    found = text.find(fragment, max(0, cursor))
    if found >= 0:
        return found
    found = text.casefold().find(fragment.casefold(), max(0, cursor))
    return found if found >= 0 else max(0, cursor)


def _semantic_units(
    batch: dict[str, Any],
    requested_count: int,
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for timing in batch.get("timings", []):
        if not isinstance(timing, dict):
            continue
        cue_text = str(timing.get("text") or "")
        if not cue_text.strip():
            continue
        cue_start = float(timing.get("start") or 0.0)
        cue_end = max(cue_start, float(timing.get("end") or cue_start))
        source_start = int(timing.get("text_start") or 0)
        cue_length = max(1, len(cue_text))
        for local_start, local_end in _sentence_spans(cue_text):
            fragment = cue_text[local_start:local_end]
            leading = len(fragment) - len(fragment.lstrip())
            trailing = len(fragment.rstrip())
            start = local_start + leading
            end = local_start + trailing
            if end <= start:
                continue
            units.append(
                {
                    "id": len(units),
                    "text": cue_text[start:end],
                    "start_seconds": cue_start
                    + (cue_end - cue_start) * start / cue_length,
                    "end_seconds": cue_start
                    + (cue_end - cue_start) * end / cue_length,
                    "text_start": source_start + start,
                    "text_end": source_start + end,
                }
            )
    if not units:
        batch_text = str(batch.get("text") or "")
        units = [
            {
                "id": 0,
                "text": batch_text,
                "start_seconds": float(batch.get("start_seconds") or 0.0),
                "end_seconds": float(batch.get("end_seconds") or 0.0),
                "text_start": 0,
                "text_end": len(batch_text),
            }
        ]
    while len(units) < requested_count:
        split_index = max(
            range(len(units)),
            key=lambda index: len(str(units[index].get("text") or "")),
        )
        split = _split_semantic_unit(units[split_index])
        if split is None:
            break
        units[split_index : split_index + 1] = list(split)
    for index, unit in enumerate(units):
        unit["id"] = index
        unit["start_seconds"] = round(float(unit["start_seconds"]), 3)
        unit["end_seconds"] = round(float(unit["end_seconds"]), 3)
    return units


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    boundaries = list(re.finditer(r"(?<=[.!?…])\s+|\n+", text))
    spans: list[tuple[int, int]] = []
    cursor = 0
    for boundary in boundaries:
        if boundary.start() > cursor:
            spans.append((cursor, boundary.start()))
        cursor = boundary.end()
    if cursor < len(text):
        spans.append((cursor, len(text)))
    return spans or [(0, len(text))]


def _split_semantic_unit(
    unit: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    text = str(unit.get("text") or "")
    if len(text.split()) < 2:
        return None
    midpoint = len(text) // 2
    spaces = [match.start() for match in re.finditer(r"\s+", text)]
    if not spaces:
        return None
    split_at = min(spaces, key=lambda value: abs(value - midpoint))
    right_start = split_at
    while right_start < len(text) and text[right_start].isspace():
        right_start += 1
    left_text = text[:split_at].rstrip()
    right_text = text[right_start:].strip()
    if not left_text or not right_text:
        return None
    ratio = right_start / max(1, len(text))
    time_start = float(unit["start_seconds"])
    time_end = float(unit["end_seconds"])
    time_split = time_start + (time_end - time_start) * ratio
    text_start = int(unit["text_start"])
    return (
        {
            **unit,
            "text": left_text,
            "end_seconds": time_split,
            "text_end": text_start + len(left_text),
        },
        {
            **unit,
            "text": right_text,
            "start_seconds": time_split,
            "text_start": text_start + right_start,
        },
    )


def _application_style_lock(
    style_mode: str,
    *,
    custom_style: str = "",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = normalize_storyboard_style_id(style_mode)
    catalog_style = storyboard_style(selected) or storyboard_style(
        "cinematic_realism"
    )
    medium = catalog_style.prompt if catalog_style is not None else "cinematic realism"
    if selected == "custom" and custom_style:
        medium = custom_style
    style: dict[str, Any] = {
        "medium": medium,
        "palette": "",
        "lighting": "",
        "characters": [],
        "groups": [],
        "negative": _DEFAULT_NEGATIVE,
    }
    if isinstance(overrides, dict):
        for key in ("medium", "palette", "lighting", "negative"):
            value = str(overrides.get(key) or "").strip()
            if value:
                style[key] = value
        if isinstance(overrides.get("characters"), list):
            style["characters"] = _string_list(overrides["characters"])
    return style


def _empty_continuity() -> dict[str, Any]:
    return {
        "version": _CONTINUITY_VERSION,
        "era_locked": False,
        "characters": [],
        "locations": [],
        "eras": [],
        "assignments": [],
        "discovery_reports": [],
    }


def _normalized_entity_id(value: object, prefix: str) -> str:
    raw_value = str(value or "").strip().casefold()
    ascii_value = unicodedata.normalize("NFKD", raw_value).encode(
        "ascii",
        "ignore",
    ).decode("ascii")
    identifier = re.sub(
        r"[^a-z0-9]+",
        "_",
        ascii_value,
    ).strip("_")
    if not identifier:
        identifier = "entity_" + hashlib.sha256(
            raw_value.encode("utf-8")
        ).hexdigest()[:10]
    return identifier if identifier.startswith(f"{prefix}_") else f"{prefix}_{identifier}"


def _seed_requested_era(
    continuity: dict[str, Any],
    era_request: str,
    total_duration: float,
) -> None:
    description = str(era_request or "").strip()
    if not description:
        return
    continuity["eras"] = [
        {
            "id": _normalized_entity_id(description, "era"),
            "description": description,
            "material_culture": "",
            "from_seconds": 0.0,
            "to_seconds": max(0.0, float(total_duration)),
            "source_unit_start": 0,
            "reason": "user_override",
            "evidence": "",
        }
    ]
    continuity["era_locked"] = True


def _unique_entity_id(
    records: list[dict[str, Any]],
    raw_id: object,
    name: object,
    prefix: str,
) -> str:
    base = _normalized_entity_id(raw_id or name, prefix) or f"{prefix}_unknown"
    existing = {str(record.get("id") or "") for record in records}
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _legacy_character_registry_from_continuity(
    continuity: dict[str, Any],
) -> dict[str, str]:
    registry: dict[str, str] = {}
    for character in continuity.get("characters", []):
        if not isinstance(character, dict):
            continue
        name = str(character.get("name") or "").strip()
        identity = str(character.get("identity_description") or "").strip()
        for state in character.get("states", []):
            if not isinstance(state, dict):
                continue
            state_id = str(state.get("id") or "").strip()
            identity, appearance = selected_appearance(character, state)
            description = ", ".join(
                value for value in (
                    name,
                    identity,
                    appearance,
                ) if value
            )
            if state_id and description:
                registry[state_id] = f"{state_id}: {description}"
    return registry


def _resolved_narrative_context(
    requested_era: str,
    scenes: list[dict[str, Any]],
) -> dict[str, Any]:
    requested = str(requested_era or "").strip()
    if requested:
        return {"era": requested, "era_source": "user"}

    detected: list[str] = []
    seen: set[str] = set()
    for scene in scenes:
        era = str(scene.get("era") or "").strip()
        key = era.casefold()
        if era and key not in seen:
            seen.add(key)
            detected.append(era)
    if not detected:
        return {
            "era": "",
            "era_source": "detected",
            "scene_eras": [],
        }
    era = (
        detected[0]
        if len(detected) == 1
        else "Multiple periods: " + "; ".join(detected)
    )
    return {
        "era": era,
        "era_source": "detected",
        "scene_eras": detected,
    }


def _text_chunks(text: str, limit: int) -> list[str]:
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    if not paragraphs:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 1 > limit:
            chunks.append(current)
            current = ""
        if len(paragraph) > limit:
            while len(paragraph) > limit:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(paragraph[:limit])
                paragraph = paragraph[limit:]
        current = f"{current}\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks or [text]


def _request_free_text(
    settings: dict[str, Any],
    system: str,
    user: str,
    *,
    trace: TraceCallback | None = None,
    cancelled: CancelCallback | None = None,
    request_label: str = "",
    output_tokens_hint: int | None = None,
    work_item_count_hint: int | None = None,
    work_item_type_hint: str = "",
    messages: list[dict[str, str]] | None = None,
) -> str:
    """Request an unconstrained narrative analysis with the configured provider."""
    provider = str(settings.get("llm_provider") or "ollama")
    requested_tokens = max(512, int(output_tokens_hint or 1400))
    analysis = settings.get("analysis", {})
    if isinstance(analysis, dict) and analysis.get("max_output_tokens") is not None:
        try:
            requested_tokens = max(
                requested_tokens,
                min(131072, int(analysis.get("max_output_tokens") or 0)),
            )
        except (TypeError, ValueError):
            pass
    if provider == "ollama":
        config = settings.get("ollama", {})
        root = _normalize_url(config.get("base_url"), "http://127.0.0.1:11434")
        model = str(config.get("model") or "").strip()
        if not model:
            raise VideoStoryboardPlanningError("No Ollama model is configured.")
        payload = {
            "model": model,
            "stream": trace is not None,
            "think": True,
            "messages": ([{"role": "user", "content": system + "\n\n" + user}]
                         if request_label.startswith("continuity discovery") else [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]),
            "options": {
                "temperature": 0.2,
                "repeat_penalty": 1.08,
                "repeat_last_n": 1024,
                "num_ctx": min(
                    32768,
                    max(4096, int(config.get("context_length") or 8192)),
                ),
                "num_predict": requested_tokens,
            },
            "keep_alive": "10m",
        }
        if messages is not None:
            payload["messages"] = deepcopy(messages)
            # Match the successful interactive chat: use the model's sampling
            # defaults, not the old JSON-planning repetition/temperature policy.
            for key in ("temperature", "repeat_penalty", "repeat_last_n"):
                payload["options"].pop(key, None)
        endpoint = f"{root}/api/chat"
        if trace:
            trace(
                {
                    "kind": "request",
                    "provider": "ollama",
                    "endpoint": endpoint,
                    "model": model,
                    "label": request_label,
                    "attempt": 1,
                    "work_item_count": work_item_count_hint or 1,
                    "work_item_type": work_item_type_hint or "analysis block",
                    "output_tokens": requested_tokens,
                    "response_mode": "plain_text",
                    "prompt": user,
                    "raw_request": payload,
                }
            )
        try:
            response = (
                _http_ollama_stream(
                    endpoint,
                    payload,
                    float(config.get("timeout_seconds") or 300),
                    trace,
                    cancelled,
                )
                if trace
                else _http_json(
                    endpoint,
                    payload,
                    float(config.get("timeout_seconds") or 300),
                )
            )
        except VideoStoryboardPlanningError as exc:
            if trace:
                _emit_provider_error(
                    trace,
                    provider="ollama",
                    label=request_label,
                    endpoint=endpoint,
                    error=exc,
                )
            raise
        content = str(response.get("message", {}).get("content", ""))
    elif provider == "litellm":
        config = settings.get("litellm", {})
        root = str(config.get("base_url") or "").strip().rstrip("/")
        model = str(config.get("model") or "").strip()
        if not model:
            raise VideoStoryboardPlanningError("No LiteLLM model is configured.")
        try:
            configured_tokens = int(config.get("max_output_tokens") or 16000)
        except (TypeError, ValueError):
            configured_tokens = 16000
        output_tokens = min(max(512, configured_tokens), requested_tokens)
        api_key = str(config.get("api_key") or "").strip()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        payload = {
            "model": model,
            "stream": trace is not None,
            "instructions": system,
            "input": user,
            "max_output_tokens": output_tokens,
        }
        if messages is not None:
            payload.pop("instructions", None)
            payload["input"] = deepcopy(messages)
        endpoint = f"{root}/responses" if root else "LiteLLM Python SDK (direct provider)"
        if trace:
            trace(
                {
                    "kind": "request",
                    "provider": "litellm",
                    "endpoint": endpoint,
                    "transport": "http" if root else "sdk",
                    "model": model,
                    "label": request_label,
                    "attempt": 1,
                    "work_item_count": work_item_count_hint or 1,
                    "work_item_type": work_item_type_hint or "analysis block",
                    "output_tokens": output_tokens,
                    "response_mode": "plain_text",
                    "prompt": user,
                    "raw_request": payload,
                    "authorization_header": (
                        "present but deliberately omitted from logs"
                        if api_key
                        else "not configured"
                    ),
                }
            )
        timeout = float(config.get("timeout_seconds") or 300)
        try:
            response = (
                _http_litellm_responses_stream(
                    f"{root}/responses",
                    payload,
                    timeout,
                    headers,
                    trace,
                    cancelled,
                )
                if root
                else _litellm_direct_responses(
                    payload,
                    api_key=api_key,
                    timeout=timeout,
                    trace=trace,
                    cancelled=cancelled,
                )
            )
        except VideoStoryboardPlanningError as exc:
            if not _responses_api_is_unsupported(exc):
                if trace:
                    _emit_provider_error(
                        trace,
                        provider="litellm",
                        label=request_label,
                        endpoint=endpoint,
                        error=exc,
                    )
                raise
            chat_payload = {
                **({"stream_options": {"include_usage": True}} if trace is not None else {}),
                "model": model,
                "stream": trace is not None,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": output_tokens,
            }
            if messages is not None:
                chat_payload["messages"] = deepcopy(messages)
            response = (
                _http_litellm_stream(
                    f"{root}/chat/completions",
                    chat_payload,
                    timeout,
                    headers,
                    trace,
                    cancelled,
                )
                if root
                else _litellm_direct_completion(
                    chat_payload,
                    api_key=api_key,
                    timeout=timeout,
                    trace=trace,
                    cancelled=cancelled,
                )
            )
        content = _normalized_content(_normalize_litellm_response(response))
    else:
        raise VideoStoryboardPlanningError(f"Unsupported LLM provider: {provider}")
    if trace:
        trace(
            {
                "kind": "raw_response",
                "provider": provider,
                "label": request_label,
                "raw_response": response,
            }
        )
    if response.get("done_reason") == "length" or response.get("status") == "incomplete" or any(
        choice.get("finish_reason") == "length" for choice in response.get("choices", [])
    ):
        raise VideoStoryboardPlanningError(
            "The analysis response reached its output-token limit. Increase the token budget and retry."
        )
    if not content.strip():
        raise VideoStoryboardPlanningError(
            "The LLM returned an empty plain-text continuity analysis."
        )
    if trace:
        trace(
            {
                "kind": "response",
                "label": request_label,
                "characters": len(content),
                "response_mode": "plain_text",
            }
        )
    return content.strip()


def _request_plan(
    settings: dict[str, Any],
    schema: dict[str, Any] | None,
    system: str,
    user: str,
    *,
    trace: TraceCallback | None = None,
    cancelled: CancelCallback | None = None,
    request_label: str = "",
    output_tokens_hint: int | None = None,
    work_item_count_hint: int | None = None,
    work_item_type_hint: str = "",
) -> dict[str, Any] | str:
    if settings.get("story_context"):
        system += ("\nOverall story context (orientation only, not evidence of who appears in this unit; "
                   "do not move later events into the current scene):\n" + str(settings["story_context"]))
    if schema is None:
        return _request_free_text(
            settings,
            system,
            user,
            trace=trace,
            cancelled=cancelled,
            request_label=request_label,
            output_tokens_hint=output_tokens_hint,
            work_item_count_hint=work_item_count_hint,
            work_item_type_hint=work_item_type_hint,
        )
    scene_count = int(
        schema.get("properties", {})
        .get("scenes", {})
        .get("minItems", 1)
        or 1
    )
    unit_count = int(
        schema.get("properties", {})
        .get("unit_bindings", {})
        .get("minItems", 0)
        or 0
    )
    work_item_count = work_item_count_hint or unit_count or scene_count
    work_item_type = work_item_type_hint or (
        "narration units" if unit_count else "fixed scenes"
    )
    output_tokens = (
        max(512, int(output_tokens_hint))
        if output_tokens_hint is not None
        else min(2400, max(800, 320 + scene_count * 260))
    )
    analysis = settings.get("analysis", {})
    if isinstance(analysis, dict) and analysis.get("max_output_tokens") is not None:
        try:
            output_tokens = max(
                output_tokens,
                min(131072, int(analysis.get("max_output_tokens") or 0)),
            )
        except (TypeError, ValueError):
            pass
    provider = str(settings.get("llm_provider") or "ollama")
    if provider == "ollama":
        config = settings.get("ollama", {})
        root = _normalize_url(config.get("base_url"), "http://127.0.0.1:11434")
        model = str(config.get("model") or "").strip()
        if not model:
            raise VideoStoryboardPlanningError("No Ollama model is configured.")
        last_content = ""
        last_reason = ""
        for attempt in range(2):
            attempt_label = (
                request_label
                if attempt == 0
                else f"{request_label} compact retry"
            )
            attempt_user = user
            if attempt:
                attempt_user += (
                    "\n\nCOMPACT RECOVERY: Generate the JSON again from scratch. "
                    "The previous response was invalid or repetitive. Use short, "
                    "non-repeating clauses and close every JSON object and array."
                )
            payload = {
                "model": model,
                "stream": trace is not None,
                # Reasoning competes with and destabilizes constrained JSON on
                # small local models. The JSON itself remains streamed live.
                "think": True,
                "format": schema,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": attempt_user},
                ],
                "options": {
                    "temperature": 0.15 if attempt == 0 else 0.05,
                    "repeat_penalty": 1.12 if attempt == 0 else 1.2,
                    "repeat_last_n": 2048,
                    "num_ctx": min(
                        32768,
                        max(4096, int(config.get("context_length") or 8192)),
                    ),
                    "num_predict": output_tokens,
                },
                "keep_alive": "10m",
            }
            if trace:
                trace(
                    {
                        "kind": "request",
                        "provider": "ollama",
                        "endpoint": f"{root}/api/chat",
                        "model": model,
                        "label": attempt_label,
                        "attempt": attempt + 1,
                        "scene_count": scene_count,
                        "work_item_count": work_item_count,
                        "work_item_type": work_item_type,
                        "output_tokens": output_tokens,
                        "prompt": attempt_user,
                        "raw_request": payload,
                    }
                )
                try:
                    response = _http_ollama_stream(
                        f"{root}/api/chat",
                        payload,
                        float(config.get("timeout_seconds") or 300),
                        trace,
                        cancelled,
                    )
                except VideoStoryboardPlanningError as exc:
                    _emit_provider_error(
                        trace,
                        provider="ollama",
                        label=attempt_label,
                        endpoint=f"{root}/api/chat",
                        error=exc,
                    )
                    raise
            else:
                response = _http_json(
                    f"{root}/api/chat",
                    payload,
                    float(config.get("timeout_seconds") or 300),
                )
            if trace:
                trace(
                    {
                        "kind": "raw_response",
                        "provider": "ollama",
                        "label": attempt_label,
                        "raw_response": response,
                    }
                )
            content = response.get("message", {}).get("content", "")
            last_content = str(content)
            last_reason = str(response.get("done_reason") or "")
            try:
                value = json.loads(last_content)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict):
                if trace:
                    trace(
                        {
                            "kind": "response",
                            "label": attempt_label,
                            "characters": len(last_content),
                        }
                    )
                return value
            if attempt == 0 and trace:
                trace(
                    {
                        "kind": "retry",
                        "label": request_label,
                        "reason": last_reason or "invalid JSON",
                    }
                )
        reason = (
            "the model exhausted its output limit"
            if last_reason == "length"
            else "the model produced incomplete or repetitive output"
        )
        raise VideoStoryboardPlanningError(
            "Ollama could not produce valid structured JSON after an automatic "
            f"compact retry because {reason}. Try the analysis again or choose "
            "a stronger Ollama model."
        )
    elif provider == "litellm":
        config = settings.get("litellm", {})
        try:
            output_tokens = int(config.get("max_output_tokens") or 16000)
        except (TypeError, ValueError):
            output_tokens = 16000
        output_tokens = max(512, min(output_tokens, 131072))
        root = str(config.get("base_url") or "").strip().rstrip("/")
        model = str(config.get("model") or "").strip()
        if not model:
            raise VideoStoryboardPlanningError("No LiteLLM model is configured.")
        headers = {}
        api_key = str(config.get("api_key") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "stream": trace is not None,
            "instructions": system,
            "input": user,
            "max_output_tokens": output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "video_storyboard_plan",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        if trace:
            endpoint = (
                f"{root}/responses"
                if root
                else "LiteLLM Python SDK (direct provider)"
            )
            trace(
                {
                    "kind": "request",
                    "provider": "litellm",
                    "endpoint": endpoint,
                    "transport": "http" if root else "sdk",
                    "model": model,
                    "label": request_label,
                    "attempt": 1,
                    "scene_count": scene_count,
                    "work_item_count": work_item_count,
                    "work_item_type": work_item_type,
                    "output_tokens": output_tokens,
                    "prompt": user,
                    "raw_request": payload,
                    "authorization_header": (
                        "present but deliberately omitted from logs"
                        if api_key
                        else "not configured"
                    ),
                }
            )
        timeout = float(config.get("timeout_seconds") or 300)
        try:
            if root:
                response = _http_litellm_responses_stream(
                    f"{root}/responses",
                    payload,
                    timeout,
                    headers,
                    trace,
                    cancelled,
                )
            else:
                response = _litellm_direct_responses(
                    payload,
                    api_key=api_key,
                    timeout=timeout,
                    trace=trace,
                    cancelled=cancelled,
                )
        except VideoStoryboardPlanningError as exc:
            if not _responses_api_is_unsupported(exc):
                if trace:
                    _emit_provider_error(
                        trace,
                        provider="litellm",
                        label=request_label,
                        endpoint=(
                            f"{root}/responses"
                            if root
                            else "LiteLLM Python SDK (direct provider)"
                        ),
                        error=exc,
                    )
                raise
            chat_payload = {
                **({"stream_options": {"include_usage": True}} if trace is not None else {}),
                "model": model,
                "stream": trace is not None,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": output_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "video_storyboard_plan",
                        "strict": True,
                        "schema": schema,
                    },
                },
            }
            fallback_label = f"{request_label} (Chat Completions fallback)"
            if trace:
                trace(
                    {
                        "kind": "retry",
                        "label": fallback_label,
                        "reason": "The selected provider does not support the Responses API.",
                    }
                )
                trace(
                    {
                        "kind": "request",
                        "provider": "litellm",
                        "endpoint": (
                            f"{root}/chat/completions" if root else "LiteLLM Python SDK (Chat Completions fallback)"
                        ),
                        "transport": "http" if root else "sdk",
                        "model": model,
                        "label": fallback_label,
                        "attempt": 2,
                        "scene_count": scene_count,
                        "work_item_count": work_item_count,
                        "work_item_type": work_item_type,
                        "output_tokens": output_tokens,
                        "prompt": user,
                        "raw_request": chat_payload,
                    }
                )
            try:
                if root:
                    response = _http_litellm_stream(
                        f"{root}/chat/completions",
                        chat_payload,
                        timeout,
                        headers,
                        trace,
                        cancelled,
                    )
                else:
                    response = _litellm_direct_completion(
                        chat_payload,
                        api_key=api_key,
                        timeout=timeout,
                        trace=trace,
                        cancelled=cancelled,
                    )
            except VideoStoryboardPlanningError as fallback_exc:
                if trace:
                    _emit_provider_error(
                        trace,
                        provider="litellm",
                        label=fallback_label,
                        endpoint=(f"{root}/chat/completions" if root else "LiteLLM Python SDK"),
                        error=fallback_exc,
                    )
                raise
        if trace:
            trace(
                {
                    "kind": "raw_response",
                    "provider": "litellm",
                    "label": request_label,
                    "raw_response": response,
                }
            )
        choices = response.get("choices", [])
        content = choices[0].get("message", {}).get("content", "") if choices else ""
    else:
        raise VideoStoryboardPlanningError(f"Unsupported LLM provider: {provider}")
    if not str(content).strip():
        raise VideoStoryboardPlanningError("The LLM returned an empty response.")
    choices = response.get("choices", []) if isinstance(response, dict) else []
    finish_reason = (
        str(choices[0].get("finish_reason") or "")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else ""
    )
    try:
        value = json.loads(str(content))
    except json.JSONDecodeError as exc:
        if finish_reason == "length":
            raise VideoStoryboardPlanningError(
                "The LLM exhausted its maximum output tokens before completing the structured response. Increase Maximum output tokens in Settings or use a model with a larger output limit."
            ) from exc
        raise VideoStoryboardPlanningError(
            "The LLM did not return valid structured JSON."
        ) from exc
    if not isinstance(value, dict):
        raise VideoStoryboardPlanningError("The LLM response is not a JSON object.")
    if trace:
        if provider != "litellm":
            trace({"kind": "content", "text": str(content)})
        trace(
            {
                "kind": "response",
                "label": request_label,
                "characters": len(str(content)),
            }
        )
    return value


def _responses_api_is_unsupported(error: VideoStoryboardPlanningError) -> bool:
    details = error.details if isinstance(error.details, dict) else {}
    try:
        status_code = int(details.get("status_code") or 0)
    except (TypeError, ValueError):
        status_code = 0
    if status_code in {404, 405, 501}:
        return True
    message = f"{error} {details.get('body', '')}".casefold()
    return (
        ("responses" in message or "/responses" in message)
        and any(token in message for token in ("unsupported", "not supported", "not found"))
    )


def _litellm_direct_completion(
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: float,
    trace: TraceCallback | None = None,
    cancelled: CancelCallback | None = None,
) -> dict[str, Any]:
    try:
        from litellm import completion
    except ImportError as exc:
        raise VideoStoryboardPlanningError(
            "The LiteLLM Python SDK is not installed. Run run_dev.bat again "
            "to install the updated application dependencies."
        ) from exc
    arguments = dict(payload)
    arguments["timeout"] = timeout
    # LiteLLM removes unsupported optional parameters for providers that do
    # not expose OpenAI-compatible structured-output controls. The system
    # prompt still requires strict JSON and the response is validated below.
    arguments["drop_params"] = True
    if api_key:
        arguments["api_key"] = api_key
    try:
        response = completion(**arguments)
    except Exception as exc:  # LiteLLM normalizes provider-specific errors.
        raise VideoStoryboardPlanningError(
            f"LiteLLM direct request failed: {exc}",
            details=_exception_details(exc),
        ) from exc
    if trace is not None:
        return _collect_litellm_stream(response, trace, cancelled)
    if isinstance(response, dict):
        return response
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        value = model_dump()
        if isinstance(value, dict):
            return value
    try:
        return dict(response)
    except (TypeError, ValueError) as exc:
        raise VideoStoryboardPlanningError(
            "LiteLLM returned an unsupported response object."
        ) from exc


def _litellm_direct_responses(
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: float,
    trace: TraceCallback | None = None,
    cancelled: CancelCallback | None = None,
) -> dict[str, Any]:
    try:
        from litellm import responses
    except ImportError as exc:
        raise VideoStoryboardPlanningError(
            "The LiteLLM Python SDK is not installed. Run run_dev.bat again."
        ) from exc
    arguments = dict(payload)
    arguments["timeout"] = timeout
    arguments["drop_params"] = True
    if api_key:
        arguments["api_key"] = api_key
    try:
        response = responses(**arguments)
    except Exception as exc:
        raise VideoStoryboardPlanningError(
            f"LiteLLM Responses request failed: {exc}",
            details=_exception_details(exc),
        ) from exc
    if trace is None:
        return _normalize_litellm_response(_response_object_dict(response))
    return _collect_litellm_responses_stream(response, trace, cancelled)


def _http_litellm_responses_stream(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    headers: dict[str, str],
    trace: TraceCallback | None,
    cancelled: CancelCallback | None,
) -> dict[str, Any]:
    if trace is None:
        return _normalize_litellm_response(_http_json(url, payload, timeout, headers))
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **headers,
        },
        method="POST",
    )
    events: list[dict[str, Any]] = []
    emitted_text = ""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                if cancelled and cancelled():
                    raise VideoStoryboardPlanningError(
                        "Storyboard analysis was cancelled."
                    )
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or line.startswith("event:"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise VideoStoryboardPlanningError(
                        "The LiteLLM Responses endpoint returned an invalid streaming event."
                    ) from exc
                if isinstance(event, dict):
                    events.append(event)
                    emitted_text = _emit_litellm_response_event(
                        event, trace, emitted_text
                    )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:10000]
        raise VideoStoryboardPlanningError(
            f"LiteLLM Responses endpoint returned HTTP {exc.code}: {detail}",
            details={"type": "HTTPError", "status_code": exc.code, "body": detail},
        ) from exc
    except urllib.error.URLError as exc:
        raise VideoStoryboardPlanningError(
            f"Cannot connect to the LLM service: {getattr(exc, 'reason', exc)}",
            details={"type": "URLError", "message": str(getattr(exc, 'reason', exc))},
        ) from exc
    return _combined_litellm_response_events(events)


def _collect_litellm_responses_stream(
    response: object,
    trace: TraceCallback,
    cancelled: CancelCallback | None,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    emitted_text = ""
    try:
        iterator = iter(response)  # type: ignore[arg-type]
    except TypeError as exc:
        raise VideoStoryboardPlanningError(
            "LiteLLM Responses did not return a streaming response."
        ) from exc
    try:
        for raw_event in iterator:
            if cancelled and cancelled():
                raise VideoStoryboardPlanningError(
                    "Storyboard analysis was cancelled."
                )
            event = _response_event_dict(raw_event)
            events.append(event)
            emitted_text = _emit_litellm_response_event(
                event, trace, emitted_text
            )
    except VideoStoryboardPlanningError:
        raise
    except Exception as exc:
        raise VideoStoryboardPlanningError(
            f"LiteLLM Responses streaming failed: {exc}",
            details=_exception_details(exc),
        ) from exc
    return _combined_litellm_response_events(events)


def _emit_litellm_response_event(
    event: dict[str, Any], trace: TraceCallback, emitted_text: str = ""
) -> str:
    raw_type = event.get("type")
    event_type = str(getattr(raw_type, "value", raw_type) or "")
    if event_type == "response.output_text.delta":
        delta = str(event.get("delta") or "")
        if delta:
            trace({"kind": "content", "text": delta})
            emitted_text += delta
    elif event_type == "response.output_text.done":
        emitted_text = _emit_unseen_response_text(
            str(event.get("text") or ""), emitted_text, trace
        )
    elif event_type in {"response.completed", "response.incomplete"}:
        response = event.get("response")
        if isinstance(response, dict):
            final_text = _normalized_content(
                _normalize_litellm_response(response)
            )
            emitted_text = _emit_unseen_response_text(
                final_text, emitted_text, trace
            )
    elif event_type == "response.reasoning_summary_text.delta":
        delta = str(event.get("delta") or "")
        if delta:
            trace({"kind": "thinking", "text": delta})
    elif event_type in {"response.failed", "response.incomplete", "error"}:
        error = event.get("error") or event.get("response") or event
        trace({"kind": "error", "message": f"LiteLLM event: {event_type}", "raw_error": error})
    return emitted_text


def _emit_unseen_response_text(
    complete_text: str,
    emitted_text: str,
    trace: TraceCallback,
) -> str:
    if not complete_text:
        return emitted_text
    if complete_text.startswith(emitted_text):
        unseen = complete_text[len(emitted_text):]
    elif complete_text == emitted_text:
        unseen = ""
    else:
        unseen = complete_text
    if unseen:
        trace({"kind": "content", "text": unseen})
    return complete_text


def _response_event_dict(response: object) -> dict[str, Any]:
    value = _response_object_dict(response)
    for name in ("type", "delta", "text", "error"):
        if not value.get(name):
            attribute = getattr(response, name, None)
            if attribute not in (None, ""):
                value[name] = attribute
    if not isinstance(value.get("response"), dict):
        nested = getattr(response, "response", None)
        if nested is not None:
            value["response"] = _response_object_dict(nested)
    return value


def _combined_litellm_response_events(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    deltas: list[str] = []
    terminal: dict[str, Any] = {}
    for event in events:
        if event.get("type") == "response.output_text.delta":
            deltas.append(str(event.get("delta") or ""))
        if event.get("type") in {"response.completed", "response.incomplete"}:
            response = event.get("response")
            if isinstance(response, dict):
                terminal = response
    normalized = _normalize_litellm_response(terminal) if terminal else {}
    terminal_text = _normalized_content(normalized)
    content = terminal_text or "".join(deltas)
    if not normalized:
        normalized = {"id": "", "model": "", "usage": None}
    normalized["choices"] = [
        {
            "finish_reason": (
                "length" if terminal.get("status") == "incomplete" else "stop"
            ),
            "message": {"role": "assistant", "content": content},
        }
    ]
    return normalized


def _normalize_litellm_response(response: dict[str, Any]) -> dict[str, Any]:
    if isinstance(response.get("choices"), list):
        return response
    text = str(response.get("output_text") or "")
    if not text:
        output = response.get("output", [])
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                content = item.get("content", [])
                if not isinstance(content, list):
                    continue
                for part in content:
                    if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                        text += str(part.get("text") or "")
    return {
        "id": str(response.get("id") or ""),
        "model": str(response.get("model") or ""),
        "choices": [
            {
                "finish_reason": "length" if response.get("status") == "incomplete" else "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": response.get("usage"),
        "status": response.get("status"),
        "incomplete_details": response.get("incomplete_details"),
    }


def _normalized_content(response: dict[str, Any]) -> str:
    choices = response.get("choices", [])
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message", {})
    return str(message.get("content") or "") if isinstance(message, dict) else ""


def _http_litellm_stream(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    headers: dict[str, str],
    trace: TraceCallback | None,
    cancelled: CancelCallback | None,
) -> dict[str, Any]:
    if trace is None:
        return _http_json(url, payload, timeout, headers)
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        **headers,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    chunks: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                if cancelled and cancelled():
                    raise VideoStoryboardPlanningError(
                        "Storyboard analysis was cancelled."
                    )
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise VideoStoryboardPlanningError(
                        "The LiteLLM proxy returned an invalid streaming event."
                    ) from exc
                if isinstance(chunk, dict):
                    chunks.append(chunk)
                    _emit_litellm_chunk(chunk, trace)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:10000]
        raise VideoStoryboardPlanningError(
            f"LiteLLM proxy returned HTTP {exc.code}: {detail}",
            details={"type": "HTTPError", "status_code": exc.code, "body": detail},
        ) from exc
    except urllib.error.URLError as exc:
        raise VideoStoryboardPlanningError(
            f"Cannot connect to the LLM service: {getattr(exc, 'reason', exc)}",
            details={"type": "URLError", "message": str(getattr(exc, 'reason', exc))},
        ) from exc
    return _combined_litellm_chunks(chunks)


def _collect_litellm_stream(
    response: object,
    trace: TraceCallback,
    cancelled: CancelCallback | None,
) -> dict[str, Any]:
    chunks: list[dict[str, Any]] = []
    try:
        iterator = iter(response)  # type: ignore[arg-type]
    except TypeError as exc:
        raise VideoStoryboardPlanningError(
            "LiteLLM did not return a streaming response."
        ) from exc
    try:
        for raw_chunk in iterator:
            if cancelled and cancelled():
                raise VideoStoryboardPlanningError(
                    "Storyboard analysis was cancelled."
                )
            chunk = _response_object_dict(raw_chunk)
            chunks.append(chunk)
            _emit_litellm_chunk(chunk, trace)
    except VideoStoryboardPlanningError:
        raise
    except Exception as exc:
        raise VideoStoryboardPlanningError(
            f"LiteLLM streaming failed: {exc}",
            details=_exception_details(exc),
        ) from exc
    return _combined_litellm_chunks(chunks)


def _response_object_dict(response: object) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        value = model_dump()
        if isinstance(value, dict):
            return value
    try:
        return dict(response)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise VideoStoryboardPlanningError(
            "LiteLLM returned an unsupported streaming chunk."
        ) from exc


def _emit_litellm_chunk(chunk: dict[str, Any], trace: TraceCallback) -> None:
    choices = chunk.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return
    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta", {}) if isinstance(choice, dict) else {}
    if not isinstance(delta, dict):
        return
    reasoning = str(delta.get("reasoning_content") or delta.get("reasoning") or "")
    content = str(delta.get("content") or "")
    if reasoning:
        trace({"kind": "thinking", "text": reasoning})
    if content:
        trace({"kind": "content", "text": content})


def _combined_litellm_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason: object = None
    usage: object = None
    response_id = ""
    model = ""
    for chunk in chunks:
        response_id = str(chunk.get("id") or response_id)
        model = str(chunk.get("model") or model)
        if chunk.get("usage") is not None:
            usage = chunk.get("usage")
        choices = chunk.get("choices", [])
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            continue
        choice = choices[0]
        if choice.get("finish_reason") is not None:
            finish_reason = choice.get("finish_reason")
        delta = choice.get("delta", {})
        if isinstance(delta, dict):
            content_parts.append(str(delta.get("content") or ""))
            reasoning_parts.append(
                str(delta.get("reasoning_content") or delta.get("reasoning") or "")
            )
    return {
        "id": response_id,
        "model": model,
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": "".join(content_parts),
                    "reasoning_content": "".join(reasoning_parts),
                },
            }
        ],
        "usage": usage,
    }


def _emit_provider_error(
    trace: TraceCallback,
    *,
    provider: str,
    label: str,
    endpoint: str,
    error: VideoStoryboardPlanningError,
) -> None:
    trace(
        {
            "kind": "error",
            "provider": provider,
            "label": label,
            "endpoint": endpoint,
            "message": str(error),
            "raw_error": error.details or {"message": str(error)},
        }
    )


def _exception_details(error: Exception) -> dict[str, Any]:
    details: dict[str, Any] = {
        "type": type(error).__name__,
        "message": str(error),
    }
    for name in ("status_code", "request_id", "body", "code", "param"):
        value = getattr(error, name, None)
        if value not in (None, ""):
            details[name] = value
    response = getattr(error, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)
        if status_code is not None:
            details.setdefault("status_code", status_code)
        text = getattr(response, "text", None)
        if text:
            details["response_text"] = str(text)[:10000]
    return details


def _http_ollama_stream(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    trace: TraceCallback,
    cancelled: CancelCallback | None,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        method="POST",
    )
    content_parts: list[str] = []
    thinking_buffer = ""
    content_buffer = ""
    done_reason = ""
    usage = {}
    last_emit = time.monotonic()

    def flush(force: bool = False) -> None:
        nonlocal thinking_buffer, content_buffer, last_emit
        due = force or time.monotonic() - last_emit >= 0.25
        if not due and len(thinking_buffer) + len(content_buffer) < 1024:
            return
        if thinking_buffer:
            trace({"kind": "thinking", "text": thinking_buffer})
            thinking_buffer = ""
        if content_buffer:
            trace({"kind": "content", "text": content_buffer})
            content_buffer = ""
        last_emit = time.monotonic()

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                if cancelled and cancelled():
                    raise VideoStoryboardPlanningError(
                        "Storyboard analysis was cancelled."
                    )
                if not raw_line.strip():
                    continue
                try:
                    chunk = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise VideoStoryboardPlanningError(
                        "Ollama returned an invalid streaming response."
                    ) from exc
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    raise VideoStoryboardPlanningError(
                        f"Ollama streaming error: {chunk['error']}"
                    )
                message = chunk.get("message", {})
                if isinstance(message, dict):
                    thinking = str(message.get("thinking") or "")
                    content = str(message.get("content") or "")
                    thinking_buffer += thinking
                    content_buffer += content
                    content_parts.append(content)
                if chunk.get("done"):
                    usage = {k: chunk[k] for k in ("prompt_eval_count", "eval_count") if k in chunk}
                    done_reason = str(chunk.get("done_reason") or "")
                flush(bool(chunk.get("done")))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise VideoStoryboardPlanningError(
            f"Ollama returned HTTP {exc.code}: {detail}",
            details={
                "type": "HTTPError",
                "status_code": exc.code,
                "body": detail,
            },
        ) from exc
    except urllib.error.URLError as exc:
        raise VideoStoryboardPlanningError(
            f"Cannot connect to Ollama: {getattr(exc, 'reason', exc)}",
            details={
                "type": "ConnectionError",
                "reason": str(getattr(exc, "reason", exc)),
            },
        ) from exc
    flush(True)
    return {
        "message": {"content": "".join(content_parts)},
        "done_reason": done_reason,
        **usage,
    }


def _http_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    headers.update(extra_headers or {})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise VideoStoryboardPlanningError(
            f"The LLM service returned HTTP {exc.code}: {detail}",
            details={
                "type": "HTTPError",
                "status_code": exc.code,
                "body": detail,
            },
        ) from exc
    except urllib.error.URLError as exc:
        raise VideoStoryboardPlanningError(
            f"Cannot connect to the LLM service: {getattr(exc, 'reason', exc)}",
            details={
                "type": "ConnectionError",
                "reason": str(getattr(exc, "reason", exc)),
            },
        ) from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoStoryboardPlanningError(
            "The LLM service returned an invalid response."
        ) from exc
    if not isinstance(value, dict):
        raise VideoStoryboardPlanningError("The LLM service response is not an object.")
    return value


def _normalize_url(value: object, fallback: str) -> str:
    url = str(value or fallback).strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise VideoStoryboardPlanningError("The LLM service URL must use HTTP or HTTPS.")
    return url


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
