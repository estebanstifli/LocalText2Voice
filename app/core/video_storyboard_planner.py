from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from difflib import SequenceMatcher
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
from app.core.video_storyboard_visual_traits import (
    compact_visual_description,
    merge_visual_description,
    limit_generated_identity,
)
from app.core.video_storyboard_appearance import (
    appearance_schema, appearance_values, legacy_appearance,
    merge_appearance, normalize_event_appearance, selected_appearance,
)
from app.core.video_storyboard_groups import group_schema, merge_groups, expand_group_reference
from app.core.storyboard_analysis_settings import (
    normalize as analysis_configuration, instruction as analysis_instruction,
    snapshot as analysis_snapshot, request_continuity, CONTRACT,
    input_limit as continuity_input_limit,
    contract_for, character_discovery_messages, story_synopsis,
)
from app.core.storyboard_analysis_review import choices as analysis_choices, fingerprint as review_fingerprint


class VideoStoryboardPlanningError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


ProgressCallback = Callable[[int, int], None]
PartialCallback = Callable[[dict[str, Any]], None]
CancelCallback = Callable[[], bool]
TraceCallback = Callable[[dict[str, Any]], None]

_MAX_COVERAGE_FRAME_SECONDS = 8.0
_MAX_PLANNING_BLOCK_SECONDS = 40.0
_MAX_PLANNING_BLOCK_CHARACTERS = 4000
_CONTINUITY_VERSION = 3

_DEFAULT_NEGATIVE = (
    "text, captions, subtitles, speech bubbles, logos, watermark, split screen, "
    "collage, comic panels, duplicate subject, multiple views, malformed hands, "
    "extra fingers, anatomical distortion"
)
_SHOT_SEQUENCE = (
    "wide establishing shot, eye level, 35mm viewpoint",
    "medium shot, three-quarter angle, 50mm viewpoint",
    "close-up, slight low angle, 85mm viewpoint",
    "over-the-shoulder shot, 50mm viewpoint",
    "medium-wide profile shot, 35mm viewpoint",
)


def plan_video_storyboard(source, settings, *, progress=None, partial=None,
                          cancelled=None, trace=None, review=None):
    """Public entry point: scene-first conversational analysis."""
    from app.core.storyboard_conversation import plan_conversation
    return plan_conversation(source, settings, progress=progress, partial=partial,
                             cancelled=cancelled, trace=trace, review=review)


