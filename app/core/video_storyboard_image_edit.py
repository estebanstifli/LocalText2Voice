from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from app.core.comfyui_http import comfyui_request_headers
from app.core.video_storyboard_camera import CAMERA_LORA_MODEL, RUNPOD_CAMERA_LORA, runpod_camera_prompt, runpod_camera_settings, camera_prompt, normalize_camera, resolve_camera_lora
from app.core.video_storyboard_comfyui import (
    VideoStoryboardImageError,
    _download,
    _find_output_image,
    _http_json,
    _normalize_url,
)


class VideoStoryboardImageEditError(RuntimeError):
    pass


StatusCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]


def prepare_image_edit_runtime(
    settings: dict[str, Any],
    *,
    status: StatusCallback | None = None,
    camera: object = None,
) -> dict[str, Any]:
    provider = str(settings.get("image_edit_provider") or "disabled")
    _validate_camera_provider(provider, camera)
    if provider == "disabled":
        raise VideoStoryboardImageEditError(
            "Image editing is disabled. Configure an image editing provider in "
            "Settings > Video Storyboard."
        )
    if provider == "runpod":
        from app.core import video_storyboard_runpod as rp
        try:
            return rp.prepare(runpod_camera_settings(settings, camera), "edit")
        except rp.RunpodError as exc:
            raise VideoStoryboardImageEditError(str(exc)) from exc
    if provider == "litellm_image":
        config = _section(settings, "litellm_image_edit")
        model = str(config.get("model") or "").strip()
        if not model:
            raise VideoStoryboardImageEditError(
                "No remote image-editing model is configured."
            )
        return {"provider": provider, "model": model}

    config = _section(settings, "comfyui_image_edit")
    root = _normalize_edit_url(config.get("base_url"))
    headers = _authorization_headers(config)
    if status:
        status("check_comfyui_edit")
    try:
        stats = _http_json(f"{root}/system_stats", timeout=20, headers=headers)
        object_info = _http_json(f"{root}/object_info", timeout=90, headers=headers)
    except VideoStoryboardImageError as exc:
        raise VideoStoryboardImageEditError(str(exc)) from exc
    required_nodes = {
        "UnetLoaderGGUF", "LoraLoaderModelOnly", "CLIPLoader", "VAELoader",
        "TextEncodeQwenImageEditPlus", "ImageScaleToTotalPixels", "KSampler",
    }
    missing = sorted(required_nodes - set(object_info))
    if missing:
        raise VideoStoryboardImageEditError(
            "ComfyUI is missing Qwen Image Edit nodes: " + ", ".join(missing)
        )
    resolved_lora = None
    if camera_prompt(camera):
        configured = str(config.get("camera_lora_model") or CAMERA_LORA_MODEL).strip()
        names = object_info.get("LoraLoaderModelOnly", {}).get("input", {}).get("required", {}).get("lora_name", [])
        available = names[0] if names and isinstance(names[0], list) else []
        try:
            resolved_lora = resolve_camera_lora(configured, available)
        except ValueError as exc:
            raise VideoStoryboardImageEditError(str(exc)) from exc
    try:
        _http_json(
            f"{root}/free",
            method="POST",
            payload={"unload_models": True, "free_memory": True},
            timeout=30,
            headers=headers,
        )
    except VideoStoryboardImageError:
        pass
    devices = stats.get("devices", []) if isinstance(stats, dict) else []
    device = devices[0] if isinstance(devices, list) and devices else {}
    return {
        "provider": "comfyui",
        "model": str(config.get("unet_model") or ""),
        "device": str(device.get("name") or "unknown"),
        "camera_lora_model": resolved_lora,
    }


