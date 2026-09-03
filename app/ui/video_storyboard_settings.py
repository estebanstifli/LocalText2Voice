from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from app.core.settings_manager import DEFAULT_SETTINGS
from app.core.video_storyboard_styles import (
    DEFAULT_STORYBOARD_STYLE_ID,
    normalize_storyboard_style_id,
    storyboard_style_sample_path,
    storyboard_styles,
)
from app.tts.model_cache import format_file_size
from app.ui.widgets import FilePicker
from app.workers.video_storyboard_service_worker import VideoStoryboardServiceDetectionWorker


Translate = Callable[..., str]


LITELLM_TEXT_MODELS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("OpenAI", (
        ("GPT-5.6 Sol", "openai/gpt-5.6-sol"),
        ("GPT-5.6 Terra", "openai/gpt-5.6-terra"),
        ("GPT-5.6 Luna", "openai/gpt-5.6-luna"),
        ("GPT-5 mini", "openai/gpt-5-mini"),
    )),
    ("Anthropic", (
        ("Claude Fable 5", "anthropic/claude-fable-5"),
        ("Claude Opus 5", "anthropic/claude-opus-5"),
        ("Claude Sonnet 5", "anthropic/claude-sonnet-5"),
        ("Claude Haiku 4.5", "anthropic/claude-haiku-4-5-20251001"),
    )),
    ("Google Gemini", (
        ("Gemini 3.6 Flash", "gemini/gemini-3.6-flash"),
        ("Gemini 3.5 Flash", "gemini/gemini-3.5-flash"),
        ("Gemini 3.5 Flash-Lite", "gemini/gemini-3.5-flash-lite"),
    )),
)

LITELLM_IMAGE_MODELS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("OpenAI", (
        ("GPT Image 2", "openai/gpt-image-2"),
        ("GPT Image 1", "openai/gpt-image-1"),
        ("GPT Image 1 Mini", "openai/gpt-image-1-mini"),
    )),
    ("Google Gemini", (
        ("Nano Banana 2", "gemini/gemini-3.1-flash-image"),
        ("Nano Banana 2 Lite", "gemini/gemini-3.1-flash-lite-image"),
        ("Nano Banana Pro", "gemini/gemini-3-pro-image"),
    )),
)


