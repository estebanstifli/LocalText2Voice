"""Public I2V adapters and indicative prices, checked 2026-09-07."""
import math

RUNPOD_SIGNUP_URL = "https://runpod.io?ref=f09rps0f"
VIDEO_MODELS = {
    "wan-2-6-i2v": {"name": "Wan 2.6 I2V", "rates": {"1280*720": 0.10, "1920*1080": 0.15}, "durations": (5, 10, 15)},
    "wan-2-2-i2v-720": {"name": "Wan 2.2 I2V · 720p", "rates": {"1280*720": 0.06}, "durations": (5, 8, 10, 15)},
    "wan-2-1-i2v-720": {"name": "Wan 2.1 I2V · 720p", "rates": {"1280*720": 0.06}, "durations": (5, 10)},
}


def model_id(config):
    value = str(config.get("video_endpoint") or "wan-2-6-i2v").strip().rstrip("/")
    prefix = "https://api.runpod.ai/v2/"
    return value[len(prefix):].split("/")[0] if value.startswith(prefix) else value


def model_name(config):
    endpoint = model_id(config)
    return VIDEO_MODELS.get(endpoint, {}).get("name", endpoint)


def duration_for(seconds, config):
    choices = VIDEO_MODELS.get(model_id(config), VIDEO_MODELS["wan-2-6-i2v"])["durations"]
    return next((n for n in choices if n >= math.ceil(float(seconds))), choices[-1])


def video_parameters(config, prompt, seconds, seed):
    endpoint = model_id(config)
    model = VIDEO_MODELS.get(endpoint, VIDEO_MODELS["wan-2-6-i2v"])
    size = config.get("video_size", "1280*720")
    if size not in model["rates"]:
        raise ValueError(f"{model['name']} supports 720p only. Select 720p in Runpod settings.")
    values = {"prompt": str(prompt).strip(), "duration": duration_for(seconds, config), "seed": seed}
    if endpoint in {"wan-2-2-i2v-720", "wan-2-1-i2v-720"}:
        values.update(size=size, num_inference_steps=30, guidance=5, flow_shift=5,
                      enable_prompt_optimization=bool(config.get("prompt_expansion", False)))
    else:
        # Live Wan 2.6 forwards size to an upstream resolution enum.
        values.update(size={"1280*720": "720p", "1920*1080": "1080p"}[size], shot_type="single",
                      enable_prompt_expansion=bool(config.get("prompt_expansion", False)))
    return values
