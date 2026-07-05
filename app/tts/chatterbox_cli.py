from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _resolve_device(requested: str) -> str:
    try:
        import torch
    except Exception:
        if requested in {"cuda", "mps"}:
            raise RuntimeError("PyTorch is not available in this runtime.")
        return "cpu"

    if requested == "auto":
        if torch.cuda.is_available():
            return "cuda"
        if (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            return "mps"
        return "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        print(
            "CUDA was requested, but PyTorch cannot see a CUDA GPU. "
            "Falling back to CPU.",
            file=sys.stderr,
        )
        return "cpu"
    if requested == "mps" and not (
        hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
    ):
        raise RuntimeError("MPS was requested, but it is not available.")
    return requested


def _configure_cache(cache_dir: str | None) -> None:
    if not cache_dir:
        return
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_path)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(cache_path / "hub")
    os.environ["TRANSFORMERS_CACHE"] = str(cache_path / "transformers")


def _load_model(model_id: str, device: str):
    if model_id == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        return ChatterboxTurboTTS.from_pretrained(device=device)
    if model_id == "multilingual_v3":
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        return ChatterboxMultilingualTTS.from_pretrained(
            device=device,
            t3_model="v3",
        )
    if model_id == "english":
        from chatterbox.tts import ChatterboxTTS

        return ChatterboxTTS.from_pretrained(device=device)
    raise ValueError(f"Unknown Chatterbox model: {model_id}")


def _generate(args: argparse.Namespace) -> None:
    import torchaudio as ta

    device = _resolve_device(args.device)
    model = _load_model(args.model, device)
    if args.warmup:
        print(f"READY model={args.model} device={device}")
        return

    if not args.input or not args.output:
        raise ValueError("--input and --output are required unless --warmup is used.")
    input_path = Path(args.input)
    output_path = Path(args.output)
    text = input_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Input text is empty.")

    reference = str(args.reference).strip() if args.reference else None
    if args.model == "turbo" and not reference:
        raise ValueError("Chatterbox Turbo requires --reference audio.")

    generation_kwargs = {
        "audio_prompt_path": reference,
        "exaggeration": args.exaggeration,
        "cfg_weight": args.cfg_weight,
    }
    if args.model == "multilingual_v3":
        wav = model.generate(
            text,
            language_id=args.language,
            **generation_kwargs,
        )
    else:
        wav = model.generate(text, **generation_kwargs)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ta.save(str(output_path), wav, model.sr)
    print(f"Created {output_path}")


def _cuda_info() -> dict[str, object]:
    try:
        import torch
    except Exception as exc:
        return {
            "torch_available": False,
            "cuda_available": False,
            "device_count": 0,
            "error": str(exc),
        }

    info: dict[str, object] = {
        "torch_available": True,
        "torch_version": getattr(torch, "__version__", ""),
        "torch_cuda_version": getattr(torch.version, "cuda", None),
        "cuda_available": bool(torch.cuda.is_available()),
        "device_count": int(torch.cuda.device_count()),
        "devices": [],
        "error": "",
    }
    devices: list[dict[str, object]] = []
    for index in range(torch.cuda.device_count()):
        try:
            properties = torch.cuda.get_device_properties(index)
            devices.append(
                {
                    "index": index,
                    "name": torch.cuda.get_device_name(index),
                    "total_memory_gb": properties.total_memory / (1024**3),
                    "capability": f"{properties.major}.{properties.minor}",
                }
            )
        except Exception as exc:
            devices.append({"index": index, "error": str(exc)})
    info["devices"] = devices
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description="LocalText2Voice Chatterbox engine")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument(
        "--model",
        choices=("multilingual_v3", "english", "turbo"),
        default="multilingual_v3",
    )
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--device",
        choices=("auto", "cuda", "cpu", "mps"),
        default="cuda",
    )
    parser.add_argument("--reference", default="")
    parser.add_argument("--exaggeration", type=float, default=0.5)
    parser.add_argument("--cfg-weight", type=float, default=0.5)
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--cuda-info", action="store_true")
    args = parser.parse_args()

    try:
        _configure_cache(args.cache_dir)
        if args.cuda_info:
            print(json.dumps(_cuda_info(), ensure_ascii=False))
            return 0
        _generate(args)
    except Exception as exc:
        print(f"Chatterbox synthesis failed: {exc}", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