def generate_edited_storyboard_image(
    reference_images: list[dict[str, Any] | str],
    prompt: str,
    settings: dict[str, Any],
    target: Path,
    *,
    seed: int = 0,
    width: int | None = None,
    height: int | None = None,
    scene_id: str = "",
    status: StatusCallback | None = None,
    cancelled: CancelCallback | None = None,
    camera: object = None,
) -> dict[str, Any]:
    references = _normalize_references(reference_images)
    if not 1 <= len(references) <= 3:
        raise VideoStoryboardImageEditError(
            "Image editing requires between one and three reference images."
        )
    instruction = str(prompt or "").strip()
    if settings.get("image_edit_provider") == "runpod":
        camera_instruction = runpod_camera_prompt(camera)
        if camera_instruction and camera_instruction not in instruction:
            instruction = f"{camera_instruction} {instruction}".strip()
    if not instruction:
        raise VideoStoryboardImageEditError("Enter an image editing instruction.")
    provider = str(settings.get("image_edit_provider") or "disabled")
    _validate_camera_provider(provider, camera)
    if provider == "disabled":
        raise VideoStoryboardImageEditError(
            "Image editing is disabled. Configure it in Settings > Video Storyboard."
        )
    if cancelled and cancelled():
        raise VideoStoryboardImageEditError("Image editing was cancelled.")
    if provider == "runpod":
        from app.core import video_storyboard_runpod as rp
        try:
            settings = runpod_camera_settings(settings, camera)
            config = rp.configuration(settings)
            lora_args = {"loras": [{"path": RUNPOD_CAMERA_LORA, "scale": 0.9}]} if normalize_camera(camera)["enabled"] else {}
            size = config["edit_size"]
            if config["edit_preserve_size"]:
                from PIL import Image, ImageOps
                with Image.open(references[0]["path"]) as original:
                    width, height = ImageOps.exif_transpose(original).size
                size = f"{width}*{height}"
            identity = dict(**lora_args, prompt=instruction, seed=seed, size=size, sources=[rp.file_digest(ref["path"]) for ref in references])
            def payload():
                return dict(**lora_args, prompt=instruction, seed=seed, size=size, output_format="png", images=[rp.source_url(ref["path"], config) for ref in references])
            result = rp.execute(settings, "edit", payload, target, identity=identity, status=status, cancelled=cancelled)
        except rp.RunpodError as exc:
            raise VideoStoryboardImageEditError(str(exc)) from exc
        return {**result, "scene_id": scene_id, "image_path": str(target), "compiled_prompt": instruction, "seed": seed, "seed_applied": True}
    if provider == "litellm_image":
        return _generate_remote_edit(
            references, instruction, settings, target, seed=seed,
            width=width, height=height, scene_id=scene_id, status=status,
            cancelled=cancelled,
        )
    return _generate_comfyui_edit(
        references, instruction, settings, target, seed=seed,
        width=width, height=height, scene_id=scene_id, status=status,
        cancelled=cancelled, camera=camera,
    )


def _generate_comfyui_edit(
    references: list[dict[str, str]],
    prompt: str,
    settings: dict[str, Any],
    target: Path,
    *,
    seed: int,
    width: int | None,
    height: int | None,
    scene_id: str,
    status: StatusCallback | None,
    cancelled: CancelCallback | None,
    camera: object = None,
) -> dict[str, Any]:
    config = _section(settings, "comfyui_image_edit")
    root = _normalize_edit_url(config.get("base_url"))
    headers = _authorization_headers(config)
    timeout = float(config.get("timeout_seconds") or 1800)
    workflow = _load_workflow(config, len(references))
    uploaded: list[str] = []
    for index, reference in enumerate(references, start=1):
        if cancelled and cancelled():
            raise VideoStoryboardImageEditError("Image editing was cancelled.")
        if status:
            status(f"upload_reference_{index}")
        uploaded.append(
            _upload_image(root, Path(reference["path"]), min(timeout, 180), headers)
        )
    _configure_qwen_workflow(
        workflow,
        uploaded,
        prompt,
        config,
        seed=max(0, int(seed)),
        width=int(width or config.get("width") or 1280),
        height=int(height or config.get("height") or 720),
        prefix=f"LocalText2Voice/image-edit/{target.stem}-{uuid.uuid4().hex[:8]}",
        camera=camera,
    )
    if status:
        status("queue_edit")
    try:
        queued = _http_json(
            f"{root}/prompt",
            method="POST",
            payload={"prompt": workflow, "client_id": f"ltv-edit-{uuid.uuid4()}"},
            timeout=60,
            headers=headers,
        )
    except VideoStoryboardImageError as exc:
        raise VideoStoryboardImageEditError(str(exc)) from exc
    prompt_id = str(queued.get("prompt_id") or "")
    if not prompt_id:
        raise VideoStoryboardImageEditError(
            "ComfyUI rejected the Qwen Image Edit workflow: "
            + json.dumps(queued.get("node_errors") or queued, ensure_ascii=False)[:1600]
        )
    if status:
        status("editing")
    history = _wait_for_history(root, prompt_id, timeout, cancelled, headers)
    try:
        image_info = _find_output_image(history)
    except VideoStoryboardImageError as exc:
        raise VideoStoryboardImageEditError(str(exc)) from exc
    query = urllib.parse.urlencode({
        "filename": image_info["filename"],
        "subfolder": image_info.get("subfolder", ""),
        "type": image_info.get("type", "output"),
    })
    if status:
        status("downloading_edit")
    _download_with_headers(f"{root}/view?{query}", target, timeout=180, headers=headers)
    return {
        "scene_id": scene_id,
        "image_path": str(target),
        "prompt_id": prompt_id,
        "compiled_prompt": prompt,
        "seed": max(0, int(seed)),
        "reference_images": references,
        "provider": "comfyui_image_edit",
        "camera": normalize_camera(camera),
    }