class VideoStoryboardSettingsWidget(QWidget):
    """Configuration and runtime discovery for Video Storyboard."""

    settingsChanged = Signal()

    def __init__(self, tr: Translate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self._restoring = False
        self.detection_threads: dict[str, QThread] = {}
        self.detection_workers: dict[str, VideoStoryboardServiceDetectionWorker] = {}
        self._build_ui()
        self._connect_signals()
        self.set_configuration(DEFAULT_SETTINGS["video_storyboard"])

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 24)
        layout.setSpacing(14)

        intro = QFrame()
        intro.setObjectName("card")
        intro_layout = QVBoxLayout(intro)
        title = QLabel(self.tr_text("video_storyboard_settings", "Video Storyboard"))
        title.setObjectName("sectionTitle")
        description = QLabel(self.tr_text(
            "video_storyboard_runtime_description",
            "Use local Ollama and ComfyUI models with an NVIDIA GPU (8 GB VRAM minimum), or connect remote text and image models over HTTP(S) with their API keys. AI models are optional and are not installed by LocalText2Voice.",
        ))
        description.setObjectName("helperLabel")
        description.setWordWrap(True)
        self.runtime_description_label = description
        self.enabled_checkbox = QCheckBox(
            self.tr_text("video_storyboard_enable", "Enable Video Storyboard")
        )
        intro_layout.addWidget(title)
        intro_layout.addWidget(description)
        intro_layout.addWidget(self.enabled_checkbox)
        layout.addWidget(intro)

        image_group = QGroupBox(
            self.tr_text("video_storyboard_image_provider", "Image generation provider")
        )
        image_layout = QVBoxLayout(image_group)
        self.image_provider_combo = QComboBox()
        self.image_provider_combo.addItem("ComfyUI", "comfyui")
        self.image_provider_combo.addItem(
            self.tr_text("video_storyboard_litellm_image", "LiteLLM image generation"),
            "litellm_image",
        )
        image_layout.addWidget(self.image_provider_combo)
        self.image_provider_stack = QStackedWidget()
        self.image_provider_stack.addWidget(self._build_comfyui_settings())
        self.image_provider_stack.addWidget(self._build_litellm_image_settings())
        image_layout.addWidget(self.image_provider_stack)
        layout.addWidget(image_group)

        llm_group = QGroupBox(
            self.tr_text("video_storyboard_llm_provider", "Storyboard LLM provider")
        )
        llm_layout = QVBoxLayout(llm_group)
        self.llm_provider_combo = QComboBox()
        self.llm_provider_combo.addItem("Ollama", "ollama")
        self.llm_provider_combo.addItem("LiteLLM / OpenAI-compatible", "litellm")
        llm_layout.addWidget(self.llm_provider_combo)
        self.llm_provider_stack = QStackedWidget()
        self.llm_provider_stack.addWidget(self._build_ollama_settings())
        self.llm_provider_stack.addWidget(self._build_litellm_settings())
        llm_layout.addWidget(self.llm_provider_stack)
        layout.addWidget(llm_group)

        layout.addWidget(self._build_scene_settings())
        layout.addWidget(self._build_image_preset_settings())
        layout.addWidget(self._build_video_settings())
        layout.addStretch(1)
        scroll.setWidget(content)
        root_layout.addWidget(scroll)

    def _build_comfyui_settings(self) -> QWidget:
        widget = QFrame()
        widget.setObjectName("card")
        form = QFormLayout(widget)
        self.comfyui_url_edit = QLineEdit()
        self.comfyui_detect_button = QPushButton(
            self.tr_text("video_storyboard_detect_models", "Detect service and models")
        )
        self.comfyui_detect_button.clicked.connect(lambda: self._start_detection("comfyui"))
        self.comfyui_status_label = QLabel(
            self.tr_text("video_storyboard_not_checked", "Not checked")
        )
        self.comfyui_status_label.setWordWrap(True)
        self.comfyui_diffusion_combo = self._editable_model_combo()
        self.comfyui_text_encoder_combo = self._editable_model_combo()
        self.comfyui_vae_combo = self._editable_model_combo()
        self.comfyui_workflow_picker = FilePicker(
            self.tr_text("browse", "Browse"),
            self.tr_text("json_files_filter", "JSON files (*.json);;All files (*)"),
        )
        self.comfyui_timeout_spin = self._seconds_spin(30, 7200)
        recommendation = QLabel(self.tr_text(
            "video_storyboard_comfyui_recommendation",
            "Recommended Z-Image Turbo setup: z_image_turbo_bf16.safetensors in diffusion_models, qwen_3_4b.safetensors in text_encoders, and ae.safetensors in vae. ComfyUI should be started with its appropriate VRAM mode.",
        ))
        recommendation.setObjectName("helperLabel")
        recommendation.setWordWrap(True)
        install_help = QLabel(
            '<a href="https://docs.comfy.org/installation/desktop/windows">'
            + self.tr_text("video_storyboard_open_comfyui_install", "ComfyUI installation guide")
            + "</a>"
        )
        install_help.setOpenExternalLinks(True)
        workflow_path = (
            Path(__file__).resolve().parents[2]
            / "assets"
            / "comfyui"
            / "z_image_turbo_api_workflow.json"
        )
        workflow_help = QLabel(
            f'<a href="{workflow_path.as_uri()}">'
            + self.tr_text(
                "video_storyboard_open_z_image_workflow",
                "Open Z-Image Turbo workflow JSON",
            )
            + "</a>"
        )
        workflow_help.setOpenExternalLinks(True)
        self.comfyui_workflow_help = workflow_help
        help_links = QWidget()
        help_links_layout = QHBoxLayout(help_links)
        help_links_layout.setContentsMargins(0, 0, 0, 0)
        help_links_layout.addWidget(install_help)
        help_links_layout.addWidget(QLabel("·"))
        help_links_layout.addWidget(workflow_help)
        help_links_layout.addStretch(1)
        form.addRow("URL", self.comfyui_url_edit)
        form.addRow(self.comfyui_detect_button)
        form.addRow(self.tr_text("status", "Status"), self.comfyui_status_label)
        form.addRow(self.tr_text("video_storyboard_diffusion_model", "Diffusion model"), self.comfyui_diffusion_combo)
        form.addRow(self.tr_text("video_storyboard_text_encoder", "Text encoder"), self.comfyui_text_encoder_combo)
        form.addRow(self.tr_text("video_storyboard_vae", "VAE"), self.comfyui_vae_combo)
        form.addRow(self.tr_text("video_storyboard_workflow", "Workflow JSON (optional)"), self.comfyui_workflow_picker)
        form.addRow(self.tr_text("timeout", "Timeout"), self.comfyui_timeout_spin)
        form.addRow(recommendation)
        form.addRow(help_links)
        return widget

    def _build_litellm_image_settings(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.litellm_image_url_edit = QLineEdit()
        self.litellm_image_url_edit.setPlaceholderText(
            self.tr_text(
                "video_storyboard_litellm_url_hint",
                "Leave empty for a direct provider; set only for a LiteLLM proxy.",
            )
        )
        self.litellm_image_model_combo = self._litellm_model_combo(
            LITELLM_IMAGE_MODELS
        )
        self.litellm_image_custom_model_label = QLabel(
            self.tr_text("custom", "Custom model")
        )
        self.litellm_image_custom_model_edit = QLineEdit()
        self.litellm_image_custom_model_edit.setPlaceholderText(
            "provider/model-name"
        )
        self.litellm_image_api_key_edit = QLineEdit()
        self.litellm_image_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.litellm_image_timeout_spin = self._seconds_spin(10, 3600)
        form.addRow(self.tr_text("video_storyboard_optional_url", "URL (optional)"), self.litellm_image_url_edit)
        form.addRow(self.tr_text("video_storyboard_model", "Model"), self.litellm_image_model_combo)
        form.addRow(self.litellm_image_custom_model_label, self.litellm_image_custom_model_edit)
        form.addRow(self.tr_text("api_key", "API key"), self.litellm_image_api_key_edit)
        form.addRow(self.tr_text("timeout", "Timeout"), self.litellm_image_timeout_spin)
        return widget

    def _build_ollama_settings(self) -> QWidget:
        widget = QFrame()
        widget.setObjectName("card")
        form = QFormLayout(widget)
        self.ollama_url_edit = QLineEdit()
        self.ollama_detect_button = QPushButton(
            self.tr_text("video_storyboard_detect_models", "Detect service and models")
        )
        self.ollama_detect_button.clicked.connect(lambda: self._start_detection("ollama"))
        self.ollama_status_label = QLabel(
            self.tr_text("video_storyboard_not_checked", "Not checked")
        )
        self.ollama_status_label.setWordWrap(True)
        self.ollama_model_combo = self._editable_model_combo()
        self.ollama_context_spin = QSpinBox()
        self.ollama_context_spin.setRange(2048, 131072)
        self.ollama_context_spin.setSingleStep(2048)
        self.ollama_timeout_spin = self._seconds_spin(10, 3600)
        recommendation = QLabel(self.tr_text(
            "video_storyboard_ollama_recommendation",
            "Recommended for an 8 GB GPU: qwen3:8b. Install it from Ollama with: ollama pull qwen3:8b",
        ))
        recommendation.setObjectName("helperLabel")
        recommendation.setWordWrap(True)
        install_help = QLabel(
            '<a href="https://ollama.com/download">'
            + self.tr_text("video_storyboard_open_ollama_install", "Download Ollama")
            + "</a>"
        )
        install_help.setOpenExternalLinks(True)
        form.addRow("URL", self.ollama_url_edit)
        form.addRow(self.ollama_detect_button)
        form.addRow(self.tr_text("status", "Status"), self.ollama_status_label)
        form.addRow(self.tr_text("video_storyboard_model", "Model"), self.ollama_model_combo)
        form.addRow(self.tr_text("video_storyboard_context", "Context length"), self.ollama_context_spin)
        form.addRow(self.tr_text("timeout", "Timeout"), self.ollama_timeout_spin)
        form.addRow(recommendation)
        form.addRow(install_help)
        return widget

    def _build_litellm_settings(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.litellm_url_edit = QLineEdit()
        self.litellm_url_edit.setPlaceholderText(
            self.tr_text(
                "video_storyboard_litellm_url_hint",
                "Leave empty for direct OpenAI, Claude, Gemini, etc.; set only for a proxy or custom endpoint.",
            )
        )
        self.litellm_model_combo = self._litellm_model_combo(
            LITELLM_TEXT_MODELS
        )
        self.litellm_custom_model_label = QLabel(
            self.tr_text("custom", "Custom model")
        )
        self.litellm_custom_model_edit = QLineEdit()
        self.litellm_custom_model_edit.setPlaceholderText("provider/model-name")
        self.litellm_api_key_edit = QLineEdit()
        self.litellm_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.litellm_timeout_spin = self._seconds_spin(10, 3600)
        self.litellm_max_output_tokens_spin = QSpinBox()
        self.litellm_max_output_tokens_spin.setRange(512, 131072)
        self.litellm_max_output_tokens_spin.setSingleStep(1000)
        self.litellm_max_output_tokens_spin.setValue(16000)
        self.litellm_max_output_tokens_spin.setSuffix(" tokens")
        self.litellm_max_output_tokens_spin.setToolTip(
            self.tr_text(
                "video_storyboard_max_output_tokens_hint",
                "Maximum response size for each analysis block. Increase it if the provider returns finish_reason: length.",
            )
        )
        form.addRow(
            self.tr_text("video_storyboard_optional_url", "URL (optional)"),
            self.litellm_url_edit,
        )
        form.addRow(self.tr_text("video_storyboard_model", "Model"), self.litellm_model_combo)
        form.addRow(self.litellm_custom_model_label, self.litellm_custom_model_edit)
        form.addRow(self.tr_text("api_key", "API key"), self.litellm_api_key_edit)
        form.addRow(
            self.tr_text("video_storyboard_max_output_tokens", "Maximum output tokens"),
            self.litellm_max_output_tokens_spin,
        )
        form.addRow(self.tr_text("timeout", "Timeout"), self.litellm_timeout_spin)
        return widget

    def _build_scene_settings(self) -> QGroupBox:
        group = QGroupBox(self.tr_text("video_storyboard_scene_timing", "Scene timing"))
        form = QFormLayout(group)
        explanation = QLabel(
            self.tr_text(
                "video_storyboard_scene_timing_help",
                "The narration is divided by semantic meaning; these limits control the preferred duration of each visual scene.",
            )
        )
        explanation.setObjectName("helperLabel")
        explanation.setWordWrap(True)
        self.scene_timing_help_label = explanation
        self.scene_minimum_spin = self._seconds_spin(4, 60)
        self.scene_target_spin = self._seconds_spin(4, 60)
        self.scene_maximum_spin = self._seconds_spin(4, 60)
        form.addRow(explanation)
        form.addRow(self.tr_text("video_storyboard_minimum", "Minimum scene"), self.scene_minimum_spin)
        form.addRow(self.tr_text("video_storyboard_target", "Target scene"), self.scene_target_spin)
        form.addRow(self.tr_text("video_storyboard_maximum", "Maximum scene"), self.scene_maximum_spin)
        return group

    def _build_image_preset_settings(self) -> QGroupBox:
        group = QGroupBox(self.tr_text("video_storyboard_image_preset", "Image preset"))
        form = QFormLayout(group)
        preset_hint = QLabel(
            self.tr_text(
                "video_storyboard_image_preset_help",
                "The default values are recommended for the built-in Z-Image Turbo workflow.",
            )
        )
        preset_hint.setObjectName("helperLabel")
        preset_hint.setWordWrap(True)
        self.image_preset_help_label = preset_hint
        form.addRow(preset_hint)
        resolution_row = QWidget()
        resolution_layout = QHBoxLayout(resolution_row)
        resolution_layout.setContentsMargins(0, 0, 0, 0)
        self.image_width_spin = QSpinBox()
        self.image_width_spin.setRange(256, 4096)
        self.image_width_spin.setSingleStep(64)
        self.image_height_spin = QSpinBox()
        self.image_height_spin.setRange(256, 4096)
        self.image_height_spin.setSingleStep(64)
        resolution_layout.addWidget(self.image_width_spin)
        resolution_layout.addWidget(QLabel("×"))
        resolution_layout.addWidget(self.image_height_spin)
        resolution_layout.addStretch(1)
        self.image_steps_spin = QSpinBox()
        self.image_steps_spin.setRange(1, 50)
        self.image_cfg_spin = self._decimal_spin(0.0, 20.0, 0.1)
        self.image_denoise_spin = self._decimal_spin(0.0, 1.0, 0.05)
        self.image_shift_spin = self._decimal_spin(0.0, 20.0, 0.1)
        self.image_sampler_edit = QLineEdit()
        self.image_scheduler_edit = QLineEdit()
        self.style_mode_combo = QComboBox()
        self.style_mode_combo.setMaxVisibleItems(10)
        self.style_mode_combo.setMinimumWidth(260)
        for style in storyboard_styles():
            self.style_mode_combo.addItem(style.name, style.id)
        self.style_mode_combo.addItem(self.tr_text("custom", "Custom"), "custom")
        style_picker = QWidget()
        style_picker_layout = QHBoxLayout(style_picker)
        style_picker_layout.setContentsMargins(0, 0, 0, 0)
        style_picker_layout.setSpacing(16)
        style_picker_layout.addWidget(self.style_mode_combo, 1, Qt.AlignmentFlag.AlignTop)
        self.style_preview_label = QLabel()
        self.style_preview_label.setObjectName("storyboardStylePreview")
        self.style_preview_label.setFixedSize(256, 256)
        self.style_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.style_preview_label.setStyleSheet(
            "QLabel#storyboardStylePreview { border: 1px solid palette(mid); "
            "border-radius: 8px; background: palette(base); }"
        )
        style_picker_layout.addWidget(self.style_preview_label)
        self.style_prompt_edit = QPlainTextEdit()
        self.style_prompt_edit.setMaximumHeight(90)
        self.style_prompt_label = QLabel(
            self.tr_text(
                "video_storyboard_style_custom",
                "Custom style instructions",
            )
        )
        form.addRow(self.tr_text("video_storyboard_resolution", "Resolution"), resolution_row)
        form.addRow(self.tr_text("video_storyboard_batch", "Batch"), QLabel("1"))
        form.addRow(self.tr_text("video_storyboard_steps", "Sampling steps"), self.image_steps_spin)
        form.addRow("CFG", self.image_cfg_spin)
        form.addRow(self.tr_text("video_storyboard_sampler", "Sampler"), self.image_sampler_edit)
        form.addRow(self.tr_text("video_storyboard_scheduler", "Scheduler"), self.image_scheduler_edit)
        form.addRow("Denoise", self.image_denoise_spin)
        form.addRow("AuraFlow shift", self.image_shift_spin)
        form.addRow(self.tr_text("video_storyboard_style", "Default visual style"), style_picker)
        form.addRow(self.style_prompt_label, self.style_prompt_edit)
        seed_note = QLabel(self.tr_text(
            "video_storyboard_seed_note",
            "The seed is locked and stored per audiobook so regenerations remain reproducible.",
        ))
        seed_note.setObjectName("helperLabel")
        seed_note.setWordWrap(True)
        form.addRow(seed_note)
        return group

    def _build_video_settings(self) -> QGroupBox:
        group = QGroupBox(self.tr_text("video_storyboard_video_output", "Video output"))
        form = QFormLayout(group)
        self.video_fps_spin = QSpinBox()
        self.video_fps_spin.setRange(12, 60)
        self.video_transition_combo = QComboBox()
        self.video_transition_combo.addItem("Fade", "fade")
        self.video_transition_combo.addItem("Dissolve", "dissolve")
        self.video_transition_combo.addItem("Wipe left", "wipeleft")
        self.video_transition_combo.addItem("Wipe right", "wiperight")
        self.video_transition_combo.addItem("Smooth left", "smoothleft")
        self.video_transition_combo.addItem("Smooth right", "smoothright")
        self.video_transition_combo.addItem("Circle open", "circleopen")
        self.video_transition_combo.addItem("Circle close", "circleclose")
        self.video_transition_seconds_spin = self._decimal_spin(0.0, 3.0, 0.05)
        self.video_transition_seconds_spin.setSuffix(" s")
        self.video_zoom_spin = self._decimal_spin(0.0, 1_000_000.0, 0.5)
        self.video_zoom_spin.setSuffix(" %")
        self.video_crf_spin = QSpinBox()
        self.video_crf_spin.setRange(0, 51)
        form.addRow("FPS", self.video_fps_spin)
        form.addRow(self.tr_text("video_storyboard_transition", "Transition"), self.video_transition_combo)
        form.addRow(self.tr_text("video_storyboard_transition_duration", "Transition duration"), self.video_transition_seconds_spin)
        form.addRow(self.tr_text("video_storyboard_zoom", "Soft zoom"), self.video_zoom_spin)
        form.addRow("Codec", QLabel("H.264 / libx264"))
        form.addRow("CRF", self.video_crf_spin)
        return group

    def _connect_signals(self) -> None:
        self.image_provider_combo.currentIndexChanged.connect(self.image_provider_stack.setCurrentIndex)
        self.llm_provider_combo.currentIndexChanged.connect(self.llm_provider_stack.setCurrentIndex)
        self.enabled_checkbox.toggled.connect(self._emit_changed)
        for combo in (self.image_provider_combo, self.llm_provider_combo, self.video_transition_combo, self.style_mode_combo):
            combo.currentIndexChanged.connect(self._emit_changed)
        self.style_mode_combo.currentIndexChanged.connect(self._sync_style_mode)
        self.litellm_model_combo.currentIndexChanged.connect(
            lambda: self._sync_litellm_custom_model(
                self.litellm_model_combo,
                self.litellm_custom_model_edit,
                self.litellm_custom_model_label,
            )
        )
        self.litellm_image_model_combo.currentIndexChanged.connect(
            lambda: self._sync_litellm_custom_model(
                self.litellm_image_model_combo,
                self.litellm_image_custom_model_edit,
                self.litellm_image_custom_model_label,
            )
        )
        self.litellm_model_combo.currentIndexChanged.connect(self._emit_changed)
        self.litellm_image_model_combo.currentIndexChanged.connect(self._emit_changed)
        for combo in (self.comfyui_diffusion_combo, self.comfyui_text_encoder_combo, self.comfyui_vae_combo, self.ollama_model_combo):
            combo.currentTextChanged.connect(self._emit_changed)
        for spin in (
            self.comfyui_timeout_spin, self.litellm_image_timeout_spin,
            self.ollama_context_spin, self.ollama_timeout_spin,
            self.litellm_timeout_spin, self.litellm_max_output_tokens_spin,
            self.scene_minimum_spin, self.scene_target_spin, self.scene_maximum_spin,
            self.image_width_spin, self.image_height_spin, self.image_steps_spin,
            self.image_cfg_spin, self.image_denoise_spin, self.image_shift_spin,
            self.video_fps_spin, self.video_transition_seconds_spin,
            self.video_zoom_spin, self.video_crf_spin,
        ):
            spin.valueChanged.connect(self._emit_changed)
        for edit in (
            self.comfyui_url_edit, self.litellm_image_url_edit,
            self.litellm_image_custom_model_edit, self.litellm_image_api_key_edit,
            self.ollama_url_edit,
            self.litellm_url_edit, self.litellm_custom_model_edit,
            self.litellm_api_key_edit, self.image_sampler_edit,
            self.image_scheduler_edit,
        ):
            edit.textChanged.connect(self._emit_changed)
        self.comfyui_workflow_picker.path_changed.connect(self._emit_changed)
        self.style_prompt_edit.textChanged.connect(self._emit_changed)

    def set_configuration(self, value: dict[str, Any] | None) -> None:
        config = self._merge(deepcopy(DEFAULT_SETTINGS["video_storyboard"]), value if isinstance(value, dict) else {})
        self._restoring = True
        try:
            self.enabled_checkbox.setChecked(bool(config.get("enabled", False)))
            self._select(self.image_provider_combo, config.get("image_provider"))
            comfyui = config["comfyui"]
            self.comfyui_url_edit.setText(str(comfyui.get("base_url", "")))
            self._set_combo_text(self.comfyui_diffusion_combo, comfyui.get("diffusion_model"))
            self._set_combo_text(self.comfyui_text_encoder_combo, comfyui.get("text_encoder"))
            self._set_combo_text(self.comfyui_vae_combo, comfyui.get("vae_model"))
            self.comfyui_workflow_picker.set_path(comfyui.get("workflow_path", ""))
            self.comfyui_timeout_spin.setValue(int(comfyui.get("timeout_seconds", 900)))
            image_litellm = config["litellm_image"]
            self.litellm_image_url_edit.setText(str(image_litellm.get("base_url", "")))
            self._set_litellm_model(
                self.litellm_image_model_combo,
                self.litellm_image_custom_model_edit,
                self.litellm_image_custom_model_label,
                image_litellm.get("model", ""),
            )
            self.litellm_image_api_key_edit.setText(str(image_litellm.get("api_key", "")))
            self.litellm_image_timeout_spin.setValue(int(image_litellm.get("timeout_seconds", 300)))
            self._select(self.llm_provider_combo, config.get("llm_provider"))
            ollama = config["ollama"]
            self.ollama_url_edit.setText(str(ollama.get("base_url", "")))
            self._set_combo_text(self.ollama_model_combo, ollama.get("model"))
            self.ollama_context_spin.setValue(int(ollama.get("context_length", 8192)))
            self.ollama_timeout_spin.setValue(int(ollama.get("timeout_seconds", 300)))
            litellm = config["litellm"]
            self.litellm_url_edit.setText(str(litellm.get("base_url", "")))
            self._set_litellm_model(
                self.litellm_model_combo,
                self.litellm_custom_model_edit,
                self.litellm_custom_model_label,
                litellm.get("model", ""),
            )
            self.litellm_api_key_edit.setText(str(litellm.get("api_key", "")))
            self.litellm_timeout_spin.setValue(int(litellm.get("timeout_seconds", 300)))
            self.litellm_max_output_tokens_spin.setValue(
                int(litellm.get("max_output_tokens", 16000))
            )
            scene = config["scene"]
            self.scene_minimum_spin.setValue(int(scene.get("minimum_seconds", 4)))
            self.scene_target_spin.setValue(int(scene.get("target_seconds", 8)))
            self.scene_maximum_spin.setValue(int(scene.get("maximum_seconds", 20)))
            image = config["image"]
            self.image_width_spin.setValue(int(image.get("width", 1280)))
            self.image_height_spin.setValue(int(image.get("height", 720)))
            self.image_steps_spin.setValue(int(image.get("steps", 8)))
            self.image_cfg_spin.setValue(float(image.get("cfg", 1.0)))
            self.image_sampler_edit.setText(str(image.get("sampler", "res_multistep")))
            self.image_scheduler_edit.setText(str(image.get("scheduler", "simple")))
            self.image_denoise_spin.setValue(float(image.get("denoise", 1.0)))
            self.image_shift_spin.setValue(float(image.get("auraflow_shift", 3.0)))
            style_mode = normalize_storyboard_style_id(
                image.get("style_mode") or DEFAULT_STORYBOARD_STYLE_ID
            )
            self._select(self.style_mode_combo, style_mode)
            self.style_prompt_edit.setPlainText(str(image.get("style_prompt", "")))
            self._sync_style_mode()
            video = config["video"]
            self.video_fps_spin.setValue(int(video.get("fps", 30)))
            self._select(self.video_transition_combo, video.get("transition"))
            self.video_transition_seconds_spin.setValue(float(video.get("transition_seconds", 0.7)))
            self.video_zoom_spin.setValue(float(video.get("zoom_percent", 30.0)))
            self.video_crf_spin.setValue(int(video.get("crf", 18)))
        finally:
            self._restoring = False

    def configuration(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled_checkbox.isChecked(),
            "image_provider": self.image_provider_combo.currentData() or "comfyui",
            "llm_provider": self.llm_provider_combo.currentData() or "ollama",
            "scene": {
                "mode": "semantic_bounded",
                "minimum_seconds": self.scene_minimum_spin.value(),
                "target_seconds": self.scene_target_spin.value(),
                "maximum_seconds": self.scene_maximum_spin.value(),
            },
            "image": {
                "width": self.image_width_spin.value(), "height": self.image_height_spin.value(),
                "batch_size": 1, "steps": self.image_steps_spin.value(),
                "cfg": self.image_cfg_spin.value(),
                "sampler": self.image_sampler_edit.text().strip() or "res_multistep",
                "scheduler": self.image_scheduler_edit.text().strip() or "simple",
                "denoise": self.image_denoise_spin.value(),
                "auraflow_shift": self.image_shift_spin.value(),
                "style_mode": self.style_mode_combo.currentData() or DEFAULT_STORYBOARD_STYLE_ID,
                "style_prompt": (
                    self.style_prompt_edit.toPlainText().strip()
                    if self.style_mode_combo.currentData() == "custom"
                    else ""
                ),
                "seed_mode": "audiobook_locked",
            },
            "comfyui": {
                "base_url": self.comfyui_url_edit.text().strip(),
                "workflow_path": self.comfyui_workflow_picker.path_edit.text().strip(),
                "diffusion_model": self.comfyui_diffusion_combo.currentText().strip(),
                "text_encoder": self.comfyui_text_encoder_combo.currentText().strip(),
                "vae_model": self.comfyui_vae_combo.currentText().strip(),
                "timeout_seconds": self.comfyui_timeout_spin.value(),
            },
            "litellm_image": {
                "base_url": self.litellm_image_url_edit.text().strip(),
                "model": self._litellm_model_value(
                    self.litellm_image_model_combo,
                    self.litellm_image_custom_model_edit,
                ),
                "api_key": self.litellm_image_api_key_edit.text().strip(),
                "timeout_seconds": self.litellm_image_timeout_spin.value(),
            },
            "ollama": {
                "base_url": self.ollama_url_edit.text().strip(),
                "model": self.ollama_model_combo.currentText().strip(),
                "context_length": self.ollama_context_spin.value(),
                "timeout_seconds": self.ollama_timeout_spin.value(),
            },
            "litellm": {
                "base_url": self.litellm_url_edit.text().strip(),
                "model": self._litellm_model_value(
                    self.litellm_model_combo,
                    self.litellm_custom_model_edit,
                ),
                "api_key": self.litellm_api_key_edit.text().strip(),
                "timeout_seconds": self.litellm_timeout_spin.value(),
                "max_output_tokens": self.litellm_max_output_tokens_spin.value(),
            },
            "video": {
                "fps": self.video_fps_spin.value(),
                "transition": self.video_transition_combo.currentData() or "fade",
                "transition_seconds": self.video_transition_seconds_spin.value(),
                "zoom_percent": self.video_zoom_spin.value(),
                "supersample": 4, "preset": "medium",
                "codec": "libx264", "crf": self.video_crf_spin.value(),
            },
        }

    def _sync_style_mode(self, *_args: object) -> None:
        is_custom = self.style_mode_combo.currentData() == "custom"
        self.style_prompt_edit.setEnabled(is_custom)
        self.style_prompt_edit.setVisible(is_custom)
        self.style_prompt_label.setVisible(is_custom)
        sample_path = storyboard_style_sample_path(
            self.style_mode_combo.currentData()
        )
        if sample_path is None:
            self.style_preview_label.clear()
            self.style_preview_label.setText(
                self.tr_text("custom", "Custom")
            )
            return
        pixmap = QPixmap(str(sample_path))
        self.style_preview_label.setText("")
        self.style_preview_label.setPixmap(
            pixmap.scaled(
                256,
                256,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _start_detection(self, service: str) -> None:
        if service in self.detection_threads:
            return
        url = self.ollama_url_edit.text().strip() if service == "ollama" else self.comfyui_url_edit.text().strip()
        button, status = self._service_widgets(service)
        button.setEnabled(False)
        status.setText(self.tr_text("video_storyboard_checking", "Checking connection and models..."))
        thread = QThread(self)
        worker = VideoStoryboardServiceDetectionWorker(service, url)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_detection_finished)
        worker.failed.connect(self._on_detection_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda selected=service: self._clear_detection(selected))
        self.detection_threads[service] = thread
        self.detection_workers[service] = worker
        thread.start()

    def _on_detection_finished(self, service: str, raw_result: object) -> None:
        result = raw_result if isinstance(raw_result, dict) else {}
        if service == "ollama":
            models = [str(value) for value in result.get("models", [])]
            selected = self.ollama_model_combo.currentText().strip()
            self._replace_combo_choices(self.ollama_model_combo, models, selected)
            loaded = [str(value) for value in result.get("loaded_models", [])]
            recommended_found = any(value.casefold() == "qwen3:8b" for value in models)
            suffix = self.tr_text(
                "video_storyboard_ollama_recommended_ready" if recommended_found else "video_storyboard_ollama_recommended_missing",
                "Recommended model detected." if recommended_found else "Recommended model missing; run: ollama pull qwen3:8b",
            )
            self.ollama_status_label.setText(self.tr_text(
                "video_storyboard_ollama_detected",
                "Connected · {count} model(s) · loaded: {loaded}. {suffix}",
                count=len(models), loaded=", ".join(loaded) if loaded else self.tr_text("none_option", "None"), suffix=suffix,
            ))
        elif service == "comfyui":
            for combo, key in (
                (self.comfyui_diffusion_combo, "diffusion_models"),
                (self.comfyui_text_encoder_combo, "text_encoders"),
                (self.comfyui_vae_combo, "vae_models"),
            ):
                self._replace_combo_choices(combo, [str(value) for value in result.get(key, [])], combo.currentText().strip())
            missing_models = self._missing_comfyui_recommendations(result)
            missing_nodes = [str(value) for value in result.get("missing_nodes", [])]
            vram = int(result.get("vram_total", 0) or 0)
            details = self.tr_text(
                "video_storyboard_comfyui_detected",
                "Connected · {version} · {device} · VRAM {vram}.",
                version=result.get("version", "unknown"), device=result.get("device", "unknown"),
                vram=format_file_size(vram) if vram else "unknown",
            )
            if missing_models:
                details += " " + self.tr_text("video_storyboard_comfyui_models_missing", "Recommended model files missing: {models}.", models=", ".join(missing_models))
            elif missing_nodes:
                details += " " + self.tr_text("video_storyboard_comfyui_nodes_missing", "Required nodes missing: {nodes}.", nodes=", ".join(missing_nodes))
            else:
                details += " " + self.tr_text("video_storyboard_comfyui_ready", "Recommended setup detected.")
            self.comfyui_status_label.setText(details)

    def _on_detection_failed(self, service: str, message: str) -> None:
        _button, status = self._service_widgets(service)
        status.setText(self.tr_text(
            "video_storyboard_service_unavailable",
            "{service} is not reachable. Install/start it and check the URL. {error}",
            service="Ollama" if service == "ollama" else "ComfyUI", error=message,
        ))

    def _clear_detection(self, service: str) -> None:
        self.detection_workers.pop(service, None)
        self.detection_threads.pop(service, None)
        self._service_widgets(service)[0].setEnabled(True)

    def detection_active(self) -> bool:
        return bool(self.detection_threads)

    def _service_widgets(self, service: str) -> tuple[QPushButton, QLabel]:
        return (self.ollama_detect_button, self.ollama_status_label) if service == "ollama" else (self.comfyui_detect_button, self.comfyui_status_label)

    @staticmethod
    def _missing_comfyui_recommendations(result: dict[str, Any]) -> list[str]:
        expected = (("diffusion_models", "z_image_turbo_bf16.safetensors"), ("text_encoders", "qwen_3_4b.safetensors"), ("vae_models", "ae.safetensors"))
        return [filename for key, filename in expected if filename not in {str(value) for value in result.get(key, [])}]

    def _emit_changed(self, *_args: object) -> None:
        if not self._restoring:
            self.settingsChanged.emit()

    @staticmethod
    def _editable_model_combo() -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        return combo

    def _litellm_model_combo(
        self,
        groups: tuple[tuple[str, tuple[tuple[str, str], ...]], ...],
    ) -> QComboBox:
        combo = QComboBox()
        combo.setMaxVisibleItems(16)
        for provider, models in groups:
            combo.addItem(f"— {provider} —", None)
            header = combo.model().item(combo.count() - 1)
            if header is not None:
                header.setEnabled(False)
            for label, model_id in models:
                combo.addItem(f"{label}  ·  {model_id}", model_id)
        combo.insertSeparator(combo.count())
        combo.addItem(self.tr_text("custom", "Custom"), "__custom__")
        return combo

    @staticmethod
    def _sync_litellm_custom_model(
        combo: QComboBox,
        edit: QLineEdit,
        label: QLabel,
    ) -> None:
        custom = combo.currentData() == "__custom__"
        edit.setVisible(custom)
        edit.setEnabled(custom)
        label.setVisible(custom)

    def _set_litellm_model(
        self,
        combo: QComboBox,
        edit: QLineEdit,
        label: QLabel,
        value: object,
    ) -> None:
        model = str(value or "").strip()
        index = combo.findData(model) if model else -1
        if index >= 0:
            combo.setCurrentIndex(index)
            edit.clear()
        else:
            custom_index = combo.findData("__custom__")
            combo.setCurrentIndex(custom_index)
            edit.setText(model)
        self._sync_litellm_custom_model(combo, edit, label)

    @staticmethod
    def _litellm_model_value(combo: QComboBox, edit: QLineEdit) -> str:
        if combo.currentData() == "__custom__":
            return edit.text().strip()
        return str(combo.currentData() or "").strip()

    @staticmethod
    def _replace_combo_choices(combo: QComboBox, values: list[str], selected: str) -> None:
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(values)
            if selected and combo.findText(selected) < 0:
                combo.addItem(selected)
            combo.setCurrentText(selected or (values[0] if values else ""))
        finally:
            combo.blockSignals(False)

    @staticmethod
    def _set_combo_text(combo: QComboBox, value: object) -> None:
        text = str(value or "").strip()
        if text and combo.findText(text) < 0:
            combo.addItem(text)
        combo.setCurrentText(text)

    @staticmethod
    def _select(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key] = VideoStoryboardSettingsWidget._merge(base[key], value)
            else:
                base[key] = value
        return base

    @staticmethod
    def _seconds_spin(minimum: int, maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSuffix(" s")
        return spin

    @staticmethod
    def _decimal_spin(minimum: float, maximum: float, step: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(2)
        return spin
