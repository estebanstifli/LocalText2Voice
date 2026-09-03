from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.ui.video_storyboard_settings import VideoStoryboardSettingsWidget
from app.core.video_storyboard_styles import storyboard_styles


def _translate(_key: str, default: str, **values: object) -> str:
    return default.format(**values)


class VideoStoryboardSettingsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_runtime_cards_detect_and_select_external_models(self) -> None:
        widget = VideoStoryboardSettingsWidget(_translate)
        self.addCleanup(widget.deleteLater)
        self.assertEqual(widget.image_provider_combo.currentData(), "comfyui")
        self.assertEqual(widget.llm_provider_combo.currentData(), "ollama")
        self.assertEqual(widget.litellm_url_edit.text(), "")
        self.assertIn("8 GB VRAM minimum", widget.runtime_description_label.text())
        self.assertIn("HTTP(S)", widget.runtime_description_label.text())
        self.assertFalse(hasattr(widget, "scene_mode_combo"))
        self.assertEqual(widget.configuration()["scene"]["mode"], "semantic_bounded")
        self.assertIn("semantic meaning", widget.scene_timing_help_label.text())
        self.assertEqual(widget.image_preset_help_label.parentWidget().title(), "Image preset")
        self.assertIn("Z-Image Turbo", widget.image_preset_help_label.text())
        workflow_href = widget.comfyui_workflow_help.text().split('href="', 1)[1].split('"', 1)[0]
        self.assertTrue(Path(workflow_href.removeprefix("file:///")).is_file())
        self.assertEqual(widget.litellm_max_output_tokens_spin.value(), 16000)
        self.assertGreaterEqual(
            widget.image_provider_combo.findData("litellm_image"), 0
        )
        self.assertGreaterEqual(
            widget.litellm_model_combo.findData("openai/gpt-5.6-sol"), 0
        )
        self.assertGreaterEqual(
            widget.litellm_model_combo.findData("anthropic/claude-sonnet-5"), 0
        )
        self.assertGreaterEqual(
            widget.litellm_model_combo.findData("gemini/gemini-3.6-flash"), 0
        )
        self.assertGreaterEqual(
            widget.litellm_image_model_combo.findData("openai/gpt-image-2"), 0
        )
        self.assertGreaterEqual(
            widget.litellm_image_model_combo.findData("gemini/gemini-3.1-flash-image"), 0
        )
        self.assertFalse(hasattr(widget, "custom_image_url_edit"))
        self.assertIn("Leave empty", widget.litellm_url_edit.placeholderText())
        self.assertFalse(hasattr(widget, "local_image_install_button"))
        self.assertFalse(hasattr(widget, "local_qwen_install_button"))
        self.assertEqual(widget.style_mode_combo.currentData(), "comic_book")
        self.assertEqual(widget.style_mode_combo.maxVisibleItems(), 10)
        self.assertEqual(widget.style_mode_combo.count(), len(storyboard_styles()) + 1)
        self.assertEqual(len(storyboard_styles()), 50)
        self.assertFalse(widget.style_preview_label.pixmap().isNull())
        self.assertEqual(widget.style_preview_label.pixmap().size().width(), 256)
        self.assertGreaterEqual(widget.style_mode_combo.findData("oil_painting"), 0)
        self.assertGreaterEqual(
            widget.style_mode_combo.findData("childrens_book_illustration"),
            0,
        )
        self.assertFalse(widget.style_prompt_edit.isEnabled())
        self.assertTrue(widget.style_prompt_edit.isHidden())
        self.assertTrue(widget.style_prompt_label.isHidden())

        widget.style_mode_combo.setCurrentIndex(
            widget.style_mode_combo.findData("cinematic_realism")
        )
        self.assertEqual(
            widget.configuration()["image"]["style_mode"],
            "cinematic_realism",
        )
        widget.style_mode_combo.setCurrentIndex(
            widget.style_mode_combo.findData("custom")
        )
        widget.style_prompt_edit.setPlainText("retro-futurist collage")
        self.assertTrue(widget.style_prompt_edit.isEnabled())
        self.assertFalse(widget.style_prompt_edit.isHidden())
        self.assertFalse(widget.style_prompt_label.isHidden())
        widget.litellm_max_output_tokens_spin.setValue(32000)
        widget.image_provider_combo.setCurrentIndex(
            widget.image_provider_combo.findData("litellm_image")
        )
        widget.litellm_image_model_combo.setCurrentIndex(
            widget.litellm_image_model_combo.findData("openai/gpt-image-1")
        )
        self.assertTrue(widget.litellm_image_custom_model_edit.isHidden())
        self.assertEqual(
            widget.configuration()["litellm"]["max_output_tokens"],
            32000,
        )
        self.assertEqual(
            widget.configuration()["litellm_image"]["model"],
            "openai/gpt-image-1",
        )
        widget.litellm_model_combo.setCurrentIndex(
            widget.litellm_model_combo.findData("__custom__")
        )
        widget.litellm_custom_model_edit.setText("openrouter/my-model")
        self.assertFalse(widget.litellm_custom_model_edit.isHidden())
        self.assertEqual(
            widget.configuration()["litellm"]["model"],
            "openrouter/my-model",
        )
        self.assertEqual(widget.style_preview_label.text(), "Custom")
        self.assertEqual(
            widget.configuration()["image"]["style_prompt"],
            "retro-futurist collage",
        )

        widget._on_detection_finished(
            "ollama",
            {
                "models": ["gemma3:4b", "qwen3:8b"],
                "loaded_models": ["qwen3:8b"],
            },
        )
        self.assertGreaterEqual(widget.ollama_model_combo.findText("qwen3:8b"), 0)
        self.assertIn("Recommended model detected", widget.ollama_status_label.text())

        widget._on_detection_finished(
            "comfyui",
            {
                "version": "0.3.test",
                "device": "NVIDIA RTX",
                "vram_total": 8 * 1024**3,
                "diffusion_models": ["z_image_turbo_bf16.safetensors"],
                "text_encoders": ["qwen_3_4b.safetensors"],
                "vae_models": ["ae.safetensors"],
                "missing_nodes": [],
            },
        )
        self.assertIn("Recommended setup detected", widget.comfyui_status_label.text())
        config = widget.configuration()
        self.assertEqual(config["comfyui"]["diffusion_model"], "z_image_turbo_bf16.safetensors")
        self.assertEqual(config["ollama"]["model"], "qwen3:8b")


if __name__ == "__main__":
    unittest.main()
