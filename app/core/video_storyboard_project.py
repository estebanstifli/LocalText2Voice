from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STORYBOARD_PROJECT_FILE = Path("storyboard") / "storyboard.json"
STORYBOARD_ANALYSIS_LOG_DIR = Path("storyboard") / "debug"
_WRITE_LOCK = threading.RLock()


def create_storyboard_analysis_request_log(
    project_dir: Path,
    *,
    title: str,
    provider: str,
    model: str,
) -> Path:
    """Create a local audit log for raw LLM exchanges in one analysis."""
    root = project_dir.resolve()
    timestamp = datetime.now().astimezone()
    target = root / STORYBOARD_ANALYSIS_LOG_DIR / (
        f"analysis-requests-{timestamp.strftime('%Y%m%d-%H%M%S-%f')}.txt"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "VIDEO STORYBOARD - RAW LLM REQUEST / RESPONSE LOG\n"
        f"Created: {timestamp.isoformat()}\n"
        f"Project: {title or '(untitled)'}\n"
        f"Provider: {provider or '(unknown)'}\n"
        f"Model: {model or '(not configured)'}\n\n"
        "This file records request bodies, raw provider responses, and provider "
        "errors during audiobook analysis, in chronological order. Authentication "
        "headers, API keys, passwords, and access tokens are never written here.\n"
    )
    target.write_text(header, encoding="utf-8")
    return target


def append_storyboard_analysis_request(
    target: Path,
    event: dict[str, Any],
    request_number: int,
) -> None:
    """Append one trace request without ever persisting authentication data."""
    raw_request = event.get("raw_request")
    if not isinstance(raw_request, dict):
        raw_request = {"prompt": str(event.get("prompt") or "")}
    timestamp = datetime.now().astimezone().isoformat()
    endpoint = str(event.get("endpoint") or "")
    provider = str(event.get("provider") or "LLM")
    label = str(event.get("label") or f"request {request_number}")
    entry = (
        f"\n\n{'=' * 96}\n"
        f"REQUEST {request_number}: {label}\n"
        f"Time: {timestamp}\n"
        f"Provider: {provider}\n"
        f"Endpoint: POST {endpoint or '(not reported)'}\n"
        f"Attempt: {event.get('attempt', 1)}\n"
        "Headers: Content-Type: application/json; Authorization: [OMITTED]\n\n"
        f"{json.dumps(_redact_sensitive(raw_request), ensure_ascii=False, indent=2)}\n"
    )
    with _WRITE_LOCK, target.open("a", encoding="utf-8") as stream:
        stream.write(entry)


def append_storyboard_analysis_result(
    target: Path,
    event: dict[str, Any],
    request_number: int,
) -> None:
    """Append a raw LLM response, warning or error beside its request."""
    kind = str(event.get("kind") or "")
    if kind not in {"raw_response", "warning", "error"}:
        return
    timestamp = datetime.now().astimezone().isoformat()
    provider = str(event.get("provider") or "LLM")
    label = str(event.get("label") or f"request {request_number}")
    if kind == "raw_response":
        heading = "RAW RESPONSE"
        payload = event.get("raw_response")
    elif kind == "warning":
        heading = "WARNING"
        payload = {"message": str(event.get("message") or "")}
    else:
        heading = "ERROR"
        payload = event.get("raw_error")
        if not isinstance(payload, (dict, list)):
            payload = {"message": str(event.get("message") or payload or "")}
    safe_payload = _redact_sensitive(payload)
    entry = (
        f"\n\n{'-' * 96}\n"
        f"{heading} FOR REQUEST {request_number}: {label}\n"
        f"Time: {timestamp}\n"
        f"Provider: {provider}\n"
        f"Endpoint: {event.get('endpoint') or '(same as request)'}\n\n"
        f"{json.dumps(safe_payload, ensure_ascii=False, indent=2)}\n"
    )
    with _WRITE_LOCK, target.open("a", encoding="utf-8") as stream:
        stream.write(entry)


