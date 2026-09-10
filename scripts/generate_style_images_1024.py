"""Generate full-resolution style samples with the app's ComfyUI Z-Image Turbo workflow."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.video_storyboard_comfyui import (
    VideoStoryboardImageError,
    build_z_image_workflow,
    generate_storyboard_frame,
    prepare_image_runtime,
)
from app.core.video_storyboard_styles import storyboard_styles

SIZE = (1024, 1024)
DEFAULT_OUTPUT = ROOT.parent / "output" / "storyboard_style_samples_1024"
DEFAULT_SCENE = (
    "An eight-year-old girl with chestnut braids, wearing a yellow summer dress "
    "and brown ankle shoes, plays with a red ball and a curious tabby cat in "
    "a sunlit village square. A stone fountain stands in the middle of the "
    "square. Cobblestones, welcoming village houses with blue shutters, and "
    "flower boxes surround the scene. Show the girl, ball, cat and central "
    "fountain together in one coherent scene."
)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--base-url", help="Override the app's ComfyUI URL.")
    parser.add_argument("--model", help="Installed Z-Image Turbo diffusion model filename.")
    parser.add_argument("--text-encoder", help="Installed text encoder filename.")
    parser.add_argument("--vae", help="Installed VAE filename.")
    parser.add_argument("--seed", type=int, default=20260901)
    parser.add_argument("--scene", default=DEFAULT_SCENE)
    parser.add_argument("--style", action="append", default=[], help="Style ID or name; repeat to select several.")
    parser.add_argument("--timeout", type=int, default=1800, help="Maximum seconds per image.")
    parser.add_argument("--force", action="store_true", help="Regenerate existing PNGs after successful validation.")
    parser.add_argument("--dry-run", action="store_true", help="Write prompts and API workflows without contacting ComfyUI.")
    args = parser.parse_args(argv)
    if not 0 <= args.seed <= 2**63 - 1:
        parser.error("--seed must be between 0 and 2**63 - 1.")
    if args.timeout <= 0 or not args.scene.strip():
        parser.error("--timeout must be positive and --scene cannot be empty.")
    return args


def settings_for(args):
    # Read configuration without constructing SettingsManager or modifying app settings.
    settings = deepcopy(json.loads((ROOT / "config.example.json").read_text(encoding="utf-8-sig"))["video_storyboard"])
    if args.config.is_file():
        configured = json.loads(args.config.read_text(encoding="utf-8-sig")).get("video_storyboard", {})
        comfy = configured.get("comfyui", {})
        settings["comfyui"].update(comfy)
    elif args.config != ROOT / "config.json":
        raise ValueError(f"Config file does not exist: {args.config}")
    comfy = settings["comfyui"]
    model = args.model or str(comfy.get("diffusion_model") or "")
    if "z_image_turbo" not in model.lower().replace("-", "_"):
        if args.model:
            raise ValueError("--model must identify a Z-Image Turbo model.")
        model = "z_image_turbo_bf16.safetensors"
        comfy.update(text_encoder="qwen_3_4b.safetensors", vae_model="ae.safetensors")
    comfy.update(diffusion_model=model, timeout_seconds=args.timeout, workflow_path="")
    for key, value in [("base_url", args.base_url), ("text_encoder", args.text_encoder), ("vae_model", args.vae)]:
        if value:
            comfy[key] = value
    settings["image_provider"] = "comfyui"
    settings["image"].update(width=1024, height=1024, batch_size=1, steps=8, cfg=1.0,
                             sampler="res_multistep", scheduler="simple", denoise=1.0, auraflow_shift=3.0)
    return settings


def make_job(style, args, settings):
    prompt = (
        f"SCENE: {args.scene.strip()} "
        f"STYLE: {style.prompt}. "
        "COMPOSITION: square image, wide three-quarter establishing shot, eye level; "
        "keep the girl, ball, cat and fountain visible, with breathing room around them. "
        "One image, no text, no captions, no logos, no watermark, no collage."
    )
    workflow = build_z_image_workflow(prompt, args.seed, settings, f"LocalText2Voice/styles_1024/{style.id}")
    fingerprint = hashlib.sha256(json.dumps(workflow, sort_keys=True).encode()).hexdigest()
    return {"id": style.id, "name": style.name, "style_prompt": style.prompt,
            "image": f"{style.id}.png", "seed": args.seed, "resolution": list(SIZE),
            "compiled_prompt": prompt, "fingerprint": fingerprint, "workflow": workflow}


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def png_hash(path):
    # Read and validate only: never resize, convert or re-encode the server's PNG.
    with Image.open(path) as image:
        if image.format != "PNG" or image.size != SIZE:
            raise ValueError(f"Expected PNG 1024x1024, received {image.format} {image.size}: {path.name}")
        image.load()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    output = args.output.expanduser().resolve()
    if output == (ROOT / "assets" / "storyboard_style_samples").resolve():
        raise ValueError("Choose a separate output directory; the app's thumbnail gallery is protected.")
    styles = list(storyboard_styles())
    if not styles:
        raise ValueError("The app style catalogue is empty.")
    requested = {name.casefold() for name in args.style}
    known = {value.casefold() for style in styles for value in [style.id, style.name]}
    if requested - known:
        raise ValueError("Unknown styles: " + ", ".join(sorted(requested - known)))
    if requested:
        styles = [s for s in styles if s.id.casefold() in requested or s.name.casefold() in requested]
    settings = settings_for(args)
    jobs = [make_job(style, args, settings) for style in styles]
    output.mkdir(parents=True, exist_ok=True)
    workflows = output / "workflows"
    workflows.mkdir(exist_ok=True)
    for job in jobs:
        write_json(workflows / f"{job['id']}.json", job["workflow"])
    public_jobs = [{key: value for key, value in job.items() if key != "workflow"} for job in jobs]
    write_json(output / "generation_plan.json", {
        "provider": "comfyui", "model": settings["comfyui"]["diffusion_model"],
        "resolution": list(SIZE), "scene": args.scene, "styles": public_jobs,
    })
    print(f"{len(jobs)} styles | Z-Image Turbo | 1024x1024 | seed {args.seed}", flush=True)
    print(f"Output: {output}", flush=True)
    if args.dry_run:
        print("Dry run: saved prompts and workflows. No GPU jobs submitted.")
        return 0

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    entries = {entry["id"]: entry for entry in manifest.get("samples", [])}
    failures = []

    def save():
        write_json(manifest_path, {"schema": "localtext2voice.style-images-1024", "version": 1,
                                  "samples": list(entries.values()), "failures": failures})

    pending = []
    for job in public_jobs:
        target = output / job["image"]
        if target.exists() and not args.force:
            previous = entries.get(job["id"], {})
            try:
                digest = png_hash(target)
                if previous.get("fingerprint") != job["fingerprint"] or previous.get("sha256") != digest:
                    raise ValueError("Existing image does not match this prompt/model/seed; use another directory or --force.")
            except (OSError, ValueError) as exc:
                failures.append({"id": job["id"], "error": str(exc)})
                print(f"{job['id']}: {exc}", flush=True)
            else:
                print(f"{job['id']}: verified; skipping", flush=True)
        else:
            pending.append(job)
    save()
    if not pending:
        return 1 if failures else 0
    print("Checking ComfyUI nodes and installed models...", flush=True)
    runtime = prepare_image_runtime(settings, status=lambda stage: print(f"runtime: {stage}", flush=True))
    print(f"ComfyUI ready: {runtime.get('device', 'unknown device')}", flush=True)
    for index, job in enumerate(pending, 1):
        target = output / job["image"]
        temporary = output / f".{job['id']}.pending.png"
        print(f"[{index}/{len(pending)}] {job['name']}", flush=True)
        try:
            scene = {"scene_id": f"style-{job['id']}",
                     "generation_overrides": {"raw_prompt": job["compiled_prompt"]}}
            result = generate_storyboard_frame(scene, {"base_seed": args.seed}, settings, temporary,
                                              status=lambda stage: print(f"  {stage}", flush=True))
            digest = png_hash(temporary)
            temporary.replace(target)
            entries[job["id"]] = {**job, "sha256": digest, "prompt_id": result.get("prompt_id", "")}
            print(f"  saved {target.name} (1024x1024, original PNG)", flush=True)
        except (OSError, ValueError, VideoStoryboardImageError) as exc:
            failures.append({"id": job["id"], "error": str(exc)})
            print(f"  FAILED: {exc}", flush=True)
        finally:
            if temporary.exists():
                temporary.unlink()
            save()
    print(f"Finished: {len(entries)} saved images in manifest; {len(failures)} failures.", flush=True)
    return 1 if failures else 0


def main(argv=None):
    try:
        return run(arguments(argv))
    except KeyboardInterrupt:
        print("\nInterrupted. Run the same command to resume; completed images are preserved.")
        return 130
    except (OSError, ValueError, VideoStoryboardImageError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
