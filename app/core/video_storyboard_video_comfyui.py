from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.comfyui_http import comfyui_request_headers
from app.core.video_storyboard_comfyui import (
    VideoStoryboardImageError,
    _http_json,
    _node_choices,
    _normalize_url,
    _unload_ollama,
)
from app.utils.ffmpeg_utils import FFmpegError, FFmpegRunner, find_ffmpeg


class VideoStoryboardVideoError(RuntimeError):
    pass


StatusCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]

WAN_VIDEO_PROFILE = "wan22_rapid"
LTX_VIDEO_PROFILE = "ltx23_i2v"
CUSTOM_VIDEO_PROFILE = "custom"
VIDEO_WORKFLOW_PROFILES = {
    WAN_VIDEO_PROFILE: "Wan 2.2 Rapid · image to video",
    LTX_VIDEO_PROFILE: "LTX 2.3 · image to video",
    CUSTOM_VIDEO_PROFILE: "Custom ComfyUI workflow",
}
VIDEO_WORKFLOW_BINDING_KEYS = (
    "image",
    "prompt",
    "seed",
    "width",
    "height",
    "duration",
    "fps",
    "frame_count",
    "output_prefix",
)

LTX_BINDINGS = {
    "image": "269.image",
    "prompt": "320:319.value",
    "seed": "320:277.noise_seed",
    "width": "320:312.value",
    "height": "320:299.value",
    "duration": "320:301.value",
    "fps": "320:300.value",
    "output_prefix": "75.filename_prefix",
}


@dataclass(frozen=True)
class BuiltVideoWorkflow:
    workflow: dict[str, Any]
    profile: str
    generated_frames: int
    generated_duration_seconds: float


def bundled_wan_video_workflow_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "assets"
        / "comfyui"
        / "wan22_rapid_aio_q4_i2v_640x360_api.json"
    )


def bundled_ltx_video_workflow_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "assets"
        / "comfyui"
        / "ltx_2_3_i2v_h100_api.json"
    )


def video_workflow_profile(value: object) -> str:
    profile = str(value or WAN_VIDEO_PROFILE).strip().casefold()
    return profile if profile in VIDEO_WORKFLOW_PROFILES else WAN_VIDEO_PROFILE


def video_workflow_path(config: dict[str, Any]) -> Path:
    configured = str(config.get("workflow_path") or "").strip()
    if configured:
        return Path(configured)
    profile = video_workflow_profile(config.get("workflow_profile"))
    if profile == WAN_VIDEO_PROFILE:
        return bundled_wan_video_workflow_path()
    if profile == LTX_VIDEO_PROFILE:
        return bundled_ltx_video_workflow_path()
    raise VideoStoryboardVideoError(
        "Choose a ComfyUI API workflow JSON for the custom video profile."
    )


