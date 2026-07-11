from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_GALLERY_ROOT = WORKSPACE_ROOT / "LocalText2Voice-VoiceGallery"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def iter_voice_files(gallery_root: Path, engine: str) -> list[Path]:
    index_path = gallery_root / "engines" / engine / "index.json"
    index = load_json(index_path)
    voices = index.get("voices", [])
    if not isinstance(voices, list):
        raise ValueError(f"{index_path} voices must be a list")
    return [index_path.parent / str(relative_path) for relative_path in voices]


def preview_text(voice: dict[str, Any]) -> str:
    return (
        str(voice.get("ref_text") or "").strip()
        or "This is a short LocalText2Voice voice preview."
    )


def python_runtime_for(app_root: Path):
    from app.tts.python_runtime_manager import PythonRuntimeManager

    return PythonRuntimeManager(runtime_dir=app_root / "runtimes" / "python311")


def render_kokoro_preview(
    app_root: Path,
    runtime_state: dict[str, Any],
    voice: dict[str, Any],
    output_wav: Path,
) -> None:
    from app.tts.kokoro_python_engine import KokoroPythonTTSEngine
    from app.tts.kokoro_python_manager import KokoroPythonManager

    if "kokoro" not in runtime_state:
        manager = KokoroPythonManager(python_runtime=python_runtime_for(app_root))
        runtime_state["kokoro"] = {
            "manager": manager,
            "engine": KokoroPythonTTSEngine(manager),
        }
    manager = runtime_state["kokoro"]["manager"]
    engine = runtime_state["kokoro"]["engine"]
    voice_id = str(voice.get("engine_voice_id") or voice.get("speaker_id") or voice["id"])
    config = {
        "engine": "kokoro",
        "voice": voice_id,
        "lang": str(voice.get("language") or "en"),
        "backend": "auto",
        "model_path": str(manager.model_path),
        "voices_path": str(manager.voices_path),
    }
    engine.synthesize_to_wav(preview_text(voice), output_wav, config)


def render_qwen_preview(
    app_root: Path,
    runtime_state: dict[str, Any],
    voice: dict[str, Any],
    output_wav: Path,
) -> None:
    from app.tts.qwen_engine import QwenTTSEngine
    from app.tts.qwen_manager import QwenManager

    if "qwen" not in runtime_state:
        manager = QwenManager(python_runtime=python_runtime_for(app_root))
        runtime_state["qwen"] = {"engine": QwenTTSEngine(manager)}
    engine = runtime_state["qwen"]["engine"]
    config = {
        "engine": "qwen",
        "model": str(voice.get("model_id") or "custom_voice_0_6b"),
        "speaker": str(voice.get("speaker_id") or voice.get("engine_voice_id") or "Serena"),
        "language": str(voice.get("language_name") or voice.get("language") or "English"),
        "device": "auto",
        "dtype": "auto",
    }
    engine.synthesize_to_wav(preview_text(voice), output_wav, config)


def render_omnivoice_preview(
    app_root: Path,
    runtime_state: dict[str, Any],
    voice: dict[str, Any],
    output_wav: Path,
) -> None:
    from app.tts.omnivoice_engine import OmniVoiceTTSEngine
    from app.tts.omnivoice_manager import OmniVoiceManager

    if "omnivoice" not in runtime_state:
        manager = OmniVoiceManager(python_runtime=python_runtime_for(app_root))
        runtime_state["omnivoice"] = {"engine": OmniVoiceTTSEngine(manager)}
    engine = runtime_state["omnivoice"]["engine"]
    install_type = str(voice.get("install_type", "engine_builtin"))
    mode = "design" if install_type == "engine_builtin" else "clone"
    config = {
        "engine": "omnivoice",
        "model": str(voice.get("model_id") or "omnivoice"),
        "mode": mode,
        "language": str(voice.get("language_name") or voice.get("language") or "auto"),
        "device": "auto",
        "dtype": "auto",
        "instruct": str(voice.get("instruct") or ""),
        "reference_audio_path": str(voice.get("ref_audio") or ""),
        "reference_text": str(voice.get("ref_text") or ""),
    }
    engine.synthesize_to_wav(preview_text(voice), output_wav, config)


def copy_reference_preview(voice_file: Path, voice: dict[str, Any], output_wav: Path) -> bool:
    for key in ("ref_audio", "preview_audio"):
        value = str(voice.get(key) or "").strip()
        if not value:
            continue
        source = voice_file.parent / value
        if source.is_file():
            shutil.copy2(source, output_wav)
            return True
    return False