def _plan_video_storyboard_legacy(
    source: dict[str, Any],
    settings: dict[str, Any],
    *,
    progress: ProgressCallback | None = None,
    partial: PartialCallback | None = None,
    cancelled: CancelCallback | None = None,
    trace: TraceCallback | None = None,
    review: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = deepcopy(settings)
    selected_choices = analysis_choices(settings.get("analysis_choices"))
    selected_plan = selected_choices["plan"]
    if "continuity_analysis" in settings:
        if not isinstance(settings.get("analysis"), dict):
            settings["analysis"] = {}
        analysis = settings["analysis"]
        limit = continuity_input_limit(analysis_configuration(settings["continuity_analysis"]))
        analysis.setdefault("max_block_characters", limit)
        analysis.setdefault("max_block_seconds", max(10.0, min(1000.0, limit / 100.0)))
    text = str(source.get("text") or "").strip()
    if not text:
        raise VideoStoryboardPlanningError("The audiobook text is empty.")
    scene_settings = settings.get("scene", {})
    minimum = max(1.0, float(scene_settings.get("minimum_seconds", 4) or 4))
    target = max(minimum, float(scene_settings.get("target_seconds", 8) or 8))
    maximum = max(target, float(scene_settings.get("maximum_seconds", 20) or 20))
    total_duration = float(source.get("duration_seconds") or 0.0)
    if total_duration <= 0:
        total_duration = max(minimum, len(text.split()) / 2.6)

    batches = _planning_batches(
        text,
        source.get("narration_cues", []),
        total_duration,
        settings,
    )
    automatic_seed = int.from_bytes(
        hashlib.sha256(text.encode("utf-8")).digest()[:6],
        "big",
    )
    try:
        requested_seed = int(settings.get("seed_override"))
    except (TypeError, ValueError):
        requested_seed = -1
    base_seed = (
        min(requested_seed, 2**63 - 1)
        if requested_seed >= 0
        else automatic_seed
    )
    voice_start_offset_seconds = max(
        0.0,
        float(source.get("voice_start_offset_seconds") or 0.0),
    )
    image_settings = settings.get("image", {})
    raw_style_mode = image_settings.get("style_mode")
    style_mode = normalize_storyboard_style_id(
        raw_style_mode or DEFAULT_STORYBOARD_STYLE_ID
    )
    custom_style = str(image_settings.get("style_prompt") or "").strip()
    if custom_style and not raw_style_mode:
        style_mode = "custom"
    narrative_context = settings.get("narrative_context", {})
    era_request = (
        str(narrative_context.get("era") or "").strip()
        if isinstance(narrative_context, dict)
        else ""
    )
    raw_style_override = settings.get("style_override", {})
    style_override = (
        dict(raw_style_override)
        if isinstance(raw_style_override, dict)
        else {}
    )
    required_style = {"medium", "palette", "lighting", "negative"}
    complete_style_override = required_style.issubset(style_override) and all(
        str(style_override.get(key) or "").strip() for key in required_style
    )
    if complete_style_override and not isinstance(
        style_override.get("characters"), list
    ):
        style_override["characters"] = []
    if complete_style_override:
        global_style = {
            key: value
            for key, value in style_override.items()
            if key != "continuity"
        }
    else:
        global_style = _application_style_lock(
            style_mode,
            custom_style=custom_style,
            overrides=style_override,
        )
    legacy_character_registry = _character_registry(global_style)
    prepared_batches: list[dict[str, Any]] = []
    unit_cursor = 0
    for batch in batches:
        duration = max(minimum, float(batch["duration_seconds"]))
        requested_scene_count = _scene_count(
            duration, minimum, target, maximum
        )
        minimum_scene_count = max(1, math.ceil(duration / maximum))
        semantic_units = _semantic_units(batch, minimum_scene_count)
        for unit in semantic_units:
            unit["id"] = unit_cursor
            unit_cursor += 1
        scene_count = min(requested_scene_count, len(semantic_units))
        ranges, warnings = _deterministic_unit_ranges(
            semantic_units,
            scene_count,
            float(batch.get("start_seconds") or 0.0),
            float(batch.get("end_seconds") or total_duration),
            minimum,
            maximum,
        )
        prepared_batches.append(
            {
                "batch": batch,
                "duration": duration,
                "scene_count": scene_count,
                "semantic_units": semantic_units,
                "ranges": ranges,
                "warnings": warnings,
            }
        )

    continuity = _empty_continuity()
    analysis_config = analysis_configuration(settings.get("continuity_analysis"))
    continuity["analysis_configuration"] = analysis_snapshot(settings)
    continuity["analysis_configuration"]["content_plan"] = selected_plan
    continuity["analysis_configuration"]["review_requested"] = selected_choices["review"]
    _seed_requested_era(continuity, era_request, total_duration)
    if selected_plan != "scenes":
        _seed_continuity_from_legacy_characters(
            continuity, legacy_character_registry, total_duration,
        )
    continuity["discovery_reports"] = []
    phase_total = len(prepared_batches) * (1 if selected_plan == "scenes" else 4)
    checkpoint_key = review_fingerprint(source, settings)
    checkpoint = settings.get("review_checkpoint", {})
    resumed = (selected_choices["review"] and isinstance(checkpoint, dict)
               and checkpoint.get("fingerprint") == checkpoint_key
               and checkpoint.get("status") in {"pending", "approved"}
               and isinstance(checkpoint.get("reports"), list)
               and len(checkpoint["reports"]) == len(prepared_batches)
               and all(isinstance(r, dict) for r in checkpoint["reports"]))
    if checkpoint and not resumed and trace:
        trace({"kind": "warning", "message": "Saved review does not match this source/configuration; preparing a new summary."})
    if resumed:
        for prepared, report in zip(prepared_batches, checkpoint["reports"]):
            prepared.update(deepcopy(report))
    discovery_batches = [] if resumed or selected_plan == "scenes" else prepared_batches
    for batch_index, prepared in enumerate(discovery_batches, start=1):
        if cancelled and cancelled():
            raise VideoStoryboardPlanningError("Storyboard analysis was cancelled.")
        if progress:
            progress(batch_index, phase_total)
        batch = prepared["batch"]
        semantic_units = prepared["semantic_units"]
        if trace:
            trace(
                {
                    "kind": "block",
                    "phase": "discovery",
                    "current": batch_index,
                    "total": len(prepared_batches),
                    "start_seconds": float(batch.get("start_seconds") or 0.0),
                    "end_seconds": float(batch.get("end_seconds") or total_duration),
                }
            )
        discovery_system, discovery_user = character_discovery_messages(
            "\n\n".join(str(unit["text"]) for unit in semantic_units), analysis_config
        )
        discovery_report = _request_plan(
            settings,
            None,
            discovery_system,
            discovery_user,
            trace=trace,
            cancelled=cancelled,
            request_label=(
                f"continuity discovery block {batch_index}/{len(prepared_batches)}"
            ),
            work_item_count_hint=len(semantic_units),
            work_item_type_hint="narration units",
            output_tokens_hint=min(
                1800,
                max(700, 260 + len(semantic_units) * 120),
            ),
        )
        prepared["discovery_report"] = str(discovery_report).strip()
        continuity["discovery_reports"].append(
            {
                "block": batch_index,
                "start_seconds": float(batch.get("start_seconds") or 0.0),
                "end_seconds": float(batch.get("end_seconds") or total_duration),
                "text": prepared["discovery_report"],
            }
        )
        if partial:
            partial(
                {
                    "title": str(source.get("title") or "Storyboard"),
                    "base_seed": base_seed,
                    "style_mode": style_mode,
                    "style": deepcopy(global_style),
                    "source_duration_seconds": total_duration,
                    "voice_start_offset_seconds": voice_start_offset_seconds,
                    "narrative_context": {
                        "era": era_request,
                        "era_source": "user" if era_request else "detected",
                    },
                    "continuity": deepcopy(continuity),
                    "alignment_debug": {"version": 2, "warnings": []},
                    "analysis_phase": "discovery",
                    "completed_blocks": batch_index,
                    "total_blocks": phase_total,
                    "scenes": [],
                }
            )

    if selected_plan != "scenes":
        if resumed and checkpoint.get("story_context"):
            story_context = str(checkpoint["story_context"])
        else:
            reports = [story_synopsis(str(p.get("discovery_report", ""))) for p in prepared_batches]
            # Pairwise reduction keeps long books bounded without dropping later chapters.
            while len(reports) > 1:
                reduced = []
                for index in range(0, len(reports), 2):
                    group = reports[index:index + 2]
                    if len(group) == 1:
                        reduced.append(group[0])
                    else:
                        reduced.append(str(_request_plan(settings, None,
                            "Combine these consecutive story reports into a brief 2–3-line synopsis. "
                            "Preserve the main subjects and story progression. Do not invent facts. Return only the synopsis.",
                            "\n\n".join(group), trace=trace, cancelled=cancelled,
                            request_label="story context summary")))
                reports = reduced
            story_context = reports[0] if reports else ""
        settings["story_context"] = story_context
        continuity["story_context"] = story_context

    if selected_choices["review"]:
        if review is None:
            raise VideoStoryboardPlanningError("Review was requested but no review handler is available.")
        if not resumed:
            if selected_plan == "scenes":
                summaries = []
                for index, prepared in enumerate(prepared_batches, 1):
                    summaries.append(str(_request_plan(settings, None,
                        "Summarize the main topics and suggest a brief visual approach for this narration. "
                        "Use plain text. Do not invent facts or create character profiles.",
                        "\n\n".join(str(u["text"]) for u in prepared["semantic_units"]),
                        trace=trace, cancelled=cancelled,
                        request_label="visual approach review" + (f" block {index}/{len(prepared_batches)}" if len(prepared_batches) > 1 else ""))))
                sections = {"directions": "\n\n".join(summaries)}
            else:
                for prepared in prepared_batches:
                    prepared["location_report"] = str(_request_plan(settings, None,
                        "List the recurring places in this passage and only their explicitly stated appearance. "
                        "Use a short plain-text report. Do not invent details.",
                        "\n\n".join(str(u["text"]) for u in prepared["semantic_units"]),
                        trace=trace, cancelled=cancelled, request_label="location summary for review"))
                sections = {
                    "characters": "\n\n".join(p.get("discovery_report", "") for p in prepared_batches),
                    "locations": "\n\n".join(p.get("location_report", "") for p in prepared_batches),
                    "era": era_request,
                    "story_summary": settings.get("story_context", ""),
                    "directions": "",
                }
            checkpoint = {"fingerprint": checkpoint_key, "status": "pending",
                "story_context": settings.get("story_context", ""),
                "choices": selected_choices, "original": deepcopy(sections), "edited": sections,
                "reports": [{k: p.get(k, "") for k in ("discovery_report", "location_report")} for p in prepared_batches]}
        checkpoint = review(deepcopy(checkpoint))
        edited = checkpoint.get("edited", {})
        if "story_summary" in edited:
            settings["story_context"] = str(edited["story_summary"])
            continuity["story_context"] = settings["story_context"]
        if "era" in edited:
            era_request = str(edited["era"]).strip()
            continuity["eras"] = []
            continuity.pop("era_locked", None)
            _seed_requested_era(continuity, era_request, total_duration)
            if not era_request and edited.get("era") != checkpoint.get("original", {}).get("era"):
                # Explicitly clearing a user era must not trigger automatic redetection.
                settings.setdefault("continuity_analysis", {})["era_mode"] = "provided"
        # Do not resend the entire book's unchanged summaries to every small-model call.
        settings["reviewed_summary"] = {
            k: deepcopy(v) for k, v in edited.items()
            if k != "story_summary" and (selected_plan == "scenes" or v != checkpoint.get("original", {}).get(k))
        }
        continuity["analysis_review"] = deepcopy(checkpoint)
        for prepared in prepared_batches:
            # The source units remain the timing evidence; edited summaries govern interpretation.
            if edited.get("characters") != checkpoint.get("original", {}).get("characters"):
                prepared["discovery_report"] = str(edited.get("characters", ""))
            if edited.get("locations") != checkpoint.get("original", {}).get("locations"):
                prepared["location_report"] = str(edited.get("locations", ""))

    continuity_focus: list[str] = []
    continuity_warnings: list[str] = []
    continuity_batches = prepared_batches if selected_plan != "scenes" else []
    for batch_index, prepared in enumerate(continuity_batches, start=1):
        if cancelled and cancelled():
            raise VideoStoryboardPlanningError("Storyboard analysis was cancelled.")
        if progress:
            progress(len(prepared_batches) + batch_index, phase_total)
        batch = prepared["batch"]
        semantic_units = prepared["semantic_units"]
        if trace:
            trace(
                {
                    "kind": "block",
                    "phase": "continuity",
                    "current": batch_index,
                    "total": len(prepared_batches),
                    "start_seconds": float(batch.get("start_seconds") or 0.0),
                    "end_seconds": float(batch.get("end_seconds") or total_duration),
                }
            )
        continuity_schema = _continuity_analyzer_schema()
        settings["reviewed_location_report"] = prepared.get("location_report")
        continuity_system, continuity_user = _continuity_analyzer_messages(
            semantic_units,
            continuity,
            continuity_focus,
            era_request=era_request,
            discovery_report=str(prepared.get("discovery_report") or ""),
        )
        continuity_result = request_continuity(
            _request_plan,
            settings,
            continuity_schema,
            continuity_system,
            continuity_user,
            context=(semantic_units, continuity, str(prepared.get("discovery_report") or ""), era_request),
            trace=trace,
            cancelled=cancelled,
            request_label=(
                f"continuity structurer block {batch_index}/{len(prepared_batches)}"
            ),
            work_item_count_hint=len(semantic_units),
            work_item_type_hint="narration units",
            output_tokens_hint=min(
                3200,
                max(1000, 400 + len(semantic_units) * 220),
            ),
        )
        _normalize_continuity_event_semantics(
            continuity_result,
            semantic_units,
            continuity,
        )
        _normalize_continuity_first_appearance_anchors(
            continuity_result,
            semantic_units,
            continuity,
        )
        continuity_issues, soft_issues = _partition_continuity_issues(
            _continuity_analyzer_validation_issues(
                continuity_result,
                semantic_units,
                continuity,
            )
        )
        if continuity_issues:
            continuity_result = request_continuity(
                _request_plan,
                settings,
                continuity_schema,
                continuity_system,
                continuity_user
                + "\n\nQUALITY RETRY: Correct the complete structured result. "
                + "; ".join(continuity_issues)
                + ". Preserve exact supplied IDs and keep descriptions concise and non-repetitive.",
                context=(semantic_units, continuity, str(prepared.get("discovery_report") or ""), era_request),
                trace=trace,
                cancelled=cancelled,
                request_label=(
                    f"continuity analyzer block {batch_index}/{len(prepared_batches)} quality retry"
                ),
                work_item_count_hint=len(semantic_units),
                work_item_type_hint="narration units",
                output_tokens_hint=min(
                    3200,
                    max(1000, 400 + len(semantic_units) * 220),
                ),
            )
            _normalize_continuity_event_semantics(
                continuity_result,
                semantic_units,
                continuity,
            )
            _normalize_continuity_first_appearance_anchors(
                continuity_result,
                semantic_units,
                continuity,
            )
            continuity_issues, soft_issues = _partition_continuity_issues(
                _continuity_analyzer_validation_issues(
                    continuity_result,
                    semantic_units,
                    continuity,
                )
            )
        missing_people = _missing_obvious_people(
            continuity_result,
            semantic_units,
            continuity,
        )
        if missing_people:
            repair_schema = _missing_character_repair_schema()
            repair_system, repair_user = _missing_character_repair_messages(
                missing_people,
                semantic_units,
            )
            try:
                repair_result = _request_plan(
                    settings,
                    repair_schema,
                    repair_system,
                    repair_user,
                    trace=trace,
                    cancelled=cancelled,
                    request_label=(
                        "missing character repair block "
                        f"{batch_index}/{len(prepared_batches)}"
                    ),
                    work_item_count_hint=len(missing_people),
                    work_item_type_hint="possible named characters",
                    output_tokens_hint=max(512, 180 + len(missing_people) * 200),
                )
            except VideoStoryboardPlanningError as exc:
                repair_result = {}
                if trace:
                    trace(
                        {
                            "kind": "warning",
                            "message": (
                                "Targeted character repair could not be completed: "
                                f"{exc}"
                            ),
                        }
                    )
            repaired_events = _accepted_missing_character_events(
                repair_result,
                missing_people,
                semantic_units,
            )
            existing_events = continuity_result.setdefault("character_events", [])
            if isinstance(existing_events, list):
                existing_events.extend(repaired_events)
            _normalize_continuity_event_semantics(
                continuity_result,
                semantic_units,
                continuity,
            )
            _normalize_continuity_first_appearance_anchors(
                continuity_result,
                semantic_units,
                continuity,
            )
            continuity_issues, soft_issues = _partition_continuity_issues(
                _continuity_analyzer_validation_issues(
                    continuity_result,
                    semantic_units,
                    continuity,
                )
            )
        placeholder_events = _placeholder_continuity_events(continuity_result, continuity)
        if placeholder_events:
            completion_schema = _continuity_visual_completion_schema(
                len(placeholder_events)
            )
            completion_system, completion_user = (
                _continuity_visual_completion_messages(
                    placeholder_events,
                    semantic_units,
                    era_request=era_request,
                )
            )
            try:
                completion_result = _request_plan(
                    settings,
                    completion_schema,
                    completion_system,
                    completion_user,
                    trace=trace,
                    cancelled=cancelled,
                    request_label=(
                        "continuity visual completion block "
                        f"{batch_index}/{len(prepared_batches)}"
                    ),
                    work_item_count_hint=len(placeholder_events),
                    work_item_type_hint="incomplete continuity definitions",
                    output_tokens_hint=max(
                        512,
                        180 + len(placeholder_events) * 120,
                    ),
                )
                _apply_continuity_visual_completions(
                    continuity_result,
                    completion_result,
                )
            except VideoStoryboardPlanningError as exc:
                if cancelled and cancelled():
                    raise
                warning = (
                    f"Continuity block {batch_index}: optional visual completion "
                    f"could not be applied ({exc}). Analysis continued."
                )
                if warning not in continuity_warnings:
                    continuity_warnings.append(warning)
                    if trace:
                        trace({"kind": "warning", "message": warning})
            _normalize_continuity_event_semantics(
                continuity_result,
                semantic_units,
                continuity,
            )
            _normalize_continuity_first_appearance_anchors(
                continuity_result,
                semantic_units,
                continuity,
            )
            continuity_issues, soft_issues = _partition_continuity_issues(
                _continuity_analyzer_validation_issues(
                    continuity_result,
                    semantic_units,
                    continuity,
                )
            )
        if continuity_issues and _repair_explicit_character_changes(
            continuity_result,
            semantic_units,
            continuity,
            continuity_focus,
        ):
            continuity_issues, soft_issues = _partition_continuity_issues(
                _continuity_analyzer_validation_issues(
                    continuity_result,
                    semantic_units,
                    continuity,
                )
            )
        for issue in soft_issues:
            fallback_detail = (
                "Analysis continued using the best available visual definition."
                if "visual states need" in issue
                else "Analysis continued without inventing an entity."
            )
            warning = f"Continuity block {batch_index}: {issue}. {fallback_detail}"
            if warning not in continuity_warnings:
                continuity_warnings.append(warning)
                if trace:
                    trace({"kind": "warning", "message": warning})
        if continuity_issues:
            raise VideoStoryboardPlanningError(
                "The LLM could not produce a valid continuity ledger: "
                + "; ".join(continuity_issues)
            )
        previous_profiles = deepcopy(continuity) if selected_plan == "basic" else None
        touched_ids = _merge_continuity_events(
            continuity,
            continuity_result,
            semantic_units,
        )
        if previous_profiles is not None:
            from app.core.storyboard_analysis_review import freeze_basic_profiles
            freeze_basic_profiles(continuity, previous_profiles)
        for warning in continuity.get("warnings", []):
            if warning not in continuity_warnings:
                continuity_warnings.append(warning)
                if trace:
                    trace({"kind": "warning", "message": warning})
        continuity_focus = list(
            dict.fromkeys([*continuity_focus, *touched_ids])
        )[-6:]
        if partial:
            partial_continuity = deepcopy(continuity)
            _finalize_continuity_periods(partial_continuity, total_duration)
            partial_style = deepcopy(global_style)
            partial_style["characters"] = list(
                _legacy_character_registry_from_continuity(
                    partial_continuity
                ).values()
            )
            partial(
                {
                    "title": str(source.get("title") or "Storyboard"),
                    "base_seed": base_seed,
                    "style_mode": style_mode,
                    "style": partial_style,
                    "source_duration_seconds": total_duration,
                    "voice_start_offset_seconds": voice_start_offset_seconds,
                    "narrative_context": {
                        "era": era_request,
                        "era_source": "user" if era_request else "detected",
                    },
                    "continuity": partial_continuity,
                    "alignment_debug": {
                        "version": 2,
                        "warnings": list(continuity_warnings),
                    },
                    "analysis_phase": "continuity",
                    "completed_blocks": len(prepared_batches) + batch_index,
                    "total_blocks": phase_total,
                    "scenes": [],
                }
            )

    if selected_plan == "basic":
        for collection in ("characters", "locations"):
            for entity in continuity.get(collection, []):
                entity["states"] = entity.get("states", [])[:1]
                if entity["states"]:
                    entity["states"][0]["to_seconds"] = total_duration
        continuity["eras"] = []
        _seed_requested_era(continuity, era_request, total_duration)
    continuity_focus = []
    for batch_index, prepared in enumerate(continuity_batches, start=1):
        if cancelled and cancelled():
            raise VideoStoryboardPlanningError("Storyboard analysis was cancelled.")
        if progress:
            progress(len(prepared_batches) * 2 + batch_index, phase_total)
        batch = prepared["batch"]
        semantic_units = prepared["semantic_units"]
        if trace:
            trace(
                {
                    "kind": "block",
                    "phase": "binding",
                    "current": batch_index,
                    "total": len(prepared_batches),
                    "start_seconds": float(batch.get("start_seconds") or 0.0),
                    "end_seconds": float(batch.get("end_seconds") or total_duration),
                }
            )
        binder_schema = _unit_binder_schema(len(semantic_units))
        binder_system, binder_user = _unit_binder_messages(
            semantic_units,
            continuity,
            continuity_focus,
        )
        binder_system = analysis_instruction(analysis_config, "binding") + "\n" + contract_for("binding")
        binder_result = _request_plan(
            settings,
            binder_schema,
            binder_system,
            binder_user,
            trace=trace,
            cancelled=cancelled,
            request_label=f"unit binder block {batch_index}/{len(prepared_batches)}",
            work_item_count_hint=len(semantic_units),
            work_item_type_hint="narration units",
            output_tokens_hint=min(1800, max(600, 160 + len(semantic_units) * 100)),
        )
        _normalize_unit_binding_references(binder_result, continuity)
        _normalize_binder_era_transitions(
            binder_result,
            semantic_units,
            continuity,
        )
        binder_issues = _unit_binder_validation_issues(
            binder_result,
            semantic_units,
            continuity,
        )
        if binder_issues:
            binder_result = _request_plan(
                settings,
                binder_schema,
                binder_system,
                binder_user
                + "\n\nVALIDATION RETRY: "
                + "; ".join(binder_issues)
                + ". Do not create or modify IDs. Return all units in the supplied order.",
                trace=trace,
                cancelled=cancelled,
                request_label=(
                    f"unit binder block {batch_index}/{len(prepared_batches)} quality retry"
                ),
                work_item_count_hint=len(semantic_units),
                work_item_type_hint="narration units",
                output_tokens_hint=min(
                    1800, max(600, 160 + len(semantic_units) * 100)
                ),
            )
            _normalize_unit_binding_references(binder_result, continuity)
            _normalize_binder_era_transitions(
                binder_result,
                semantic_units,
                continuity,
            )
            binder_issues = _unit_binder_validation_issues(
                binder_result,
                semantic_units,
                continuity,
            )
        if binder_issues:
            raise VideoStoryboardPlanningError(
                "The LLM could not bind narration units to the continuity ledger: "
                + "; ".join(binder_issues)
            )
        continuity_focus = _merge_unit_bindings(
            continuity,
            binder_result,
            semantic_units,
        )
        if partial:
            partial_continuity = deepcopy(continuity)
            _finalize_continuity_periods(partial_continuity, total_duration)
            partial_style = deepcopy(global_style)
            partial_style["characters"] = list(
                _legacy_character_registry_from_continuity(
                    partial_continuity
                ).values()
            )
            partial(
                {
                    "title": str(source.get("title") or "Storyboard"),
                    "base_seed": base_seed,
                    "style_mode": style_mode,
                    "style": partial_style,
                    "source_duration_seconds": total_duration,
                    "voice_start_offset_seconds": voice_start_offset_seconds,
                    "narrative_context": {
                        "era": era_request,
                        "era_source": "user" if era_request else "detected",
                    },
                    "continuity": partial_continuity,
                    "alignment_debug": {
                        "version": 2,
                        "warnings": list(continuity_warnings),
                    },
                    "analysis_phase": "binding",
                    "completed_blocks": len(prepared_batches) * 2 + batch_index,
                    "total_blocks": phase_total,
                    "scenes": [],
                }
            )
    _finalize_continuity_periods(continuity, total_duration)
    _apply_continuity_boundaries(prepared_batches, continuity)
    character_registry = _legacy_character_registry_from_continuity(continuity)
    global_style["characters"] = list(character_registry.values())

    combined_scenes: list[dict[str, Any]] = []
    debug_units: list[dict[str, Any]] = []
    alignment_warnings: list[str] = list(continuity_warnings)
    for batch_index, prepared in enumerate(prepared_batches, start=1):
        if cancelled and cancelled():
            raise VideoStoryboardPlanningError("Storyboard analysis was cancelled.")
        if progress:
            progress(
                batch_index if selected_plan == "scenes" else len(prepared_batches) * 3 + batch_index,
                len(prepared_batches) if selected_plan == "scenes" else phase_total,
            )
        batch = prepared["batch"]
        if trace:
            trace(
                {
                    "kind": "block",
                    "phase": "scenes",
                    "current": batch_index,
                    "total": len(prepared_batches),
                    "start_seconds": float(batch.get("start_seconds") or 0.0),
                    "end_seconds": float(batch.get("end_seconds") or total_duration),
                }
            )
        semantic_units = prepared["semantic_units"]
        scene_count = prepared["scene_count"]
        ranges = prepared["ranges"]
        warnings = prepared["warnings"]
        alignment_warnings.extend(
            f"Block {batch_index}: {warning}" for warning in warnings
        )
        schema = _plan_schema(scene_count)
        system, user = _messages(
            semantic_units,
            ranges,
            era_request=era_request,
            continuity=continuity,
        )
        if selected_plan == "scenes":
            system = (
                "Create one concrete English still-image description for each supplied scene, based only on its narration. "
                "Return the supplied JSON schema in the same order. All character_state_ids and location_state_ids "
                "must be empty arrays and era_state_id an empty string. There is no continuity registry. "
                "Describe visible subjects and environment in 25-55 words. Do not invent facts or later events. "
                "Do not include style, camera, timings or rendering instructions."
            )
            user = json.dumps({"scenes": [{"scene_position": i, "narration": _assigned_narration(semantic_units, r)}
                                         for i, r in enumerate(ranges, 1)], "era": era_request}, ensure_ascii=False)
        if settings.get("reviewed_summary"):
            user += "\nUser-approved directions take precedence for visual interpretation (source units still determine timing):\n" + json.dumps(settings["reviewed_summary"], ensure_ascii=False)
        result = _request_plan(
            settings,
            schema,
            system,
            user,
            trace=trace,
            cancelled=cancelled,
            request_label=f"scene block {batch_index}/{len(prepared_batches)}",
        )
        scenes = result.get("scenes")
        if not isinstance(scenes, list) or len(scenes) != scene_count:
            found = len(scenes) if isinstance(scenes, list) else 0
            raise VideoStoryboardPlanningError(
                f"The LLM returned {found} scenes for block {batch_index}; "
                f"{scene_count} were required."
            )
        _normalize_scene_continuity_references(
            scenes,
            semantic_units,
            ranges,
            continuity,
        )
        terse_indexes = _terse_prompt_indexes(scenes)
        invalid_reference_indexes = _invalid_scene_reference_indexes(
            scenes,
            semantic_units,
            ranges,
            continuity,
        )
        if terse_indexes or invalid_reference_indexes:
            reasons: list[str] = []
            if terse_indexes:
                reasons.append(
                    "terse image prompts at positions "
                    + ", ".join(str(value) for value in terse_indexes)
                )
            if invalid_reference_indexes:
                reasons.append(
                    "unknown or temporally invalid continuity IDs at positions "
                    + ", ".join(str(value) for value in invalid_reference_indexes)
                )
            result = _request_plan(
                settings,
                schema,
                system,
                user
                + "\n\nQUALITY RETRY: The previous draft failed the fixed-scene contract: "
                + "; ".join(reasons)
                + ". Rewrite the complete JSON result in the exact scene order. "
                + "Never move a description to another assignment. Expand every visual to 25-55 concrete English words. "
                + "Do not add unsupported events. Previous draft:\n"
                + json.dumps(result, ensure_ascii=False),
                trace=trace,
                cancelled=cancelled,
                request_label=(
                    f"scene block {batch_index}/{len(prepared_batches)} quality retry"
                ),
            )
        scenes = result.get("scenes")
        if not isinstance(scenes, list) or len(scenes) != scene_count:
            found = len(scenes) if isinstance(scenes, list) else 0
            raise VideoStoryboardPlanningError(
                f"The LLM returned {found} scenes for block {batch_index}; "
                f"{scene_count} were required."
            )
        _normalize_scene_continuity_references(
            scenes,
            semantic_units,
            ranges,
            continuity,
        )
        invalid_reference_indexes = _invalid_scene_reference_indexes(
            scenes,
            semantic_units,
            ranges,
            continuity,
        )
        if invalid_reference_indexes:
            raise VideoStoryboardPlanningError(
                "The LLM returned invalid continuity references for scene positions "
                + ", ".join(str(value) for value in invalid_reference_indexes)
                + "."
            )
        for unit in semantic_units:
            debug_units.append({**unit, "batch": batch_index})
        for scene, (unit_start, unit_end) in zip(
            scenes,
            ranges,
            strict=True,
        ):
            assigned_units = semantic_units[unit_start : unit_end + 1]
            exact_narration = " ".join(
                str(unit["text"]).strip() for unit in assigned_units
            ).strip()
            llm_narration = exact_narration
            aligned_start = (
                0.0
                if not combined_scenes
                else float(assigned_units[0]["start_seconds"])
            )
            combined_scenes.append(
                {
                    "id": f"{len(combined_scenes) + 1:03d}",
                    "duration": 0.0,
                    "narration": exact_narration,
                    "characters": _scene_reference_ids(
                        scene,
                        "character_state_ids",
                        continuity,
                        "characters",
                        float(assigned_units[0]["start_seconds"]),
                    ),
                    "locations": _scene_reference_ids(
                        scene,
                        "location_state_ids",
                        continuity,
                        "locations",
                        float(assigned_units[0]["start_seconds"]),
                    ),
                    "shot": _semantic_shot(
                        len(combined_scenes),
                        exact_narration,
                    ),
                    "era_state_id": _scene_era_state_id(scene, continuity),
                    "era": _scene_era_description(
                        scene,
                        continuity,
                        era_request,
                    ),
                    "prompt": str(
                        scene.get("visual") or scene.get("prompt") or ""
                    ).strip(),
                    "motion": _choice(
                        scene.get("motion"),
                        {"zoom_in", "zoom_out"},
                        "zoom_in" if len(combined_scenes) % 2 == 0 else "zoom_out",
                    ),
                    "transition": _choice(
                        scene.get("transition"),
                        {
                            "fade", "dissolve", "wipeleft", "wiperight",
                            "smoothleft", "smoothright", "circleopen", "circleclose",
                        },
                        "fade",
                    ),
                    "image_path": "",
                    "status": "planned",
                    "source_unit_start": int(assigned_units[0]["id"]),
                    "source_unit_end": int(assigned_units[-1]["id"]),
                    "source_text_start": int(assigned_units[0]["text_start"]),
                    "source_text_end": int(assigned_units[-1]["text_end"]),
                    "aligned_start_seconds": aligned_start,
                    "llm_narration": llm_narration,
                    "alignment_confidence": round(
                        _text_similarity(llm_narration, exact_narration),
                        3,
                    ),
                }
            )
        current_quality_warnings = _semantic_quality_warnings(combined_scenes)
        partial_scenes = _expand_cinematic_coverage(
            _finalize_aligned_durations(
                combined_scenes,
                float(batch.get("end_seconds") or total_duration),
            ),
            _MAX_COVERAGE_FRAME_SECONDS,
        )
        if partial:
            partial(
                {
                    "title": str(source.get("title") or "Storyboard"),
                    "base_seed": base_seed,
                    "style_mode": style_mode,
                    "style": global_style or {},
                    "source_duration_seconds": total_duration,
                    "voice_start_offset_seconds": voice_start_offset_seconds,
                    "narrative_context": _resolved_narrative_context(
                        era_request,
                        combined_scenes,
                    ),
                    "continuity": continuity,
                    "analysis_phase": "scenes",
                    "completed_blocks": batch_index if selected_plan == "scenes" else len(prepared_batches) * 3 + batch_index,
                    "total_blocks": phase_total,
                    "alignment_debug": {
                        "version": 2,
                        "maximum_coverage_frame_seconds": _MAX_COVERAGE_FRAME_SECONDS,
                        "semantic_scene_count": len(combined_scenes),
                        "units": [dict(unit) for unit in debug_units],
                        "warnings": list(alignment_warnings) + current_quality_warnings,
                    },
                    "scenes": partial_scenes,
                }
            )
    semantic_scenes = _finalize_aligned_durations(
        combined_scenes,
        total_duration,
    )
    alignment_warnings.extend(_semantic_quality_warnings(semantic_scenes))
    combined_scenes = _expand_cinematic_coverage(
        semantic_scenes,
        _MAX_COVERAGE_FRAME_SECONDS,
    )
    return {
        "title": str(source.get("title") or "Storyboard"),
        "base_seed": base_seed,
        "style_mode": style_mode,
        "style": global_style or {},
        "source_duration_seconds": total_duration,
        "voice_start_offset_seconds": voice_start_offset_seconds,
        "narrative_context": _resolved_narrative_context(
            era_request,
            semantic_scenes,
        ),
        "continuity": continuity,
        "alignment_debug": {
            "version": 2,
            "maximum_coverage_frame_seconds": _MAX_COVERAGE_FRAME_SECONDS,
            "semantic_scene_count": len(semantic_scenes),
            "units": debug_units,
            "warnings": alignment_warnings,
        },
        "scenes": combined_scenes,
    }


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


def _deterministic_unit_ranges(
    units: list[dict[str, Any]],
    scene_count: int,
    batch_start: float,
    batch_end: float,
    minimum: float,
    maximum: float,
) -> tuple[list[tuple[int, int]], list[str]]:
    warnings: list[str] = []
    starts = [0]
    for index in range(1, scene_count):
        target_time = batch_start + (batch_end - batch_start) * index / scene_count
        minimum_index = starts[-1] + 1
        maximum_index = len(units) - (scene_count - index)
        candidate = min(
            range(minimum_index, maximum_index + 1),
            key=lambda unit_index: abs(
                float(units[unit_index]["start_seconds"]) - target_time
            ),
        )
        starts.append(candidate)
    ends = [starts[index + 1] - 1 for index in range(scene_count - 1)]
    ends.append(len(units) - 1)
    ranges = list(zip(starts, ends, strict=True))
    scene_starts = [batch_start] + [
        float(units[start]["start_seconds"]) for start in starts[1:]
    ]
    durations = [
        (scene_starts[index + 1] if index + 1 < scene_count else batch_end)
        - scene_starts[index]
        for index in range(scene_count)
    ]
    for index, duration in enumerate(durations, start=1):
        if duration < minimum - 0.05 or duration > maximum + 0.05:
            warnings.append(
                f"Scene {index} aligned duration is {duration:.2f}s outside the requested {minimum:g}-{maximum:g}s range."
            )
    return ranges, warnings


def _assigned_narration(
    units: list[dict[str, Any]],
    unit_range: tuple[int, int],
) -> str:
    start, end = unit_range
    return " ".join(
        str(unit.get("text") or "").strip()
        for unit in units[start : end + 1]
    ).strip()


def _mismatched_assignment_indexes(
    scenes: object,
    units: list[dict[str, Any]],
    ranges: list[tuple[int, int]],
) -> list[int]:
    if not isinstance(scenes, list) or len(scenes) != len(ranges):
        return list(range(1, len(ranges) + 1))
    return [
        index
        for index, (scene, unit_range) in enumerate(
            zip(scenes, ranges, strict=True),
            start=1,
        )
        if not isinstance(scene, dict)
        or _text_similarity(
            str(scene.get("narration") or ""),
            _assigned_narration(units, unit_range),
        ) < 0.92
    ]


def _finalize_aligned_durations(
    scenes: list[dict[str, Any]],
    end_seconds: float,
) -> list[dict[str, Any]]:
    finalized = [dict(scene) for scene in scenes]
    for index, scene in enumerate(finalized):
        start = 0.0 if index == 0 else float(scene.get("aligned_start_seconds") or 0.0)
        next_start = (
            float(finalized[index + 1].get("aligned_start_seconds") or start)
            if index + 1 < len(finalized)
            else float(end_seconds)
        )
        scene["duration"] = round(max(0.1, next_start - start), 3)
        scene["start_seconds"] = round(start, 3)
    return finalized


def _expand_cinematic_coverage(
    semantic_scenes: list[dict[str, Any]],
    maximum_frame_seconds: float,
) -> list[dict[str, Any]]:
    """Split long semantic scenes into continuity-safe cinematic coverage frames."""
    maximum = max(1.0, float(maximum_frame_seconds))
    expanded: list[dict[str, Any]] = []
    for semantic_index, source in enumerate(semantic_scenes, start=1):
        duration = max(0.1, float(source.get("duration") or 0.0))
        coverage_count = max(1, math.ceil(duration / maximum))
        semantic_id = str(source.get("id") or f"{semantic_index:03d}")
        semantic_start = float(source.get("start_seconds") or 0.0)
        cursor = semantic_start
        allocated = 0.0
        for coverage_index in range(1, coverage_count + 1):
            if coverage_index < coverage_count:
                frame_duration = round(duration / coverage_count, 3)
                allocated += frame_duration
            else:
                frame_duration = round(duration - allocated, 3)
            strategy, framing = (
                ("semantic_shot", "")
                if coverage_index == 1
                else _coverage_framing(source, semantic_id, coverage_index)
            )
            scene = {
                **source,
                "id": f"{len(expanded) + 1:03d}",
                "duration": max(0.1, frame_duration),
                "start_seconds": round(cursor, 3),
                "aligned_start_seconds": round(cursor, 3),
                "semantic_scene_id": semantic_id,
                "coverage_index": coverage_index,
                "coverage_count": coverage_count,
                "shot_strategy": strategy,
                "coverage_prompt_version": 2,
            }
            if coverage_index > 1:
                # Keep the semantic visual identical; only the application-owned
                # camera coverage changes for additional frames.
                scene["shot"] = framing.rstrip(".")
                scene["motion"] = (
                    "zoom_in" if len(expanded) % 2 == 0 else "zoom_out"
                )
                scene["transition"] = "dissolve"
            expanded.append(scene)
            cursor += scene["duration"]
    return expanded


def _coverage_framing(
    scene: dict[str, Any],
    semantic_id: str,
    coverage_index: int,
) -> tuple[str, str]:
    characters = _string_list(scene.get("characters"))
    if characters:
        subject = characters[(coverage_index - 2) % len(characters)]
        choices = [
            ("close_up", f"Close-up of {subject}."),
            ("profile", f"Profile view of {subject}."),
            ("three_quarter", f"Three-quarter view of {subject}."),
            ("low_angle", f"Low-angle view of {subject}."),
        ]
    else:
        choices = [
            ("close_up", "Close-up of the most important visible object or landmark."),
            ("side_view", "Side view of the main visible subject."),
            ("three_quarter", "Three-quarter view of the main visible subject."),
            ("high_angle", "High-angle view of the most important visual element."),
        ]
    stable_key = f"{semantic_id}|{scene.get('prompt', '')}".encode("utf-8")
    offset = int.from_bytes(hashlib.sha256(stable_key).digest()[:2], "big")
    return choices[(offset + coverage_index - 2) % len(choices)]


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


def _character_registry(style: dict[str, Any] | None) -> dict[str, str]:
    registry: dict[str, str] = {}
    definitions = style.get("characters", []) if isinstance(style, dict) else []
    for definition in _string_list(definitions):
        state_id = definition.split(":", 1)[0].strip()
        if state_id:
            registry[state_id] = definition
    return registry


def _merge_character_states(
    registry: dict[str, str],
    raw_states: object,
) -> None:
    if not isinstance(raw_states, list):
        return
    for raw_state in raw_states:
        if not isinstance(raw_state, dict):
            continue
        state_id = re.sub(
            r"[^\w]+",
            "_",
            str(raw_state.get("id") or "").strip().casefold(),
            flags=re.UNICODE,
        ).strip("_")
        name = str(raw_state.get("name") or "").strip()
        description = str(raw_state.get("description") or "").strip()
        if not state_id or not description:
            continue
        matching_override = next(
            (
                existing_id
                for existing_id in registry
                if name and existing_id.casefold() == name.casefold()
            ),
            "",
        )
        if matching_override:
            # A user-authored identity definition is authoritative. Repoint the
            # scene to it instead of adding a competing generated definition.
            raw_state["id"] = matching_override
            continue
        if name and name.casefold() not in description.casefold():
            description = f"{name}, {description}"
        registry.setdefault(state_id, f"{state_id}: {description}")


def _scene_character_state_ids(
    scene: dict[str, Any],
    narration: str,
    registry: dict[str, str],
) -> list[str]:
    raw_states = scene.get("character_states")
    if isinstance(raw_states, list):
        active: list[str] = []
        for raw_state in raw_states:
            if isinstance(raw_state, dict):
                state_id = re.sub(
                    r"[^\w]+",
                    "_",
                    str(raw_state.get("id") or "").strip().casefold(),
                    flags=re.UNICODE,
                ).strip("_")
            else:
                state_id = str(raw_state or "").strip()
            if state_id and state_id in registry and state_id not in active:
                active.append(state_id)
        return active
    return _active_scene_characters(
        narration,
        {"characters": list(registry.values())},
    )


def _semantic_shot(scene_index: int, narration: str) -> str:
    stable_key = f"{scene_index}|{narration}".encode("utf-8")
    offset = int.from_bytes(hashlib.sha256(stable_key).digest()[:2], "big")
    return _SHOT_SEQUENCE[(scene_index + offset) % len(_SHOT_SEQUENCE)]


def _active_scene_characters(
    narration: str,
    style: dict[str, Any] | None,
) -> list[str]:
    definitions = style.get("characters", []) if isinstance(style, dict) else []
    if not isinstance(definitions, list):
        return []
    mentioned: list[tuple[int, str]] = []
    for definition in definitions:
        name = str(definition).split(":", 1)[0].strip()
        if not name:
            continue
        match = re.search(
            rf"(?<!\w){re.escape(name)}(?!\w)",
            narration,
            flags=re.IGNORECASE,
        )
        if match:
            mentioned.append((match.start(), name))
    return [name for _position, name in sorted(mentioned)]


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


def _seed_continuity_from_legacy_characters(
    continuity: dict[str, Any],
    registry: dict[str, str],
    total_duration: float,
) -> None:
    characters = continuity.setdefault("characters", [])
    for legacy_id, definition in registry.items():
        raw_description = str(definition).split(":", 1)[-1].strip()
        name = str(legacy_id).strip()
        entity_id = _normalized_entity_id(legacy_id, "char")
        if not entity_id:
            continue
        characters.append(
            {
                "id": entity_id,
                "name": name or str(legacy_id),
                "aliases": [str(legacy_id)],
                "identity_description": "",
                "user_authored": True,
                "states": [
                    {
                        "id": f"{entity_id}_state_1",
                        "description": raw_description,
                        "from_seconds": 0.0,
                        "to_seconds": max(0.0, float(total_duration)),
                        "source_unit_start": 0,
                        "change_reason": "user_override",
                        "evidence": "",
                    }
                ],
            }
        )


def _continuity_analyzer_schema() -> dict[str, Any]:
    character_event = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "id", "name", "aliases", "effective_unit_id", "event_type",
            "context_description", "identity_description", "appearance",
            "evidence",
        ],
        "properties": {
            "id": {"type": "string", "maxLength": 80},
            "name": {"type": "string", "maxLength": 100},
            "aliases": {
                "type": "array", "maxItems": 8,
                "items": {"type": "string", "maxLength": 100},
            },
            "effective_unit_id": {"type": "integer"},
            "event_type": {
                "type": "string",
                "enum": ["first_appearance", "stable_revelation", "explicit_change"],
            },
            "context_description": {"type": "string", "maxLength": 260},
            "identity_description": {"type": "string", "maxLength": 320},
            "appearance": appearance_schema("character"),
            "evidence": {"type": "string", "maxLength": 220},
        },
    }
    location_event = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "id", "name", "aliases", "effective_unit_id", "event_type",
            "context_description", "identity_description", "appearance",
            "evidence",
        ],
        "properties": {
            **character_event["properties"],
            "appearance": appearance_schema("location"),
        },
    }
    era_event = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "id", "effective_unit_id", "description", "material_culture", "evidence",
        ],
        "properties": {
            "id": {"type": "string", "maxLength": 80},
            "effective_unit_id": {"type": "integer"},
            "description": {"type": "string", "maxLength": 200},
            "material_culture": {"type": "string", "maxLength": 260},
            "evidence": {"type": "string", "maxLength": 220},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["character_events", "location_events", "era_events", "groups"],
        "properties": {
            "character_events": {"type": "array", "items": character_event},
            "location_events": {"type": "array", "items": location_event},
            "era_events": {"type": "array", "items": era_event},
            "groups": group_schema(),
        },
    }


