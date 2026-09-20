"""Veo models currently documented by the Gemini API (September 2026)."""
VEO_MODELS = (
    ("Veo 3.1 Fast · Preview", "gemini/veo-3.1-fast-generate-preview"),
    ("Veo 3.1 · Preview", "gemini/veo-3.1-generate-preview"),
    ("Veo 3.1 Lite · Preview", "gemini/veo-3.1-lite-generate-preview"),
)
LITELLM_VIDEO_DEFAULTS = {
    "model": VEO_MODELS[0][1], "api_key": "", "base_url": "",
    "resolution": "720p", "aspect_ratio": "16:9", "timeout_seconds": 1800,
}


def configuration(settings):
    return {**LITELLM_VIDEO_DEFAULTS, **settings.get("litellm_video", {})}


def reference_mode(scene, frame_role, config):
    from app.core.storyboard_video_references import video_reference_paths
    if frame_role == "end":
        raise ValueError("Veo requires a starting frame for last-frame generation. Choose Frame at start, or None with + Img Ref.")
    paths = video_reference_paths(scene, frame_role)
    if frame_role == "start" and len(paths) > 1:
        raise ValueError("For multiple Veo references, choose reference position None. Frame at start uses only the starting image.")
    if frame_role == "none" and paths and ("3.1" not in config["model"] or "lite" in config["model"]):
        raise ValueError("Multiple asset references require Veo 3.1 or Veo 3.1 Fast. With Lite, use Frame at start.")
    return paths


def generation_seconds(scene, config, references=False):
    if references or config["resolution"] != "720p":
        return 8
    duration = max(0.1, float(scene.get("duration_seconds") or 8))
    return next((seconds for seconds in (4, 6, 8) if seconds >= duration), 8)
