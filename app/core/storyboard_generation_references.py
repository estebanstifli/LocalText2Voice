"""Reference inputs for the selected image-generation model."""
from pathlib import Path


def litellm_reference_generation(references, config, prompt, width, height):
    # File-conditioned requests use the provider's multipart image endpoint.
    # The model, credentials and URL belong exclusively to image generation.
    from app.core.video_storyboard_image_edit import (
        VideoStoryboardImageEditError, _litellm_direct_edit, _multipart_json,
    )
    from app.core.video_storyboard_comfyui import VideoStoryboardImageError

    model = str(config.get("model") or "").strip()
    api_key = str(config.get("api_key") or "").strip()
    timeout = float(config.get("timeout_seconds") or 300)
    root = str(config.get("base_url") or "").strip().rstrip("/")
    try:
        if not root:
            return _litellm_direct_edit(references, model, prompt, api_key, timeout, width, height)
        endpoint = root if root.endswith("/images/edits") else f"{root}/images/edits"
        field = "image[]" if len(references) > 1 else "image"
        files = [(field, Path(ref["path"])) for ref in references]
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        from app.core.storyboard_image_sizes import image_parameters
        import json
        sizing = image_parameters(model, width, height)
        fields = {"model": model, "prompt": prompt, "response_format": "b64_json",
                  **{k: json.dumps(v) if isinstance(v, dict) else v for k, v in sizing.items()}}
        try:
            return _multipart_json(endpoint, fields, files, timeout, headers)
        except VideoStoryboardImageEditError as exc:
            from app.core.storyboard_generation_errors import is_moderation_error
            if is_moderation_error(exc):
                raise
            detail = str(exc).casefold()
            if not (("http 400" in detail or "http 422" in detail)
                    and ("size" in detail or "response_format" in detail)):
                raise
            return _multipart_json(endpoint, {"model": model, "prompt": prompt}, files, timeout, headers)
    except VideoStoryboardImageEditError as exc:
        raise VideoStoryboardImageError(
            f"The selected generation model ({model}) could not generate with reference images: {exc}"
        ) from exc