def _missing_character_repair_schema() -> dict[str, Any]:
    """Small schema used only when the main pass omitted a likely proper name."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["character_events"],
        "properties": {
            "character_events": {
                "type": "array",
                "items": deepcopy(
                    _continuity_analyzer_schema()["properties"]["character_events"][
                        "items"
                    ]
                ),
            }
        },
    }


def _continuity_visual_completion_schema(item_count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["replacements"],
        "properties": {
            "replacements": {
                "type": "array",
                "minItems": item_count,
                "maxItems": item_count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "collection",
                        "id",
                        "effective_unit_id",
                        "identity_description",
                    ],
                    "properties": {
                        "collection": {
                            "type": "string",
                            "enum": ["character_events", "location_events"],
                        },
                        "id": {"type": "string"},
                        "effective_unit_id": {"type": "integer"},
                        "identity_description": {"type": "string", "maxLength": 320},
                    },
                },
            }
        },
    }


def _unit_binder_schema(unit_count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["unit_bindings"],
        "properties": {
            "unit_bindings": {
                "type": "array",
                "minItems": unit_count,
                "maxItems": unit_count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "unit_id", "character_ids", "location_ids", "era_id",
                    ],
                    "properties": {
                        "unit_id": {"type": "integer"},
                        "character_ids": {
                            "type": "array", "items": {"type": "string"},
                        },
                        "location_ids": {
                            "type": "array", "items": {"type": "string"},
                        },
                        "era_id": {"type": "string"},
                    },
                },
            },
        },
    }


def _continuity_analyzer_validation_issues(
    result: dict[str, Any],
    semantic_units: list[dict[str, Any]],
    continuity: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    expected_ids = {int(unit["id"]) for unit in semantic_units}
    unit_lookup = {int(unit["id"]): unit for unit in semantic_units}
    for key in ("character_events", "location_events", "era_events"):
        events = result.get(key)
        if not isinstance(events, list):
            issues.append(f"{key} must be an array")
            continue
        for event in events:
            if not isinstance(event, dict) or not str(event.get("id") or "").strip():
                issues.append(f"every {key} item must contain a non-empty ID")
                break
            identifier = str(event.get("id") or "")
            if not re.fullmatch(r"[a-z0-9_]+", identifier):
                issues.append(f"every {key} ID must be lowercase_snake_case")
                break
            try:
                effective_unit_id = int(event.get("effective_unit_id"))
            except (TypeError, ValueError):
                effective_unit_id = -1
            if effective_unit_id not in expected_ids:
                issues.append(f"every {key} item must use a supplied unit ID")
                break
            for field, maximum_words in (
                ("context_description", 38),
                ("identity_description", 45),
                ("state_description", 38),
                ("material_culture", 38),
                ("evidence", 32),
            ):
                description = str(event.get(field) or "").strip()
                if description and len(description.split()) > maximum_words:
                    issues.append(
                        f"{key}.{field} must contain no more than {maximum_words} words"
                    )
                    break
            event_type = str(event.get("event_type") or "")
            if (
                (
                    key == "character_events"
                    and event_type
                    in {"first_appearance", "stable_revelation", "explicit_change"}
                )
                or (key == "location_events" and event_type == "first_appearance")
            ):
                if _continuity_event_needs_visual_completion(key, event, continuity):
                    issues.append(
                        f"{key} visual states need concrete correctly separated "
                        "identity and state traits, not placeholders"
                    )
            if key == "character_events" and "appearance" not in event:
                age_issue = _explicit_age_state_issue(event, unit_lookup)
                if age_issue:
                    issues.append(age_issue)
    missing_people = _missing_obvious_people(result, semantic_units, continuity)
    if missing_people:
        issues.append(
            "missing first_appearance character events for explicitly named people: "
            + ", ".join(missing_people)
        )
    missing_changes = _missing_explicit_character_change_units(
        result, semantic_units
    )
    if missing_changes:
        issues.append(
            "missing explicit_change character events for visible age, clothing or "
            "assistive-equipment changes at unit IDs: "
            + ", ".join(str(value) for value in missing_changes)
        )
    return issues


def _contains_continuity_placeholder(value: object) -> bool:
    return bool(
        re.search(
            r"\b(unknown|unspecified|not described|not specified|"
            r"not mentioned|no (?:specific )?[a-z -]{0,30}mentioned|"
            r"assumed|presumed|likely)\b",
            str(value or "").casefold(),
        )
    )


def _placeholder_continuity_events(
    result: dict[str, Any],
    continuity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for collection in ("character_events", "location_events"):
        for event in result.get(collection, []):
            if not isinstance(event, dict):
                continue
            if _continuity_event_needs_visual_completion(collection, event, continuity):
                record = _entity_by_reference(
                    (continuity or {}).get("characters" if collection == "character_events" else "locations", []),
                    event.get("id"), event.get("name"),
                ) or {}
                states = record.get("states", [])
                previous = states[-1] if states else {}
                values.append(
                    {
                        "collection": collection,
                        "id": str(event.get("id") or ""),
                        "name": str(event.get("name") or record.get("name") or ""),
                        "context": str(event.get("context_description") or record.get("context_description") or ""),
                        "evidence": str(event.get("evidence") or ""),
                        "effective_unit_id": event.get("effective_unit_id"),
                        "known_identity_facts": _without_continuity_placeholders(
                            record.get("identity_description") or event.get("identity_description")
                        ),
                        "known_state_facts": _without_continuity_placeholders(
                            previous.get("description") or event.get("state_description")
                        ),
                    }
                )
    return list(
        {
            (
                value["collection"],
                value["id"],
                value["effective_unit_id"],
            ): value
            for value in values
        }.values()
    )


def _without_continuity_placeholders(value: object) -> str:
    clauses = [
        clause.strip()
        for clause in re.split(r"\s*[,;]\s*", str(value or ""))
        if clause.strip() and not _contains_continuity_placeholder(clause)
    ]
    return ", ".join(clauses)


def _continuity_event_needs_visual_completion(
    collection: str,
    event: dict[str, Any],
    continuity: dict[str, Any] | None = None,
) -> bool:
    # Empty fields in an update mean "no new appearance facts", not an
    # invitation to re-design a known entity. Only bootstrap new identities.
    if str(event.get("event_type") or "") != "first_appearance":
        return False
    records = (continuity or {}).get("characters" if collection == "character_events" else "locations", [])
    if _entity_by_reference(records, event.get("id"), event.get("name")):
        return False
    identity = str(event.get("identity_description") or "").strip()
    state = str(event.get("state_description") or "").strip()
    combined = f"{identity} {state}"
    if _contains_continuity_placeholder(combined):
        return True
    event_type = str(event.get("event_type") or "")
    if collection == "location_events":
        return event_type == "first_appearance" and not identity
    # Age, clothes and mobility are NOT mandatory. This works equally for
    # people and animals, without a brittle enumeration of species.
    return not identity or identity.casefold().strip(" .") in {
        "female", "male", "woman", "man", "girl", "boy", "animal",
    }


def _continuity_visual_completion_messages(
    events: list[dict[str, Any]],
    semantic_units: list[dict[str, Any]],
    *,
    era_request: str,
) -> tuple[str, str]:
    system = (
        "Complete only missing visual continuity traits and return the requested JSON. "
        "Preserve every supplied collection, ID and unit ID exactly and return one replacement per input item in the same order. "
        "Choose concise concrete plausible traits consistent with name, role, narration and era. Never use unknown, unspecified, not described, assumed, presumed or likely. "
        "Preserve supplied known facts exactly; fill only absent visual traits for this first design. "
        "Complete identity_description only: at most 25 words, 3-5 distinctive permanent visual anchors. Identify the exact named entity using its context and evidence, not another subject nearby in the narration. "
        "Do not change temporal appearance, invent ages, clothes, injuries or mobility information. For places describe stable architecture/materials, not current events or condition. "
        "Invent visual appearance only; never invent an action, event, relationship or new entity."
    )
    units = [
        {
            "unit_id": int(unit["id"]),
            "start_seconds": float(unit.get("start_seconds") or 0.0),
            "narration": str(unit.get("text") or ""),
        }
        for unit in semantic_units
    ]
    user = (
        f"Era override: {era_request or '(none)'}\n"
        "Incomplete definitions:\n"
        f"{json.dumps(events, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "Narration evidence:\n"
        f"{json.dumps(units, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "Complete every item with actual production-ready visual choices."
    )
    return system, user


def _apply_continuity_visual_completions(
    result: dict[str, Any],
    completion: dict[str, Any],
) -> None:
    replacements = completion.get("replacements")
    if not isinstance(replacements, list):
        return
    for replacement in replacements:
        if not isinstance(replacement, dict):
            continue
        collection = str(replacement.get("collection") or "")
        if collection not in {"character_events", "location_events"}:
            continue
        try:
            replacement_unit = int(replacement.get("effective_unit_id"))
        except (TypeError, ValueError):
            continue
        matching = None
        for event in result.get(collection, []):
            if not isinstance(event, dict) or str(event.get("id") or "") != str(
                replacement.get("id") or ""
            ):
                continue
            try:
                event_unit = int(event.get("effective_unit_id"))
            except (TypeError, ValueError):
                continue
            if event_unit == replacement_unit:
                matching = event
                break
        if matching is None:
            continue
        fields = ("identity_description",) if "appearance" in matching else ("identity_description", "state_description")
        for field in fields:
            value = str(replacement.get(field) or "").strip()
            if value:
                # Completion may fill absent slots, never replace facts already
                # supplied for this new entity (even within the same block).
                matching[field], _ = merge_visual_description(
                    _without_continuity_placeholders(matching.get(field)),
                    value, initial=True,
                )


def _normalize_continuity_event_semantics(
    result: dict[str, Any],
    semantic_units: list[dict[str, Any]],
    continuity: dict[str, Any],
) -> None:
    """Keep discovery facts distinct from real timeline changes."""
    _normalize_continuity_event_ids(result, continuity)
    unit_text = {
        int(unit["id"]): str(unit.get("text") or "")
        for unit in semantic_units
        if isinstance(unit, dict) and isinstance(unit.get("id"), int)
    }
    character_change_pattern = re.compile(
        r"\b(years later|decades later|much later|later in life|now aged|"
        r"grew older|became older|changed clothes|changed into|began using|started using|"
        r"now (?:wearing|uses|needed)|removed (?:his|her) glasses|"
        r"a[nñ]os despu[eé]s|d[eé]cadas despu[eé]s|mucho tiempo despu[eé]s|"
        r"ya (?:ten[ií]a|era)|envejec|se cambi[oó] de ropa|empez[oó] a usar|"
        r"comenz[oó] a utilizar|se quit[oó] las gafas)",
        flags=re.IGNORECASE,
    )
    location_change_pattern = re.compile(
        r"\b(destroyed|demolished|rebuilt|renovated|burned|flooded|collapsed|"
        r"abandoned|ruined|destruido|demolido|reconstruido|reformado|"
        r"incendiado|inundado|derrumbado|abandonado|en ruinas)\b",
        flags=re.IGNORECASE,
    )
    for event_key, record_key in (
        ("character_events", "characters"),
        ("location_events", "locations"),
    ):
        known_ids = {
            str(record.get("id") or "").casefold()
            for record in continuity.get(record_key, [])
            if isinstance(record, dict)
        }
        seen_ids = set(known_ids)
        events = result.get(event_key)
        if not isinstance(events, list):
            continue
        events.sort(key=_event_unit_order)
        for event in events:
            if not isinstance(event, dict):
                continue
            event.setdefault("_reported_event_type", str(event.get("event_type") or ""))
            typed = isinstance(event.get("appearance"), dict)
            if event_key == "character_events" and not typed:
                _separate_character_identity_and_state(event)
            normalize_event_appearance(event, "character" if event_key == "character_events" else "location")
            identifier = str(event.get("id") or "").casefold()
            try:
                narration = unit_text.get(int(event.get("effective_unit_id")), "")
            except (TypeError, ValueError):
                narration = ""
            is_known = identifier in seen_ids
            if not is_known:
                event["event_type"] = "first_appearance"
                seen_ids.add(identifier)
                continue
            if typed:
                # The model interprets changes in any source language. A quote
                # corroborates an update, but never converts an action into a
                # state: only the entity's typed appearance slots are accepted.
                if event.get("event_type") == "first_appearance":
                    event["event_type"] = "stable_revelation"
                continue
            change_pattern = (
                character_change_pattern
                if event_key == "character_events"
                else location_change_pattern
            )
            if change_pattern.search(narration):
                event["event_type"] = "explicit_change"
            elif str(event.get("event_type") or "") == "first_appearance":
                event["event_type"] = "stable_revelation"
            elif str(event.get("event_type") or "") == "explicit_change":
                quote = re.sub(r"^Unit\s+\d+\s*:\s*", "", str(event.get("evidence") or ""), flags=re.I).strip(' .\"“”')
                if len(quote) < 12 or quote.casefold() not in narration.casefold():
                    event["event_type"] = "stable_revelation"


def _normalize_continuity_event_ids(
    result: dict[str, Any],
    continuity: dict[str, Any],
) -> None:
    """Resolve known IDs and transliterate new model-created IDs before validation."""
    for event_key, record_key, prefix in (
        ("character_events", "characters", "char"),
        ("location_events", "locations", "loc"),
    ):
        records = [
            record
            for record in continuity.get(record_key, [])
            if isinstance(record, dict)
        ]
        for event in result.get(event_key, []):
            if not isinstance(event, dict):
                continue
            existing = _entity_by_reference(
                records,
                event.get("id"),
                event.get("name"),
            )
            event["id"] = (
                str(existing.get("id") or "")
                if existing is not None
                else _normalized_entity_id(
                    event.get("id") or event.get("name"),
                    prefix,
                )
            )
            # Resolve later events against new entities in this SAME response,
            # not just the ledger from previous blocks.
            if existing is None:
                records.append({"id": event["id"], "name": event.get("name", ""),
                                "aliases": event.get("aliases", [])})
            else:
                existing = deepcopy(existing)
                existing["aliases"] = list(dict.fromkeys([
                    *existing.get("aliases", []), *event.get("aliases", []),
                ]))
                records = [existing if r.get("id") == existing["id"] else r for r in records]
    eras = [
        era for era in continuity.get("eras", []) if isinstance(era, dict)
    ]
    for event in result.get("era_events", []):
        if not isinstance(event, dict):
            continue
        raw_id = str(event.get("id") or "")
        description = str(event.get("description") or "")
        existing = next(
            (
                era
                for era in eras
                if raw_id.casefold()
                in {
                    str(era.get("id") or "").casefold(),
                    str(era.get("description") or "").casefold(),
                }
                or description.casefold()
                == str(era.get("description") or "").casefold()
            ),
            None,
        )
        event["id"] = (
            str(existing.get("id") or "")
            if existing is not None
            else _normalized_entity_id(raw_id or description, "era")
        )


def _separate_character_identity_and_state(event: dict[str, Any]) -> None:
    """Move mutable age/wardrobe wording out of the permanent identity field."""
    identity = str(event.get("identity_description") or "").strip()
    state = str(event.get("state_description") or "").strip()
    if not identity:
        return
    age_pattern = re.compile(
        r"\b(?:young adult|(?:early|mid|late)[ -](?:teens|twenties|thirties|"
        r"forties|fifties|sixties)|middle-aged|elderly|older|aged? \d+|"
        r"\d+[ -]years?[ -]old)\b",
        flags=re.IGNORECASE,
    )
    age_match = age_pattern.search(identity)
    if age_match:
        age_value = age_match.group(0).strip()
        identity = age_pattern.sub("", identity, count=1)
        if age_value.casefold() not in state.casefold() and not re.search(
            r"\b(?:age|newborn|baby|infant|child|girl|boy|puppy|kitten|teen|"
            r"young|adult|woman|man|middle-aged|elderly|older|twenties|"
            r"thirties|forties|fifties|sixties|years? old)\b",
            state,
            flags=re.IGNORECASE,
        ):
            state = f"{age_value}, {state}".strip(", ")
    wardrobe_match = re.search(
        r"\b(?:wearing|dressed in)\b.+$",
        identity,
        flags=re.IGNORECASE,
    )
    if wardrobe_match:
        wardrobe = wardrobe_match.group(0).strip(" ,.;")
        identity = identity[: wardrobe_match.start()]
        if wardrobe.casefold() not in state.casefold():
            state = f"{state}, {wardrobe}".strip(", ")
    identity = re.sub(r"\s{2,}", " ", identity).strip(" ,.;-")
    state = re.sub(r"\s{2,}", " ", state).strip(" ,.;-")
    event["identity_description"] = identity
    event["state_description"] = state


def _normalize_continuity_first_appearance_anchors(
    result: dict[str, Any],
    semantic_units: list[dict[str, Any]],
    _continuity: dict[str, Any],
) -> None:
    """Anchor new entities to their earliest literal mention in the block.

    Small models often anchor a character where physical details are first
    revealed instead of where the name first appears. Literal source matching
    is deterministic and leaves genuine later changes untouched.
    """
    ordered_units = sorted(
        (unit for unit in semantic_units if isinstance(unit, dict)),
        key=lambda unit: int(unit.get("id") or 0),
    )
    for event_key in ("character_events", "location_events"):
        events = result.get(event_key)
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "")
            if event_type != "first_appearance":
                continue
            terms = [event.get("name"), *_string_list(event.get("aliases"))]
            earliest_id: int | None = None
            for unit in ordered_units:
                narration = str(unit.get("text") or "")
                if any(
                    str(term or "").strip()
                    and re.search(
                        rf"(?<!\w){re.escape(str(term).strip())}(?!\w)",
                        narration,
                        re.IGNORECASE,
                    )
                    for term in terms
                ):
                    earliest_id = int(unit["id"])
                    break
            if earliest_id is not None:
                # A literal name can prove an EARLIER appearance, never refute
                # the LLM's earlier group/pronoun introduction.
                try:
                    supplied = int(event.get("effective_unit_id"))
                except (TypeError, ValueError):
                    supplied = earliest_id
                valid = {int(unit["id"]) for unit in ordered_units}
                event["effective_unit_id"] = min(supplied, earliest_id) if supplied in valid else earliest_id


def _partition_continuity_issues(
    issues: list[str],
) -> tuple[list[str], list[str]]:
    """Keep heuristic omissions visible without making analysis destructive.

    Capitalization is only a hint that a token may be a person's name. It is
    not strong enough evidence to force a small model to invent a character,
    nor to discard all completed analysis blocks.
    """
    soft_prefixes = (
        "missing first_appearance character events for explicitly named people:",
        "character_events visual states need concrete correctly separated",
        "location_events visual states need concrete correctly separated",
    )
    hard: list[str] = []
    soft: list[str] = []
    for issue in issues:
        (soft if issue.startswith(soft_prefixes) else hard).append(issue)
    return hard, soft


def _explicit_age_state_issue(
    event: dict[str, Any],
    unit_lookup: dict[int, dict[str, Any]],
) -> str:
    try:
        unit_id = int(event.get("effective_unit_id"))
    except (TypeError, ValueError):
        return ""
    narration = str(unit_lookup.get(unit_id, {}).get("text") or "").casefold()
    state = str(event.get("state_description") or "").casefold()
    if not state:
        return ""
    if re.search(r"\b(was born|is born|newborn|naci[oó]|al nacer|nacimiento)\b", narration):
        if not re.search(r"\b(newborn|new-born|baby|infant)\b", state):
            return (
                f"character event at unit {unit_id} describes a birth and its "
                "state must explicitly say newborn, baby or infant"
            )
    if re.search(
        r"\b(as a child|during childhood|de niñ[oa]|cuando era niñ[oa]|infancia)\b",
        narration,
    ) and not re.search(r"\b(child|boy|girl|school-age)\b", state):
        return (
            f"character event at unit {unit_id} explicitly describes childhood "
            "and its state must say child, boy or girl"
        )
    return ""


def _missing_explicit_character_change_units(
    result: dict[str, Any],
    semantic_units: list[dict[str, Any]],
) -> list[int]:
    if "groups" in result or any(
        isinstance(event, dict) and "appearance" in event
        for event in result.get("character_events", [])
    ):
        # The typed contract already delegates temporal semantics to the LLM.
        # A word like "wearing", "childhood" or "wheelchair" alone neither
        # identifies its owner nor proves an evolution. Retain this older
        # heuristic only for compatibility with pre-v3 replies.
        return []
    change_pattern = re.compile(
        r"\b(as a child|during childhood|de niñ[oa]|cuando era niñ[oa]|"
        r"at the age of|a la edad de|years old|años de edad|"
        r"synthetic voice|speech synthesizer|voz sintética|sintetizador de voz|"
        r"wheelchair|silla de ruedas|without glasses|removed (?:his|her) glasses|"
        r"sin gafas|se quitó las gafas|changed clothes|se cambió de ropa|"
        r"wearing|dressed in|vestid[oa] con|llevaba puesto)\b",
        flags=re.IGNORECASE,
    )
    event_units = {
        int(event.get("effective_unit_id"))
        for event in result.get("character_events", [])
        if isinstance(event, dict)
        and isinstance(event.get("effective_unit_id"), int)
    }
    return [
        int(unit["id"])
        for unit in semantic_units
        if change_pattern.search(str(unit.get("text") or ""))
        and not any(abs(int(unit["id"]) - event_unit) <= 1 for event_unit in event_units)
    ]


def _repair_explicit_character_changes(
    result: dict[str, Any],
    semantic_units: list[dict[str, Any]],
    continuity: dict[str, Any],
    focus_ids: list[str],
) -> bool:
    missing = _missing_explicit_character_change_units(result, semantic_units)
    if not missing:
        return False
    records = [
        value for value in continuity.get("characters", [])
        if isinstance(value, dict)
    ]
    if not records:
        return False
    focus_records = [
        record for record in records
        if str(record.get("id") or "") in set(focus_ids)
    ]
    repaired = False
    events = result.setdefault("character_events", [])
    if not isinstance(events, list):
        return False
    units_by_id = {int(unit["id"]): unit for unit in semantic_units}
    for unit_id in missing:
        unit = units_by_id.get(unit_id, {})
        narration = str(unit.get("text") or "")
        folded = narration.casefold()
        named = [
            record for record in records
            if any(
                value and re.search(
                    rf"(?<!\w){re.escape(str(value).casefold())}(?!\w)",
                    folded,
                )
                for value in [record.get("name"), *record.get("aliases", [])]
            )
        ]
        candidates = named or focus_records or (records if len(records) == 1 else [])
        if len(candidates) != 1:
            continue
        record = candidates[0]
        states = [
            state for state in record.get("states", []) if isinstance(state, dict)
        ]
        previous = str(states[-1].get("description") or "").strip() if states else ""
        delta = _deterministic_visible_change(folded)
        if not delta:
            continue
        state_description = ", ".join(
            value for value in (previous, delta) if value
        )
        events.append(
            {
                "id": str(record.get("id") or ""),
                "name": str(record.get("name") or ""),
                "aliases": [],
                "effective_unit_id": unit_id,
                "event_type": "explicit_change",
                "identity_description": "",
                "state_description": state_description[:260],
                "evidence": narration[:220],
            }
        )
        repaired = True
    return repaired


def _deterministic_visible_change(narration: str) -> str:
    if "synthetic voice" in narration or "voz sintética" in narration:
        return "using a synthetic voice and speech synthesizer"
    if "speech synthesizer" in narration or "sintetizador de voz" in narration:
        return "using a speech synthesizer"
    if "wheelchair" in narration or "silla de ruedas" in narration:
        return "using a wheelchair"
    if any(value in narration for value in ("without glasses", "removed his glasses", "removed her glasses", "sin gafas", "se quitó las gafas")):
        return "without glasses"
    return ""


def _missing_obvious_people(
    result: dict[str, Any],
    semantic_units: list[dict[str, Any]],
    continuity: dict[str, Any],
) -> list[str]:
    narration = " ".join(str(unit.get("text") or "") for unit in semantic_units)
    candidates = list(dict.fromkeys(
        match.group(0).strip()
        for match in re.finditer(
            r"(?<!\w)[A-ZÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÄËÏÖÜÑÇ]"
            r"[\wÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÄËÏÖÜÑÇáéíóúàèìòùâêîôûäëïöüñç'-]+"
            r"(?:\s+[A-ZÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÄËÏÖÜÑÇ]"
            r"[\wÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÄËÏÖÜÑÇáéíóúàèìòùâêîôûäëïöüñç'-]+){1,3}",
            narration,
        )
    ))
    leading_discourse_words = {
        "and", "as", "but", "during", "finally", "he", "her", "his",
        "meanwhile", "she", "the", "then", "they", "while", "with", "yet",
        "aquel", "aquella", "con", "ella", "entonces", "era", "es", "fue",
        "mientras", "pero", "sin", "su", "sus", "un", "una", "unos",
        "unas", "y", "él",
    }
    cleaned_candidates: list[str] = []
    for candidate in candidates:
        words = candidate.split()
        while len(words) > 1 and words[0].casefold() in leading_discourse_words:
            words.pop(0)
        cleaned = " ".join(words)
        if cleaned and cleaned not in cleaned_candidates:
            cleaned_candidates.append(cleaned)
    for match in re.finditer(
        r"(?<!\w)[A-ZÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÄËÏÖÜÑÇ]"
        r"[\wÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÄËÏÖÜÑÇáéíóúàèìòùâêîôûäëïöüñç'-]{2,}(?!\w)",
        narration,
    ):
        candidate = match.group(0)
        if candidate.casefold() in leading_discourse_words:
            continue
        before = narration[: match.start()].rstrip()
        after = narration[match.end():]
        if not before or before[-1] in ".!?…“”\"'«»¡¿—–-:":
            continue
        if re.search(
            r"\b(?:the|el|la|los|las|in|at|from|to|near|inside|outside|"
            r"en|desde|hacia|cerca de|dentro de)\s*$",
            before,
            re.IGNORECASE,
        ):
            continue
        if re.search(
            r"[A-ZÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÄËÏÖÜÑÇ][\w'-]*\s+$", before
        ) or re.match(
            r"^\s+[A-ZÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÄËÏÖÜÑÇ]", after
        ):
            continue
        if candidate not in cleaned_candidates:
            cleaned_candidates.append(candidate)
    candidates = cleaned_candidates
    if not candidates:
        return []
    classified_non_people = [
        str(event.get(field) or "").casefold()
        for key, fields in (
            ("location_events", ("name", "id")),
            ("era_events", ("description", "id")),
        )
        for event in result.get(key, [])
        if isinstance(event, dict)
        for field in fields
        if str(event.get(field) or "").strip()
    ]
    character_terms = [
        str(event.get(field) or "").casefold()
        for event in result.get("character_events", [])
        if isinstance(event, dict)
        for field in ("name", "id")
        if str(event.get(field) or "").strip()
    ]
    character_terms.extend(
        str(record.get(field) or "").casefold()
        for record in continuity.get("characters", [])
        if isinstance(record, dict)
        for field in ("name", "id")
        if str(record.get(field) or "").strip()
    )
    for record in continuity.get("characters", []):
        if isinstance(record, dict):
            character_terms.extend(
                str(alias).casefold()
                for alias in record.get("aliases", [])
                if str(alias).strip()
            )

    def represented(candidate: str, terms: list[str]) -> bool:
        folded = candidate.casefold().replace("_", " ")
        return any(
            folded == term.replace("_", " ")
            or folded in term.replace("_", " ")
            or term.replace("_", " ") in folded
            for term in terms
        )

    return [
        candidate for candidate in candidates
        if not represented(candidate, character_terms)
        and not represented(candidate, classified_non_people)
    ]


def _unit_binder_validation_issues(
    result: dict[str, Any],
    semantic_units: list[dict[str, Any]],
    continuity: dict[str, Any],
) -> list[str]:
    bindings = result.get("unit_bindings")
    if not isinstance(bindings, list):
        return ["unit_bindings must be an array"]
    expected_ids = [int(unit["id"]) for unit in semantic_units]
    actual_ids = [
        value.get("unit_id") for value in bindings if isinstance(value, dict)
    ]
    issues: list[str] = []
    if actual_ids != expected_ids:
        issues.append("unit_bindings must contain each supplied unit ID once and in order")
    valid_characters = {
        str(value.get("id") or "") for value in continuity.get("characters", [])
        if isinstance(value, dict)
    }
    valid_locations = {
        str(value.get("id") or "") for value in continuity.get("locations", [])
        if isinstance(value, dict)
    }
    valid_eras = {
        str(value.get("id") or "") for value in continuity.get("eras", [])
        if isinstance(value, dict)
    }
    for binding in bindings:
        if not isinstance(binding, dict):
            issues.append("every unit_binding must be an object")
            continue
        for field, valid in (
            ("character_ids", valid_characters),
            ("location_ids", valid_locations),
        ):
            values = binding.get(field)
            if not isinstance(values, list):
                issues.append(f"{field} must be an array")
                continue
            if any(
                not isinstance(value, str)
                or not re.fullmatch(r"[a-z0-9_]+", value)
                or value not in valid
                for value in values
            ):
                issues.append(f"{field} may contain only supplied ledger IDs")
        era_id = binding.get("era_id")
        if not isinstance(era_id, str) or (
            era_id and (not re.fullmatch(r"[a-z0-9_]+", era_id) or era_id not in valid_eras)
        ):
            issues.append("era_id must be empty or an exact supplied ledger ID")
    return list(dict.fromkeys(issues))


def _normalize_unit_binding_references(
    result: dict[str, Any],
    continuity: dict[str, Any],
) -> None:
    """Resolve binder names/aliases to ledger IDs and drop invented references."""
    bindings = result.get("unit_bindings")
    if not isinstance(bindings, list):
        return
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        for field, collection in (
            ("character_ids", "characters"),
            ("location_ids", "locations"),
        ):
            records = [
                value for value in continuity.get(collection, [])
                if isinstance(value, dict)
            ]
            normalized: list[str] = []
            raw_values = binding.get(field)
            if isinstance(raw_values, list):
                for raw_value in raw_values:
                    try:
                        binding_unit_id = int(binding.get("unit_id"))
                    except (TypeError, ValueError):
                        binding_unit_id = -1
                    if field == "character_ids":
                        members = expand_group_reference(continuity.get("groups", []), raw_value, binding_unit_id, _entity_by_reference)
                        if members is not None:
                            normalized.extend(member for member in members if member not in normalized)
                            continue
                    record = _entity_by_reference(records, raw_value)
                    state_units = [
                        int(state.get("source_unit_start"))
                        for state in record.get("states", [])
                        if isinstance(state, dict)
                        and isinstance(state.get("source_unit_start"), int)
                    ] if record else []
                    if state_units and binding_unit_id < min(state_units):
                        continue
                    entity_id = str(record.get("id") or "") if record else ""
                    if entity_id and entity_id not in normalized:
                        normalized.append(entity_id)
            binding[field] = normalized
        raw_era = str(binding.get("era_id") or "").strip().casefold()
        matching_era = next(
            (
                era for era in continuity.get("eras", [])
                if isinstance(era, dict)
                and raw_era in {
                    str(era.get("id") or "").casefold(),
                    str(era.get("description") or "").casefold(),
                }
            ),
            None,
        )
        binding["era_id"] = (
            str(matching_era.get("id") or "") if matching_era else ""
        )


def _normalize_binder_era_transitions(
    result: dict[str, Any],
    semantic_units: list[dict[str, Any]],
    continuity: dict[str, Any],
) -> None:
    if continuity.get("era_locked"):
        return
    bindings = result.get("unit_bindings")
    if not isinstance(bindings, list):
        return
    bindings_by_unit = {
        value.get("unit_id"): value
        for value in bindings
        if isinstance(value, dict)
    }
    era_starts = {
        int(era.get("source_unit_start"))
        for era in continuity.get("eras", [])
        if isinstance(era, dict) and isinstance(era.get("source_unit_start"), int)
    }
    assignments = [
        value for value in continuity.get("assignments", [])
        if isinstance(value, dict)
    ]
    suppress_era = not bool(
        assignments and str(assignments[-1].get("era_id") or "").strip()
    )
    temporal_break = re.compile(
        r"\b(years later|decades later|many years later|later in life|"
        r"años después|décadas después|muchos años después|"
        r"died in|murió en|in (?:19|20)\d{2}|en (?:19|20)\d{2})\b",
        flags=re.IGNORECASE,
    )
    for unit in semantic_units:
        unit_id = int(unit["id"])
        binding = bindings_by_unit.get(unit_id)
        if not isinstance(binding, dict):
            continue
        if unit_id in era_starts:
            suppress_era = False
        elif temporal_break.search(str(unit.get("text") or "")):
            suppress_era = True
        if suppress_era:
            binding["era_id"] = ""


def _continuity_discovery_messages(
    semantic_units: list[dict[str, Any]],
) -> tuple[str, str]:
    """Ask for a readable evidence pass before imposing a JSON structure."""
    units = [
        {
            "unit": int(unit["id"]),
            "time": (
                f"{float(unit['start_seconds']):.2f}-"
                f"{float(unit['end_seconds']):.2f}s"
            ),
            "text": str(unit["text"]),
        }
        for unit in semantic_units
    ]
    system = (
        "Analyze narrative continuity in plain text, not JSON. Be observant rather than restrictive. "
        "Write exactly two short sections: CHARACTERS and PLACES. "
        "Under CHARACTERS identify people/recurring animals and group membership. Summarize each with only its most distinctive source-described appearance and necessary relationship/aliases. Record lasting visible changes (age, outfit, grooming, injury, equipment or bodily transformation) at their unit/time. Actions, achievements, emotion, hunger and momentary expressions are not appearance changes. Include aids only if the story mentions them. "
        "Under PLACES list recurring or story-important locations, stable visual traits, changes, and any explicit historical period or material culture with its timestamp. "
        "Report source facts only; do not fill missing appearance, age or clothing and do not discuss normal mobility. Keep the first group appearance even when members are named individually later. "
        "Interpret grammar in the narration's language and do not split one person into a second character because of a pronoun, alias or role phrase. Merge those facts into the canonical character and never list that reference as another bullet. Spanish 'Era Clara, su amiga' means one character named Clara whose relationship is 'her friend'; Era is a verb and 'su amiga' is not a second character. "
        "Distinguish an actual physical change from a detail revealed later. For places distinguish construction, completion, damage and restoration from occupancy or actions nearby. Do not write image prompts or camera/style directions."
    )
    user = (
        "Produce a compact continuity report for these timestamped narration units. "
        "State when information is unknown instead of silently omitting a named character.\n\n"
        f"{json.dumps(units, ensure_ascii=False, separators=(',', ':'))}"
    )
    return system, user


def _continuity_analyzer_messages(
    semantic_units: list[dict[str, Any]],
    continuity: dict[str, Any],
    focus_ids: list[str],
    *,
    era_request: str,
    discovery_report: str = "",
) -> tuple[str, str]:
    system = (
        "Convert the supplied continuity report into the requested JSON. "
        "Keep entity types strict: people and named/recurring animals go only in character_events; physical places go only in location_events; periods go only in era_events. "
        "Merge pronouns, aliases and role phrases into their canonical entity. If the report says that one label refers to another named character, never create a second entity for that label. "
        "A group of individually tracked characters is a groups reference with member_ids, never another visual character. Record its initial membership at the collective introduction and updates only if members change; reuse individual IDs. Anonymous crowds without separately tracked members may remain collective characters. "
        "For NEW entities only, create first_appearance at the first mention, including a collective introduction; choose a few distinctive plausible visual traits once. Never invent an exact age. "
        "Existing ledger entities must keep their design. Return only newly revealed SOURCE facts as stable_revelation; empty appearance fields mean no new visual facts. Omit unchanged entities. Never fill, paraphrase or redesign their appearance again. "
        "Use explicit_change only for a lasting physical change at that time; stable_revelation for a previously true detail newly disclosed. "
        "Reuse ledger IDs exactly. context_description stores necessary relationships; identity_description is a 3-5 anchor visual portrait, not a biography. "
        "appearance contains only the changed visual slots: null means unchanged/not specified; empty string clears a previous value. A changed slot is its complete new value, never old plus new. Keep other slots null. "
        "Characters' actions, achievements, emotion, hunger, momentary expressions and carried props never create states. A character building a house changes the HOUSE's condition, not the character's appearance. Locations' construction/completion, destruction or restoration are physical changes; occupancy and events are not. "
        "Never invent mobility, injuries or absence of aids. A full transformation uses form; clearing form restores the original identity. Detect non-contemporary eras but omit present day. "
        "Interpret source-language grammar before naming entities: a verb before a proper noun is not part of the name. For example, Spanish 'Era Clara, su amiga' identifies one character named Clara; Era is the verb and 'su amiga' is a relationship. "
        "Do not bind units or add image, style or camera instructions."
    )
    context = _compact_continuity_context(
        continuity,
        " ".join(str(unit.get("text") or "") for unit in semantic_units) + " " + discovery_report,
        focus_ids,
    )
    units = [
        {
            "unit_id": int(unit["id"]),
            "start_seconds": float(unit["start_seconds"]),
            "end_seconds": float(unit["end_seconds"]),
            "narration": str(unit["text"]),
        }
        for unit in semantic_units
    ]
    user = (
        "Structure this discovery report and anchor every event to the supplied narration unit where it first applies.\n\n"
        "Plain-text discovery report:\n"
        f"{str(discovery_report or '').strip()}\n\n"
        f"User era override: {era_request or '(none)'}\n"
        "Active/relevant continuity context:\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "Narration units:\n"
        f"{json.dumps(units, ensure_ascii=False, indent=2)}\n\n"
        "New IDs: lowercase_snake_case. Existing entities: copy the supplied entity ID, never a state ID. "
        "Descriptions must be concise English visual facts. Do not repeat unchanged facts. "
        "Keep identity_description under 25 words; use at most 30 words TOTAL across non-null appearance slots, selecting only distinctive relevant traits. "
        "Only NEW entities need invented visual choices; age, clothing and aids are not mandatory. Existing entities need source-backed deltas only. Evidence must quote the supplied narration supporting that delta."
    )
    return system, user


def _missing_character_repair_messages(
    names: list[str],
    semantic_units: list[dict[str, Any]],
) -> tuple[str, str]:
    """Build a tiny, non-destructive retry for names omitted by a small model."""
    folded_names = {name.casefold(): name for name in names}
    evidence: list[dict[str, Any]] = []
    for unit in semantic_units:
        narration = str(unit.get("text") or "")
        folded = narration.casefold()
        mentioned = [
            name for key, name in folded_names.items()
            if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", folded)
        ]
        if mentioned:
            evidence.append(
                {
                    "unit_id": int(unit["id"]),
                    "names": mentioned,
                    "narration": narration,
                }
            )
    system = (
        "Return only JSON. Decide whether each supplied name is a person or recurring named animal. "
        "For each real character, emit one first_appearance event at its earliest supplied unit. "
        "Preserve explicit facts, then complete any missing permanent visual traits once with plausible, neutral details so the identity remains visually consistent. "
        "Put an explicit role or relationship in context_description. Ignore places, objects, dialogue words and generic groups."
    )
    user = (
        f"Possible omitted names: {json.dumps(names, ensure_ascii=False)}\n"
        f"Evidence: {json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))}\n"
        "Use a lowercase_snake_case ID. Keep the canonical source spelling. "
        "Set event_type to first_appearance, aliases to [], and evidence to a short source excerpt."
    )
    return system, user


def _accepted_missing_character_events(
    result: dict[str, Any],
    names: list[str],
    semantic_units: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Accept only requested names and anchor them to their first supplied mention."""
    units_by_name: dict[str, list[dict[str, Any]]] = {}
    for name in names:
        pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
        units_by_name[name] = [
            unit for unit in semantic_units
            if pattern.search(str(unit.get("text") or ""))
        ]
    accepted: list[dict[str, Any]] = []
    for raw_event in result.get("character_events", []):
        if not isinstance(raw_event, dict):
            continue
        raw_name = str(raw_event.get("name") or "").strip().casefold()
        raw_id = str(raw_event.get("id") or "").strip().casefold().replace("_", " ")
        candidate = next(
            (
                name for name in names
                if raw_name == name.casefold()
                or raw_id in {
                    name.casefold(),
                    f"char {name.casefold()}",
                }
            ),
            "",
        )
        evidence_units = units_by_name.get(candidate, [])
        if not candidate or not evidence_units:
            continue
        first_unit = evidence_units[0]
        accepted.append(
            {
                "id": re.sub(r"[^a-z0-9]+", "_", candidate.casefold()).strip("_"),
                "name": candidate,
                "aliases": [],
                "effective_unit_id": int(first_unit["id"]),
                "event_type": "first_appearance",
                "context_description": str(
                    raw_event.get("context_description") or ""
                ).strip(),
                "identity_description": str(
                    raw_event.get("identity_description") or ""
                ).strip(),
                "state_description": str(
                    raw_event.get("state_description") or ""
                ).strip(),
                **({"appearance": deepcopy(raw_event["appearance"])} if isinstance(raw_event.get("appearance"), dict) else {}),
                "evidence": str(
                    raw_event.get("evidence") or first_unit.get("text") or ""
                ).strip()[:220],
            }
        )
    return accepted