def prepare_video_runtime(
    settings: dict[str, Any],
    *,
    status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Release local AI models and validate the configured ComfyUI workflow."""
    if settings.get("video_provider") == "disabled":
        raise VideoStoryboardVideoError("Choose a video provider in Settings > Video Storyboard.")
    if settings.get("video_provider") == "runpod":
        from app.core import video_storyboard_runpod as rp
        try:
            return rp.prepare(settings, "video")
        except rp.RunpodError as exc:
            raise VideoStoryboardVideoError(str(exc)) from exc
    config = _video_config(settings)
    root = _normalize_url(config.get("base_url"), "http://127.0.0.1:8188")
    headers = _authorization_headers(config)
    workflow = _load_video_workflow(config)
    profile = video_workflow_profile(config.get("workflow_profile"))
    try:
        if status:
            status("unload_ollama")
        _unload_ollama(settings)
        if status:
            status("unload_comfyui")
        try:
            _http_json(
                f"{root}/free",
                method="POST",
                payload={"unload_models": True, "free_memory": True},
                timeout=30,
                headers=headers,
            )
        except VideoStoryboardImageError:
            # Older ComfyUI builds may not expose /free. Runtime validation below
            # still provides a useful connection or node error.
            pass
        if status:
            status("check_comfyui_video")
        object_info = _http_json(
            f"{root}/object_info", timeout=60, headers=headers
        )
        stats = _wait_for_comfyui_memory_release(root, headers=headers)
    except VideoStoryboardImageError as exc:
        raise VideoStoryboardVideoError(str(exc)) from exc

    workflow_nodes = {
        str(node.get("class_type") or "")
        for node in workflow.values()
        if isinstance(node, dict) and node.get("class_type")
    }
    # Validate the actual workflow rather than a hand-maintained subset. This
    # makes built-in overrides and arbitrary custom workflows fail early with
    # a useful list of missing custom nodes.
    missing_nodes = sorted(workflow_nodes - set(object_info))
    if missing_nodes:
        raise VideoStoryboardVideoError(
            "ComfyUI is missing video nodes: " + ", ".join(missing_nodes)
        )

    required_models = (
        (
            "UnetLoaderGGUF",
            "unet_name",
            str(config.get("unet_model") or ""),
        ),
        (
            "CLIPLoader",
            "clip_name",
            str(config.get("text_encoder") or ""),
        ),
        ("VAELoader", "vae_name", str(config.get("vae_model") or "")),
        (
            "CLIPVisionLoader",
            "clip_name",
            str(config.get("clip_vision_model") or ""),
        ),
    )
    if profile == WAN_VIDEO_PROFILE:
        missing_models: list[str] = []
        for node, field, expected in required_models:
            choices = _node_choices(object_info, node, field)
            if expected and expected not in choices:
                missing_models.append(expected)
        if missing_models:
            raise VideoStoryboardVideoError(
                "ComfyUI is missing configured video models: "
                + ", ".join(missing_models)
            )

    devices = stats.get("devices", []) if isinstance(stats, dict) else []
    device = devices[0] if isinstance(devices, list) and devices else {}
    return {
        "provider": "comfyui",
        "workflow_profile": profile,
        "device": str(device.get("name") or "unknown"),
        "vram_total": int(device.get("vram_total", 0) or 0),
        "vram_free": int(device.get("vram_free", 0) or 0),
    }


def generate_storyboard_scene_video(
    scene: dict[str, Any],
    plan: dict[str, Any],
    settings: dict[str, Any],
    target: Path,
    *,
    prompt: str,
    frame_role: str = "start",
    status: StatusCallback | None = None,
    cancelled: CancelCallback | None = None,
) -> dict[str, Any]:
    if settings.get("video_provider") == "runpod":
        return _generate_runpod_video(scene, plan, settings, target, prompt=prompt, frame_role=frame_role, status=status, cancelled=cancelled)
    if settings.get("video_provider") == "disabled":
        raise VideoStoryboardVideoError("Choose a video provider in Settings > Video Storyboard.")
    config = _video_config(settings)
    root = _normalize_url(config.get("base_url"), "http://127.0.0.1:8188")
    headers = _authorization_headers(config)
    image_path = Path(str(scene.get("image_path") or ""))
    if not image_path.is_file():
        raise VideoStoryboardVideoError(
            "Generate or import the scene frame before creating its video."
        )
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise VideoStoryboardVideoError("The video prompt is empty.")
    role = "end" if str(frame_role).casefold() == "end" else "start"
    target_duration = max(0.1, float(scene.get("duration_seconds") or 0.1))
    timeout = float(config.get("timeout_seconds") or 1800)
    if cancelled and cancelled():
        raise VideoStoryboardVideoError("Video generation was cancelled.")

    if status:
        status("uploading_frame")
    auth_kwargs = {"headers": headers} if headers else {}
    uploaded_name = _upload_image(
        root,
        image_path,
        min(timeout, 180.0),
        **auth_kwargs,
    )
    built = build_video_workflow(
        settings,
        prompt=clean_prompt,
        image_name=uploaded_name,
        seed=int(plan.get("base_seed") or 0),
        prefix=f"LocalText2Voice/video/{target.stem}-{uuid.uuid4().hex[:8]}",
        duration_seconds=target_duration,
    )
    workflow = built.workflow
    if status:
        status("queue_video")
    try:
        queued = _http_json(
            f"{root}/prompt",
            method="POST",
            payload={"prompt": workflow, "client_id": f"ltv-video-{uuid.uuid4()}"},
            timeout=30,
            headers=headers,
        )
    except VideoStoryboardImageError as exc:
        raise VideoStoryboardVideoError(str(exc)) from exc
    prompt_id = str(queued.get("prompt_id") or "")
    if not prompt_id:
        errors = queued.get("node_errors") or queued
        raise VideoStoryboardVideoError(
            "ComfyUI rejected the video workflow: "
            + json.dumps(errors, ensure_ascii=False)[:1600]
        )

    if status:
        status("generating_video")
    history = _wait_for_video_history(
        root,
        prompt_id,
        timeout,
        cancelled,
        **auth_kwargs,
    )
    video_info = _find_output_video(history, str(config.get("output_node") or "") if config.get("workflow_profile") == "custom" else "")
    query = urllib.parse.urlencode(
        {
            "filename": video_info["filename"],
            "subfolder": video_info.get("subfolder", ""),
            "type": video_info.get("type", "output"),
        }
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if status:
        status("downloading_video")
    if role == "end":
        forward = target.with_name(f".{target.stem}.forward{target.suffix}")
        try:
            _download_video(
                f"{root}/view?{query}",
                forward,
                min(timeout, 300.0),
                **auth_kwargs,
            )
            if status:
                status("reversing_video")
            _reverse_video(forward, target, settings)
        finally:
            forward.unlink(missing_ok=True)
    else:
        _download_video(
            f"{root}/view?{query}",
            target,
            min(timeout, 300.0),
            **auth_kwargs,
        )
    continuation = {}
    if built.profile == WAN_VIDEO_PROFILE:
        if config.get("wan_behavior") == "stretch":
            settings = {**settings, "comfyui_video": {**config, "fps": built.generated_frames / target_duration}}
        elif target_duration > built.generated_duration_seconds + 0.0001:
            target_duration = built.generated_duration_seconds
            last_frame = target.with_suffix(".continuation.png")
            runner = FFmpegRunner(find_ffmpeg(settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe")))
            runner.run(["-y", "-i", str(target), "-vf", f"select=eq(n\\,{built.generated_frames - 1})", "-frames:v", "1", str(last_frame)])
            if not last_frame.is_file():
                raise VideoStoryboardVideoError("Could not extract the final Wan frame for the continuation scene.")
            continuation = {"duration_seconds": target_duration}
    downloaded_duration = _probe_media_duration(target, settings)
    generated_duration = downloaded_duration or built.generated_duration_seconds
    if status:
        status("retiming_video")
    _retime_video(
        target,
        target_duration,
        settings,
        source_duration_seconds=generated_duration,
    )
    if continuation:
        target.with_suffix(".continuation.json").write_text(json.dumps(continuation), encoding="utf-8")
    return {
        "scene_id": str(scene.get("scene_id") or scene.get("id") or ""),
        "video_path": str(target),
        "prompt_id": prompt_id,
        "video_prompt": clean_prompt,
        "video_frame_role": role,
        "video_duration_seconds": target_duration,
        "workflow_profile": built.profile,
        "generated_frames": built.generated_frames,
        "generated_duration_seconds": generated_duration,
        "seed": int(plan.get("base_seed") or 0),
    }


def build_video_workflow(
    settings: dict[str, Any],
    *,
    prompt: str,
    image_name: str,
    seed: int,
    prefix: str,
    duration_seconds: float | None = None,
) -> BuiltVideoWorkflow:
    config = _video_config(settings)
    profile = video_workflow_profile(config.get("workflow_profile"))
    workflow = _load_video_workflow(config)
    duration = max(0.1, float(duration_seconds or 0.1))
    generated_frames, generated_duration = video_generation_metrics(
        config,
        duration_seconds,
    )
    if profile == WAN_VIDEO_PROFILE:
        if config.get("wan_behavior") == "stretch":
            config = {**config, "fps": generated_frames / generated_duration}
        _customize_wan_workflow(
            workflow,
            config,
            prompt=prompt,
            image_name=image_name,
            seed=seed,
            prefix=prefix,
            frame_count=generated_frames,
        )
    elif profile == LTX_VIDEO_PROFILE:
        ltx_duration = max(1, math.ceil(duration))
        _customize_ltx_workflow(
            workflow,
            config,
            prompt=prompt,
            image_name=image_name,
            seed=seed,
            prefix=prefix,
            duration_seconds=ltx_duration,
            frame_count=generated_frames,
        )
    else:
        replacements = _generic_video_replacements(
            config,
            prompt=prompt,
            image_name=image_name,
            seed=seed,
            prefix=prefix,
            duration_seconds=duration,
            frame_count=generated_frames,
        )
        serialized = json.dumps(workflow)
        workflow = _replace_video_placeholders(workflow, replacements)
        applied = _apply_configured_bindings(
            workflow,
            config.get("bindings"),
            replacements,
        )
        _apply_custom_fallbacks(
            workflow,
            replacements,
            serialized=serialized,
            applied=applied,
        )
    return BuiltVideoWorkflow(
        workflow=workflow,
        profile=profile,
        generated_frames=generated_frames,
        generated_duration_seconds=generated_duration,
    )


def video_generation_metrics(
    config: dict[str, Any],
    duration_seconds: float | None,
) -> tuple[int, float]:
    profile = video_workflow_profile(config.get("workflow_profile"))
    fps = max(1, int(config.get("fps") or 24))
    duration = max(0.1, float(duration_seconds or 0.1))
    if profile == WAN_VIDEO_PROFILE:
        if duration_seconds is None:
            frames = min(81, max(5, int(config.get("frames") or 49)))
            return frames, frames / fps
        frames = wan_video_frame_count(
            duration,
            fps,
            fallback_frames=int(config.get("frames") or 49),
        )
        frames = min(81, frames)
        return frames, (duration if config.get("wan_behavior") == "stretch" else min(duration, frames / fps))
    if profile == LTX_VIDEO_PROFILE:
        whole_seconds = max(1, math.ceil(duration))
        frames = whole_seconds * fps + 1
        return frames, frames / fps
    frames = max(1, math.ceil(duration * fps))
    return frames, duration


def build_wan_video_workflow(
    settings: dict[str, Any],
    *,
    prompt: str,
    image_name: str,
    seed: int,
    prefix: str,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper retained for existing callers and tests."""
    selected = deepcopy(settings)
    config = dict(selected.get("comfyui_video", {}))
    config["workflow_profile"] = WAN_VIDEO_PROFILE
    selected["comfyui_video"] = config
    return build_video_workflow(
        selected,
        prompt=prompt,
        image_name=image_name,
        seed=seed,
        prefix=prefix,
        duration_seconds=duration_seconds,
    ).workflow


def _load_video_workflow(config: dict[str, Any]) -> dict[str, Any]:
    workflow_path = video_workflow_path(config)
    try:
        raw = json.loads(workflow_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VideoStoryboardVideoError(
            f"Cannot read the ComfyUI video workflow: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise VideoStoryboardVideoError("The ComfyUI video workflow is not an object.")
    return deepcopy(raw)


def _customize_wan_workflow(
    workflow: dict[str, Any],
    config: dict[str, Any],
    *,
    prompt: str,
    image_name: str,
    seed: int,
    prefix: str,
    frame_count: int,
) -> None:
    serialized = json.dumps(workflow)
    positive_node: dict[str, Any] | None = None
    load_image_found = False
    save_video_found = False
    sampler_found = False
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("class_type") or "")
        inputs = node.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            continue
        if node_type == "LoadImage":
            inputs["image"] = image_name
            load_image_found = True
        elif node_type == "CLIPTextEncode" and (
            str(node_id) == "5" or positive_node is None
        ):
            positive_node = node
        elif node_type == "KSampler":
            inputs["seed"] = seed
            inputs["steps"] = int(config.get("steps") or 4)
            sampler_found = True
        elif node_type == "WanImageToVideo":
            inputs["width"] = int(config.get("width") or 640)
            inputs["height"] = int(config.get("height") or 360)
            inputs["length"] = frame_count
            inputs["batch_size"] = 1
        elif node_type == "CreateVideo":
            inputs["fps"] = float(config.get("fps") or 24.0)
        elif node_type == "SaveVideo":
            inputs["filename_prefix"] = prefix
            inputs["format"] = "mp4"
            save_video_found = True
        elif node_type == "UnetLoaderGGUF":
            inputs["unet_name"] = str(config.get("unet_model") or inputs.get("unet_name") or "")
        elif node_type == "CLIPLoader":
            inputs["clip_name"] = str(config.get("text_encoder") or inputs.get("clip_name") or "")
        elif node_type == "VAELoader":
            inputs["vae_name"] = str(config.get("vae_model") or inputs.get("vae_name") or "")
        elif node_type == "CLIPVisionLoader":
            inputs["clip_name"] = str(config.get("clip_vision_model") or inputs.get("clip_name") or "")
    if positive_node is not None:
        positive_node.setdefault("inputs", {})["text"] = prompt
    if "{{PROMPT}}" in serialized:
        replaced = _replace_video_placeholders(workflow, {"{{PROMPT}}": prompt})
        workflow.clear()
        workflow.update(replaced)
    if not load_image_found or not sampler_found or not save_video_found:
        raise VideoStoryboardVideoError(
            "The video workflow must contain LoadImage, KSampler and SaveVideo nodes."
        )
    if positive_node is None and "{{PROMPT}}" not in serialized:
        raise VideoStoryboardVideoError(
            "The video workflow needs a CLIPTextEncode node or {{PROMPT}}."
        )


def _customize_ltx_workflow(
    workflow: dict[str, Any],
    config: dict[str, Any],
    *,
    prompt: str,
    image_name: str,
    seed: int,
    prefix: str,
    duration_seconds: int,
    frame_count: int,
) -> None:
    replacements = _generic_video_replacements(
        config,
        prompt=prompt,
        image_name=image_name,
        seed=seed,
        prefix=prefix,
        duration_seconds=duration_seconds,
        frame_count=frame_count,
    )
    _apply_bindings(workflow, LTX_BINDINGS, replacements, required=True)
    enhance = workflow.get("320:328")
    if isinstance(enhance, dict) and isinstance(enhance.get("inputs"), dict):
        enhance["inputs"]["value"] = bool(config.get("ltx_prompt_enhance", False))


def _generic_video_replacements(
    config: dict[str, Any],
    *,
    prompt: str,
    image_name: str,
    seed: int,
    prefix: str,
    duration_seconds: float | int,
    frame_count: int,
) -> dict[str, Any]:
    fps = max(1, int(config.get("fps") or 24))
    values = {
        "image": image_name,
        "prompt": prompt,
        "seed": int(seed),
        "width": int(config.get("width") or 640),
        "height": int(config.get("height") or 360),
        "duration": duration_seconds,
        "fps": fps,
        "frame_count": int(frame_count),
        "output_prefix": prefix,
    }
    return {
        "{{IMAGE}}": values["image"],
        "{{PROMPT}}": values["prompt"],
        "{{SEED}}": values["seed"],
        "{{WIDTH}}": values["width"],
        "{{HEIGHT}}": values["height"],
        "{{DURATION}}": values["duration"],
        "{{DURATION_SECONDS}}": values["duration"],
        "{{FPS}}": values["fps"],
        "{{FRAMES}}": values["frame_count"],
        "{{FRAME_COUNT}}": values["frame_count"],
        "{{OUTPUT_PREFIX}}": values["output_prefix"],
        **values,
    }


def _replace_video_placeholders(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value in replacements:
            return replacements[value]
        result = value
        for token, replacement in replacements.items():
            if isinstance(token, str) and token.startswith("{{"):
                result = result.replace(token, str(replacement))
        return result
    if isinstance(value, list):
        return [_replace_video_placeholders(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_video_placeholders(item, replacements)
            for key, item in value.items()
        }
    return value


def _apply_configured_bindings(
    workflow: dict[str, Any],
    raw_bindings: object,
    replacements: dict[str, Any],
) -> set[str]:
    bindings = raw_bindings if isinstance(raw_bindings, dict) else {}
    return _apply_bindings(workflow, bindings, replacements, required=False)


def _apply_bindings(
    workflow: dict[str, Any],
    bindings: dict[str, Any],
    replacements: dict[str, Any],
    *,
    required: bool,
) -> set[str]:
    applied: set[str] = set()
    for name in VIDEO_WORKFLOW_BINDING_KEYS:
        locator = str(bindings.get(name) or "").strip()
        if not locator:
            if required and name in {"image", "prompt", "seed", "width", "height", "duration", "fps", "output_prefix"}:
                raise VideoStoryboardVideoError(
                    f"The {name} binding is missing from the video workflow profile."
                )
            continue
        node_id, separator, input_name = locator.rpartition(".")
        node = workflow.get(node_id) if separator else None
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not node_id or not input_name or not isinstance(inputs, dict):
            raise VideoStoryboardVideoError(
                f"Video workflow binding '{name}' points to an invalid node: {locator}."
            )
        inputs[input_name] = replacements[name]
        applied.add(name)
    return applied


def _apply_custom_fallbacks(
    workflow: dict[str, Any],
    replacements: dict[str, Any],
    *,
    serialized: str,
    applied: set[str],
) -> None:
    placeholder_names = {
        "image": "{{IMAGE}}",
        "prompt": "{{PROMPT}}",
        "output_prefix": "{{OUTPUT_PREFIX}}",
    }
    for name, token in placeholder_names.items():
        if token in serialized:
            applied.add(name)
    for node in workflow.values():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        node_type = str(node.get("class_type") or "")
        inputs = node["inputs"]
        if node_type == "LoadImage" and "image" not in applied:
            inputs["image"] = replacements["image"]
            applied.add("image")
        elif node_type == "SaveVideo" and "output_prefix" not in applied:
            inputs["filename_prefix"] = replacements["output_prefix"]
            applied.add("output_prefix")
    missing = sorted({"image", "prompt"} - applied)
    if missing:
        raise VideoStoryboardVideoError(
            "The custom workflow must bind or contain placeholders for: "
            + ", ".join(missing)
            + "."
        )


def _video_config(settings: dict[str, Any]) -> dict[str, Any]:
    configured = settings.get("comfyui_video", {})
    result = dict(configured) if isinstance(configured, dict) else {}
    image_comfy = settings.get("comfyui", {})
    if isinstance(image_comfy, dict):
        result.setdefault("base_url", image_comfy.get("base_url"))
        result.setdefault("timeout_seconds", image_comfy.get("timeout_seconds"))
    return result


def _authorization_headers(config: dict[str, Any]) -> dict[str, str]:
    token = str(config.get("auth_token") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def wan_video_frame_count(
    duration_seconds: float | None,
    fps: float,
    *,
    fallback_frames: int = 49,
) -> int:
    """Return the smallest Wan-compatible frame count covering a scene.

    Wan's temporal VAE expects `4n + 1` frames.  We round upwards, rather
    than shortening narration, and trim the tiny surplus during finalization.
    """
    try:
        duration = float(duration_seconds) if duration_seconds is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    try:
        frame_rate = max(1.0, float(fps))
    except (TypeError, ValueError):
        frame_rate = 24.0
    if duration <= 0:
        requested = max(5, int(fallback_frames or 49))
    else:
        requested = max(5, math.ceil(duration * frame_rate))
    return max(5, 4 * math.ceil((requested - 1) / 4) + 1)


def _wait_for_comfyui_memory_release(
    root: str,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Wait briefly because /free returns before CUDA memory stats settle."""
    started = time.monotonic()
    deadline = started + 15.0
    previous_free = -1
    stable_reads = 0
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = _http_json(
            f"{root}/system_stats", timeout=15, headers=headers
        )
        devices = latest.get("devices", []) if isinstance(latest, dict) else []
        device = devices[0] if isinstance(devices, list) and devices else {}
        free = int(device.get("vram_free", 0) or 0)
        if free > 0 and abs(free - previous_free) < 16 * 1024**2:
            stable_reads += 1
        else:
            stable_reads = 0
        previous_free = free
        if time.monotonic() - started >= 1.5 and stable_reads >= 1:
            return latest
        time.sleep(0.5)
    return latest


def _upload_image(
    root: str,
    path: Path,
    timeout: float,
    *,
    headers: dict[str, str] | None = None,
) -> str:
    boundary = f"----LocalText2Voice{uuid.uuid4().hex}"
    remote_name = f"ltv-video-{uuid.uuid4().hex[:12]}{path.suffix.casefold() or '.png'}"
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n".encode(),
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{remote_name}\"\r\nContent-Type: image/png\r\n\r\n"
        ).encode(),
        path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    request = urllib.request.Request(
        f"{root}/upload/image",
        data=b"".join(parts),
        method="POST",
        headers=comfyui_request_headers(
            headers,
            content_type=f"multipart/form-data; boundary={boundary}",
        ),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise VideoStoryboardVideoError(
            f"Cannot upload the reference frame to ComfyUI: {exc}"
        ) from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoStoryboardVideoError(
            "ComfyUI returned an invalid image-upload response."
        ) from exc
    if not isinstance(result, dict) or not result.get("name"):
        raise VideoStoryboardVideoError("ComfyUI did not accept the reference frame.")
    subfolder = str(result.get("subfolder") or "").strip("/\\")
    name = str(result["name"])
    return f"{subfolder}/{name}" if subfolder else name


def _wait_for_video_history(
    root: str,
    prompt_id: str,
    timeout: float,
    cancelled: CancelCallback | None,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    endpoint = f"{root}/history/{urllib.parse.quote(prompt_id)}"
    while time.monotonic() < deadline:
        if cancelled and cancelled():
            try:
                _http_json(
                    f"{root}/interrupt",
                    method="POST",
                    payload={},
                    timeout=10,
                    headers=headers,
                )
            except VideoStoryboardImageError:
                pass
            raise VideoStoryboardVideoError("Video generation was cancelled.")
        try:
            history = _http_json(endpoint, timeout=30, headers=headers)
        except VideoStoryboardImageError as exc:
            raise VideoStoryboardVideoError(str(exc)) from exc
        entry = history.get(prompt_id)
        if isinstance(entry, dict):
            state = entry.get("status", {})
            if isinstance(state, dict) and state.get("status_str") == "error":
                raise VideoStoryboardVideoError(
                    "ComfyUI video generation failed: "
                    + json.dumps(state, ensure_ascii=False)[:1800]
                )
            if isinstance(state, dict) and state.get("completed"):
                return entry
        time.sleep(1.0)
    raise VideoStoryboardVideoError(
        f"ComfyUI did not finish the video within {round(timeout)} seconds."
    )


def _find_output_video(history: dict[str, Any], output_node: str = "") -> dict[str, Any]:
    outputs = history.get("outputs", {})
    if output_node:
        outputs = {output_node: outputs.get(output_node, {})}
    ordered: list[Any] = []
    if isinstance(outputs, dict):
        ordered.append(outputs.get("14"))
        ordered.extend(value for key, value in outputs.items() if key != "14")
    for output in ordered:
        candidate = _nested_video_file(output)
        if candidate is not None:
            return candidate
    raise VideoStoryboardVideoError(
        "ComfyUI completed the job but the workflow returned no video file."
    )


def _nested_video_file(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("filename"):
            suffix = Path(str(value["filename"])).suffix.casefold()
            if suffix in {".mp4", ".webm", ".mov", ".mkv"}:
                return value
        for child in value.values():
            candidate = _nested_video_file(child)
            if candidate is not None:
                return candidate
    elif isinstance(value, list):
        for child in value:
            candidate = _nested_video_file(child)
            if candidate is not None:
                return candidate
    return None


def _download_video(
    url: str,
    target: Path,
    timeout: float,
    *,
    headers: dict[str, str] | None = None,
) -> None:
    temporary = target.with_name(f".{target.stem}.reverse{target.suffix}")
    request = urllib.request.Request(
        url,
        headers=comfyui_request_headers(
            headers,
            accept="video/*,*/*",
        ),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise VideoStoryboardVideoError(
            f"Cannot download the ComfyUI video: {exc}"
        ) from exc
    if not data:
        raise VideoStoryboardVideoError("ComfyUI returned an empty video.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(data)
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise VideoStoryboardVideoError(
            f"Cannot save the generated video: {exc}"
        ) from exc


def _reverse_video(source: Path, target: Path, settings: dict[str, Any]) -> None:
    temporary = target.with_suffix(target.suffix + ".part")
    temporary.unlink(missing_ok=True)
    try:
        runner = FFmpegRunner(
            find_ffmpeg(settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"))
        )
        runner.run(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                "reverse,setpts=PTS-STARTPTS",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                str(temporary),
            ]
        )
        os.replace(temporary, target)
    except (FFmpegError, OSError) as exc:
        temporary.unlink(missing_ok=True)
        raise VideoStoryboardVideoError(
            f"Cannot place the reference frame at the end of the clip: {exc}"
        ) from exc


def _retime_video(
    source: Path,
    duration_seconds: float,
    settings: dict[str, Any],
    *,
    source_frames: int | None = None,
    source_duration_seconds: float | None = None,
) -> None:
    """Stretch a generated clip over the complete storyboard scene."""
    config = _video_config(settings)
    frames = max(1, int(source_frames or config.get("frames") or 49))
    fps = max(0.001, float(config.get("fps") or 24.0))
    measured_duration = (
        0.0
        if source_duration_seconds is not None
        else _probe_media_duration(source, settings)
    )
    source_duration = max(
        0.001,
        measured_duration
        or (
            float(source_duration_seconds)
            if source_duration_seconds is not None
            else frames / fps
        ),
    )
    target_duration = max(0.1, float(duration_seconds))
    speed_factor = target_duration / source_duration
    temporary = source.with_name(f".{source.stem}.retimed-{uuid.uuid4().hex}.mp4")
    temporary.unlink(missing_ok=True)
    try:
        runner = FFmpegRunner(
            find_ffmpeg(settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"))
        )
        runner.run(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                (
                    f"setpts={speed_factor:.9f}*(PTS-STARTPTS),"
                    f"fps={fps:g},"
                    f"tpad=stop_mode=clone:stop_duration={target_duration:.6f},"
                    f"trim=duration={target_duration:.6f},setpts=PTS-STARTPTS"
                ),
                "-r",
                f"{fps:.12g}",
                "-fps_mode",
                "cfr",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(temporary),
            ]
        )
        os.replace(temporary, source)
    except (FFmpegError, OSError) as exc:
        temporary.unlink(missing_ok=True)
        raise VideoStoryboardVideoError(
            f"Cannot fit the generated video to the scene duration: {exc}"
        ) from exc


def _probe_media_duration(source: Path, settings: dict[str, Any]) -> float:
    """Read the downloaded clip duration without assuming an engine codec."""
    try:
        executable = find_ffmpeg(
            settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe")
        )
        creation_flags = (
            subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        process = subprocess.run(
            [str(executable), "-hide_banner", "-i", str(source)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creation_flags,
            check=False,
        )
    except (FFmpegError, OSError):
        return 0.0
    output = process.stderr.decode("utf-8", errors="replace")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        return 0.0
    return (
        int(match.group(1)) * 3600
        + int(match.group(2)) * 60
        + float(match.group(3))
    )


def _generate_runpod_video(scene, plan, settings, target, *, prompt, frame_role, status, cancelled):
    from app.core import video_storyboard_runpod as rp
    image_path = Path(str(scene.get("image_path") or ""))
    if not image_path.is_file() or not str(prompt).strip():
        raise VideoStoryboardVideoError("Choose a source frame and enter a video prompt.")
    config = rp.configuration(settings)
    duration = max(0.1, float(scene.get("duration_seconds") or 0.1))
    seed = int(plan.get("base_seed") or 0)
    role = "end" if frame_role == "end" else "start"
    source = target.with_name(f".{target.stem}.runpod-source.mp4")
    try:
        from app.core.runpod_video_models import video_parameters
        parameters = video_parameters(config, prompt, duration, seed)
        identity = {**parameters, "image": rp.file_digest(image_path), "frame_role": role}
        result = rp.execute(settings, "video", lambda: {**parameters, "image": rp.source_url(image_path, config)}, source, identity=identity, status=status, cancelled=cancelled)
        measured = _probe_media_duration(source, settings)
        if not measured:
            raise VideoStoryboardVideoError("Runpod returned an unreadable video.")
        if role == "end":
            _reverse_video(source, target, settings)
        else:
            import shutil
            shutil.copyfile(source, target)
        _retime_video(target, duration, settings, source_duration_seconds=measured)
    except (rp.RunpodError, ValueError) as exc:
        raise VideoStoryboardVideoError(str(exc)) from exc
    finally:
        source.unlink(missing_ok=True)
    return {**result, "scene_id": str(scene.get("scene_id") or scene.get("id") or ""), "video_path": str(target),
            "video_prompt": str(prompt).strip(), "video_frame_role": role, "video_duration_seconds": duration,
            "workflow_profile": config["video_endpoint"], "generated_duration_seconds": measured, "seed": seed}
