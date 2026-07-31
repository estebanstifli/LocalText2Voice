from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.tts.python_runtime_manager import PythonRuntimeManager
from app.tts.qwen_engine import QwenTTSEngine
from app.tts.qwen_manager import (
    QWEN_CUDA_BLACKWELL_PROFILE,
    QWEN_CUDA_LEGACY_PROFILE,
    QwenManager,
)
from app.utils.gpu_detection import detect_gpus, format_gpu_detection
from app.utils.paths import models_root


PROFILE_CHOICES = {
    "legacy": QWEN_CUDA_LEGACY_PROFILE,
    "blackwell": QWEN_CUDA_BLACKWELL_PROFILE,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install and execute real CUDA probes for the isolated Qwen "
            "PyTorch runtime profiles."
        )
    )
    parser.add_argument(
        "--profile",
        choices=("all", *PROFILE_CHOICES),
        default="all",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPOSITORY_ROOT / "build" / "runtime_profile_validation",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Validate profiles already present under the output root.",
    )
    parser.add_argument(
        "--synthesize",
        action="store_true",
        help="Generate a short WAV after validating each selected profile.",
    )
    parser.add_argument(
        "--model-cache",
        type=Path,
        default=models_root() / "qwen" / "hf-cache",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "validation-report.json"
    python_runtime = PythonRuntimeManager()
    manager = QwenManager(
        install_dir=output_root / "models" / "qwen",
        python_runtime=python_runtime,
        dependencies_root=output_root / "engine-deps",
        timeout_seconds=120,
    )
    selected_profiles = (
        list(PROFILE_CHOICES.values())
        if args.profile == "all"
        else [PROFILE_CHOICES[args.profile]]
    )
    report: dict[str, Any] = {
        "gpu_detection": format_gpu_detection(detect_gpus()),
        "python_runtime": str(python_runtime.python_exe),
        "profiles": {},
    }
    gpu_summary = str(report["gpu_detection"])

    def progress(current: int, total: int, message: str) -> None:
        print(f"[{current:03d}/{total:03d}] {message}", flush=True)

    exit_code = 0
    for profile in selected_profiles:
        action = "VALIDATE" if args.skip_install else "INSTALL"
        print(f"=== {action} {profile.profile_id} ===", flush=True)
        try:
            if args.skip_install:
                profile_root = manager.profile_root(profile)
                profile_manifest = manager._read_manifest(
                    profile_root / manager.PROFILE_INSTALL_FILENAME
                )
                requirements_value = profile_manifest.get("requirements", [])
                requirements = (
                    [str(item) for item in requirements_value]
                    if isinstance(requirements_value, list)
                    else []
                )
                runtime_info = manager._validate_runtime(
                    None,
                    manager.profile_dependency_dir(profile),
                )
                manager._validate_profile_runtime(profile, runtime_info)
            else:
                requirements, runtime_info = manager._install_profile_environment(
                    profile,
                    progress,
                    None,
                )
            profile_report: dict[str, Any] = {
                "status": "passed",
                "profile": profile.manifest_data(),
                "requirements": requirements,
                "runtime_info": runtime_info,
            }
            report["profiles"][profile.profile_id] = profile_report
            print(f"=== PASSED {profile.profile_id} ===", flush=True)
            print(json.dumps(runtime_info, indent=2), flush=True)

            if args.synthesize:
                manager.cache_dir = args.model_cache.resolve()
                manager._write_cli()
                manager._write_runtime_manifest(
                    "installed",
                    requirements,
                    profile,
                    profile,
                    gpu_summary,
                    runtime_info,
                )
                manager.is_installed = lambda: True  # type: ignore[method-assign]
                output_path = (
                    output_root / "audio" / f"{profile.profile_id}.wav"
                )
                engine = QwenTTSEngine(manager)
                engine.set_log_callback(
                    lambda message: print(f"[Qwen] {message}", flush=True)
                )
                started_at = time.perf_counter()
                try:
                    engine.synthesize_to_wav(
                        "Esta es una prueba del perfil CUDA de LocalText2Voice.",
                        output_path,
                        {
                            "model": "custom_voice_0_6b",
                            "device": "cuda",
                            "dtype": "auto",
                            "speaker": "Serena",
                            "language": "Spanish",
                            "max_new_tokens": 256,
                        },
                    )
                finally:
                    engine.close()
                profile_report["synthesis"] = {
                    "output": str(output_path),
                    "bytes": output_path.stat().st_size,
                    "elapsed_seconds": round(
                        time.perf_counter() - started_at,
                        3,
                    ),
                }
                print(
                    f"=== SYNTHESIZED {profile.profile_id}: {output_path} ===",
                    flush=True,
                )
        except Exception as exc:
            exit_code = 1
            report["profiles"][profile.profile_id] = {
                "status": "failed",
                "profile": profile.manifest_data(),
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            print(f"=== FAILED {profile.profile_id}: {exc} ===", flush=True)

        report_path.write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )

    print(f"Validation report: {report_path}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
