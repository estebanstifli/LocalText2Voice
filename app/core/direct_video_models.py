"""Capabilities shared by direct video adapters and their settings UI.

Add model variants here when they share an existing provider protocol.
Prices are indicative USD/second, verified against provider docs 2026-09-21.
"""
from copy import deepcopy
from dataclasses import dataclass
import math
from urllib.parse import urlsplit


@dataclass(frozen=True)
class VideoModel:
    label: str
    durations: tuple[int, ...]
    resolutions: tuple[str, ...] = ("720p", "1080p")
    rates: tuple[float, ...] = ()
    audio_optional: bool = True
    end_frame: bool = False
    max_references: int = 1


MODELS = {
    "ltx": {
        "ltx-2-3-fast": VideoModel("LTX 2.3 Fast", tuple(range(6, 21, 2)), rates=(.03, .06)),
        "ltx-2-5-fast": VideoModel("LTX 2.5 Fast", tuple(range(6, 21, 2)), rates=(.09, .13)),
    },
    "minimax": {
        "MiniMax-H3": VideoModel("MiniMax H3", tuple(range(4, 16)), ("720p",), (.08,),
                                 audio_optional=False, end_frame=True, max_references=9),
    },
    "byteplus": {
        "dreamina-seedance-2-5-260628": VideoModel("Seedance 2.5", tuple(range(4, 31)),
                                                ("720p",), (.303 * 1.525,), max_references=30),
    },
}
PROVIDERS = {
    "ltx": ("LTX", "https://api.ltx.io"),
    "minimax": ("MiniMax", "https://api.minimax.io"),
    "byteplus": ("BytePlus LAS · Seedance", "https://operator.las.ap-southeast-1.bytepluses.com/api/v1"),
}
DEFAULTS = {provider: {"model": next(iter(MODELS[provider])), "api_key": "", "base_url": root,
                       "resolution": "720p", "audio": provider == "minimax", "timeout_seconds": 1800}
            for provider, (_, root) in PROVIDERS.items()}


def configuration(settings, provider=None):
    provider = provider or settings.get("video_provider")
    return {**DEFAULTS[provider], **settings.get(provider + "_video", {})}


def normalize(value, provider):
    config = deepcopy(DEFAULTS[provider])
    if isinstance(value, dict):
        config.update({key: value[key] for key in config if key in value})
    if not isinstance(config["model"], str) or config["model"] not in MODELS[provider]:
        config["model"] = DEFAULTS[provider]["model"]
    model = MODELS[provider][config["model"]]
    config["resolution"] = str(config["resolution"]).lower()
    if config["resolution"] not in model.resolutions:
        config["resolution"] = model.resolutions[0]
    for key in ("api_key", "base_url"):
        config[key] = str(config[key] or DEFAULTS[provider][key]).strip()
    try:
        config["timeout_seconds"] = max(60, min(7200, int(config["timeout_seconds"])))
    except (ValueError, TypeError):
        config["timeout_seconds"] = 1800
    config["audio"] = config["audio"] is True if model.audio_optional else True
    return config


def api_root(config):
    root = str(config["base_url"]).strip().rstrip("/")
    parsed = urlsplit(root)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter an HTTPS provider API URL without credentials or query parameters.")
    return root


def prepare(settings):
    provider = settings["video_provider"]
    config = configuration(settings)
    api_root(config)
    if not str(config["api_key"]).strip():
        raise ValueError(f"Set your {PROVIDERS[provider][0]} API key in Settings > Video Storyboard.")
    if config["model"] not in MODELS[provider]:
        raise ValueError("Select a supported video model.")
    if config["resolution"] not in MODELS[provider][config["model"]].resolutions:
        raise ValueError("Select a supported resolution for this video model.")
    return {"provider": provider, "model": config["model"]}


def generation_seconds(scene, provider, config):
    durations = MODELS[provider][config["model"]].durations
    seconds = math.ceil(float(scene.get("duration_seconds") or 5))
    return next((value for value in durations if value >= seconds), durations[-1])


def frame_size(config):
    return (1920, 1080) if config["resolution"] == "1080p" else (1280, 720)