def _unit_binder_messages(
    semantic_units: list[dict[str, Any]],
    continuity: dict[str, Any],
    focus_ids: list[str],
) -> tuple[str, str]:
    narration = " ".join(str(unit.get("text") or "") for unit in semantic_units)
    at_seconds = min(
        (float(unit.get("start_seconds") or 0.0) for unit in semantic_units),
        default=0.0,
    )
    ledger = _compact_continuity_context(
        continuity,
        narration,
        focus_ids,
        at_seconds=at_seconds,
    )
    units = [
        {"unit_id": int(unit["id"]), "narration": str(unit["text"])}
        for unit in semantic_units
    ]
    system = (
        "You are a unit binder. Return only the requested JSON. "
        "For each narration unit, select the exact IDs of people, recurring locations, and era referenced in that unit. "
        "Resolve pronouns and role references from consecutive context. Use only IDs supplied in the ledger. "
        "For a collective reference you may use its supplied group ID in character_ids; code expands the membership applicable to that unit. Do not infer that every member is visible. "
        "Apply an era only while the narration is actually set in that era; a later time jump normally requires an empty era_id. "
        "Do not create entities, change descriptions, or make visual/style decisions. A reference does not mean the entity must be visible."
    )
    user = (
        "Ledger (the only allowed IDs):\n"
        f"{json.dumps(ledger, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "Consecutive narration units:\n"
        f"{json.dumps(units, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "Return exactly one unit_binding for every unit, in this exact order. "
        "Use [] and an empty era_id when nothing applies."
    )
    return system, user