def finish_storyboard_analysis_request_log(
    target: Path,
    *,
    status: str,
    detail: str = "",
) -> None:
    timestamp = datetime.now().astimezone().isoformat()
    footer = (
        f"\n\n{'=' * 96}\n"
        f"ANALYSIS {status.upper()}\n"
        f"Time: {timestamp}\n"
        f"Detail: {detail}\n"
    )
    with _WRITE_LOCK, target.open("a", encoding="utf-8") as stream:
        stream.write(footer)


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            normalized = name.casefold().replace("-", "_")
            if normalized in {
                "api_key",
                "apikey",
                "authorization",
                "password",
                "secret",
                "access_token",
                "refresh_token",
            }:
                result[name] = "[OMITTED]"
            else:
                result[name] = _redact_sensitive(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_sensitive(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _redact_sensitive(model_dump())
        except Exception:
            pass
    return str(value)


def save_storyboard_state(
    project_dir: Path,
    state: dict[str, Any],
    *,
    analysis_status: str = "ready",
    error: str = "",
) -> Path:
    """Atomically persist a portable storyboard beside its project assets."""
    root = project_dir.resolve()
    source = deepcopy(state.get("source", {}))
    plan = deepcopy(state.get("plan", {}))
    scenes = deepcopy(state.get("scenes", []))
    if not isinstance(source, dict):
        source = {}
    if not isinstance(plan, dict):
        plan = {}
    if not isinstance(scenes, list):
        scenes = []
    source["audio_path"] = _portable_path(
        str(source.get("audio_path") or ""),
        root,
    )

    portable_scenes: list[dict[str, Any]] = []
    for raw_scene in scenes:
        if not isinstance(raw_scene, dict):
            continue
        scene = deepcopy(raw_scene)
        scene["image_path"] = _portable_path(
            str(scene.get("image_path") or ""),
            root,
        )
        scene["video_path"] = _portable_path(
            str(scene.get("video_path") or ""),
            root,
        )
        portable_scenes.append(scene)

    text = str(source.get("text") or "")
    document = {
        "schema": "localtext2voice.video-storyboard",
        "version": 3,
        "analysis_status": str(analysis_status or "ready"),
        "analysis_error": str(error or ""),
        "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source": source,
        "plan": plan,
        "scenes": portable_scenes,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    document = _migrate_storyboard_document(document)
    target = root / STORYBOARD_PROJECT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(document, ensure_ascii=False, indent=2)
    with _WRITE_LOCK:
        temporary.write_text(payload, encoding="utf-8")
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def load_storyboard_state(project_dir: Path) -> dict[str, Any] | None:
    root = project_dir.resolve()
    path = root / STORYBOARD_PROJECT_FILE
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    if document.get("schema") != "localtext2voice.video-storyboard":
        return None

    document = _migrate_storyboard_document(document)

    result = deepcopy(document)
    source = result.get("source", {})
    if isinstance(source, dict):
        source["audio_path"] = _restored_path(
            str(source.get("audio_path") or ""),
            root,
        )
    scenes = result.get("scenes", [])
    restored_scenes: list[dict[str, Any]] = []
    if isinstance(scenes, list):
        for raw_scene in scenes:
            if not isinstance(raw_scene, dict):
                continue
            scene = deepcopy(raw_scene)
            scene["image_path"] = _restored_path(
                str(scene.get("image_path") or ""),
                root,
            )
            scene["video_path"] = _restored_path(
                str(scene.get("video_path") or ""),
                root,
            )
            restored_scenes.append(scene)
    result["scenes"] = restored_scenes
    return result


def storyboard_matches_source(state: dict[str, Any], text: str) -> bool:
    expected = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
    return str(state.get("source_hash") or "") == expected


def _migrate_storyboard_document(document: dict[str, Any]) -> dict[str, Any]:
    """Upgrade legacy flat character/era metadata without breaking scene IDs."""
    result = deepcopy(document)
    plan = result.get("plan", {})
    scenes = result.get("scenes", [])
    if not isinstance(plan, dict):
        plan = {}
        result["plan"] = plan
    if not isinstance(scenes, list):
        scenes = []
        result["scenes"] = scenes
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        scene.setdefault("video_path", "")
        scene.setdefault("video_prompt", "")
        scene.setdefault("video_frame_role", "start")
        scene.setdefault("video_duration_seconds", 0.0)
        scene.setdefault("video_motion_in", "none")
        scene.setdefault("video_motion_out", "none")
    continuity = plan.get("continuity")
    if isinstance(continuity, dict):
        continuity.setdefault("version", 1)
        continuity.setdefault("era_locked", False)
        continuity.setdefault("characters", [])
        continuity.setdefault("locations", [])
        continuity.setdefault("eras", [])
        continuity.setdefault("assignments", [])
        continuity.setdefault("discovery_reports", [])
        result["version"] = 3
        return result

    duration = max(
        [
            float(scene.get("start_seconds") or 0.0)
            + float(scene.get("duration_seconds") or scene.get("duration") or 0.0)
            for scene in scenes
            if isinstance(scene, dict)
        ]
        + [float(plan.get("source_duration_seconds") or 0.0)]
    )
    characters: list[dict[str, Any]] = []
    style = plan.get("style", {})
    definitions = style.get("characters", []) if isinstance(style, dict) else []
    if isinstance(definitions, list):
        for raw in definitions:
            definition = str(raw or "").strip()
            if not definition:
                continue
            state_id, separator, description = definition.partition(":")
            state_id = state_id.strip()
            description = description.strip() if separator else state_id
            if not state_id:
                continue
            related = [
                scene for scene in scenes
                if isinstance(scene, dict)
                and state_id in scene.get("characters", [])
            ]
            starts = [float(scene.get("start_seconds") or 0.0) for scene in related]
            ends = [
                float(scene.get("start_seconds") or 0.0)
                + float(scene.get("duration_seconds") or scene.get("duration") or 0.0)
                for scene in related
            ]
            display_name = description.split(",", 1)[0].strip() or state_id
            characters.append(
                {
                    "id": f"char_{_portable_slug(state_id)}",
                    "name": display_name,
                    "aliases": [state_id],
                    "identity_description": "",
                    "states": [
                        {
                            "id": state_id,
                            "description": description,
                            "from_seconds": min(starts, default=0.0),
                            "to_seconds": max(ends, default=duration),
                            "source_unit_start": -1,
                            "change_reason": "legacy_migration",
                            "evidence": "",
                        }
                    ],
                }
            )

    eras: list[dict[str, Any]] = []
    previous_key = ""
    current: dict[str, Any] | None = None
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        description = str(scene.get("era") or "").strip()
        key = description.casefold()
        if not description:
            previous_key = ""
            current = None
            continue
        start = float(scene.get("start_seconds") or 0.0)
        end = start + float(
            scene.get("duration_seconds") or scene.get("duration") or 0.0
        )
        if current is None or key != previous_key:
            era_id = f"era_legacy_{len(eras) + 1}"
            current = {
                "id": era_id,
                "description": description,
                "material_culture": "",
                "from_seconds": start,
                "to_seconds": end,
                "source_unit_start": int(scene.get("source_unit_start") or -1),
                "reason": "legacy_migration",
                "evidence": "",
            }
            eras.append(current)
        else:
            current["to_seconds"] = end
        scene["era_state_id"] = str(current["id"])
        previous_key = key

    plan["continuity"] = {
        "version": 1,
        "era_locked": False,
        "characters": characters,
        "locations": [],
        "eras": eras,
        "assignments": [],
    }
    result["version"] = 3
    return result


def _portable_slug(value: str) -> str:
    return re.sub(r"[^\w]+", "_", value.casefold()).strip("_") or "unknown"


def _portable_path(value: str, project_dir: Path) -> str:
    if not value:
        return ""
    path = Path(value)
    try:
        relative = path.resolve().relative_to(project_dir)
    except (OSError, ValueError):
        return str(path)
    return relative.as_posix()


def _restored_path(value: str, project_dir: Path) -> str:
    if not value:
        return ""
    path = Path(value)
    return str(path if path.is_absolute() else project_dir / path)
