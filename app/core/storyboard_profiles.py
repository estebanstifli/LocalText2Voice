"""Named visual-engine presets; creative settings and the LLM remain independent."""
from copy import deepcopy

PROFILE_IDS = ("local", "custom_comfyui", "litellm", "runpod")
VISUAL_KEYS = (
    "image_provider", "image_edit_provider", "video_provider", "comfyui",
    "comfyui_video", "comfyui_image_edit", "litellm_image", "litellm_image_edit", "runpod",
)
RUNPOD_DEFAULTS = {
    "api_key_encrypted": "", "image_endpoint": "z-image-turbo",
    "edit_endpoint": "qwen-image-edit-2511", "video_endpoint": "wan-2-6-i2v",
    "video_size": "1280*720", "edit_size": "1536*1080",
    "edit_preserve_size": True,
    "timeout_seconds": 1800, "prompt_expansion": False,
    "s3_endpoint": "", "s3_bucket": "", "s3_region": "auto",
    "s3_access_key": "", "s3_secret_encrypted": "",
    "reference_storage": "auto", "temporary_storage_url": "",
    "temporary_storage_token_encrypted": "",
}


def infer_profile(config):
    if config.get("active_profile") in PROFILE_IDS:
        return config["active_profile"]
    return {"runpod": "runpod", "litellm_image": "litellm", "custom_comfyui": "custom_comfyui"}.get(config.get("image_provider"), "local")


def visual_snapshot(config):
    return {key: deepcopy(config[key]) for key in VISUAL_KEYS if key in config}


def switch_profile(config, profile, defaults):
    result = deepcopy(config)
    saved = result.setdefault("profiles", {})
    saved[infer_profile(config)] = visual_snapshot(config)
    if profile in saved:
        result.update(deepcopy(saved[profile]))
    else:
        result.update(visual_snapshot(defaults))
        if profile == "custom_comfyui":
            result["image_provider"] = "custom_comfyui"
            result["comfyui_video"]["workflow_profile"] = "custom"
        elif profile == "litellm":
            result.update(image_provider="litellm_image", image_edit_provider="litellm_image", video_provider="disabled")
        elif profile == "runpod":
            result.update(image_provider="runpod", image_edit_provider="runpod", video_provider="runpod")
    result["active_profile"] = profile
    return result