def _compact_continuity_context(
    continuity: dict[str, Any],
    narration: str,
    focus_ids: list[str],
    *,
    at_seconds: float | None = None,
) -> dict[str, Any]:
    folded = narration.casefold()
    recent_assignment_ids: list[str] = []
    for assignment in reversed(continuity.get("assignments", [])):
        if not isinstance(assignment, dict):
            continue
        for field in ("character_ids", "location_ids"):
            for identifier in reversed(_string_list(assignment.get(field))):
                if identifier not in recent_assignment_ids:
                    recent_assignment_ids.append(identifier)
        if len(recent_assignment_ids) >= 6:
            break
    compact_focus = list(dict.fromkeys([*focus_ids, *reversed(recent_assignment_ids)]))[-6:]
    focus = set(compact_focus)

    def relevant(record: dict[str, Any]) -> bool:
        if str(record.get("id") or "") in focus:
            return True
        terms = [record.get("name"), *record.get("aliases", [])]
        return any(
            term and re.search(rf"(?<!\w){re.escape(str(term).casefold())}(?!\w)", folded)
            for term in terms
        )

    characters = []
    for record in continuity.get("characters", []):
        if not isinstance(record, dict):
            continue
        states = record.get("states", [])
        current = (
            _active_state(record, at_seconds)
            if at_seconds is not None
            else (states[-1] if isinstance(states, list) and states else {})
        ) or {}
        characters.append(
            {
                "id": record.get("id", ""),
                "name": record.get("name", ""),
                "aliases": record.get("aliases", []),
                "context": record.get("context_description", ""),
                "identity_description": compact_visual_description(str(record.get("identity_description") or ""), max_words=35),
                "current_state": compact_visual_description(str(current.get("description") or ""), max_words=30),
            }
        )
    locations = []
    for record in continuity.get("locations", []):
        if not isinstance(record, dict) or not relevant(record):
            continue
        states = record.get("states", [])
        current = (
            _active_state(record, at_seconds)
            if at_seconds is not None
            else (states[-1] if isinstance(states, list) and states else {})
        ) or {}
        locations.append(
            {
                "id": record.get("id", ""),
                "name": record.get("name", ""),
                "aliases": record.get("aliases", []),
                "context": record.get("context_description", ""),
                "identity_description": record.get("identity_description", ""),
                "current_state": current.get("description", ""),
            }
        )
    eras = continuity.get("eras", [])
    assignments = [
        value for value in continuity.get("assignments", [])
        if isinstance(value, dict)
    ]
    current_era = (
        eras[-1]
        if isinstance(eras, list)
        and eras
        and (
            not assignments
            or str(assignments[-1].get("era_id") or "").strip()
        )
        else {}
    )
    return {
        "characters": characters,
        "groups": [{"id": g.get("id"), "name": g.get("name"), "aliases": g.get("aliases", []),
                    "membership": [{"from_unit": s.get("source_unit_start"), "member_ids": s.get("member_ids", [])}
                                   for s in g.get("states", [])]}
                   for g in continuity.get("groups", []) if isinstance(g, dict)],
        "known_locations": [{"id": r.get("id"), "name": r.get("name"), "aliases": r.get("aliases", [])}
                            for r in continuity.get("locations", []) if isinstance(r, dict)],
        "locations": locations,
        "current_era": current_era,
        "recent_focus_ids": compact_focus,
    }


