from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.ui.video_storyboard_settings import VideoStoryboardSettingsWidget
from app.core.video_storyboard_styles import storyboard_styles


def _translate(_key: str, default: str, **values: object) -> str:
    return default.format(**values)


class VideoStoryboardSettingsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_runpod_profile_switch_preserves_custom_and_secrets(self) -> None:
        widget = VideoStoryboardSettingsWidget(_translate)
        widget.profile_combo.setCurrentIndex(widget.profile_combo.findData("custom_comfyui"))
        widget.comfyui_workflow_picker.set_path("my-workflow.json")
        widget.comfyui_binding_edits["prompt"].setText("45.text")
        widget.profile_combo.setCurrentIndex(widget.profile_combo.findData("runpod"))
        self.assertFalse(widget.runpod_settings.isHidden())
        self.assertTrue(widget.engine_sections["image"].isHidden())
        widget.runpod_settings.key.setText("private-test-key")
        first = widget.configuration()
        self.assertEqual(first["video_provider"], "runpod")
        self.assertEqual(first["image_edit_provider"], "runpod")
        self.assertNotIn("private-test-key", str(first))
        self.assertEqual(widget.configuration(), first)
        widget.profile_combo.setCurrentIndex(widget.profile_combo.findData("custom_comfyui"))
        self.assertEqual(widget.comfyui_workflow_picker.path_edit.text(), "my-workflow.json")
        self.assertEqual(widget.comfyui_binding_edits["prompt"].text(), "45.text")
        widget.set_configuration(first)
        self.assertEqual(widget.runpod_settings.key.text(), "private-test-key")
        self.assertEqual(widget.configuration(), first)
        widget.deleteLater()

    def test_image_model_and_simplified_timing_controls(self):
        widget = VideoStoryboardSettingsWidget(_translate)
        self.addCleanup(widget.deleteLater)
        self.assertTrue(widget.scene_minimum_spin.isHidden())
        self.assertTrue(widget.scene_target_spin.isHidden())
        self.assertFalse(hasattr(widget, "profile_help"))
        self.assertIn("8 GB", widget.profile_combo.itemText(0))
        self.assertIn("paid", widget.profile_combo.itemText(1))
        self.assertTrue(widget.engine_sections["video"].isAncestorOf(widget.wan_behavior_panel))
        widget.profile_combo.setCurrentIndex(widget.profile_combo.findData("runpod"))
        selector = widget.runpod_settings.image_model
        self.assertEqual(selector.findData("wan-2-6-t2i"), -1)
        self.assertEqual(selector.findData("seedream-v4-t2i"), -1)
        selector.setCurrentIndex(selector.findData("p-image-t2i"))
        self.assertEqual(widget.configuration()["runpod"]["image_endpoint"], "p-image-t2i")
        widget.profile_combo.setCurrentIndex(widget.profile_combo.findData("local"))
        widget.profile_combo.setCurrentIndex(widget.profile_combo.findData("runpod"))
        self.assertEqual(selector.currentData(), "p-image-t2i")
        widget.runpod_settings.fields["image_endpoint"].setText("my-custom-endpoint")
        self.assertEqual(selector.currentData(), "custom")

    def test_custom_image_provider_mapping_roundtrip(self) -> None:
        widget = VideoStoryboardSettingsWidget(_translate)
        self.assertEqual([widget.image_provider_combo.itemText(i) for i in range(3)], ["ComfyUI Z-Image-Turbo", "Custom ComfyUI", "LiteLLM"])
        widget.image_provider_combo.setCurrentIndex(1)
        self.assertFalse(widget.comfyui_custom_mapping.isHidden())
        self.assertTrue(widget.comfyui_diffusion_combo.isHidden())
        widget.comfyui_auth_token_edit.setText("secret")
        widget.comfyui_binding_edits["prompt"].setText("9.text")
        config = widget.configuration()
        widget.set_configuration(config)
        self.assertEqual(widget.configuration(), config)
        self.assertEqual(config["image_provider"], "custom_comfyui")
        self.assertEqual(config["comfyui"]["auth_token"], "secret")
        widget.image_provider_combo.setCurrentIndex(0)
        self.assertTrue(widget.comfyui_custom_mapping.isHidden())
        self.assertFalse(widget.comfyui_auth_token_edit.isHidden())
        widget.image_provider_combo.setCurrentIndex(2)
        self.assertEqual(widget.image_provider_stack.currentIndex(), 1)
        widget.deleteLater()

    def test_runtime_cards_detect_and_select_external_models(self) -> None:
        widget = VideoStoryboardSettingsWidget(_translate)
        self.addCleanup(widget.deleteLater)
        self.assertEqual(widget.image_provider_combo.currentData(), "comfyui")
        self.assertEqual(widget.image_edit_provider_combo.currentData(), "disabled")
        self.assertEqual(widget.image_edit_provider_stack.currentIndex(), 0)
        self.assertEqual(widget.llm_provider_combo.currentData(), "ollama")
        self.assertEqual(widget.litellm_url_edit.text(), "")
        self.assertIn("8 GB VRAM minimum", widget.runtime_description_label.text())
        self.assertIn("HTTP(S)", widget.runtime_description_label.text())
        self.assertFalse(hasattr(widget, "scene_mode_combo"))
        self.assertEqual(widget.configuration()["scene"]["mode"], "semantic_bounded")
        self.assertIn("maximum duration", widget.scene_timing_help_label.text())
        self.assertEqual(widget.image_preset_help_label.parentWidget().title(), "Image preset")
        self.assertIn("resolution", widget.image_preset_help_label.text())
        layout = widget.settings_content.layout()
        self.assertEqual(layout.itemAt(1).widget().title(), "Scene timing")
        self.assertEqual(layout.itemAt(2).widget().title(), "Image preset")
        self.assertEqual(layout.itemAt(3).widget().title(), "Final video output")
        self.assertFalse(hasattr(widget, "image_steps_spin"))
        saved = widget.configuration()
        saved["image"].update(steps=12, cfg=1.7, sampler="euler", scheduler="normal", denoise=0.8, auraflow_shift=2.5)
        widget.set_configuration(saved)
        self.assertEqual(widget.configuration()["image"], saved["image"])
        self.assertFalse(any(
            item.widget() and hasattr(item.widget(), "title")
            and item.widget().title() == "Advanced image settings"
            for item in (layout.itemAt(i) for i in range(layout.count()))
        ))
        workflow_href = widget.comfyui_workflow_help.text().split('href="', 1)[1].split('"', 1)[0]
        self.assertTrue(Path(workflow_href.removeprefix("file:///")).is_file())
        self.assertEqual(widget.litellm_max_output_tokens_spin.value(), 16000)
        self.assertEqual(widget.comfyui_video_url_edit.text(), "http://127.0.0.1:8188")
        self.assertEqual(
            widget.comfyui_video_profile_combo.currentData(),
            "wan22_rapid",
        )
        self.assertEqual(widget.comfyui_video_profile_stack.currentIndex(), 0)
        self.assertTrue(widget.comfyui_video_auth_token_edit.text() == "")
        self.assertEqual(
            widget.comfyui_video_unet_edit.text(),
            "wan2.2-i2v-rapid-aio-v10-Q4_K.gguf",
        )
        self.assertEqual(widget.configuration()["comfyui_video"]["width"], 640)
        self.assertEqual(widget.configuration()["comfyui_video"]["frames"], 49)
        self.assertEqual(widget.comfyui_video_width_spin.value(), 640)
        self.assertEqual(widget.comfyui_video_height_spin.value(), 360)
        widget.comfyui_video_width_spin.setValue(960)
        widget.comfyui_video_height_spin.setValue(540)
        self.assertEqual(widget.configuration()["comfyui_video"]["width"], 960)
        self.assertEqual(widget.configuration()["comfyui_video"]["height"], 540)
        widget.comfyui_video_profile_combo.setCurrentIndex(
            widget.comfyui_video_profile_combo.findData("ltx23_i2v")
        )
        widget.comfyui_video_auth_token_edit.setText("remote-secret")
        self.assertEqual(widget.comfyui_video_profile_stack.currentIndex(), 1)
        self.assertEqual(widget.comfyui_video_width_spin.value(), 1280)
        self.assertEqual(widget.comfyui_video_height_spin.value(), 720)
        self.assertEqual(widget.comfyui_video_fps_spin.value(), 25)
        self.assertEqual(
            widget.configuration()["comfyui_video"]["workflow_profile"],
            "ltx23_i2v",
        )
        self.assertEqual(widget.configuration()["comfyui_video"]["fps"], 25)
        self.assertEqual(
            widget.configuration()["comfyui_video"]["auth_token"],
            "remote-secret",
        )
        widget.comfyui_video_profile_combo.setCurrentIndex(
            widget.comfyui_video_profile_combo.findData("custom")
        )
        widget.comfyui_video_binding_edits["prompt"].setText("12.text")
        self.assertEqual(widget.comfyui_video_profile_stack.currentIndex(), 2)
        self.assertEqual(
            widget.configuration()["comfyui_video"]["bindings"]["prompt"],
            "12.text",
        )
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
        widget.image_edit_provider_combo.setCurrentIndex(
            widget.image_edit_provider_combo.findData("comfyui")
        )
        self.assertEqual(widget.image_edit_provider_stack.currentIndex(), 1)
        self.assertEqual(
            widget.configuration()["comfyui_image_edit"]["unet_model"],
            "Qwen-Image-Edit-2509-Q3_K_S.gguf",
        )
        self.assertEqual(
            widget.configuration()["comfyui_image_edit"]["width"], 1280
        )
        widget.image_edit_provider_combo.setCurrentIndex(
            widget.image_edit_provider_combo.findData("litellm_image")
        )
        self.assertEqual(widget.image_edit_provider_stack.currentIndex(), 2)
        widget.litellm_image_edit_model_combo.setCurrentIndex(
            widget.litellm_image_edit_model_combo.findData("openai/gpt-image-1")
        )
        self.assertEqual(
            widget.configuration()["litellm_image_edit"]["model"],
            "openai/gpt-image-1",
        )
        self.assertFalse(hasattr(widget, "custom_image_url_edit"))
        self.assertIn("Leave empty", widget.litellm_url_edit.placeholderText())
        self.assertFalse(hasattr(widget, "local_image_install_button"))
        self.assertFalse(hasattr(widget, "local_qwen_install_button"))
        self.assertEqual(widget._style_mode_value(), "comic_book")
        self.assertEqual(widget.style_mode_list.count(), len(storyboard_styles()) + 1)
        self.assertGreaterEqual(
            widget.style_mode_list.height(),
            widget.style_mode_list.sizeHintForRow(0) * 10,
        )
        self.assertEqual(len(storyboard_styles()), 50)
        self.assertFalse(widget.style_preview_label.pixmap().isNull())
        self.assertEqual(widget.style_preview_label.pixmap().size().width(), 256)
        self.assertTrue(any(
            widget.style_mode_list.item(index).data(Qt.ItemDataRole.UserRole)
            == "oil_painting"
            for index in range(widget.style_mode_list.count())
        ))
        self.assertTrue(any(
            widget.style_mode_list.item(index).data(Qt.ItemDataRole.UserRole)
            == "childrens_book_illustration"
            for index in range(widget.style_mode_list.count())
        ))
        self.assertFalse(widget.style_prompt_edit.isEnabled())
        self.assertTrue(widget.style_prompt_edit.isHidden())
        self.assertTrue(widget.style_prompt_label.isHidden())

        widget._select_style_mode("cinematic_realism")
        self.assertEqual(
            widget.configuration()["image"]["style_mode"],
            "cinematic_realism",
        )
        widget._select_style_mode("custom")
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
