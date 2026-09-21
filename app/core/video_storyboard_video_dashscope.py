"""Wan video providers via Model Studio's async REST API."""
from __future__ import annotations

import base64
import hashlib
import math
import mimetypes
from urllib.parse import urlsplit

from app.core.storyboard_generation_errors import redact_generation_error
from app.core.storyboard_video_references import single_video_reference

WAN_MODEL = "wan2.6-i2v-flash"
WAN_MODELS = {
    WAN_MODEL: {"label": "Wan 2.6 I2V Flash", "max_seconds": 15, "price_720": "0.025"},
    "wan3.0-video": {"label": "Wan 3.0 Video", "max_seconds": 30, "price_720": "0.10"},
    "wan3.0-video-prime": {"label": "Wan 3.0 Video Prime", "max_seconds": 30, "price_720": "0.14"},
    "wan2.7-i2v": {"label": "Wan 2.7 I2V", "max_seconds": 15, "price_720": "0.10"},
}
DASHSCOPE_DEFAULTS = {
    "model": WAN_MODEL,
    "audio": False,
    "api_key": "",
    "base_url": "https://ws-l7a60zigulckx5xs.ap-southeast-1.maas.aliyuncs.com/api/v1",
    "resolution": "720P",
    "timeout_seconds": 1800,
}


def configuration(settings):
    return {**DASHSCOPE_DEFAULTS, **settings.get("dashscope_video", {})}


def api_root(config):
    root = str(config.get("base_url") or DASHSCOPE_DEFAULTS["base_url"]).strip().rstrip("/")
    parsed = urlsplit(root)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter an HTTPS DashScope API URL without credentials or query parameters.")
    if root.endswith("/compatible-mode/v1"):
        root = root.removesuffix("/compatible-mode/v1") + "/api/v1"
    elif not parsed.path or parsed.path == "/":
        root += "/api/v1"
    if not root.endswith("/api/v1"):
        raise ValueError("The DashScope API URL must end with /api/v1.")
    return root


def prepare(settings):
    config = configuration(settings)
    api_root(config)
    if not str(config["api_key"]).strip():
        raise ValueError("Set your Model Studio API key in Settings > Video Storyboard > Alibaba Cloud · Wan.")
    if config["model"] not in WAN_MODELS:
        raise ValueError("Select a supported Alibaba Cloud video model.")
    if config["resolution"] not in {"720P", "1080P"}:
        raise ValueError("Wan supports 720P or 1080P.")
    return {"provider": "dashscope", "model": config["model"]}


def reference_image(scene, frame_role):
    if frame_role == "end":
        raise ValueError("This Wan integration supports a starting image only, not an ending frame.")
    path = single_video_reference(scene, frame_role)
    if path is None:
        raise ValueError("Wan requires one starting image. Select a scene frame or add one image reference.")
    return path


def generation_seconds(scene, model=WAN_MODEL):
    return min(WAN_MODELS[model]["max_seconds"], max(2, math.ceil(float(scene.get("duration_seconds") or 5))))


class DashScopeAdapter:
    def submitted_id(self, data):
        return data.get("output", {}).get("task_id", "")

    def status(self, data):
        from app.core.video_provider_protocol import VideoStatus, error_detail
        output = data.get("output", {})
        state = output.get("task_status")
        if state == "SUCCEEDED":
            return VideoStatus("succeeded", output.get("video_url", ""))
        if state in {"FAILED", "CANCELED", "UNKNOWN"}:
            return VideoStatus("failed", detail=error_detail(output))
        if state not in {"PENDING", "RUNNING"}:
            raise ValueError("Wan returned an unknown job status.")
        return VideoStatus("pending", detail=state)


def generate(scene, plan, settings, target, *, prompt, frame_role, status=None, cancelled=None):
    from app.core.video_storyboard_video_comfyui import VideoStoryboardVideoError
    from app.core.video_provider_protocol import VideoRequest
    from app.core.video_provider_jobs import run_job
    try:
        prepare(settings)
        config = configuration(settings)
        root = api_root(config)
        path = reference_image(scene, frame_role)
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/bmp"} or path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Use a PNG, JPEG, WebP or BMP image no larger than 20 MB.")
        if not prompt.strip():
            raise ValueError("The video prompt is empty.")
        model = config["model"]
        parameters = {"resolution": config["resolution"], "duration": generation_seconds(scene, model),
                      "prompt_extend": False, "watermark": False}
        if model != "wan2.7-i2v":
            parameters["audio"] = bool(config["audio"])
        if model == WAN_MODEL:
            parameters["shot_type"] = "single"
        image = path.read_bytes()
        # Preserve the existing recovery fingerprint so in-flight Wan jobs survive migration.
        identity = {"root": root, "model": model, "parameters": parameters, "prompt": prompt.strip(),
                    "scene": scene.get("scene_id", scene.get("id")), "image": hashlib.sha256(image).hexdigest(),
                    "accepted_video": scene.get("video_path", "")}
        image_url = f"data:{mime};base64," + base64.b64encode(image).decode("ascii")
        inputs = {"prompt": prompt.strip()}
        if model == WAN_MODEL:
            inputs["img_url"] = image_url
        else:
            inputs["media"] = [{"type": "first_frame", "url": image_url}]
        request = VideoRequest("dashscope", model, root, "/services/aigc/video-generation/video-synthesis",
                               "/tasks/{id}", {"model": model, "input": inputs, "parameters": parameters},
                               headers={"X-DashScope-Async": "enable"}, identity=identity)
        return run_job(request, DashScopeAdapter(), config, scene, settings, target, prompt=prompt.strip(),
                       frame_role=frame_role, status=status, cancelled=cancelled)
    except VideoStoryboardVideoError:
        raise
    except Exception as exc:
        raise VideoStoryboardVideoError(redact_generation_error(str(exc), settings)) from None