def _entity_by_reference(
    records: list[dict[str, Any]],
    raw_id: object,
    name: object = "",
) -> dict[str, Any] | None:
    target = str(raw_id or "").strip().casefold()
    target_name = str(name or "").strip().casefold()
    # Canonical IDs win. A shared alias ("the brothers", "the house") must
    # never silently resolve to the first of several distinct entities.
    for match in (
        lambda r: bool(target) and target == str(r.get("id") or "").casefold(),
        lambda r: bool(target) and bool(re.fullmatch(rf"{re.escape(str(r.get('id') or '').casefold())}_state_\d+", target)),
        lambda r: bool(r.get("name")) and str(r["name"]).casefold() in {target, re.sub(r"\s+state\s+\d+$", "", target_name)},
        lambda r: bool({target, target_name} - {""}) and bool(({target, target_name} - {""}) & {str(a).casefold() for a in r.get("aliases", [])}),
    ):
        candidates = [r for r in records if match(r)]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return None
    return None


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


def _merge_continuity_facts(existing: str, added: str) -> str:
    """Accumulate newly revealed facts without discarding the earlier lock."""
    current = str(existing or "").strip().rstrip(".")
    incoming = str(added or "").strip().rstrip(".")
    if not incoming:
        return current
    if not current:
        return incoming
    normalized_current = " ".join(current.casefold().split())
    normalized_incoming = " ".join(incoming.casefold().split())
    if normalized_incoming in normalized_current:
        return current
    if normalized_current in normalized_incoming:
        return incoming
    clauses = [
        value.strip()
        for value in re.split(r"\s*[;,]\s*", incoming)
        if value.strip()
    ] or [incoming]
    merged = current
    normalized_merged = normalized_current
    for clause in clauses:
        normalized_clause = " ".join(clause.casefold().split())
        if normalized_clause and normalized_clause not in normalized_merged:
            merged = f"{merged}, {clause}"
            normalized_merged = " ".join(merged.casefold().split())
    return merged


