from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.core.video_storyboard_services import (
    REQUIRED_COMFYUI_NODES,
    detect_comfyui,
    detect_ollama,
)


class _Response:
    def __init__(self, value: object) -> None:
        self.payload = json.dumps(value).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class VideoStoryboardServiceTests(unittest.TestCase):
    def test_detect_ollama_lists_installed_and_loaded_models(self) -> None:
        responses = [
            _Response({"models": [{"name": "qwen3:8b"}, {"name": "gemma3:4b"}]}),
            _Response({"models": [{"name": "qwen3:8b"}]}),
        ]
        with patch(
            "app.core.video_storyboard_services.urllib.request.urlopen",
            side_effect=responses,
        ):
            result = detect_ollama("http://127.0.0.1:11434/")
        self.assertEqual(result["models"], ["gemma3:4b", "qwen3:8b"])
        self.assertEqual(result["loaded_models"], ["qwen3:8b"])

    def test_detect_comfyui_lists_models_gpu_and_required_nodes(self) -> None:
        object_info = {node: {} for node in REQUIRED_COMFYUI_NODES}
        object_info["UNETLoader"] = {
            "input": {"required": {"unet_name": [["z_image_turbo_bf16.safetensors"]]}}
        }
        object_info["CLIPLoader"] = {
            "input": {"required": {"clip_name": [["qwen_3_4b.safetensors"]]}}
        }
        object_info["VAELoader"] = {
            "input": {"required": {"vae_name": [["ae.safetensors"]]}}
        }
        responses = [
            _Response(
                {
                    "system": {"comfyui_version": "0.3.test"},
                    "devices": [
                        {
                            "name": "NVIDIA RTX",
                            "vram_total": 8 * 1024**3,
                            "vram_free": 6 * 1024**3,
                        }
                    ],
                }
            ),
            _Response(object_info),
        ]
        with patch(
            "app.core.video_storyboard_services.urllib.request.urlopen",
            side_effect=responses,
        ):
            result = detect_comfyui("http://127.0.0.1:8188")
        self.assertEqual(result["version"], "0.3.test")
        self.assertEqual(result["device"], "NVIDIA RTX")
        self.assertEqual(result["diffusion_models"], ["z_image_turbo_bf16.safetensors"])
        self.assertEqual(result["text_encoders"], ["qwen_3_4b.safetensors"])
        self.assertEqual(result["vae_models"], ["ae.safetensors"])
        self.assertEqual(result["missing_nodes"], [])


if __name__ == "__main__":
    unittest.main()
