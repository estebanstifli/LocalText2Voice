from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="LocalText2Voice Kokoro engine")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--voice", required=True)
    parser.add_argument("--lang", required=True)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--model", required=True)
    parser.add_argument("--voices", required=True)
    parser.add_argument(
        "--provider",
        choices=("auto", "cpu", "cuda", "directml"),
        default="cpu",
    )
    args = parser.parse_args()

    if args.provider not in {"auto", "cpu"}:
        print(
            f"Provider {args.provider!r} is reserved for a future build. "
            "Use CPU for now.",
            file=sys.stderr,
        )
        return 2

    try:
        import soundfile as sf
        from kokoro_onnx import Kokoro
    except Exception as exc:
        print(
            "Kokoro runtime dependencies are missing. Build the separate "
            "kokoro_engine.exe with build_kokoro_engine.bat. "
            f"Details: {exc}",
            file=sys.stderr,
        )
        return 3

    input_path = Path(args.input)
    output_path = Path(args.output)
    model_path = Path(args.model)
    voices_path = Path(args.voices)
    if not model_path.is_file():
        print(f"Model file not found: {model_path}", file=sys.stderr)
        return 4
    if not voices_path.is_file():
        print(f"Voices file not found: {voices_path}", file=sys.stderr)
        return 4

    text = input_path.read_text(encoding="utf-8")
    try:
        kokoro = Kokoro(str(model_path), str(voices_path))
        samples, sample_rate = kokoro.create(
            text,
            voice=args.voice,
            speed=args.speed,
            lang=args.lang,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), samples, sample_rate)
    except Exception as exc:
        print(f"Kokoro synthesis failed: {exc}", file=sys.stderr)
        return 5
    print(f"Created {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