def _merge_entity_event(
    records: list[dict[str, Any]],
    event: dict[str, Any],
    unit_lookup: dict[int, dict[str, Any]],
    *,
    prefix: str,
) -> tuple[str, str]:
    raw_id = str(event.get("id") or "").strip()
    name = str(event.get("name") or raw_id).strip()
    name = _source_spelling(name, unit_lookup) or name
    record = _entity_by_reference(records, raw_id, name)
    is_new_entity = record is None
    state_like_reference = bool(
        record
        and re.fullmatch(
            rf"{re.escape(str(record.get('id') or '').casefold())}_state_\d+",
            raw_id.casefold(),
        )
    )
    if record is None:
        entity_id = _unique_entity_id(records, raw_id, name, prefix)
        record = {
            "id": entity_id,
            "name": name or raw_id or entity_id,
            "aliases": [],
            "context_description": "",
            "identity_description": "",
            "states": [],
        }
        records.append(record)
    entity_id = str(record["id"])
    aliases = record.setdefault("aliases", [])
    alias_candidates = event.get("aliases", []) if state_like_reference else [
        name, raw_id, *event.get("aliases", []),
    ]
    for alias in alias_candidates:
        value = str(alias or "").strip()
        if value and value.casefold() not in {
            str(existing).casefold() for existing in aliases
        } and value.casefold() != str(record.get("name") or "").casefold():
            aliases.append(value)
    event_type = str(event.get("event_type") or "first_appearance")
    if is_new_entity:
        event_type = "first_appearance"
    if state_like_reference and event_type != "explicit_change":
        event_type = "explicit_change"
    identity = str(event.get("identity_description") or "").strip()
    context_description = str(event.get("context_description") or "").strip()
    user_authored = bool(record.get("user_authored"))
    try:
        unit_id = int(event.get("effective_unit_id"))
    except (TypeError, ValueError):
        unit_id = next(iter(unit_lookup), 0)
    unit = unit_lookup.get(unit_id) or next(iter(unit_lookup.values()))
    if "_simple_profile" in event:
        from app.core.storyboard_simple_continuity import merge_simple_profile
        merge_simple_profile(record, event, unit)
        return raw_id.casefold(), entity_id
    evidence = str(unit.get("text") or event.get("evidence") or "")
    quote = re.sub(r"^Unit\s+\d+\s*:\s*", "", str(event.get("evidence") or ""), flags=re.I).strip(' .\"“”')
    # The semantic interpretation belongs to the LLM, not an English-only
    # keyword list. A genuine source-delta event with a verifiable quotation
    # also supports translated descriptions. Repeated first appearances never
    # qualify for this fallback, even when normalized to stable_revelation.
    source_claimed = (
        event.get("_reported_event_type", event_type) in {"stable_revelation", "explicit_change"}
        and len(quote) >= 4 and quote.casefold() in evidence.casefold()
    )
    if context_description and not user_authored and event_type != "explicit_change":
        record["context_description"] = _merge_continuity_facts(
            str(record.get("context_description") or ""),
            context_description,
        )
        record["context_description"] = " ".join(record["context_description"].split()[:60])
    if identity and not user_authored and event_type != "explicit_change":
        record["identity_description"], record["identity_traits"] = merge_visual_description(
            str(record.get("identity_description") or ""),
            identity,
            evidence=evidence, unit_id=unit_id,
            metadata=record.get("identity_traits"), initial=is_new_entity,
            source_claimed=source_claimed,
        )
        record["identity_description"], record["identity_traits"] = limit_generated_identity(
            record["identity_description"], record["identity_traits"],
        )
    at_seconds = float(unit.get("start_seconds") or 0.0)
    states = record.setdefault("states", [])
    kind = "character" if prefix == "char" else "location"
    delta = (appearance_values(event["appearance"], kind)
             if isinstance(event.get("appearance"), dict)
             else legacy_appearance(str(event.get("state_description") or ""), kind))
    if states and "appearance" not in event and delta.get("wardrobe"):
        # Legacy responses supplied clothing fragments, not a whole slot.
        previous_wardrobe = (states[-1].get("visual_traits") or {}).get("slots", {}).get("wardrobe", "")
        delta["wardrobe"], _ = merge_visual_description(
            previous_wardrobe, delta["wardrobe"], evidence=evidence,
            explicit_change=True, source_claimed=source_claimed,
        )
    if not states:
        state_description, state_traits = merge_appearance(
            "", None, delta, kind=kind, initial=True, changed=False, evidence=evidence,
        )
        states.append(
            {
                "id": f"{entity_id}_state_1",
                "description": state_description,
                "visual_traits": state_traits,
                "from_seconds": at_seconds,
                "to_seconds": None,
                "source_unit_start": unit_id,
                "change_reason": event_type,
                "evidence": str(event.get("evidence") or "").strip(),
            }
        )
    elif event_type == "explicit_change":
        current = states[-1]
        if user_authored:
            return raw_id.casefold(), entity_id
        state_description, state_traits = merge_appearance(
            str(current.get("description") or ""), current.get("visual_traits"), delta,
            kind=kind, initial=False, changed=True, evidence=evidence,
            source_backed=source_claimed,
        )
        same_state = (
            " ".join(state_description.casefold().split())
            == " ".join(str(current.get("description") or "").casefold().split())
        )
        if same_state:
            pass
        elif (
            at_seconds > float(current.get("from_seconds") or 0.0) + 0.001
        ):
            states.append(
                {
                    "id": f"{entity_id}_state_{len(states) + 1}",
                    "description": state_description,
                    "visual_traits": state_traits,
                    "from_seconds": at_seconds,
                    "to_seconds": None,
                    "source_unit_start": unit_id,
                    "change_reason": "explicit_change",
                    "evidence": str(event.get("evidence") or "").strip(),
                }
            )
        else:
            current["description"] = state_description
            current["visual_traits"] = state_traits
    elif event_type == "stable_revelation" and delta and not user_authored:
        states[-1]["description"], states[-1]["visual_traits"] = merge_appearance(
            str(states[-1].get("description") or ""),
            states[-1].get("visual_traits"), delta,
            kind=kind, initial=False, changed=False, evidence=evidence,
            source_backed=source_claimed,
        )
    return raw_id.casefold(), entity_id


def _source_spelling(
    name: str,
    unit_lookup: dict[int, dict[str, Any]],
) -> str:
    candidate = str(name or "").strip().replace("_", " ")
    if not candidate:
        return ""
    pattern = re.compile(rf"(?<!\w){re.escape(candidate)}(?!\w)", re.IGNORECASE)
    for unit in unit_lookup.values():
        match = pattern.search(str(unit.get("text") or ""))
        if match:
            return match.group(0)
    return ""


def _merge_era_event(
    continuity: dict[str, Any],
    event: dict[str, Any],
    unit_lookup: dict[int, dict[str, Any]],
) -> tuple[str, str]:
    raw_id = str(event.get("id") or "").strip()
    description = str(event.get("description") or "").strip()
    if continuity.get("era_locked") and continuity.get("eras"):
        locked = continuity["eras"][0]
        return raw_id.casefold(), str(locked.get("id") or "")
    normalized_era = " ".join(
        (raw_id or description).casefold().replace("_", " ").split()
    )
    if normalized_era in {
        "current", "current era", "modern day", "present", "present day",
        "contemporary", "contemporary era",
    }:
        return raw_id.casefold(), ""
    if not description:
        return raw_id.casefold(), ""
    eras = continuity.setdefault("eras", [])
    existing = next(
        (
            era for era in eras
            if isinstance(era, dict)
            and (
                str(era.get("id") or "").casefold() == raw_id.casefold()
                or str(era.get("description") or "").casefold() == description.casefold()
            )
        ),
        None,
    )
    if existing is not None:
        material = str(event.get("material_culture") or "").strip()
        if material:
            existing["material_culture"] = material
        return raw_id.casefold(), str(existing.get("id") or "")
    try:
        unit_id = int(event.get("effective_unit_id"))
    except (TypeError, ValueError):
        unit_id = next(iter(unit_lookup), 0)
    unit = unit_lookup.get(unit_id) or next(iter(unit_lookup.values()))
    era_id = _unique_entity_id(eras, raw_id, description, "era")
    eras.append(
        {
            "id": era_id,
            "description": description,
            "material_culture": str(event.get("material_culture") or "").strip(),
            "from_seconds": float(unit.get("start_seconds") or 0.0),
            "to_seconds": None,
            "source_unit_start": unit_id,
            "reason": "detected",
            "evidence": str(event.get("evidence") or "").strip(),
        }
    )
    return raw_id.casefold(), era_id


def _merge_continuity_events(
    continuity: dict[str, Any],
    result: dict[str, Any],
    semantic_units: list[dict[str, Any]],
) -> list[str]:
    unit_lookup = {int(unit["id"]): unit for unit in semantic_units}
    if result.get("_simple_reports"):
        continuity.setdefault("simple_reports", []).append({
            "first_unit": min(unit_lookup), "reports": deepcopy(result["_simple_reports"]),
        })
    for warning in result.get("_simple_warnings", []):
        continuity.setdefault("warnings", []).append(warning)
    character_map: dict[str, str] = {}
    location_map: dict[str, str] = {}
    era_map: dict[str, str] = {}
    characters = continuity.setdefault("characters", [])
    locations = continuity.setdefault("locations", [])
    for raw_event in sorted(result.get("character_events", []), key=_event_unit_order):
        if isinstance(raw_event, dict):
            raw_id, entity_id = _merge_entity_event(
                characters, raw_event, unit_lookup, prefix="char"
            )
            character_map[raw_id] = entity_id
    for raw_event in sorted(result.get("location_events", []), key=_event_unit_order):
        if isinstance(raw_event, dict):
            raw_id, entity_id = _merge_entity_event(
                locations, raw_event, unit_lookup, prefix="loc"
            )
            location_map[raw_id] = entity_id
    for raw_event in sorted(result.get("era_events", []), key=_event_unit_order):
        if isinstance(raw_event, dict):
            raw_id, era_id = _merge_era_event(continuity, raw_event, unit_lookup)
            era_map[raw_id] = era_id

    merge_groups(continuity, result.get("groups"), unit_lookup, _entity_by_reference, _normalized_entity_id)

    return list(dict.fromkeys([
        *character_map.values(), *location_map.values(), *era_map.values(),
    ]))


def _event_unit_order(event: object) -> int:
    try:
        return int(event.get("effective_unit_id", 0)) if isinstance(event, dict) else 0
    except (TypeError, ValueError):
        return 0


def _merge_unit_bindings(
    continuity: dict[str, Any],
    result: dict[str, Any],
    semantic_units: list[dict[str, Any]],
) -> list[str]:
    unit_lookup = {int(unit["id"]): unit for unit in semantic_units}
    characters = continuity.setdefault("characters", [])
    locations = continuity.setdefault("locations", [])

    assignments = continuity.setdefault("assignments", [])
    assignments_by_unit = {
        int(value.get("unit_id")): value
        for value in assignments
        if isinstance(value, dict) and isinstance(value.get("unit_id"), int)
    }
    focus: list[str] = []
    raw_bindings = result.get("unit_bindings", [])
    bindings_by_unit = {
        int(value.get("unit_id")): value
        for value in raw_bindings
        if isinstance(value, dict) and isinstance(value.get("unit_id"), int)
    } if isinstance(raw_bindings, list) else {}
    for unit_id, unit in unit_lookup.items():
        binding = bindings_by_unit.get(unit_id, {})
        character_ids = _canonical_binding_ids(
            binding.get("character_ids"), characters, {}
        )
        location_ids = _canonical_binding_ids(
            binding.get("location_ids"), locations, {}
        )
        raw_era = str(binding.get("era_id") or "").strip()
        matching_era = next(
            (
                era for era in continuity.get("eras", [])
                if isinstance(era, dict)
                and str(era.get("id") or "").casefold() == raw_era.casefold()
            ),
            None,
        )
        era_id = str(matching_era.get("id") or "") if matching_era else ""
        assignment = {
            "unit_id": unit_id,
            "start_seconds": float(unit.get("start_seconds") or 0.0),
            "end_seconds": float(unit.get("end_seconds") or 0.0),
            "character_ids": character_ids,
            "location_ids": location_ids,
            "era_id": era_id,
        }
        assignments_by_unit[unit_id] = assignment
        focus.extend(character_ids)
        focus.extend(location_ids)
    continuity["assignments"] = [
        assignments_by_unit[key] for key in sorted(assignments_by_unit)
    ]
    return list(dict.fromkeys(focus))[-6:]


def _merge_continuity_result(
    continuity: dict[str, Any],
    result: dict[str, Any],
    semantic_units: list[dict[str, Any]],
) -> list[str]:
    """Compatibility helper for saved fixtures from the former monolithic call."""
    _merge_continuity_events(continuity, result, semantic_units)
    return _merge_unit_bindings(continuity, result, semantic_units)


def _canonical_binding_ids(
    raw_values: object,
    records: list[dict[str, Any]],
    new_id_map: dict[str, str],
) -> list[str]:
    values = raw_values if isinstance(raw_values, list) else []
    resolved: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        canonical = new_id_map.get(value.casefold(), "")
        if not canonical:
            record = _entity_by_reference(records, value)
            canonical = str(record.get("id") or "") if record else ""
        if canonical and canonical not in resolved:
            resolved.append(canonical)
    return resolved


def _finalize_continuity_periods(
    continuity: dict[str, Any],
    total_duration: float,
) -> None:
    end = max(0.0, float(total_duration))
    for collection in ("characters", "locations", "groups"):
        for record in continuity.get(collection, []):
            if not isinstance(record, dict):
                continue
            states = [
                state for state in record.get("states", [])
                if isinstance(state, dict)
            ]
            states.sort(key=lambda value: float(value.get("from_seconds") or 0.0))
            for index, state in enumerate(states):
                state["to_seconds"] = (
                    float(states[index + 1].get("from_seconds") or end)
                    if index + 1 < len(states)
                    else end
                )
            record["states"] = states
    era_definitions = {
        str(era.get("id") or ""): era
        for era in continuity.get("eras", [])
        if isinstance(era, dict) and str(era.get("id") or "")
    }
    assignments = [
        value for value in continuity.get("assignments", [])
        if isinstance(value, dict)
    ]
    assignments.sort(key=lambda value: float(value.get("start_seconds") or 0.0))
    if continuity.get("era_locked") and era_definitions:
        locked_id = next(iter(era_definitions))
        for assignment in assignments:
            assignment["era_id"] = locked_id
    periods: list[dict[str, Any]] = []
    period_counts: dict[str, int] = {}
    current_base_id = ""
    current_period: dict[str, Any] | None = None
    for assignment in assignments:
        base_id = str(assignment.get("era_id") or "")
        if base_id != current_base_id:
            current_period = None
            current_base_id = base_id
        if not base_id or base_id not in era_definitions:
            continue
        if current_period is None:
            period_counts[base_id] = period_counts.get(base_id, 0) + 1
            occurrence = period_counts[base_id]
            period_id = base_id if occurrence == 1 else f"{base_id}_period_{occurrence}"
            definition = era_definitions[base_id]
            current_period = {
                **definition,
                "id": period_id,
                "from_seconds": float(assignment.get("start_seconds") or 0.0),
                "to_seconds": float(assignment.get("end_seconds") or end),
                "source_unit_start": int(assignment.get("unit_id") or 0),
            }
            periods.append(current_period)
        else:
            current_period["to_seconds"] = float(
                assignment.get("end_seconds") or end
            )
        assignment["era_id"] = str(current_period["id"])
    continuity["assignments"] = assignments
    continuity["eras"] = periods


