"""Contract for the private H3 ComfyUI queue worker, not MiniMax's hosted API.

Matches handler_voice.py: fast=4 steps, normal=20, one initial image,
1–10 seconds, 24 FPS, native workflow resolution; output.files[].base64.
"""
import base64
import binascii
import math
from pathlib import Path
import uuid


def is_h3(config):
    return config.get("video_adapter") == "h3"


def parameters(config, prompt, seconds, seed):
    seconds = float(seconds)
    if not math.isfinite(seconds) or not 1 <= seconds <= 10:
        raise ValueError("The private H3 workflow accepts 1–10 seconds per clip. Split longer scenes first.")
    preset = config.get("h3_preset", "fast")
    if preset not in {"fast", "normal"}:
        raise ValueError("Select H3 Fast (4 steps) or Normal (20 steps).")
    return {"preset": preset, "seconds": seconds, "seed": int(seed),
            "prompt": str(prompt).strip(), "return_base64": True}


def encode_reference(path):
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= 7_000_000:
        raise ValueError("H3 requires a reference image of at most 7 MB (base64 must fit Runpod's request limit).")
    from PIL import Image
    with Image.open(path) as image:
        image.verify()
    return base64.b64encode(path.read_bytes()).decode("ascii")


def save_output(output, target, cancelled=None):
    files = output.get("files")
    videos = [entry for entry in files if isinstance(entry, dict)
              and str(entry.get("filename", "")).lower().endswith(".mp4")] if isinstance(files, list) else []
    if len(videos) != 1:
        raise ValueError("H3 returned no unique MP4. The saved job can be recovered without generating again.")
    item = videos[0]
    encoded = item.get("base64")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("H3 completed but did not return inline video data (the worker has a 7 MB output limit). Recover the MP4 from the Runpod volume; do not generate it again.")
    if len(encoded) > 10_700_000:
        raise ValueError("H3 returned unexpectedly large inline video data.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("H3 returned invalid base64 video data. The paid job is saved for recovery.") from exc
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise ValueError("H3 returned data without an MP4 header.")
    if item.get("bytes") is not None and int(item["bytes"]) != len(data):
        raise ValueError("H3 returned an incomplete MP4. The paid job is saved for recovery.")
    if cancelled and cancelled():
        raise ValueError("Video save cancelled. The completed job is saved for recovery.")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
    try:
        temporary.write_bytes(data)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
