"""Standalone worker source, written into the isolated IndexTTS runtime."""

INDEXTTS_CLI = r"""
import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

def emit(message):
    print(json.dumps(message, ensure_ascii=False), flush=True)

def check_precision(torch, device, dtype):
    if device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("IndexTTS CUDA requested but no NVIDIA GPU is available.")
        if dtype == "bfloat16" and not torch.cuda.is_bf16_supported(including_emulation=False):
            raise RuntimeError("This GPU does not support native BF16. Select float32 explicitly.")
    elif dtype == "bfloat16":
        raise RuntimeError("IndexTTS BF16 requires CUDA. Select float32 for CPU.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--use-qwen-emo", choices=("0", "1"), default="0")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--emotion-batch", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--model-revision")
    args = parser.parse_args()
    model_dir = str(Path(args.model_dir).resolve())
    sys.path.insert(0, str(Path(args.source_dir).resolve()))
    # Some upstream resources use relative paths. Keep them in this engine's storage.
    os.chdir(args.source_dir)
    os.environ["HF_HOME"] = args.cache_dir
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    if args.emotion_batch:
        # A short-lived process guarantees all Qwen allocations are released
        # before the separate IndexTTS worker is started.
        if args.device == "cpu":
            os.environ["CUDA_VISIBLE_DEVICES"] = ""
        prompts = json.loads(sys.stdin.readline())["prompts"]
        with contextlib.redirect_stdout(sys.stderr):
            import torch
            from indextts.infer_v2_5 import QwenEmotion
            emotion = QwenEmotion(str(Path(model_dir) / "qwen0.6bemo4-merge"))
            order = ("happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm")
            vectors = []
            with torch.inference_mode():
                for prompt in prompts:
                    scores = emotion.inference(prompt)
                    vectors.append([float(scores[key]) for key in order])
        emit({"type": "emotions", "vectors": vectors})
        return 0
    with contextlib.redirect_stdout(sys.stderr):
        if args.prepare:
            from huggingface_hub import snapshot_download
            snapshot_download("IndexTeam/IndexTTS-2.5", revision=args.model_revision, local_dir=model_dir)
            # Download explicitly, including on repair: upstream skips partial directories.
            # IndexTTS-2.5 uses its own codec.pth and does not need the v2 MaskGCT codec.
            for repo, revision, folder, patterns in (
                ("facebook/w2v-bert-2.0", "da985ba0987f70aaeb84a80f2851cfac8c697a7b", "w2v-bert-2.0",
                 ["config.json", "preprocessor_config.json", "model.safetensors", "README.md", "LICENSE*"]),
                ("funasr/campplus", "e4b6ede7ce16997aff4ae69fbca1f0175e2afede", "",
                 ["campplus_cn_common.bin", "README.md", "LICENSE*"]),
                ("nvidia/bigvgan_v2_22khz_80band_256x", "633ff708ed5b74903e86ff1298cf4a98e921c513", "bigvgan",
                 ["config.json", "bigvgan_generator.pt", "README.md", "LICENSE*"]),
            ):
                snapshot_download(repo, revision=revision, local_dir=str(Path(model_dir) / "hf_cache" / folder),
                                  allow_patterns=patterns)
        import torch
        check_precision(torch, args.device, args.dtype)
        from indextts.infer_v2_5 import IndexTTS2
        os.environ["HF_HUB_CACHE"] = str(Path(model_dir) / "hf_cache")
        tts = IndexTTS2(
            cfg_path=str(Path(model_dir) / "config.yaml"), model_dir=model_dir,
            device="cuda:0" if args.device == "cuda" else "cpu",
            use_bf16=args.dtype == "bfloat16", use_qwen_emo=args.use_qwen_emo == "1",
            use_cuda_kernel=False, use_deepspeed=False, use_accel=False, use_torch_compile=False,
        )
    emit({"type": "ready", "device": args.device, "dtype": args.dtype})
    if args.prepare:
        return 0
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if request.get("type") == "shutdown":
                return 0
            if request.get("type") != "synthesize":
                raise ValueError("Unknown IndexTTS worker request.")
            with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
                tts.infer(**request["arguments"])
            emit({"type": "result", "id": request["id"], "output": request["arguments"]["output_path"]})
        except Exception as exc:
            emit({"type": "error", "id": request.get("id"), "message": str(exc)})
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        emit({"type": "fatal", "message": str(exc)})
        sys.exit(1)
"""
