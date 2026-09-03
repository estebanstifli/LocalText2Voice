from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from app.utils.paths import resource_root


DEFAULT_STORYBOARD_STYLE_ID = "comic_book"


@dataclass(frozen=True)
class StoryboardStyle:
    id: str
    name: str
    prompt: str


LEGACY_STYLE_ALIASES = {
    "illustration": "childrens_book_illustration",
    "animation_3d": "3d_animation",
}


def style_id(name: str) -> str:
    normalized = name.casefold().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")


@lru_cache(maxsize=1)
def storyboard_styles() -> tuple[StoryboardStyle, ...]:
    path = resource_root() / "assets" / "video_storyboard_styles.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(raw, list):
        return ()
    styles: list[StoryboardStyle] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or "").strip()
        prompt = str(value.get("prompt") or "").strip()
        identifier = style_id(name)
        if not identifier or not name or not prompt or identifier in seen:
            continue
        seen.add(identifier)
        styles.append(StoryboardStyle(identifier, name, prompt))
    return tuple(styles)


def storyboard_style_ids() -> set[str]:
    return {style.id for style in storyboard_styles()}


def normalize_storyboard_style_id(value: object) -> str:
    identifier = str(value or DEFAULT_STORYBOARD_STYLE_ID).strip()
    identifier = LEGACY_STYLE_ALIASES.get(identifier, identifier)
    if identifier == "automatic":
        return DEFAULT_STORYBOARD_STYLE_ID
    if identifier in {"custom"} | storyboard_style_ids():
        return identifier
    return DEFAULT_STORYBOARD_STYLE_ID


def storyboard_style(identifier: object) -> StoryboardStyle | None:
    normalized = normalize_storyboard_style_id(identifier)
    return next(
        (style for style in storyboard_styles() if style.id == normalized),
        None,
    )


def storyboard_style_id_from_prompt(prompt: object) -> str:
    value = str(prompt or "").strip()
    for style in storyboard_styles():
        if style.prompt == value:
            return style.id
    return "custom" if value else DEFAULT_STORYBOARD_STYLE_ID


def storyboard_style_sample_path(identifier: object):
    normalized = normalize_storyboard_style_id(identifier)
    if normalized == "custom":
        return None
    path = (
        resource_root()
        / "assets"
        / "storyboard_style_samples"
        / f"{normalized}.png"
    )
    return path if path.is_file() else None
