"""Provider request sizes and deterministic crop-to-fit for storyboard frames."""
from pathlib import Path
from PIL import Image, ImageOps


def image_parameters(model, width, height):
    name = model.rsplit("/", 1)[-1].lower()
    if name in {"gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5"}:
        size = "1536x1024" if width > height else "1024x1536" if height > width else "1024x1024"
        return {"size": size}
    if model.startswith("gemini/") and "image" in name:
        ratios = {"16:9": 16/9, "9:16": 9/16, "1:1": 1, "4:3": 4/3, "3:4": 3/4, "3:2": 1.5, "2:3": 2/3}
        ratio = min(ratios, key=lambda key: abs(ratios[key] - width / height))
        tier = "1K" if width * height <= 1280 * 720 else "2K"
        return {"imageConfig": {"aspectRatio": ratio, "imageSize": tier}}
    return {"size": f"{width}x{height}"}


def fit_frame(path: Path, width: int, height: int):
    """Center crop to the target aspect ratio, then resize without stretching."""
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original)
        if image.size == (width, height):
            return
        fitted = ImageOps.fit(image.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
    temporary = path.with_suffix(path.suffix + ".sized")
    try:
        fitted.save(temporary, format="PNG")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
