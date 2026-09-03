from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.settings_manager import SettingsManager  # noqa: E402
from app.core.video_storyboard_comfyui import (  # noqa: E402
    VideoStoryboardImageError,
    compile_effective_scene_prompt,
    generate_storyboard_frame,
    prepare_image_runtime,
)
from app.core.video_storyboard_styles import storyboard_styles  # noqa: E402


SAMPLE_SEED = 20260901
SAMPLE_SCENE = (
    "An eight-year-old girl in a yellow summer dress plays with a red ball "
    "on a sunlit cobblestone village square, while a curious tabby cat watches "
    "beside a stone fountain; a welcoming house with blue shutters and flower "
    "boxes stands behind them"
)
SAMPLE_SHOT = "wide three-quarter establishing shot, eye level, 35mm viewpoint"
SAMPLE_CHARACTER = (
    "girl: an eight-year-old girl with chestnut braids, a yellow summer dress "
    "and brown ankle shoes"
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate reusable 512x512 Video Storyboard style samples.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "assets" / "storyboard_style_samples",
    )
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    parser.add_argument("--style", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    selected_ids = {str(value).strip() for value in args.style if str(value).strip()}
    styles = [
        style
        for style in storyboard_styles()
        if not selected_ids or style.id in selected_ids
    ]
    if selected_ids - {style.id for style in styles}:
        missing = ", ".join(sorted(selected_ids - {style.id for style in styles}))
        print(f"Unknown style IDs: {missing}", flush=True)
        return 2
    settings = deepcopy(SettingsManager().settings["video_storyboard"])
    settings.setdefault("image", {}).update(
        {"width": 512, "height": 512, "batch_size": 1}
    )
    args.output.mkdir(parents=True, exist_ok=True)
    print("Checking ComfyUI and loading the configured model...", flush=True)
    runtime = prepare_image_runtime(
        settings,
        status=lambda stage: print(f"runtime: {stage}", flush=True),
    )
    print(
        f"ComfyUI ready ({runtime.get('device_name', 'unknown device')}). "
        f"Generating {len(styles)} styles with seed {args.seed}.",
        flush=True,
    )
    completed: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    for index, style in enumerate(styles, start=1):
        target = args.output / f"{style.id}.png"
        if target.is_file() and not args.force:
            print(f"[{index}/{len(styles)}] {style.name}: already exists", flush=True)
            completed.append(_manifest_entry(style, target, args.seed))
            continue
        plan = _sample_plan(style, args.seed)
        scene = _sample_scene(style)
        print(f"[{index}/{len(styles)}] {style.name}: generating...", flush=True)
        try:
            result = generate_storyboard_frame(
                scene,
                plan,
                settings,
                target,
                status=lambda stage, name=style.name: print(
                    f"[{name}] {stage}", flush=True
                ),
            )
        except VideoStoryboardImageError as exc:
            failures.append({"id": style.id, "error": str(exc)})
            print(f"[{index}/{len(styles)}] {style.name}: FAILED: {exc}", flush=True)
            continue
        entry = _manifest_entry(style, target, args.seed)
        entry["compiled_prompt"] = str(result.get("compiled_prompt") or "")
        completed.append(entry)
        print(f"[{index}/{len(styles)}] {style.name}: saved {target}", flush=True)
    manifest = {
        "schema": "localtext2voice.storyboard-style-samples",
        "version": 1,
        "resolution": [512, 512],
        "seed": args.seed,
        "scene": SAMPLE_SCENE,
        "shot": SAMPLE_SHOT,
        "samples": completed,
        "failures": failures,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Finished: {len(completed)} generated, {len(failures)} failed. "
        f"Manifest: {args.output / 'manifest.json'}",
        flush=True,
    )
    return 1 if failures else 0


def _manifest_entry(style, target: Path, seed: int) -> dict[str, object]:
    compiled_prompt = compile_effective_scene_prompt(
        _sample_plan(style, seed),
        _sample_scene(style),
    )
    return {
        "id": style.id,
        "name": style.name,
        "style_prompt": style.prompt,
        "image": target.name,
        "seed": seed,
        "compiled_prompt": compiled_prompt,
    }


def _sample_plan(style, seed: int) -> dict[str, object]:
    return {
        "base_seed": max(0, min(int(seed), 2**63 - 1)),
        "style": {
            "medium": style.prompt,
            "palette": "",
            "lighting": "",
            "characters": [SAMPLE_CHARACTER],
            "negative": (
                "text, captions, subtitles, logos, watermark, split screen, "
                "collage, comic panels, duplicate subject, multiple views, "
                "malformed hands, extra fingers, anatomical distortion"
            ),
        },
        "narrative_context": {"era": ""},
    }


def _sample_scene(style) -> dict[str, object]:
    return {
        "scene_id": f"style-{style.id}",
        "prompt": SAMPLE_SCENE,
        "shot": SAMPLE_SHOT,
        "era": "",
        "characters": ["girl"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
