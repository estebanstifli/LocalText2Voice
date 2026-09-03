from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class VideoStoryboardServiceError(RuntimeError):
    pass


REQUIRED_COMFYUI_NODES = {
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "CLIPTextEncode",
    "ConditioningZeroOut",
    "EmptySD3LatentImage",
    "ModelSamplingAuraFlow",
    "KSampler",
    "VAEDecode",
    "SaveImage",
}


def detect_ollama(base_url: str, timeout: float = 10) -> dict[str, Any]:
    root = _normalize_url(base_url)
    tags = _http_json(f"{root}/api/tags", timeout)
    running = _http_json(f"{root}/api/ps", timeout)
    models = sorted(
        {
            str(item.get("name") or item.get("model") or "").strip()
            for item in tags.get("models", [])
            if isinstance(item, dict)
            and str(item.get("name") or item.get("model") or "").strip()
        },
        key=str.casefold,
    )
    loaded = sorted(
        {
            str(item.get("name") or item.get("model") or "").strip()
            for item in running.get("models", [])
            if isinstance(item, dict)
            and str(item.get("name") or item.get("model") or "").strip()
        },
        key=str.casefold,
    )
    return {"service": "ollama", "models": models, "loaded_models": loaded}


def detect_comfyui(base_url: str, timeout: float = 30) -> dict[str, Any]:
    root = _normalize_url(base_url)
    stats = _http_json(f"{root}/system_stats", min(timeout, 15))
    object_info = _http_json(f"{root}/object_info", timeout)
    if not isinstance(object_info, dict):
        raise VideoStoryboardServiceError("ComfyUI returned invalid node information.")
    devices = stats.get("devices", []) if isinstance(stats, dict) else []
    device = devices[0] if isinstance(devices, list) and devices else {}
    system = stats.get("system", {}) if isinstance(stats, dict) else {}
    return {
        "service": "comfyui",
        "version": str(system.get("comfyui_version", "unknown")),
        "device": str(device.get("name", "unknown")),
        "vram_total": int(device.get("vram_total", 0) or 0),
        "vram_free": int(device.get("vram_free", 0) or 0),
        "diffusion_models": _node_choices(
            object_info, "UNETLoader", "unet_name"
        ),
        "text_encoders": _node_choices(object_info, "CLIPLoader", "clip_name"),
        "vae_models": _node_choices(object_info, "VAELoader", "vae_name"),
        "missing_nodes": sorted(REQUIRED_COMFYUI_NODES - set(object_info)),
    }


def _node_choices(
    object_info: dict[str, Any],
    node_name: str,
    field_name: str,
) -> list[str]:
    node = object_info.get(node_name, {})
    try:
        values = node["input"]["required"][field_name][0]
    except (KeyError, IndexError, TypeError):
        return []
    if not isinstance(values, list):
        return []
    return sorted(
        {str(value).strip() for value in values if str(value).strip()},
        key=str.casefold,
    )


def _normalize_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise VideoStoryboardServiceError("The service URL must use HTTP or HTTPS.")
    return url


def _http_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "LocalText2Voice"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise VideoStoryboardServiceError(
            f"HTTP {exc.code} returned by {url}."
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise VideoStoryboardServiceError(f"Cannot connect to {url}: {reason}") from exc
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoStoryboardServiceError(f"Invalid JSON returned by {url}.") from exc
    if not isinstance(value, dict):
        raise VideoStoryboardServiceError(f"Unexpected response returned by {url}.")
    return value