def _generate_remote_edit(
    references: list[dict[str, str]],
    prompt: str,
    settings: dict[str, Any],
    target: Path,
    *,
    seed: int,
    width: int | None,
    height: int | None,
    scene_id: str,
    status: StatusCallback | None,
    cancelled: CancelCallback | None,
) -> dict[str, Any]:
    config = _section(settings, "litellm_image_edit")
    model = str(config.get("model") or "").strip()
    if not model:
        raise VideoStoryboardImageEditError("No remote image-editing model is configured.")
    root = str(config.get("base_url") or "").strip().rstrip("/")
    if not root:
        response = _litellm_direct_edit(
            references,
            model,
            prompt,
            str(config.get("api_key") or "").strip(),
            float(config.get("timeout_seconds") or 600),
            width,
            height,
        )
        if status:
            status("downloading_edit")
        _save_remote_image(response, target, float(config.get("timeout_seconds") or 600))
        return {
            "scene_id": scene_id,
            "image_path": str(target),
            "prompt_id": str(response.get("id") or ""),
            "compiled_prompt": prompt,
            "seed": seed,
            "reference_images": references,
            "provider": "litellm_image_edit",
        }
    endpoint = root if root.endswith("/images/edits") else f"{root}/images/edits"
    fields = {"model": model, "prompt": prompt, "response_format": "b64_json"}
    if width and height:
        fields["size"] = f"{int(width)}x{int(height)}"
    files = [("image", Path(value["path"])) for value in references]
    api_key = str(config.get("api_key") or "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if status:
        status("editing")
    try:
        response = _multipart_json(
            endpoint, fields, files, float(config.get("timeout_seconds") or 600), headers
        )
    except VideoStoryboardImageEditError as exc:
        # Some edit APIs reject size/response_format. Retry with the minimal,
        # widely-supported OpenAI-compatible request.
        detail = str(exc).casefold()
        if not (
            ("http 400" in detail or "http 422" in detail)
            and ("size" in detail or "response_format" in detail or "unknown" in detail)
        ):
            raise
        minimal = {"model": model, "prompt": prompt}
        response = _multipart_json(
            endpoint, minimal, files, float(config.get("timeout_seconds") or 600), headers
        )
    if cancelled and cancelled():
        raise VideoStoryboardImageEditError("Image editing was cancelled.")
    if status:
        status("downloading_edit")
    _save_remote_image(response, target, float(config.get("timeout_seconds") or 600))
    return {
        "scene_id": scene_id,
        "image_path": str(target),
        "prompt_id": str(response.get("id") or ""),
        "compiled_prompt": prompt,
        "seed": seed,
        "reference_images": references,
        "provider": "litellm_image_edit",
    }


def _litellm_direct_edit(
    references: list[dict[str, str]],
    model: str,
    prompt: str,
    api_key: str,
    timeout: float,
    width: int | None,
    height: int | None,
) -> dict[str, Any]:
    try:
        from litellm import image_edit
    except ImportError as exc:
        raise VideoStoryboardImageEditError(
            "The LiteLLM Python SDK is not installed. Run run_dev.bat again."
        ) from exc
    optional: dict[str, Any] = {"response_format": "b64_json"}
    if width and height:
        optional["size"] = f"{int(width)}x{int(height)}"
    with ExitStack() as stack:
        handles = [
            stack.enter_context(Path(value["path"]).open("rb"))
            for value in references
        ]
        arguments: dict[str, Any] = {
            "image": handles,
            "prompt": prompt,
            "model": model,
            "timeout": timeout,
            "drop_params": True,
        }
        if api_key:
            arguments["api_key"] = api_key
        while True:
            try:
                response = image_edit(**arguments, **optional)
                if hasattr(response, "model_dump"):
                    value = response.model_dump()
                elif hasattr(response, "dict"):
                    value = response.dict()
                elif isinstance(response, dict):
                    value = dict(response)
                else:
                    raise VideoStoryboardImageEditError(
                        "LiteLLM returned an unexpected image-edit response."
                    )
                return value
            except Exception as exc:
                message = str(exc).casefold()
                rejected = next(
                    (name for name in tuple(optional) if name.casefold() in message),
                    "",
                )
                if rejected:
                    optional.pop(rejected, None)
                    for handle in handles:
                        handle.seek(0)
                    continue
                raise VideoStoryboardImageEditError(
                    f"LiteLLM image-editing request failed: {exc}"
                ) from exc


def _load_workflow(config: dict[str, Any], count: int) -> dict[str, Any]:
    configured = str(config.get(f"workflow_path_{count}") or "").strip()
    if configured:
        try:
            value = json.loads(Path(configured).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VideoStoryboardImageEditError(
                f"Cannot read the {count}-reference image-edit workflow: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise VideoStoryboardImageEditError("The image-edit workflow is not a JSON object.")
        return value
    return _builtin_qwen_workflow(count)


def _builtin_qwen_workflow(count: int) -> dict[str, Any]:
    workflow: dict[str, Any] = {
        "115": {"inputs": {"unet_name": "Qwen-Image-Edit-2509-Q3_K_S.gguf"}, "class_type": "UnetLoaderGGUF"},
        "117": {"inputs": {"lora_name": "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors", "strength_model": 1.0, "model": ["115", 0]}, "class_type": "LoraLoaderModelOnly"},
        "66": {"inputs": {"shift": 3.0, "model": ["117", 0]}, "class_type": "ModelSamplingAuraFlow"},
        "75": {"inputs": {"strength": 1.0, "pre_cfg": False, "model": ["66", 0]}, "class_type": "CFGNorm"},
        "38": {"inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}, "class_type": "CLIPLoader"},
        "39": {"inputs": {"vae_name": "qwen_image_vae.safetensors"}, "class_type": "VAELoader"},
        "112": {"inputs": {"width": 1280, "height": 720, "batch_size": 1}, "class_type": "EmptySD3LatentImage"},
        "3": {"inputs": {"seed": 0, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "model": ["75", 0], "positive": ["111", 0], "negative": ["110", 0], "latent_image": ["112", 0]}, "class_type": "KSampler"},
        "8": {"inputs": {"samples": ["3", 0], "vae": ["39", 0]}, "class_type": "VAEDecode"},
        "60": {"inputs": {"filename_prefix": "LocalText2Voice/image-edit", "images": ["8", 0]}, "class_type": "SaveImage"},
    }
    positive: dict[str, Any] = {"prompt": "", "clip": ["38", 0], "vae": ["39", 0]}
    negative: dict[str, Any] = {"prompt": "", "clip": ["38", 0], "vae": ["39", 0]}
    for index, node_id in enumerate(("78", "106", "108")[:count], start=1):
        scaled_id = "93" if index == 1 else node_id
        workflow[node_id] = {"inputs": {"image": f"reference_{index}.png"}, "class_type": "LoadImage"}
        if index == 1:
            workflow["93"] = {"inputs": {"upscale_method": "lanczos", "megapixels": 0.9, "resolution_steps": 1, "image": ["78", 0]}, "class_type": "ImageScaleToTotalPixels"}
        positive[f"image{index}"] = [scaled_id, 0]
        negative[f"image{index}"] = [scaled_id, 0]
    workflow["111"] = {"inputs": positive, "class_type": "TextEncodeQwenImageEditPlus"}
    workflow["110"] = {"inputs": negative, "class_type": "TextEncodeQwenImageEditPlus"}
    return workflow


def _configure_qwen_workflow(
    workflow: dict[str, Any], uploads: list[str], prompt: str,
    config: dict[str, Any], *, seed: int, width: int, height: int, prefix: str,
    camera: object = None,
) -> None:
    load_nodes = ("78", "106", "108")
    required_nodes = {
        "111", "110", "115", "117", "38", "39", "112", "3", "60",
        *load_nodes[: len(uploads)],
    }
    missing_nodes = sorted(required_nodes - set(workflow))
    if missing_nodes:
        raise VideoStoryboardImageEditError(
            "The selected Qwen Image Edit workflow is not the supported API "
            "workflow; missing node IDs: " + ", ".join(missing_nodes)
        )
    for index, upload in enumerate(uploads):
        workflow[load_nodes[index]]["inputs"]["image"] = upload
    workflow["111"]["inputs"]["prompt"] = prompt
    workflow["110"]["inputs"]["prompt"] = ""
    workflow["115"]["inputs"]["unet_name"] = str(config.get("unet_model") or "Qwen-Image-Edit-2509-Q3_K_S.gguf")
    workflow["117"]["inputs"]["lora_name"] = str(config.get("lora_model") or "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors")
    workflow["38"]["inputs"]["clip_name"] = str(config.get("text_encoder") or "qwen_2.5_vl_7b_fp8_scaled.safetensors")
    workflow["39"]["inputs"]["vae_name"] = str(config.get("vae_model") or "qwen_image_vae.safetensors")
    workflow["112"]["inputs"].update({"width": width, "height": height, "batch_size": 1})
    workflow["3"]["inputs"].update({"seed": seed, "steps": int(config.get("steps") or 4), "cfg": float(config.get("cfg") or 1.0)})
    workflow["60"]["inputs"]["filename_prefix"] = prefix
    if camera_prompt(camera):
        # Chain after Lightning, before its consumers; never load this adapter
        # for ordinary text edits. Work on a fresh workflow for every request.
        node_id = "ltv_camera_angles"
        if node_id in workflow:
            raise VideoStoryboardImageEditError("The workflow already contains the reserved camera adapter node.")
        for node in workflow.values():
            inputs = node.get("inputs", {})
            if inputs.get("model") == ["117", 0]:
                inputs["model"] = [node_id, 0]
        workflow[node_id] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["117", 0],
                "lora_name": str(config.get("camera_lora_model") or CAMERA_LORA_MODEL),
                "strength_model": float(config.get("camera_lora_strength", 1.0)),
            },
        }


def _validate_camera_provider(provider: str, camera: object) -> None:
    if normalize_camera(camera)["enabled"] and provider not in {"comfyui", "runpod"}:
        raise VideoStoryboardImageEditError(
            "Camera controls require Qwen Edit with ComfyUI or Runpod. "
            "Disable Change camera to use ordinary image editing with another provider."
        )


def _normalize_references(values: list[dict[str, Any] | str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        path = str(value.get("path") or "") if isinstance(value, dict) else str(value or "")
        label = str(value.get("label") or Path(path).stem) if isinstance(value, dict) else Path(path).stem
        resolved = str(Path(path).resolve()) if path else ""
        key = resolved.casefold()
        if not resolved or key in seen or not Path(resolved).is_file():
            continue
        seen.add(key)
        result.append({"path": resolved, "label": label})
    return result[:3]


def _upload_image(root: str, path: Path, timeout: float, headers: dict[str, str]) -> str:
    if not path.is_file():
        raise VideoStoryboardImageEditError(f"Reference image does not exist: {path}")
    response = _multipart_json(
        f"{root}/upload/image",
        {"type": "input", "overwrite": "true"},
        [("image", path)], timeout, headers,
    )
    name = str(response.get("name") or "")
    if not name:
        raise VideoStoryboardImageEditError("ComfyUI did not accept a reference image.")
    subfolder = str(response.get("subfolder") or "").strip("/\\")
    return f"{subfolder}/{name}" if subfolder else name


def _multipart_json(
    url: str, fields: dict[str, Any], files: list[tuple[str, Path]],
    timeout: float, headers: dict[str, str],
) -> dict[str, Any]:
    boundary = f"----LocalText2Voice{uuid.uuid4().hex}"
    body: list[bytes] = []
    for name, value in fields.items():
        body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, path in files:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{path.name}"\r\nContent-Type: {content_type}\r\n\r\n'.encode())
        body.append(path.read_bytes())
        body.append(b"\r\n")
    body.append(f"--{boundary}--\r\n".encode("ascii"))
    request = urllib.request.Request(
        url, data=b"".join(body), method="POST",
        headers=comfyui_request_headers(headers, content_type=f"multipart/form-data; boundary={boundary}"),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1600]
        raise VideoStoryboardImageEditError(f"HTTP {exc.code} from image editing service: {detail}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise VideoStoryboardImageEditError(f"Cannot connect to the image editing service: {getattr(exc, 'reason', exc)}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoStoryboardImageEditError("The image editing service returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise VideoStoryboardImageEditError("The image editing service returned an unexpected response.")
    return value


def _wait_for_history(
    root: str, prompt_id: str, timeout: float, cancelled: CancelCallback | None,
    headers: dict[str, str],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    endpoint = f"{root}/history/{urllib.parse.quote(prompt_id)}"
    while time.monotonic() < deadline:
        if cancelled and cancelled():
            try:
                _http_json(f"{root}/interrupt", method="POST", payload={}, timeout=10, headers=headers)
            except VideoStoryboardImageError:
                pass
            raise VideoStoryboardImageEditError("Image editing was cancelled.")
        try:
            history = _http_json(endpoint, timeout=30, headers=headers)
        except VideoStoryboardImageError as exc:
            raise VideoStoryboardImageEditError(str(exc)) from exc
        entry = history.get(prompt_id)
        if isinstance(entry, dict):
            state = entry.get("status", {})
            if isinstance(state, dict) and state.get("status_str") == "error":
                raise VideoStoryboardImageEditError("ComfyUI image editing failed: " + json.dumps(state, ensure_ascii=False)[:1800])
            if not state or not isinstance(state, dict) or state.get("completed"):
                return entry
        time.sleep(1.0)
    raise VideoStoryboardImageEditError("ComfyUI did not finish image editing before the timeout.")


def _download_with_headers(url: str, target: Path, *, timeout: float, headers: dict[str, str]) -> None:
    request = urllib.request.Request(url, headers=comfyui_request_headers(headers, accept="image/*,*/*"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise VideoStoryboardImageEditError(f"Cannot download the edited image: {exc}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.part{target.suffix}")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise VideoStoryboardImageEditError(f"Cannot save the edited image: {exc}") from exc


def _save_remote_image(response: dict[str, Any], target: Path, timeout: float) -> None:
    values = response.get("data", [])
    item = values[0] if isinstance(values, list) and values and isinstance(values[0], dict) else response
    encoded = str(item.get("b64_json") or "") if isinstance(item, dict) else ""
    url = str(item.get("url") or "") if isinstance(item, dict) else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    if encoded:
        target.write_bytes(base64.b64decode(encoded))
        return
    if url:
        _download(url, target, timeout)
        return
    raise VideoStoryboardImageEditError("The remote image editor returned no image.")


def _section(settings: dict[str, Any], name: str) -> dict[str, Any]:
    value = settings.get(name, {})
    return dict(value) if isinstance(value, dict) else {}


def _authorization_headers(config: dict[str, Any]) -> dict[str, str]:
    token = str(config.get("auth_token") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _normalize_edit_url(value: object) -> str:
    try:
        return _normalize_url(value, "http://127.0.0.1:8188")
    except VideoStoryboardImageError as exc:
        raise VideoStoryboardImageEditError(str(exc)) from exc
