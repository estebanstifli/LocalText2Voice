"""Safe display names from the settings captured by the running job."""
import json
from pathlib import Path
from urllib.parse import urlsplit


def provider_label(settings, role="image"):
    from app.core.direct_video_models import PROVIDERS, configuration
    key = "image_edit" if role == "edit" else role
    provider = str(settings.get(f"{key}_provider") or ("disabled" if role == "edit" else "comfyui"))
    names = {"litellm_image": "LiteLLM", "comfyui": "ComfyUI", "custom_comfyui": "ComfyUI", "runpod": "Runpod", "disabled": "Disabled"}
    model = ""
    if provider in PROVIDERS and role == "video":
        return PROVIDERS[provider][0] + " · " + configuration(settings, provider)["model"]
    if provider == "dashscope" and role == "video":
        return "Alibaba Cloud · " + str(settings.get("dashscope_video", {}).get("model") or "wan2.6-i2v-flash")
    if provider == "litellm" and role == "video":
        return "LiteLLM · " + str(settings.get("litellm_video", {}).get("model") or "Veo")
    if provider == "runpod":
        from app.core.storyboard_profiles import RUNPOD_DEFAULTS
        model = str(settings.get("runpod", {}).get(f"{role}_endpoint") or RUNPOD_DEFAULTS.get(f"{role}_endpoint") or "")
        if "://" in model:
            model = urlsplit(model).path.strip("/").removeprefix("v2/").split("/")[0]
    elif provider == "litellm_image":
        section = "litellm_image_edit" if role == "edit" else "litellm_image"
        model = str(settings.get(section, {}).get("model") or "")
    elif provider in {"comfyui", "custom_comfyui"}:
        section = "comfyui" if role == "image" else f"comfyui_{key}"
        config = settings.get(section, {})
        if provider == "custom_comfyui":
            try:
                workflow = json.loads(Path(str(config.get("workflow_path") or "")).read_text(encoding="utf-8"))
                models = [str(value) for node in workflow.values() if isinstance(node, dict)
                          for name, value in node.get("inputs", {}).items()
                          if name in {"unet_name", "ckpt_name", "diffusion_model"} and isinstance(value, str)]
                model = ", ".join(dict.fromkeys(models))
            except (OSError, ValueError, AttributeError):
                pass
        else:
            model = str(config.get("diffusion_model" if role == "image" else "unet_model") or "")
    return " · ".join(v for v in (names.get(provider, provider), model) if v)


def active_provider_label(window, role="image"):
    attribute = {"image": "video_storyboard_frame_worker", "edit": "video_storyboard_image_edit_worker", "video": "video_storyboard_video_worker"}[role]
    settings = getattr(getattr(window, attribute, None), "settings", None)
    if not isinstance(settings, dict):
        settings = getattr(window, "settings", {}).get("video_storyboard", {})
    return provider_label(settings, role)
