from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from app.core.video_storyboard_services import REQUIRED_COMFYUI_NODES


class VideoStoryboardImageError(RuntimeError):
    pass


StatusCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]


def generate_storyboard_frame(
    scene: dict[str, Any],
    plan: dict[str, Any],
    settings: dict[str, Any],
    target: Path,
    *,
    status: StatusCallback | None = None,
    cancelled: CancelCallback | None = None,
) -> dict[str, Any]:
    if str(settings.get("image_provider") or "comfyui") == "litellm_image":
        return _generate_litellm_storyboard_frame(
            scene, plan, settings, target, status=status, cancelled=cancelled
        )
    comfy = settings.get("comfyui", {})
    root = _normalize_url(comfy.get("base_url"), "http://127.0.0.1:8188")
    effective_plan = _effective_frame_plan(plan, scene)
    prompt = compile_effective_scene_prompt(plan, scene)
    seed = int(effective_plan.get("base_seed") or 0)
    image = settings.get("image", {})
    prefix = f"LocalText2Voice/{target.stem}-{uuid.uuid4().hex[:8]}"
    values = {
        "prompt": prompt,
        "negative": str(effective_plan.get("style", {}).get("negative") or ""),
        "seed": seed,
        "width": int(image.get("width") or 1280),
        "height": int(image.get("height") or 720),
        "prefix": prefix,
    }
    workflow_path = str(comfy.get("workflow_path") or "").strip()
    if workflow_path:
        try:
            template = json.loads(Path(workflow_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VideoStoryboardImageError(
                f"Cannot read the custom ComfyUI workflow: {exc}"
            ) from exc
        if not isinstance(template, dict):
            raise VideoStoryboardImageError("The custom ComfyUI workflow is not an object.")
        workflow = customize_workflow(template, values)
    else:
        workflow = build_z_image_workflow(prompt, seed, settings, prefix)
    if cancelled and cancelled():
        raise VideoStoryboardImageError("Frame generation was cancelled.")
    if status:
        status("queue")
    result = _http_json(
        f"{root}/prompt",
        method="POST",
        payload={"prompt": workflow, "client_id": f"ltv-{uuid.uuid4()}"},
        timeout=30,
    )
    prompt_id = str(result.get("prompt_id") or "")
    if not prompt_id:
        errors = result.get("node_errors") or result
        raise VideoStoryboardImageError(
            "ComfyUI rejected the workflow: "
            + json.dumps(errors, ensure_ascii=False)[:1200]
        )
    if status:
        status("generating")
    history = _wait_for_history(
        root,
        prompt_id,
        float(comfy.get("timeout_seconds") or 900),
        cancelled,
    )
    image_info = _find_output_image(history)
    if status:
        status("downloading")
    query = urllib.parse.urlencode(
        {
            "filename": image_info["filename"],
            "subfolder": image_info.get("subfolder", ""),
            "type": image_info.get("type", "output"),
        }
    )
    _download(f"{root}/view?{query}", target, timeout=120)
    return {
        "scene_id": str(scene.get("scene_id") or scene.get("id") or ""),
        "image_path": str(target),
        "prompt_id": prompt_id,
        "compiled_prompt": prompt,
        "seed": seed,
        "seed_applied": True,
    }


def compile_effective_scene_prompt(
    plan: dict[str, Any],
    scene: dict[str, Any],
) -> str:
    """Return the exact positive prompt inserted into ComfyUI's workflow."""
    overrides = scene.get("generation_overrides", {})
    if isinstance(overrides, dict):
        raw_prompt = str(overrides.get("raw_prompt") or "").strip()
        if raw_prompt:
            return raw_prompt
    return compile_scene_prompt(_effective_frame_plan(plan, scene), scene)


def prepare_image_runtime(
    settings: dict[str, Any],
    *,
    status: StatusCallback | None = None,
) -> dict[str, Any]:
    if str(settings.get("image_provider") or "comfyui") == "litellm_image":
        config = settings.get("litellm_image", {})
        model = str(config.get("model") or "").strip()
        if not model:
            raise VideoStoryboardImageError(
                "No LiteLLM image-generation model is configured."
            )
        return {
            "provider": "litellm_image",
            "model": model,
            "transport": "proxy" if str(config.get("base_url") or "").strip() else "sdk",
        }
    if status:
        status("unload_ollama")
    _unload_ollama(settings)
    comfy = settings.get("comfyui", {})
    root = _normalize_url(comfy.get("base_url"), "http://127.0.0.1:8188")
    if status:
        status("check_comfyui")
    stats = _http_json(f"{root}/system_stats", timeout=15)
    object_info = _http_json(f"{root}/object_info", timeout=60)
    missing_nodes = sorted(REQUIRED_COMFYUI_NODES - set(object_info))
    if missing_nodes:
        raise VideoStoryboardImageError(
            f"ComfyUI is missing required nodes: {', '.join(missing_nodes)}"
        )
    required_models = (
        ("UNETLoader", "unet_name", str(comfy.get("diffusion_model") or "")),
        ("CLIPLoader", "clip_name", str(comfy.get("text_encoder") or "")),
        ("VAELoader", "vae_name", str(comfy.get("vae_model") or "")),
    )
    missing_models: list[str] = []
    for node, field, expected in required_models:
        choices = _node_choices(object_info, node, field)
        if expected not in choices:
            missing_models.append(expected or f"{node}.{field}")
    if missing_models:
        raise VideoStoryboardImageError(
            f"ComfyUI is missing configured models: {', '.join(missing_models)}"
        )
    try:
        _http_json(
            f"{root}/free",
            method="POST",
            payload={"unload_models": True, "free_memory": True},
            timeout=30,
        )
    except VideoStoryboardImageError:
        pass
    devices = stats.get("devices", []) if isinstance(stats, dict) else []
    device = devices[0] if isinstance(devices, list) and devices else {}
    return {
        "version": str(stats.get("system", {}).get("comfyui_version", "unknown")),
        "device": str(device.get("name", "unknown")),
        "vram_total": int(device.get("vram_total", 0) or 0),
        "vram_free": int(device.get("vram_free", 0) or 0),
    }


def _generate_litellm_storyboard_frame(
    scene: dict[str, Any],
    plan: dict[str, Any],
    settings: dict[str, Any],
    target: Path,
    *,
    status: StatusCallback | None,
    cancelled: CancelCallback | None,
) -> dict[str, Any]:
    config = settings.get("litellm_image", {})
    model = str(config.get("model") or "").strip()
    if not model:
        raise VideoStoryboardImageError(
            "No LiteLLM image-generation model is configured."
        )
    if cancelled and cancelled():
        raise VideoStoryboardImageError("Frame generation was cancelled.")
    effective_plan = _effective_frame_plan(plan, scene)
    prompt = compile_effective_scene_prompt(plan, scene)
    seed = int(effective_plan.get("base_seed") or 0)
    image = settings.get("image", {})
    width = int(image.get("width") or 1280)
    height = int(image.get("height") or 720)
    timeout = float(config.get("timeout_seconds") or 300)
    root = str(config.get("base_url") or "").strip().rstrip("/")
    api_key = str(config.get("api_key") or "").strip()
    if status:
        status("generating")
    if root:
        response = _litellm_image_proxy(
            root,
            model=model,
            prompt=prompt,
            size=f"{width}x{height}",
            seed=seed,
            api_key=api_key,
            timeout=timeout,
        )
    else:
        response = _litellm_image_direct(
            model=model,
            prompt=prompt,
            size=f"{width}x{height}",
            seed=seed,
            api_key=api_key,
            timeout=timeout,
        )
    if cancelled and cancelled():
        raise VideoStoryboardImageError("Frame generation was cancelled.")
    if status:
        status("downloading")
    _save_litellm_image(response, target, timeout)
    return {
        "scene_id": str(scene.get("scene_id") or scene.get("id") or ""),
        "image_path": str(target),
        "prompt_id": str(response.get("id") or ""),
        "compiled_prompt": prompt,
        "seed": seed,
        "seed_applied": "seed" in response.get("_ltv_parameters", {}),
    }


def _litellm_image_direct(
    *, model: str, prompt: str, size: str, seed: int, api_key: str, timeout: float
) -> dict[str, Any]:
    try:
        from litellm import image_generation
    except ImportError as exc:
        raise VideoStoryboardImageError(
            "The LiteLLM Python SDK is not installed. Run run_dev.bat again."
        ) from exc
    arguments: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "timeout": timeout,
        "drop_params": True,
    }
    optional = _litellm_image_optional_parameters(model, size, seed)
    if api_key:
        arguments["api_key"] = api_key
    while True:
        try:
            response = image_generation(**arguments, **optional)
            value = _response_dict(response)
            value["_ltv_parameters"] = dict(optional)
            return value
        except Exception as exc:
            rejected = _rejected_image_parameter(exc, optional)
            if rejected:
                optional.pop(rejected, None)
                continue
            raise VideoStoryboardImageError(
                f"LiteLLM image-generation request failed: {exc}"
            ) from exc


def _litellm_image_proxy(
    root: str,
    *, model: str, prompt: str, size: str, seed: int, api_key: str, timeout: float
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    optional = _litellm_image_optional_parameters(model, size, seed)
    while True:
        payload = {"model": model, "prompt": prompt, **optional}
        request = urllib.request.Request(
            f"{root}/images/generations",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as http_response:
                raw = http_response.read()
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            rejected = (
                _rejected_image_parameter_message(detail, optional)
                if exc.code in {400, 422}
                else ""
            )
            if rejected:
                optional.pop(rejected, None)
                continue
            raise VideoStoryboardImageError(
                f"LiteLLM image proxy returned HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise VideoStoryboardImageError(
                f"Cannot connect to the LiteLLM image proxy: {getattr(exc, 'reason', exc)}"
            ) from exc
    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoStoryboardImageError(
            "The LiteLLM image proxy returned invalid JSON."
        ) from exc
    if not isinstance(response, dict):
        raise VideoStoryboardImageError(
            "The LiteLLM image proxy returned an unexpected response."
        )
    response["_ltv_parameters"] = dict(optional)
    return response


def _litellm_image_optional_parameters(
    model: str,
    size: str,
    seed: int,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "n": 1,
        "size": size,
        "response_format": "b64_json",
    }
    provider = model.split("/", 1)[0].strip().casefold()
    if provider in {"stability", "fal_ai", "black_forest_labs"}:
        parameters["seed"] = seed
    return parameters


def _rejected_image_parameter(
    error: Exception,
    parameters: dict[str, Any],
) -> str:
    status_code = getattr(error, "status_code", None)
    if not isinstance(error, TypeError) and status_code not in {400, 422}:
        return ""
    return _rejected_image_parameter_message(str(error), parameters)


def _rejected_image_parameter_message(
    message: str,
    parameters: dict[str, Any],
) -> str:
    normalized = message.casefold()
    rejection_markers = (
        "unsupported", "not supported", "unknown parameter",
        "unrecognized", "unexpected keyword", "not permitted", "extra_forbidden",
        "invalid parameter", "invalid_request_error",
    )
    if not any(marker in normalized for marker in rejection_markers):
        return ""
    for name in ("seed", "response_format", "size", "n"):
        mentioned = (
            bool(re.search(r"(?:parameter|field|argument|param)\s*['\"]?n['\"]?\b", normalized))
            if name == "n"
            else name in normalized
        )
        if name in parameters and mentioned:
            return name
    # Some compatible APIs report only a generic invalid-parameter error.
    for name in ("seed", "response_format", "size", "n"):
        if name in parameters:
            return name
    return ""


def _response_dict(response: object) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        value = model_dump()
        if isinstance(value, dict):
            return value
    try:
        value = dict(response)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise VideoStoryboardImageError(
            "LiteLLM returned an unsupported image response object."
        ) from exc
    return value


def _save_litellm_image(response: dict[str, Any], target: Path, timeout: float) -> None:
    data = response.get("data", [])
    item = data[0] if isinstance(data, list) and data else None
    if not isinstance(item, dict):
        raise VideoStoryboardImageError("LiteLLM returned no generated image.")
    encoded = str(item.get("b64_json") or "").strip()
    if encoded:
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise VideoStoryboardImageError(
                "LiteLLM returned invalid base64 image data."
            ) from exc
        if not image_bytes:
            raise VideoStoryboardImageError("LiteLLM returned an empty image.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            temporary.write_bytes(image_bytes)
            os.replace(temporary, target)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise VideoStoryboardImageError(
                f"Cannot save the LiteLLM image: {exc}"
            ) from exc
        return
    url = str(item.get("url") or "").strip()
    if url:
        _download(url, target, timeout=min(timeout, 300))
        return
    raise VideoStoryboardImageError(
        "LiteLLM returned neither b64_json nor an image URL."
    )


def release_comfyui_memory(settings: dict[str, Any]) -> None:
    if str(settings.get("image_provider") or "comfyui") != "comfyui":
        return
    comfy = settings.get("comfyui", {})
    root = _normalize_url(comfy.get("base_url"), "http://127.0.0.1:8188")
    try:
        _http_json(
            f"{root}/free",
            method="POST",
            payload={"unload_models": True, "free_memory": True},
            timeout=30,
        )
    except VideoStoryboardImageError:
        return


def compile_scene_prompt(plan: dict[str, Any], scene: dict[str, Any]) -> str:
    style = plan.get("style", {}) if isinstance(plan.get("style"), dict) else {}
    requested_names = {
        str(name).strip().casefold()
        for name in scene.get("characters", [])
        if str(name).strip()
    } if isinstance(scene.get("characters"), list) else set()
    descriptions: list[str] = []
    character_locks: list[str] = []
    resolved_character_state_ids: set[str] = set()
    location_locks: list[str] = []
    continuity = (
        plan.get("continuity", {})
        if isinstance(plan.get("continuity"), dict)
        else {}
    )
    if continuity:
        for character in continuity.get("characters", []):
            if not isinstance(character, dict):
                continue
            name = str(character.get("name") or "").strip()
            identity = str(character.get("identity_description") or "").strip()
            for state in character.get("states", []):
                if not isinstance(state, dict):
                    continue
                state_id = str(state.get("id") or "").strip()
                if not state_id or state_id.casefold() not in requested_names:
                    continue
                resolved_character_state_ids.add(state_id.casefold())
                state_description = str(state.get("description") or "").strip()
                if name and identity.casefold().startswith(name.casefold()):
                    identity = identity[len(name):].lstrip(" ,:;.-")
                combined = ", ".join(
                    value for value in (name, identity, state_description) if value
                )
                details = ", ".join(
                    value.rstrip(" .")
                    for value in (identity, state_description)
                    if value
                )
                descriptions.append(
                    f"{name}: {details}" if name and details else combined
                )
                if combined:
                    character_locks.append(
                        f"{name} ({details})" if name and details else combined
                    )
    if isinstance(style.get("characters"), list):
        for value in style["characters"]:
            description = str(value).strip()
            name = description.split(":", 1)[0].strip()
            if not name or name.casefold() not in requested_names:
                continue
            if name.casefold() in resolved_character_state_ids:
                continue
            if description not in descriptions:
                descriptions.append(description)
                character_locks.append(description)
    requested_locations = {
        str(value).strip().casefold()
        for value in scene.get("locations", [])
        if str(value).strip()
    } if isinstance(scene.get("locations"), list) else set()
    for location in continuity.get("locations", []):
        if not isinstance(location, dict):
            continue
        name = str(location.get("name") or "").strip()
        identity = str(location.get("identity_description") or "").strip()
        for state in location.get("states", []):
            if not isinstance(state, dict):
                continue
            state_id = str(state.get("id") or "").strip()
            if not state_id or state_id.casefold() not in requested_locations:
                continue
            state_description = str(state.get("description") or "").strip()
            combined = ", ".join(
                value for value in (name, identity, state_description) if value
            )
            if combined:
                location_locks.append(combined)
    scene_prompt, scene_shot = _generation_scene_fields(
        scene,
        descriptions,
    )
    narrative_context = (
        plan.get("narrative_context", {})
        if isinstance(plan.get("narrative_context"), dict)
        else {}
    )
    era = ""
    era_state_id = str(scene.get("era_state_id") or "").strip().casefold()
    if era_state_id:
        for value in continuity.get("eras", []):
            if not isinstance(value, dict):
                continue
            if str(value.get("id") or "").strip().casefold() != era_state_id:
                continue
            era = "; ".join(
                part for part in (
                    str(value.get("description") or "").strip(),
                    str(value.get("material_culture") or "").strip(),
                ) if part
            )
            break
    if not era:
        era = str(
            scene.get("era") or narrative_context.get("era") or ""
        ).strip()
    medium = str(style.get("medium") or "").strip()
    palette = str(style.get("palette") or "").strip()
    lighting = str(style.get("lighting") or "").strip()
    style_parts = [medium] if medium else []
    if palette:
        style_parts.append(f"palette: {palette}")
    if lighting:
        style_parts.append(f"lighting: {lighting}")
    style_prompt = "; ".join(style_parts)
    scene_parts = [scene_prompt]
    if character_locks:
        scene_parts.append("CHARACTERS: " + " | ".join(character_locks))
    if location_locks:
        scene_parts.append(
            "Location continuity: " + " | ".join(location_locks)
        )
    compiled_scene = ". ".join(
        part.rstrip(". ") for part in scene_parts if part.strip()
    )
    return " ".join(
        part
        for part in (
            (
                f"SCENE: {compiled_scene}"
                + ("" if compiled_scene.endswith((".", "!", "?")) else ".")
            ),
            f"STYLE: {style_prompt}." if style_prompt else "",
            f"SHOT AND COMPOSITION: {scene_shot}." if scene_shot else "",
            (
                f"ERA AND MATERIAL CULTURE: {era}."
                if era
                else ""
            ),
        )
        if part
    )


def canonical_character_lines_for_prompt(
    plan: dict[str, Any],
    scene: dict[str, Any],
    prompt: str,
) -> list[str]:
    """Return active continuity-state lines for canonical names in a prompt."""
    continuity = (
        plan.get("continuity", {})
        if isinstance(plan.get("continuity"), dict)
        else {}
    )
    characters = [
        value
        for value in continuity.get("characters", [])
        if isinstance(value, dict) and str(value.get("name") or "").strip()
    ]
    if not characters or not str(prompt).strip():
        return []

    try:
        start = float(scene.get("start_seconds") or 0.0)
    except (TypeError, ValueError):
        start = 0.0
    try:
        duration = max(0.0, float(scene.get("duration_seconds") or 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    reference_time = start + (duration / 2.0)

    # Prefer the longest canonical name, so a mention of "Maria Elena" does
    # not also select a different character named "Maria" from the same span.
    occupied_spans: list[tuple[int, int]] = []
    matched: list[dict[str, Any]] = []
    for character in sorted(
        characters,
        key=lambda value: len(str(value.get("name") or "")),
        reverse=True,
    ):
        name = str(character.get("name") or "").strip()
        match = re.search(
            rf"(?<!\w){re.escape(name)}(?!\w)",
            str(prompt),
            flags=re.IGNORECASE,
        )
        if match is None or any(
            match.start() < end and match.end() > begin
            for begin, end in occupied_spans
        ):
            continue
        occupied_spans.append(match.span())
        matched.append(character)

    lines: list[str] = []
    for character in matched:
        active_states: list[dict[str, Any]] = []
        for state in character.get("states", []):
            if not isinstance(state, dict) or not str(state.get("id") or "").strip():
                continue
            try:
                from_seconds = float(state.get("from_seconds", float("-inf")))
            except (TypeError, ValueError):
                from_seconds = float("-inf")
            try:
                to_seconds = float(state.get("to_seconds", float("inf")))
            except (TypeError, ValueError):
                to_seconds = float("inf")
            if from_seconds <= reference_time <= to_seconds:
                active_states.append(state)
        if not active_states:
            continue
        active_state = max(
            active_states,
            key=lambda state: float(state.get("from_seconds") or 0.0),
        )
        state_id = str(active_state.get("id") or "").strip()
        details = ", ".join(
            value
            for value in (
                str(character.get("name") or "").strip(),
                str(character.get("identity_description") or "").strip(),
                str(active_state.get("description") or "").strip(),
            )
            if value
        )
        lines.append(f"{state_id}: {details}" if details else state_id)
    return lines


def _effective_frame_plan(
    plan: dict[str, Any],
    scene: dict[str, Any],
) -> dict[str, Any]:
    effective = deepcopy(plan)
    overrides = scene.get("generation_overrides", {})
    if not isinstance(overrides, dict):
        return effective
    override_style = overrides.get("style", {})
    if isinstance(override_style, dict):
        effective["style"] = {
            **dict(effective.get("style", {})),
            **override_style,
        }
    try:
        override_seed = int(overrides.get("seed"))
    except (TypeError, ValueError):
        override_seed = -1
    if override_seed >= 0:
        effective["base_seed"] = min(override_seed, 2**63 - 1)
    return effective


def _generation_scene_fields(
    scene: dict[str, Any],
    active_descriptions: list[str],
) -> tuple[str, str]:
    prompt = str(scene.get("prompt") or "").strip()
    shot = str(scene.get("shot") or "").strip()
    legacy_marker = " Cinematic coverage frame "
    if legacy_marker not in prompt:
        return prompt, shot

    # Projects analyzed by the first coverage implementation are repaired at
    # generation time as well, so they never send the verbose meta-instruction
    # to ComfyUI even before the user runs Analyze again.
    base_prompt = prompt.split(legacy_marker, 1)[0].rstrip()
    coverage_index = max(1, int(scene.get("coverage_index") or 1))
    if coverage_index == 1:
        return base_prompt, ""

    characters = [
        value.split(":", 1)[0].strip()
        for value in active_descriptions
        if value.split(":", 1)[0].strip()
    ]
    if characters:
        subject = characters[(coverage_index - 2) % len(characters)]
        choices = [
            f"Close-up of {subject}.",
            f"Profile view of {subject}.",
            f"Three-quarter view of {subject}.",
            f"Low-angle view of {subject}.",
        ]
    else:
        choices = [
            "Close-up of the most important visible object or landmark.",
            "Side view of the main visible subject.",
            "Three-quarter view of the main visible subject.",
            "High-angle view of the most important visual element.",
        ]
    stable_key = (
        f"{scene.get('semantic_scene_id', '')}|{base_prompt}"
    ).encode("utf-8")
    offset = int.from_bytes(hashlib.sha256(stable_key).digest()[:2], "big")
    framing = choices[(offset + coverage_index - 2) % len(choices)]
    separator = "" if base_prompt.endswith((".", "!", "?")) else "."
    return f"{base_prompt}{separator} {framing}".strip(), ""


def build_z_image_workflow(
    prompt: str,
    seed: int,
    settings: dict[str, Any],
    prefix: str,
) -> dict[str, Any]:
    comfy = settings.get("comfyui", {})
    image = settings.get("image", {})
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": str(comfy.get("diffusion_model") or ""),
                "weight_dtype": "default",
            },
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": str(comfy.get("text_encoder") or ""),
                "type": "lumina2",
                "device": "default",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": str(comfy.get("vae_model") or "")},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["2", 0]},
        },
        "5": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["4", 0]},
        },
        "6": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {
                "width": int(image.get("width") or 1280),
                "height": int(image.get("height") or 720),
                "batch_size": 1,
            },
        },
        "7": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {
                "model": ["1", 0],
                "shift": float(image.get("auraflow_shift") or 3.0),
            },
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["7", 0],
                "seed": seed,
                "steps": int(image.get("steps") or 8),
                "cfg": float(image.get("cfg") or 1.0),
                "sampler_name": str(image.get("sampler") or "res_multistep"),
                "scheduler": str(image.get("scheduler") or "simple"),
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
                "denoise": float(image.get("denoise") or 1.0),
            },
        },
        "9": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["8", 0], "vae": ["3", 0]},
        },
        "10": {
            "class_type": "SaveImage",
            "inputs": {"images": ["9", 0], "filename_prefix": prefix},
        },
    }


