"""Manual, billable Seedream contract probe. Run only with user authorization."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import video_storyboard_runpod as rp
from app.core.settings_manager import SettingsManager


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--field", default="size")
    parser.add_argument("--size", default="1024*1024")
    args = parser.parse_args()
    settings = SettingsManager(Path(__file__).resolve().parents[1] / "config.json").settings["video_storyboard"]
    settings["runpod"]["image_endpoint"] = "seedream-v4-t2i"
    payload = {"prompt": "A small orange cat sitting beside a stone fountain in a sunny village square, cinematic illustration.",
               "seed": 42, args.field: args.size}
    target = Path(__file__).resolve().parents[1] / "tmp" / f"seedream-{args.field}-{args.size.replace('*', 'x')}.png"
    print("Input:", payload, flush=True)
    try:
        result = rp.execute(settings, "image", payload, target, status=lambda s: print(s, flush=True))
        print("Result:", result, flush=True)
        from PIL import Image
        with Image.open(target) as image:
            print("Verified image:", image.size, target, flush=True)
    except rp.RunpodError as exc:
        print(str(exc), flush=True)
        sys.exit(1)