def render_piper_preview(
    app_root: Path,
    gallery_root: Path,
    voice: dict[str, Any],
    output_wav: Path,
) -> None:
    from app.tts.piper_engine import PiperTTSEngine
    from app.tts.voice_manager import VoiceManager

    from app.tts.kokoro_preview import kokoro_preview_text_for_language

    voices_root = app_root / "voices"
    voices = VoiceManager(voices_root).discover()
    requested_id = str(voice.get("engine_voice_id") or voice.get("id") or "")
    matched = next(
        (candidate for candidate in voices if candidate.voice_id.casefold() == requested_id.casefold()),
        None,
    )
    if matched is None:
        raise RuntimeError(f"Piper voice is not installed locally: {requested_id}")
    piper_path = app_root / "engines" / "piper" / "piper.exe"
    engine = PiperTTSEngine(piper_path)
    config = matched.as_config(speed=1.0)
    engine.synthesize_to_wav(
        preview_text(voice) or kokoro_preview_text_for_language(matched.language),
        output_wav,
        config,
    )


def render_preview(
    app_root: Path,
    runtime_state: dict[str, Any],
    gallery_root: Path,
    engine: str,
    voice_file: Path,
    voice: dict[str, Any],
    output_wav: Path,
) -> None:
    if engine == "kokoro":
        render_kokoro_preview(app_root, runtime_state, voice, output_wav)
    elif engine == "qwen":
        render_qwen_preview(app_root, runtime_state, voice, output_wav)
    elif engine == "omnivoice":
        if str(voice.get("install_type")) == "reference_audio" and copy_reference_preview(
            voice_file,
            voice,
            output_wav,
        ):
            return
        render_omnivoice_preview(app_root, runtime_state, voice, output_wav)
    elif engine == "chatterbox":
        if not copy_reference_preview(voice_file, voice, output_wav):
            raise RuntimeError(f"Chatterbox voice has no reference audio: {voice_file}")
    elif engine == "piper":
        render_piper_preview(app_root, gallery_root, voice, output_wav)
    else:
        raise RuntimeError(f"Preview generation is not implemented for engine: {engine}")


def generate_previews(gallery_root: Path, app_root: Path, engine: str, force: bool) -> int:
    generated = 0
    skipped = 0
    runtime_state: dict[str, Any] = {}
    try:
        for voice_file in iter_voice_files(gallery_root, engine):
            voice = load_json(voice_file)
            preview_name = str(voice.get("preview_audio") or "preview.wav")
            preview_path = voice_file.parent / preview_name
            if preview_path.is_file() and not force:
                continue
            with tempfile.TemporaryDirectory(prefix="ltv_voice_preview_") as temporary_name:
                temporary_wav = Path(temporary_name) / "preview.wav"
                try:
                    render_preview(
                        app_root,
                        runtime_state,
                        gallery_root,
                        engine,
                        voice_file,
                        voice,
                        temporary_wav,
                    )
                except Exception as exc:
                    skipped += 1
                    print(f"Skipped preview: {voice_file} ({exc})")
                    continue
                if not temporary_wav.is_file() or temporary_wav.stat().st_size == 0:
                    raise RuntimeError(f"Preview was not created for {voice_file}")
                preview_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(temporary_wav, preview_path)
            voice["preview_audio"] = preview_path.name
            write_json(voice_file, voice)
            generated += 1
            print(f"Generated preview: {voice_file}")
    finally:
        for runtime in runtime_state.values():
            if isinstance(runtime, dict):
                runtime = runtime.get("engine")
            close = getattr(runtime, "close", None)
            if callable(close):
                close()
    if skipped:
        print(f"Skipped {skipped} preview(s) for {engine}.")
    return generated


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate LocalText2Voice voice gallery previews")
    parser.add_argument("engine", choices=("chatterbox", "kokoro", "omnivoice", "piper", "qwen"))
    parser.add_argument("--gallery-root", default=str(DEFAULT_GALLERY_ROOT))
    parser.add_argument(
        "--app-root",
        default=str(PROJECT_ROOT),
        help=(
            "Portable LocalText2Voice app root. Use dist/LocalText2Voice when "
            "generating previews with bundled engines."
        ),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    count = generate_previews(
        Path(args.gallery_root).resolve(),
        Path(args.app_root).resolve(),
        args.engine,
        args.force,
    )
    print(f"Generated {count} preview(s) for {args.engine}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
