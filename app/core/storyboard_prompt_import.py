"""External storyboard interchange. Parsing is deterministic and never calls an LLM."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
import re
import uuid

MAX_TEXT_LENGTH = 1_000_000
MAX_SCENES = 5000
_TIME = re.compile(r"(?:(\d{1,3}):)?(\d{1,4}):(\d{2})(?:[.,](\d{1,3}))?\Z")
_MARKER = re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$")


class PromptImportError(ValueError):
    def __init__(self, key: str, default: str, **values):
        self.key, self.default, self.values = key, default, values
        super().__init__(default.format(**values))


@dataclass(frozen=True)
class ImportedPrompt:
    start_ms: int
    end_ms: int
    prompt: str
    line: int


def format_timestamp(milliseconds: int) -> str:
    seconds, ms = divmod(max(0, int(milliseconds)), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    base = f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"
    return base + (f".{ms:03}" if ms else "")


def parse_prompts(text: str, duration_seconds: float, *, fill_opening: bool = False) -> list[ImportedPrompt]:
    """A block starts on a new line. Do not silently sort, drop or repair input."""
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise PromptImportError("prompt_import_no_duration", "The audio duration is unavailable. Generate or load the final audio first.")
    end_ms = round(duration_seconds * 1000)
    if len(text) > MAX_TEXT_LENGTH:
        raise PromptImportError("prompt_import_too_large", "This text is too large. The limit is 1,000,000 characters.")
    lines = text.lstrip("\ufeff").splitlines()
    # Accept a single fenced response, but never hide prose outside the fence.
    nonempty = [i for i, line in enumerate(lines) if line.strip()]
    if nonempty and re.fullmatch(r"```(?:text|txt|plaintext)?", lines[nonempty[0]].strip(), re.I):
        if len(nonempty) < 2 or lines[nonempty[-1]].strip() != "```":
            raise PromptImportError("prompt_import_fence", "Close the code block or remove its opening ``` line.")
        lines[nonempty[0]] = lines[nonempty[-1]] = ""
    entries: list[tuple[int, str, int]] = []
    start, first_line, parts = None, 0, []

    def finish():
        if start is None:
            return
        prompt = "\n".join(parts).strip()
        if not prompt:
            raise PromptImportError("prompt_import_empty_prompt", "Line {line}: write an image description after the timestamp.", line=first_line)
        entries.append((start, prompt, first_line))

    for number, line in enumerate(lines, 1):
        marker = _MARKER.match(line)
        if marker:
            if re.search(r"\[\d+:\d{2}", marker[2]):
                raise PromptImportError("prompt_import_new_line", "Line {line}: put each timestamp in brackets at the start of a new line.", line=number)
            stamp = marker[1]
            match = _TIME.fullmatch(stamp)
            if not match or int(match[3]) >= 60 or (match[1] is not None and int(match[2]) >= 60):
                raise PromptImportError("prompt_import_bad_time", "Line {line}: invalid time [{time}]. Use [MM:SS], [HH:MM:SS] or [MM:SS.mmm].", line=number, time=stamp)
            finish()
            hours, minutes, seconds, millis = match.groups()
            start = ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int((millis or "0").ljust(3, "0"))
            if entries and start <= entries[-1][0]:
                raise PromptImportError("prompt_import_order", "Line {line}: timestamps must increase; duplicate times are not allowed.", line=number)
            if start >= end_ms:
                raise PromptImportError("prompt_import_outside", "Line {line}: the scene must start before the audio ends at {end}.", line=number, end=format_timestamp(end_ms))
            first_line, parts = number, [marker[2]]
        elif line.strip():
            if start is None or line.lstrip().startswith(("[", "```")):
                raise PromptImportError("prompt_import_bad_line", "Line {line}: expected [MM:SS] followed by a prompt. Remove headings and commentary.", line=number)
            # Catch common missing brackets / inline timestamps instead of absorbing them as prose.
            if re.match(r"\s*\d+:\d{2}", line) or re.search(r"\[\d+:\d{2}", line):
                raise PromptImportError("prompt_import_new_line", "Line {line}: put each timestamp in brackets at the start of a new line.", line=number)
            parts.append(line)
        elif parts:
            parts.append("")
    finish()
    if not entries:
        raise PromptImportError("prompt_import_empty", "Paste at least one timestamp and its image prompt.")
    if len(entries) > MAX_SCENES:
        raise PromptImportError("prompt_import_many", "Use at most 5,000 scenes in one storyboard.")
    if entries[0][0] and not fill_opening:
        raise PromptImportError("prompt_import_opening", "The first scene starts at {time}. Start at [00:00] or enable the option to show the first image from the beginning.", time=format_timestamp(entries[0][0]))
    if fill_opening:
        entries[0] = (0, entries[0][1], entries[0][2])
    result = [ImportedPrompt(start, entries[i + 1][0] if i + 1 < len(entries) else end_ms, prompt, line)
              for i, (start, prompt, line) in enumerate(entries)]
    for entry in result:
        if entry.end_ms - entry.start_ms < 100:
            raise PromptImportError("prompt_import_short", "Line {line}: each scene must last at least 0.1 seconds for video rendering.", line=entry.line)
    return result


def export_prompts(scenes: list[dict]) -> str:
    cursor = 0
    blocks = []
    for scene in scenes:
        blocks.append(f"[{format_timestamp(cursor)}] {_editable_text(scene)}")
        cursor += round(float(scene.get("duration_seconds", scene.get("duration", 0))) * 1000)
    return "\n\n".join(blocks)


def _editable_text(scene: dict) -> str:
    overrides = scene.get("generation_overrides") or {}
    if overrides.get("prompt_mode") == "complete" and overrides.get("raw_prompt"):
        return str(overrides["raw_prompt"]).strip()
    return str(scene.get("prompt") or "").strip()


def source_transcript(source: dict) -> str:
    """Export real segment boundaries, explicitly not invented word alignment."""
    cues = source.get("narration_cues") or []
    if not cues or any(not cue.get("timing_ready") for cue in cues):
        return ""
    return "\n".join(f"[{format_timestamp(round(float(cue['start_seconds']) * 1000))}] {cue.get('text', '').strip()}"
                     for cue in cues if str(cue.get("text") or "").strip())


def build_import_plan(entries: list[ImportedPrompt], source: dict, state: dict, *, complete_prompts: bool) -> dict:
    """Keep references/settings and unchanged media; replace changed scenes without deleting files."""
    old_plan = state.get("plan") or {}
    plan = {key: deepcopy(old_plan[key]) for key in (
        "style", "style_mode", "base_seed", "continuity", "narrative_context", "video_overrides",
    ) if key in old_plan}
    overrides = source.get("storyboard_overrides") or {}
    for origin, target in (("style", "style"), ("style_mode", "style_mode"), ("seed", "base_seed"), ("narrative", "narrative_context"), ("video", "video_overrides")):
        if origin in overrides:
            plan[target] = deepcopy(overrides[origin])
    plan.setdefault("continuity", {"characters": [], "locations": [], "objects": [], "eras": []})
    old_scenes = state.get("scenes") or []
    old_by_interval = {}
    cursor = 0
    for old in old_scenes:
        end = cursor + round(float(old.get("duration_seconds", old.get("duration", 0))) * 1000)
        old_by_interval[(cursor, end)] = old
        cursor = end
    scenes = []
    for entry in entries:
        previous = old_by_interval.get((entry.start_ms, entry.end_ms), {})
        previous_mode = (previous.get("generation_overrides") or {}).get("prompt_mode") == "complete"
        unchanged = previous and _editable_text(previous) == entry.prompt and previous_mode == complete_prompts
        scene = deepcopy(previous) if unchanged else {
            "scene_id": "import-" + uuid.uuid4().hex[:12], "image_path": "", "video_path": "", "status": "planned",
            "characters": [], "locations": [], "shot": "", "motion": "none", "transition": "none",
        }
        narration = " ".join(str(c.get("text") or "") for c in source.get("narration_cues", [])
                             if float(c.get("start_seconds", 0)) < entry.end_ms / 1000
                             and float(c.get("end_seconds", float(c.get("start_seconds", 0)) + float(c.get("duration_seconds", 0)))) > entry.start_ms / 1000)
        scene.update(prompt=entry.prompt, start_seconds=entry.start_ms / 1000,
                     duration_seconds=(entry.end_ms - entry.start_ms) / 1000,
                     aligned_start_seconds=entry.start_ms / 1000, narration=narration)
        generation = scene.setdefault("generation_overrides", {})
        if not unchanged or complete_prompts:
            generation.pop("raw_prompt", None)
        generation["prompt_mode"] = "complete" if complete_prompts else "project_style"
        scenes.append(scene)
    plan.update(scenes=scenes, plan_origin="external_prompts", imported_prompt_mode="complete" if complete_prompts else "project_style",
                voice_start_offset_seconds=float(source.get("voice_start_offset_seconds") or 0),
                rendered_output_path="", analysis_phase="complete")
    return plan