def customize_workflow(
    template: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    serialized = json.dumps(template)
    workflow = _replace_placeholders(
        deepcopy(template),
        {
            "{{PROMPT}}": values["prompt"],
            "{{NEGATIVE_PROMPT}}": values.get("negative", ""),
            "{{SEED}}": values["seed"],
            "{{WIDTH}}": values["width"],
            "{{HEIGHT}}": values["height"],
            "{{PREFIX}}": values["prefix"],
        },
    )
    nodes = [node for node in workflow.values() if isinstance(node, dict)]
    if "{{PROMPT}}" not in serialized:
        text_node = next(
            (
                node
                for node in nodes
                if node.get("class_type") == "CLIPTextEncode"
                and isinstance(node.get("inputs", {}).get("text"), str)
            ),
            None,
        )
        if text_node is None:
            raise VideoStoryboardImageError(
                "The custom workflow needs CLIPTextEncode or {{PROMPT}}."
            )
        text_node["inputs"]["text"] = values["prompt"]
    sampler_found = False
    save_found = False
    for node in nodes:
        node_type = node.get("class_type")
        inputs = node.setdefault("inputs", {})
        if node_type == "KSampler":
            inputs["seed"] = values["seed"]
            sampler_found = True
        if node_type in {"EmptyLatentImage", "EmptySD3LatentImage"}:
            inputs["width"] = values["width"]
            inputs["height"] = values["height"]
        if node_type == "SaveImage":
            inputs["filename_prefix"] = values["prefix"]
            save_found = True
    if not sampler_found and "{{SEED}}" not in serialized:
        raise VideoStoryboardImageError("The custom workflow needs KSampler or {{SEED}}.")
    if not save_found and "{{PREFIX}}" not in serialized:
        raise VideoStoryboardImageError("The custom workflow needs SaveImage or {{PREFIX}}.")
    return workflow


def _unload_ollama(settings: dict[str, Any]) -> None:
    ollama = settings.get("ollama", {})
    model = str(ollama.get("model") or "").strip()
    if not model:
        return
    root = _normalize_url(ollama.get("base_url"), "http://127.0.0.1:11434")
    try:
        _http_json(
            f"{root}/api/generate",
            method="POST",
            payload={"model": model, "keep_alive": 0},
            timeout=60,
        )
    except VideoStoryboardImageError:
        return
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            running = _http_json(f"{root}/api/ps", timeout=5).get("models", [])
        except VideoStoryboardImageError:
            return
        if not any(
            str(item.get("name") or item.get("model") or "") == model
            for item in running
            if isinstance(item, dict)
        ):
            return
        time.sleep(0.5)


def _wait_for_history(
    root: str,
    prompt_id: str,
    timeout: float,
    cancelled: CancelCallback | None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    endpoint = f"{root}/history/{urllib.parse.quote(prompt_id)}"
    while time.monotonic() < deadline:
        if cancelled and cancelled():
            try:
                _http_json(f"{root}/interrupt", method="POST", payload={}, timeout=10)
            except VideoStoryboardImageError:
                pass
            raise VideoStoryboardImageError("Frame generation was cancelled.")
        history = _http_json(endpoint, timeout=30)
        entry = history.get(prompt_id)
        if isinstance(entry, dict):
            status = entry.get("status", {})
            if isinstance(status, dict) and status.get("status_str") == "error":
                raise VideoStoryboardImageError(
                    "ComfyUI generation failed: "
                    + json.dumps(status, ensure_ascii=False)[:1500]
                )
            if isinstance(status, dict) and status.get("completed"):
                return entry
        time.sleep(1.0)
    raise VideoStoryboardImageError(
        f"ComfyUI did not finish within {round(timeout)} seconds."
    )


def _find_output_image(history: dict[str, Any]) -> dict[str, Any]:
    outputs = history.get("outputs", {})
    if isinstance(outputs, dict):
        for output in outputs.values():
            images = output.get("images", []) if isinstance(output, dict) else []
            for image in images:
                if isinstance(image, dict) and image.get("filename"):
                    return image
    raise VideoStoryboardImageError(
        "ComfyUI completed the job but returned no output image."
    )


def _download(url: str, target: Path, timeout: float) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"Accept": "image/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise VideoStoryboardImageError(f"Cannot download the ComfyUI image: {exc}") from exc
    if not data:
        raise VideoStoryboardImageError("ComfyUI returned an empty image.")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise VideoStoryboardImageError(f"Cannot save the generated image: {exc}") from exc


def _node_choices(
    object_info: dict[str, Any],
    node_name: str,
    field_name: str,
) -> list[str]:
    try:
        values = object_info[node_name]["input"]["required"][field_name][0]
    except (KeyError, IndexError, TypeError):
        return []
    return [str(value) for value in values] if isinstance(values, list) else []


def _replace_placeholders(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, str):
        result = value
        for token, replacement in replacements.items():
            result = result.replace(token, str(replacement))
        return result
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_placeholders(item, replacements)
            for key, item in value.items()
        }
    return value


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise VideoStoryboardImageError(
            f"HTTP {exc.code} from {url}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise VideoStoryboardImageError(
            f"Cannot connect to {url}: {getattr(exc, 'reason', exc)}"
        ) from exc
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoStoryboardImageError(f"Invalid JSON returned by {url}.") from exc
    if not isinstance(value, dict):
        raise VideoStoryboardImageError(f"Unexpected response returned by {url}.")
    return value


def _normalize_url(value: object, fallback: str) -> str:
    url = str(value or fallback).strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise VideoStoryboardImageError("The service URL must use HTTP or HTTPS.")
    return url
