"""Public T2I contracts from docs.runpod.io/public-endpoints/models (2026-09-10)."""
import math

IMAGE_MODELS = {
    "z-image-turbo": {"name": "Z-Image Turbo", "price": 0.005},
    "qwen-image-t2i": {"name": "Qwen Image", "price": 0.02},
    "p-image-t2i": {"name": "P-Image T2I", "price": 0.005},
}


def image_model_id(config):
    value = str(config.get("image_endpoint") or "z-image-turbo").strip().rstrip("/")
    prefix = "https://api.runpod.ai/v2/"
    return value[len(prefix):].split("/")[0] if value.startswith(prefix) else value


def image_parameters(config, prompt, seed, width, height):
    endpoint = image_model_id(config)
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive.")
    values = {"prompt": prompt, "seed": int(seed)}
    size = f"{width}*{height}"
    if endpoint == "p-image-t2i":
        ratios = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")
        def distance(ratio):
            a, b = map(int, ratio.split(":"))
            return abs(math.log((width / height) / (a / b)))
        values["aspect_ratio"] = min(ratios, key=distance)
    else:
        if endpoint == "z-image-turbo" and size not in {
            "512*512", "768*768", "1024*1024", "1280*1280", "1024*768", "768*1024", "1280*720", "720*1280"
        }:
            raise ValueError("Z-Image Turbo requires a supported resolution, e.g. 1280 × 720 or 1024 × 1024.")
        if endpoint == "seedream-v4-t2i":
            # Runpod's live allAiApiPublicConfigs.schema lists square sizes
            # only. Unsupported rectangles produce a misleading format error.
            # image_size is ignored and silently generates the 2048² default.
            if size not in {"1024*1024", "2048*2048", "4096*4096"}:
                raise ValueError("Seedream 4.0 on Runpod supports only 1024 × 1024, 2048 × 2048 or 4096 × 4096. "
                                 "Select a square resolution in Settings > Video Storyboard > Image preset, "
                                 "or choose Qwen Image / P-Image for widescreen images. No request was sent.")
            values["seed"] = int(seed) % (2 ** 32)
        if endpoint == "wan-2-6-t2i":
            if size not in {"1024*1024", "1024*768", "1440*1024"}:
                raise ValueError("WAN 2.6 T2I on Runpod supports only 1024 × 1024, 1024 × 768 or 1440 × 1024. "
                                 "Choose one in Settings > Video Storyboard > Image preset. No request was sent.")
            values["seed"] = int(seed) % (2 ** 32)
        values["size"] = size
        if endpoint not in {"qwen-image-t2i", "seedream-v4-t2i", "wan-2-6-t2i"}:
            # Preserve the existing Z-Image-compatible custom endpoint contract.
            values["output_format"] = "png"
    return values