def _apply_continuity_boundaries(
    prepared_batches: list[dict[str, Any]],
    continuity: dict[str, Any],
) -> None:
    forced_unit_ids: set[int] = set()
    for collection in ("characters", "locations"):
        for record in continuity.get(collection, []):
            if not isinstance(record, dict):
                continue
            for state in record.get("states", [])[1:]:
                if isinstance(state, dict):
                    try:
                        forced_unit_ids.add(int(state.get("source_unit_start")))
                    except (TypeError, ValueError):
                        pass
    eras = [era for era in continuity.get("eras", []) if isinstance(era, dict)]
    for era in eras[1:]:
        try:
            forced_unit_ids.add(int(era.get("source_unit_start")))
        except (TypeError, ValueError):
            pass
    if not forced_unit_ids:
        return
    for prepared in prepared_batches:
        units = prepared.get("semantic_units", [])
        ranges = prepared.get("ranges", [])
        if not isinstance(units, list) or not isinstance(ranges, list):
            continue
        split_ranges: list[tuple[int, int]] = []
        for start, end in ranges:
            boundaries = [
                index for index in range(start + 1, end + 1)
                if int(
                    units[index].get("id")
                    if units[index].get("id") is not None
                    else -1
                ) in forced_unit_ids
            ]
            cursor = start
            for boundary in boundaries:
                split_ranges.append((cursor, boundary - 1))
                cursor = boundary
            split_ranges.append((cursor, end))
        prepared["ranges"] = split_ranges
        prepared["scene_count"] = len(split_ranges)


def _active_state(record: dict[str, Any], at_seconds: float) -> dict[str, Any] | None:
    states = record.get("states", [])
    if not isinstance(states, list):
        return None
    active = None
    for state in states:
        if not isinstance(state, dict):
            continue
        start = float(state.get("from_seconds") or 0.0)
        raw_end = state.get("to_seconds")
        end = float(raw_end) if raw_end is not None else float("inf")
        if start <= at_seconds < end or (active is None and start <= at_seconds):
            active = state
    return active


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


def _scene_reference_ids(
    scene: dict[str, Any],
    field: str,
    continuity: dict[str, Any],
    collection: str,
    at_seconds: float,
) -> list[str]:
    requested = scene.get(field, [])
    if not isinstance(requested, list):
        return []
    valid: dict[str, str] = {}
    for record in continuity.get(collection, []):
        if not isinstance(record, dict):
            continue
        for candidate in record.get("states", []):
            if isinstance(candidate, dict):
                candidate_id = str(candidate.get("id") or "")
                if candidate_id:
                    valid[candidate_id.casefold()] = candidate_id
        state = _active_state(record, at_seconds)
        if state is None:
            continue
        state_id = str(state.get("id") or "")
        entity_id = str(record.get("id") or "")
        if state_id:
            valid[state_id.casefold()] = state_id
            valid[entity_id.casefold()] = state_id
    result: list[str] = []
    for raw in requested:
        state_id = valid.get(str(raw or "").strip().casefold(), "")
        if state_id and state_id not in result:
            result.append(state_id)
    return result


def _scene_era_state_id(
    scene: dict[str, Any],
    continuity: dict[str, Any],
) -> str:
    requested = str(scene.get("era_state_id") or "").strip().casefold()
    for era in continuity.get("eras", []):
        if isinstance(era, dict) and str(era.get("id") or "").casefold() == requested:
            return str(era.get("id") or "")
    return ""


def _scene_era_description(
    scene: dict[str, Any],
    continuity: dict[str, Any],
    fallback: str,
) -> str:
    era_id = _scene_era_state_id(scene, continuity)
    for era in continuity.get("eras", []):
        if isinstance(era, dict) and str(era.get("id") or "") == era_id:
            return str(era.get("description") or "").strip()
    return str(fallback or "").strip()


def _continuity_for_units(
    continuity: dict[str, Any],
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    unit_ids = {int(unit.get("id") or 0) for unit in units}
    assignments = [
        value for value in continuity.get("assignments", [])
        if isinstance(value, dict) and int(value.get("unit_id") or 0) in unit_ids
    ]
    character_ids = {
        str(identifier)
        for assignment in assignments
        for identifier in assignment.get("character_ids", [])
        if str(identifier).strip()
    }
    location_ids = {
        str(identifier)
        for assignment in assignments
        for identifier in assignment.get("location_ids", [])
        if str(identifier).strip()
    }
    era_ids = {
        str(assignment.get("era_id") or "")
        for assignment in assignments
        if str(assignment.get("era_id") or "").strip()
    }
    narration = " ".join(str(unit.get("text") or "") for unit in units).casefold()

    def explicitly_named(record: dict[str, Any]) -> bool:
        return any(
            value and re.search(
                rf"(?<!\w){re.escape(str(value).casefold())}(?!\w)", narration
            )
            for value in [record.get("name"), *record.get("aliases", [])]
        )

    at_seconds = min(
        (float(unit.get("start_seconds") or 0.0) for unit in units),
        default=0.0,
    )
    characters: list[dict[str, Any]] = []
    for record in continuity.get("characters", []):
        if not isinstance(record, dict):
            continue
        if str(record.get("id") or "") not in character_ids and not explicitly_named(record):
            continue
        entity_id = str(record.get("id") or "")
        reference_time = min(
            (
                float(assignment.get("start_seconds") or at_seconds)
                for assignment in assignments
                if entity_id in assignment.get("character_ids", [])
            ),
            default=at_seconds,
        )
        state = _active_state(record, reference_time)
        if state is None:
            continue
        identity, appearance = selected_appearance(record, state)
        characters.append(
            {
                "entity_id": record.get("id", ""),
                "state_id": state.get("id", ""),
                "name": record.get("name", ""),
                "locked_identity": identity,
                "locked_state": appearance,
            }
        )
    locations: list[dict[str, Any]] = []
    for record in continuity.get("locations", []):
        if not isinstance(record, dict):
            continue
        if str(record.get("id") or "") not in location_ids and not explicitly_named(record):
            continue
        entity_id = str(record.get("id") or "")
        reference_time = min(
            (
                float(assignment.get("start_seconds") or at_seconds)
                for assignment in assignments
                if entity_id in assignment.get("location_ids", [])
            ),
            default=at_seconds,
        )
        state = _active_state(record, reference_time)
        if state is None:
            continue
        identity, appearance = selected_appearance(record, state)
        locations.append(
            {
                "entity_id": record.get("id", ""),
                "state_id": state.get("id", ""),
                "name": record.get("name", ""),
                "locked_identity": identity,
                "locked_state": appearance,
            }
        )
    eras = []
    for era in continuity.get("eras", []):
        if not isinstance(era, dict):
            continue
        start = float(era.get("from_seconds") or 0.0)
        raw_end = era.get("to_seconds")
        end = float(raw_end) if raw_end is not None else float("inf")
        if str(era.get("id") or "") in era_ids or start <= at_seconds < end:
            eras.append(
                {
                    "state_id": era.get("id", ""),
                    "description": era.get("description", ""),
                    "material_culture": era.get("material_culture", ""),
                }
            )
    return {
        "characters": characters,
        "locations": locations,
        "eras": eras[:1],
    }


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


def _text_similarity(first: str, second: str) -> float:
    left = " ".join(re.findall(r"\w+", first.casefold()))
    right = " ".join(re.findall(r"\w+", second.casefold()))
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _terse_prompt_indexes(raw_scenes: object) -> list[int]:
    if not isinstance(raw_scenes, list):
        return []
    return [
        index
        for index, scene in enumerate(raw_scenes, start=1)
        if not isinstance(scene, dict)
        or len(
            re.findall(
                r"\b[\w'-]+\b",
                str(scene.get("visual") or scene.get("prompt") or ""),
            )
        ) < 25
    ]


def _invalid_scene_reference_indexes(
    raw_scenes: object,
    semantic_units: list[dict[str, Any]],
    ranges: list[tuple[int, int]],
    continuity: dict[str, Any],
) -> list[int]:
    if not isinstance(raw_scenes, list) or len(raw_scenes) != len(ranges):
        return list(range(1, len(ranges) + 1))
    invalid: list[int] = []
    for index, (scene, (start, end)) in enumerate(
        zip(raw_scenes, ranges, strict=True),
        start=1,
    ):
        if not isinstance(scene, dict):
            invalid.append(index)
            continue
        available = _continuity_for_units(
            continuity,
            semantic_units[start : end + 1],
        )
        allowed_characters = {
            str(value.get("state_id") or "")
            for value in available.get("characters", [])
            if isinstance(value, dict)
        }
        allowed_locations = {
            str(value.get("state_id") or "")
            for value in available.get("locations", [])
            if isinstance(value, dict)
        }
        allowed_eras = {
            str(value.get("state_id") or "")
            for value in available.get("eras", [])
            if isinstance(value, dict)
        }
        requested_characters = scene.get("character_state_ids")
        requested_locations = scene.get("location_state_ids")
        requested_era = str(scene.get("era_state_id") or "")
        if (
            not isinstance(requested_characters, list)
            or not isinstance(requested_locations, list)
            or any(str(value) not in allowed_characters for value in requested_characters)
            or any(str(value) not in allowed_locations for value in requested_locations)
            or (requested_era and requested_era not in allowed_eras)
        ):
            invalid.append(index)
    return invalid


def _normalize_scene_continuity_references(
    raw_scenes: object,
    semantic_units: list[dict[str, Any]],
    ranges: list[tuple[int, int]],
    continuity: dict[str, Any],
) -> None:
    """Convert entity IDs to active state IDs and discard hallucinated IDs."""
    if not isinstance(raw_scenes, list) or len(raw_scenes) != len(ranges):
        return
    for scene, (start, end) in zip(raw_scenes, ranges, strict=True):
        if not isinstance(scene, dict):
            continue
        available = _continuity_for_units(
            continuity,
            semantic_units[start : end + 1],
        )
        for field, collection in (
            ("character_state_ids", "characters"),
            ("location_state_ids", "locations"),
        ):
            allowed: dict[str, str] = {}
            for record in available.get(collection, []):
                if not isinstance(record, dict):
                    continue
                state_id = str(record.get("state_id") or "")
                entity_id = str(record.get("entity_id") or "")
                if state_id:
                    allowed[state_id.casefold()] = state_id
                    if entity_id:
                        allowed[entity_id.casefold()] = state_id
            normalized: list[str] = []
            requested = scene.get(field, [])
            if isinstance(requested, list):
                for raw_id in requested:
                    state_id = allowed.get(str(raw_id or "").strip().casefold(), "")
                    if state_id and state_id not in normalized:
                        normalized.append(state_id)
            scene[field] = normalized
        allowed_eras = {
            str(record.get("state_id") or "").casefold(): str(
                record.get("state_id") or ""
            )
            for record in available.get("eras", [])
            if isinstance(record, dict) and str(record.get("state_id") or "")
        }
        requested_era = str(scene.get("era_state_id") or "").strip().casefold()
        scene["era_state_id"] = allowed_eras.get(requested_era, "")


def _semantic_quality_warnings(
    scenes: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    seen_prompts: dict[str, str] = {}
    for scene in scenes:
        scene_id = str(scene.get("id") or "?")
        confidence = float(scene.get("alignment_confidence") or 0.0)
        if confidence < 0.45:
            warnings.append(
                f"Scene {scene_id}: Ollama narration differs from its assigned source units (confidence {confidence:.2f})."
            )
        prompt = " ".join(re.findall(r"\w+", str(scene.get("prompt") or "").casefold()))
        prompt_words = len(prompt.split())
        if prompt_words < 25:
            warnings.append(
                f"Scene {scene_id}: image prompt remains terse after retry ({prompt_words} words)."
            )
        if prompt and prompt in seen_prompts:
            warnings.append(
                f"Scenes {seen_prompts[prompt]} and {scene_id} have an identical image prompt."
            )
        elif prompt:
            seen_prompts[prompt] = scene_id
    return warnings


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


def _scene_count(
    duration: float,
    minimum: float,
    target: float,
    maximum: float,
) -> int:
    ideal = max(1, round(duration / max(1.0, target)))
    minimum_count = max(1, math.ceil(duration / max(1.0, maximum)))
    maximum_count = max(1, math.floor(duration / max(1.0, minimum)))
    return max(minimum_count, min(maximum_count, ideal))


def _plan_schema(
    scene_count: int,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "scenes": {
            "type": "array",
            "minItems": scene_count,
            "maxItems": scene_count,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "character_state_ids",
                    "location_state_ids",
                    "era_state_id",
                    "visual",
                ],
                "properties": {
                    "character_state_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "location_state_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "era_state_id": {"type": "string"},
                    "visual": {"type": "string"},
                },
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["scenes"],
        "properties": properties,
    }


def _messages(
    semantic_units: list[dict[str, Any]],
    ranges: list[tuple[int, int]],
    *,
    era_request: str,
    continuity: dict[str, Any],
) -> tuple[str, str]:
    system = (
        "You are a storyboard semantic visual planner. Return only JSON matching the supplied schema; no Markdown or explanation. "
        "For each fixed scene, describe one single visible still image in concise English and bind it to the supplied continuity state IDs. "
        "Describe only visible subjects, poses, expressions, action and environment supported by that scene. Show an abstract ending with a concrete pose or expression, never explain what the image symbolizes or proves (lasting happiness, lessons, relationships). "
        "Return only character states and locations actually visible in the chosen image, using the supplied IDs exactly. A person merely mentioned, remembered or discussed is not visible unless the image intentionally depicts that memory. "
        "Do not invent IDs. Every visible named person must retain the exact canonical full name; never replace Stephen Hawking, Luis or another named person with generic wording such as a boy, a student, a man or a woman. "
        "Do not repeat or contradict locked appearance, clothing, architecture or material-culture descriptions inside visual; refer to the canonical subject or place by name and concentrate on visible action and composition content. "
        "Do not include art style, camera, lens, resolution, seed, timing, narration, negative prompts, transitions, motion, captions, text, logos or rendering instructions."
    )
    assignments_json = json.dumps(
        [
            {
                "scene_position": index,
                "narration": _assigned_narration(
                    semantic_units,
                    (start, end),
                ),
                "available_continuity": _continuity_for_units(
                    continuity,
                    semantic_units[start : end + 1],
                ),
            }
            for index, (start, end) in enumerate(ranges, start=1)
        ],
        ensure_ascii=False,
        indent=2,
    )
    era_guidance = era_request or (
        "Use the supplied era state ID; return an empty string when no "
        "non-contemporary period applies."
    )
    user = f"""Generate semantic visual content for these fixed storyboard scenes.

Era guidance: {era_guidance}
Scenes:
{assignments_json}

Return exactly one output scene per input scene, in the same order.
Each visual must contain 25-55 concrete English words and represent only its assigned narration.
Do not invent later actions or characters. Do not combine multiple moments or compositions in one image."""
    return system, user


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


def _choice(value: object, allowed: set[str], fallback: str) -> str:
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in allowed else fallback
