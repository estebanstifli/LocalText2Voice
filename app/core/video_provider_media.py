"""Validated local image references for direct video APIs."""
import base64
from io import BytesIO
from PIL import Image, ImageOps

from app.core.storyboard_video_references import video_reference_paths
from app.core.direct_video_models import MODELS


def reference_paths(scene, frame_role, provider, config):
    model = MODELS[provider][config["model"]]
    if frame_role == "end" and not model.end_frame:
        raise ValueError("This provider requires a starting frame; an ending frame alone is not supported.")
    paths = video_reference_paths(scene, frame_role)
    limit = model.max_references if frame_role == "none" else 1
    if len(paths) > limit:
        raise ValueError(f"This reference mode accepts at most {limit} image(s). Choose None for unanchored references where supported.")
    return paths


def image_uri(path, provider):
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("Reference images must be no larger than 30 MB.")
    with Image.open(path) as image:
        if image.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("Use a PNG, JPEG or WebP reference image.")
        mime = Image.MIME[image.format]
        data = path.read_bytes()
        if provider in {"minimax", "byteplus"}:
            low, high = (256, 5760) if provider == "minimax" else (300, 6000)
            if not all(low <= value <= high for value in image.size) or not .4 <= image.width / image.height <= 2.5:
                raise ValueError(f"Reference dimensions must be {low}–{high} pixels, with aspect ratio between 0.4 and 2.5.")
        # LTX caps the encoded data URI at 7 MB. Use a deterministic JPEG
        # representation when a high-detail PNG exceeds that transport limit.
        if provider == "ltx" and len(data) * 4 / 3 > 7 * 1024 * 1024 - 100:
            output = BytesIO()
            converted = ImageOps.exif_transpose(image).convert("RGB")
            converted.thumbnail((3840, 3840), Image.Resampling.LANCZOS)
            converted.save(output, format="JPEG", quality=90)
            data, mime = output.getvalue(), "image/jpeg"
    uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    if provider == "ltx" and len(uri) > 7 * 1024 * 1024:
        raise ValueError("The encoded LTX reference image exceeds 7 MB. Resize the image before retrying.")
    return uri
