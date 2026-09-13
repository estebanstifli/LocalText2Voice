from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer, QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget, QGridLayout,
)

from app.core.settings_manager import DEFAULT_SETTINGS
from app.core.storyboard_profiles import infer_profile, switch_profile
from app.ui.storyboard_runpod_settings import RunpodSettingsWidget
from app.ui.storyboard_workflow_mapping import map_workflow
from app.core.video_storyboard_camera import CAMERA_LORA_MODEL, CAMERA_LORA_URL
from app.core.video_storyboard_video_comfyui import (
    CUSTOM_VIDEO_PROFILE,
    LTX_VIDEO_PROFILE,
    VIDEO_WORKFLOW_BINDING_KEYS,
    VIDEO_WORKFLOW_PROFILES,
    WAN_VIDEO_PROFILE,
    bundled_ltx_video_workflow_path,
    bundled_wan_video_workflow_path,
)
from app.core.video_storyboard_styles import (
    DEFAULT_STORYBOARD_STYLE_ID,
    normalize_storyboard_style_id,
    storyboard_style_sample_path,
    storyboard_styles,
)
from app.tts.model_cache import format_file_size
from app.ui.widgets import FilePicker
from app.workers.video_storyboard_service_worker import VideoStoryboardServiceDetectionWorker
from app.core.video_storyboard_install import ROLES, load_manifest, endpoint_for
from app.ui.video_storyboard_install_dialog import StoryboardInstallDialog
from app.workers.video_storyboard_install_worker import StoryboardInstallWorker
from app.ui.icons import ui_icon


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
    continuitySettingsRequested = Signal()

    def __init__(self, tr: Translate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self._profiles = {}
        self._active_profile = "local"
        self._video_provider = "comfyui"
        self._restoring = False
        self._engine_thread = None
        self._engine_worker = None
        self._engine_dialog = None
        self._engine_role = ""
        self._engine_options = {}
        self._engine_inventory = {}
        self._engine_inventory_key = None
        self._engine_probe_key = None
        self._installation_settings = {}
        self._checked_on_show = False
        self.detection_threads: dict[str, QThread] = {}
        self.detection_workers: dict[str, VideoStoryboardServiceDetectionWorker] = {}
        self._build_ui()
        self._connect_signals()
        self.set_configuration(DEFAULT_SETTINGS["video_storyboard"])

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        self.settings_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.settings_content = content
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 24)
        layout.setSpacing(14)

        intro = QFrame()
        intro.setObjectName("card")
        intro_layout = QVBoxLayout(intro)
        title = QLabel(self.tr_text("video_storyboard_settings", "Video Storyboard (Beta)"))
        title.setObjectName("sectionTitle")
        description = QLabel(self.tr_text(
            "storyboard_engine_runtime_description",
            "Use local Ollama and ComfyUI models with an NVIDIA GPU (8 GB VRAM minimum), or connect remote models over HTTP(S) with their API keys. Default engines and models are optional: download them only when you choose Install.",
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
        profile_form = QFormLayout()
        self.profile_combo = QComboBox()
        for label, value in ((self.tr_text("storyboard_local_profile_gpu", "Local · Default · min. 8 GB GPU VRAM"), "local"), (self.tr_text("storyboard_runpod_profile_paid", "Runpod · Pro · Recommended, paid"), "runpod"), ("Custom ComfyUI", "custom_comfyui"), ("LiteLLM", "litellm")):
            self.profile_combo.addItem(label, value)
        profile_form.addRow(self.tr_text("storyboard_profile", "Generation profile"), self.profile_combo)
        intro_layout.addLayout(profile_form)
        self.runpod_settings = RunpodSettingsWidget(self.tr_text)
        intro_layout.addWidget(self.runpod_settings)
        self.runpod_settings.hide()
        self.visual_advanced = QCheckBox(self.tr_text("storyboard_visual_advanced", "Show visual engine settings"))
        intro_layout.addWidget(self.visual_advanced)
        self.engine_cards = {}
        self.engine_sections = {}
        cards = QGridLayout()
        for column, (role, label, icon) in enumerate((
            ("llm", self.tr_text("storyboard_card_llm", "LLM provider"), "file"),
            ("image", self.tr_text("storyboard_card_image", "Image generation"), "replace_image"),
            ("video", self.tr_text("storyboard_card_video", "Video generation"), "video_track"),
            ("edit", self.tr_text("storyboard_card_edit", "Image editing"), "edit"),
        )):
            frame = QFrame()
            frame.setObjectName("card")
            box = QVBoxLayout(frame)
            heading = QLabel(label)
            heading.setObjectName("sectionTitle")
            box.addWidget(heading)
            model = QLabel()
            model.setTextFormat(Qt.TextFormat.PlainText)
            model.setWordWrap(True)
            model.setMinimumHeight(46)
            box.addWidget(model)
            status = QLabel()
            status.setWordWrap(True)
            status.setTextFormat(Qt.TextFormat.PlainText)
            status.setObjectName("helperLabel")
            box.addWidget(status)
            install = QPushButton()
            install.setIcon(ui_icon("download"))
            install.clicked.connect(lambda _checked=False, selected=role: self._open_engine_install(selected))
            settings = QPushButton(self.tr_text("settings", "Settings"))
            settings.setIcon(ui_icon("settings"))
            settings.clicked.connect(lambda _checked=False, selected=role: self._focus_engine_section(selected))
            box.addWidget(install)
            box.addWidget(settings)
            cards.addWidget(frame, 0, column)
            cards.setColumnStretch(column, 1)
            self.engine_cards[role] = {"model": model, "status": status, "install": install, "settings": settings, "frame": frame}
        self.engine_cards_widget = QWidget()
        self.engine_cards_widget.setLayout(cards)
        intro_layout.addWidget(self.engine_cards_widget)
        self.engine_refresh_button = QPushButton(self.tr_text("storyboard_refresh_engines", "Check engines and installed models"))
        self.engine_refresh_button.setIcon(ui_icon("refresh"))
        self.engine_refresh_button.clicked.connect(self._refresh_engine_inventory)
        intro_layout.addWidget(self.engine_refresh_button)
        layout.addWidget(intro)
        layout.addWidget(self._build_scene_settings())
        layout.addWidget(self._build_image_preset_settings())
        layout.addWidget(self._build_video_settings())

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
        self.continuity_settings_button = QPushButton(
            self.tr_text("continuity_settings_open", "Continuity analysis…")
        )
        self.continuity_settings_button.setIcon(ui_icon("settings"))
        self.continuity_settings_button.setToolTip(self.tr_text(
            "continuity_settings_open_help", "Configure the analysis process, block size and instructions."
        ))
        self.continuity_settings_button.clicked.connect(self.continuitySettingsRequested.emit)
        llm_layout.addWidget(self.continuity_settings_button)
        layout.addWidget(llm_group)
        self.engine_sections["llm"] = llm_group

        image_group = QGroupBox(
            self.tr_text("video_storyboard_image_provider", "Image generation provider")
        )
        image_layout = QVBoxLayout(image_group)
        self.image_provider_combo = QComboBox()
        self.image_provider_combo.addItem("ComfyUI Z-Image-Turbo", "comfyui")
        self.image_provider_combo.addItem("Custom ComfyUI", "custom_comfyui")
        self.image_provider_combo.addItem(
            "LiteLLM",
            "litellm_image",
        )
        self.image_provider_combo.addItem("Runpod", "runpod")
        image_layout.addWidget(self.image_provider_combo)
        self.image_provider_stack = QStackedWidget()
        self.image_provider_stack.addWidget(self._build_comfyui_settings())
        self.image_provider_stack.addWidget(self._build_litellm_image_settings())
        self.image_provider_stack.addWidget(QLabel("Runpod · Z-Image-Turbo"))
        image_layout.addWidget(self.image_provider_stack)
        layout.addWidget(image_group)
        self.engine_sections["image"] = image_group

        self.engine_sections["video"] = self._build_comfyui_video_settings()
        self.engine_sections["edit"] = self._build_image_edit_settings()
        layout.addWidget(self.engine_sections["video"])
        layout.addWidget(self.engine_sections["edit"])

        layout.addStretch(1)
        scroll.setWidget(content)
        root_layout.addWidget(scroll)

    def _build_comfyui_settings(self) -> QWidget:
        widget = QFrame()
        widget.setObjectName("card")
        form = QFormLayout(widget)
        self.comfyui_url_edit = QLineEdit()
        self.comfyui_auth_token_edit = QLineEdit()
        self.comfyui_auth_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.comfyui_form = form
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
        form.addRow(self.tr_text("video_storyboard_bearer_token", "Bearer token (optional)"), self.comfyui_auth_token_edit)
        form.addRow(self.comfyui_detect_button)
        form.addRow(self.tr_text("status", "Status"), self.comfyui_status_label)
        form.addRow(self.tr_text("video_storyboard_diffusion_model", "Diffusion model"), self.comfyui_diffusion_combo)
        form.addRow(self.tr_text("video_storyboard_text_encoder", "Text encoder"), self.comfyui_text_encoder_combo)
        form.addRow(self.tr_text("video_storyboard_vae", "VAE"), self.comfyui_vae_combo)
        form.addRow(self.tr_text("storyboard_custom_image_workflow", "Workflow JSON (API format)"), self.comfyui_workflow_picker)
        form.addRow(self.tr_text("timeout", "Timeout"), self.comfyui_timeout_spin)
        form.addRow(recommendation)
        form.addRow(help_links)
        self.comfyui_default_fields = [self.comfyui_diffusion_combo, self.comfyui_text_encoder_combo, self.comfyui_vae_combo, recommendation, help_links]
        self.comfyui_custom_mapping = QWidget()
        mapping = QFormLayout(self.comfyui_custom_mapping)
        help_label = QLabel(self.tr_text("storyboard_custom_image_mapping_help", "Load a ComfyUI API workflow JSON. Map values as node-id.input-name or use {{PROMPT}}, {{NEGATIVE_PROMPT}}, {{SEED}}, {{WIDTH}}, {{HEIGHT}}, {{STEPS}}, {{CFG}}, {{OUTPUT_PREFIX}}. Blank optional mappings preserve the workflow values."))
        help_label.setWordWrap(True)
        mapping.addRow(help_label)
        self.comfyui_binding_edits = {}
        for name in ("prompt", "negative_prompt", "seed", "width", "height", "steps", "cfg", "output_prefix"):
            edit = QLineEdit()
            edit.setPlaceholderText("node-id.input-name")
            self.comfyui_binding_edits[name] = edit
            mapping.addRow(name.replace("_", " ").capitalize(), edit)
        self.comfyui_output_node_edit = QLineEdit()
        mapping.addRow(self.tr_text("storyboard_output_node", "Output node"), self.comfyui_output_node_edit)
        map_button = QPushButton(self.tr_text("storyboard_map_inputs", "Choose workflow inputs…"))
        map_button.clicked.connect(lambda: map_workflow(self, self.comfyui_workflow_picker, self.comfyui_binding_edits, self.comfyui_output_node_edit, self.tr_text))
        mapping.addRow(map_button)
        form.addRow(self.comfyui_custom_mapping)
        self.comfyui_custom_mapping.hide()
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

    def _build_image_edit_settings(self) -> QGroupBox:
        group = QGroupBox(
            self.tr_text(
                "video_storyboard_image_edit_provider",
                "Image editing and reference provider",
            )
        )
        layout = QVBoxLayout(group)
        help_label = QLabel(
            self.tr_text(
                "video_storyboard_image_edit_help",
                "Optional. Enables natural-language frame edits and generation from 1–3 character/location reference images.",
            )
        )
        help_label.setObjectName("helperLabel")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.image_edit_provider_combo = QComboBox()
        self.image_edit_provider_combo.addItem(
            self.tr_text("disabled", "Disabled"), "disabled"
        )
        self.image_edit_provider_combo.addItem(
            "ComfyUI · Qwen Image Edit 2509", "comfyui"
        )
        self.image_edit_provider_combo.addItem(
            self.tr_text("video_storyboard_litellm_image_edit", "LiteLLM / OpenAI-compatible image editing"),
            "litellm_image",
        )
        layout.addWidget(self.image_edit_provider_combo)
        self.image_edit_provider_combo.addItem("Runpod · Qwen Image Edit 2511", "runpod")
        self.image_edit_provider_stack = QStackedWidget()
        disabled = QLabel(
            self.tr_text(
                "video_storyboard_image_edit_disabled_help",
                "Reference-image and AI edit controls remain available, but generation asks you to configure this provider.",
            )
        )
        disabled.setObjectName("helperLabel")
        disabled.setWordWrap(True)
        self.image_edit_provider_stack.addWidget(disabled)

        comfy = QWidget()
        comfy_layout = QVBoxLayout(comfy)
        comfy_note = QLabel(
            self.tr_text(
                "video_storyboard_qwen_edit_requirements",
                "Built-in 1/2/3-reference API workflows use Qwen Image Edit 2509 (Q3_K_S), its 4-step Lightning LoRA, Qwen 2.5 VL text encoder, Qwen Image VAE and ComfyUI-GGUF. Custom workflow paths override each built-in workflow.",
            )
        )
        comfy_note.setObjectName("helperLabel")
        comfy_note.setWordWrap(True)
        comfy_layout.addWidget(comfy_note)
        comfy_form = QFormLayout()
        comfy_layout.addLayout(comfy_form)
        self.comfyui_image_edit_url_edit = QLineEdit()
        self.comfyui_image_edit_token_edit = QLineEdit()
        self.comfyui_image_edit_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.comfyui_image_edit_token_edit.setPlaceholderText(
            self.tr_text("video_storyboard_optional_bearer_token_hint", "Optional Bearer token for a protected remote ComfyUI")
        )
        self.comfyui_image_edit_workflow_pickers: list[FilePicker] = []
        for _index in range(3):
            self.comfyui_image_edit_workflow_pickers.append(
                FilePicker(
                    self.tr_text("browse", "Browse"),
                    self.tr_text("json_files_filter", "JSON files (*.json);;All files (*)"),
                )
            )
        self.comfyui_image_edit_unet_edit = QLineEdit()
        self.comfyui_image_edit_lora_edit = QLineEdit()
        self.comfyui_image_edit_camera_lora_edit = QLineEdit()
        self.comfyui_image_edit_camera_strength_spin = self._decimal_spin(0.1, 2.0, 0.05)
        self.comfyui_image_edit_text_encoder_edit = QLineEdit()
        self.comfyui_image_edit_vae_edit = QLineEdit()
        self.comfyui_image_edit_width_spin = QSpinBox()
        self.comfyui_image_edit_height_spin = QSpinBox()
        for spin in (self.comfyui_image_edit_width_spin, self.comfyui_image_edit_height_spin):
            spin.setRange(256, 4096)
            spin.setSingleStep(64)
        resolution = QWidget()
        resolution_layout = QHBoxLayout(resolution)
        resolution_layout.setContentsMargins(0, 0, 0, 0)
        resolution_layout.addWidget(self.comfyui_image_edit_width_spin)
        resolution_layout.addWidget(QLabel("×"))
        resolution_layout.addWidget(self.comfyui_image_edit_height_spin)
        resolution_layout.addStretch(1)
        self.comfyui_image_edit_steps_spin = QSpinBox()
        self.comfyui_image_edit_steps_spin.setRange(1, 100)
        self.comfyui_image_edit_cfg_spin = self._decimal_spin(0.0, 20.0, 0.1)
        self.comfyui_image_edit_timeout_spin = self._seconds_spin(30, 7200)
        comfy_form.addRow("URL", self.comfyui_image_edit_url_edit)
        comfy_form.addRow(self.tr_text("video_storyboard_bearer_token", "Bearer token (optional)"), self.comfyui_image_edit_token_edit)
        for index, picker in enumerate(self.comfyui_image_edit_workflow_pickers, start=1):
            comfy_form.addRow(
                self.tr_text("video_storyboard_reference_workflow", "{count}-reference workflow (optional)", count=index),
                picker,
            )
        comfy_form.addRow("Qwen Edit GGUF", self.comfyui_image_edit_unet_edit)
        comfy_form.addRow("Lightning LoRA", self.comfyui_image_edit_lora_edit)
        comfy_form.addRow(self.tr_text("storyboard_camera_lora", "Camera LoRA (optional)"), self.comfyui_image_edit_camera_lora_edit)
        comfy_form.addRow(self.tr_text("storyboard_camera_strength", "Camera LoRA strength"), self.comfyui_image_edit_camera_strength_spin)
        camera_help = QLabel(
            self.tr_text("storyboard_camera_install", "Install the <a href='{url}'>Multiple-angles LoRA</a> in ComfyUI/models/loras. Enter its filename (including any subfolder) above. Loaded only when Change camera is active in Edit frame.", url=CAMERA_LORA_URL)
        )
        camera_help.setWordWrap(True)
        camera_help.setOpenExternalLinks(True)
        camera_help.setObjectName("helperLabel")
        comfy_form.addRow(camera_help)
        comfy_form.addRow(self.tr_text("video_storyboard_text_encoder", "Text encoder"), self.comfyui_image_edit_text_encoder_edit)
        comfy_form.addRow(self.tr_text("video_storyboard_vae", "VAE"), self.comfyui_image_edit_vae_edit)
        comfy_form.addRow(self.tr_text("video_storyboard_resolution", "Resolution"), resolution)
        comfy_form.addRow(self.tr_text("video_storyboard_steps", "Sampling steps"), self.comfyui_image_edit_steps_spin)
        comfy_form.addRow("CFG", self.comfyui_image_edit_cfg_spin)
        comfy_form.addRow(self.tr_text("timeout", "Timeout"), self.comfyui_image_edit_timeout_spin)
        self.image_edit_provider_stack.addWidget(comfy)

        remote = QWidget()
        remote_form = QFormLayout(remote)
        self.litellm_image_edit_url_edit = QLineEdit()
        self.litellm_image_edit_url_edit.setPlaceholderText(
            self.tr_text("video_storyboard_litellm_url_hint", "Leave empty for the OpenAI endpoint; set only for a compatible proxy.")
        )
        self.litellm_image_edit_model_combo = self._litellm_model_combo(LITELLM_IMAGE_MODELS)
        self.litellm_image_edit_custom_label = QLabel(self.tr_text("custom", "Custom model"))
        self.litellm_image_edit_custom_edit = QLineEdit()
        self.litellm_image_edit_custom_edit.setPlaceholderText("provider/model-name")
        self.litellm_image_edit_api_key_edit = QLineEdit()
        self.litellm_image_edit_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.litellm_image_edit_timeout_spin = self._seconds_spin(10, 3600)
        remote_form.addRow(self.tr_text("video_storyboard_optional_url", "URL (optional)"), self.litellm_image_edit_url_edit)
        remote_form.addRow(self.tr_text("video_storyboard_model", "Model"), self.litellm_image_edit_model_combo)
        remote_form.addRow(self.litellm_image_edit_custom_label, self.litellm_image_edit_custom_edit)
        remote_form.addRow(self.tr_text("api_key", "API key"), self.litellm_image_edit_api_key_edit)
        remote_form.addRow(self.tr_text("timeout", "Timeout"), self.litellm_image_edit_timeout_spin)
        self.image_edit_provider_stack.addWidget(remote)
        self.image_edit_provider_stack.addWidget(QLabel("Runpod · Qwen Image Edit 2511"))
        layout.addWidget(self.image_edit_provider_stack)
        return group

    def _build_comfyui_video_settings(self) -> QGroupBox:
        group = QGroupBox(
            self.tr_text(
                "video_storyboard_video_generation_provider",
                "Video generation provider",
            )
        )
        outer = QVBoxLayout(group)
        description = QLabel(
            self.tr_text(
                "video_storyboard_video_generation_help",
                "Choose a built-in or custom ComfyUI API workflow. The app supplies the scene image, prompt, seed, resolution, duration, FPS and output name.",
            )
        )
        description.setObjectName("helperLabel")
        description.setWordWrap(True)
        outer.addWidget(description)

        generic = QFrame()
        generic.setObjectName("card")
        form = QFormLayout(generic)
        self.video_provider_combo = QComboBox()
        for label, provider in (("ComfyUI", "comfyui"), ("Runpod · Wan 2.6", "runpod"), (self.tr_text("disabled", "Disabled"), "disabled")):
            self.video_provider_combo.addItem(label, provider)
        form.addRow(self.tr_text("storyboard_video_provider", "Video provider"), self.video_provider_combo)
        self.comfyui_video_profile_combo = QComboBox()
        for profile, label in VIDEO_WORKFLOW_PROFILES.items():
            self.comfyui_video_profile_combo.addItem(label, profile)
        self.comfyui_video_url_edit = QLineEdit()
        self.comfyui_video_auth_token_edit = QLineEdit()
        self.comfyui_video_auth_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.comfyui_video_auth_token_edit.setPlaceholderText(
            self.tr_text(
                "video_storyboard_optional_bearer_token_hint",
                "Optional Bearer token for a protected remote ComfyUI",
            )
        )
        self.comfyui_video_workflow_picker = FilePicker(
            self.tr_text("browse", "Browse"),
            self.tr_text("json_files_filter", "JSON files (*.json);;All files (*)"),
        )
        self.comfyui_video_width_spin = QSpinBox()
        self.comfyui_video_width_spin.setRange(64, 4096)
        self.comfyui_video_width_spin.setSingleStep(64)
        self.comfyui_video_height_spin = QSpinBox()
        self.comfyui_video_height_spin.setRange(64, 4096)
        self.comfyui_video_height_spin.setSingleStep(64)
        resolution_row = QWidget()
        resolution_layout = QHBoxLayout(resolution_row)
        resolution_layout.setContentsMargins(0, 0, 0, 0)
        resolution_layout.addWidget(self.comfyui_video_width_spin)
        resolution_layout.addWidget(QLabel("×"))
        resolution_layout.addWidget(self.comfyui_video_height_spin)
        resolution_layout.addStretch(1)
        self.comfyui_video_fps_spin = QSpinBox()
        self.comfyui_video_fps_spin.setRange(1, 120)
        self.comfyui_video_fps_spin.setSuffix(" FPS")
        self.comfyui_video_timeout_spin = self._seconds_spin(30, 7200)
        form.addRow(
            self.tr_text("video_storyboard_workflow_profile", "Workflow profile"),
            self.comfyui_video_profile_combo,
        )
        form.addRow("URL", self.comfyui_video_url_edit)
        form.addRow(
            self.tr_text("video_storyboard_bearer_token", "Bearer token (optional)"),
            self.comfyui_video_auth_token_edit,
        )
        form.addRow(
            self.tr_text(
                "video_storyboard_workflow_override",
                "Workflow JSON override (optional)",
            ),
            self.comfyui_video_workflow_picker,
        )
        form.addRow(
            self.tr_text("video_storyboard_resolution", "Resolution"),
            resolution_row,
        )
        form.addRow("FPS", self.comfyui_video_fps_spin)
        form.addRow(self.tr_text("timeout", "Timeout"), self.comfyui_video_timeout_spin)
        outer.addWidget(generic)

        self.comfyui_video_profile_stack = QStackedWidget()

        wan_widget = QFrame()
        wan_widget.setObjectName("card")
        wan_form = QFormLayout(wan_widget)
        self.wan_behavior_panel = QWidget()
        self.wan_behavior_form = QFormLayout(self.wan_behavior_panel)
        self.wan_behavior_form.setContentsMargins(0, 0, 0, 0)
        wan_form.addRow(self.wan_behavior_panel)
        self.wan_behavior_combo = QComboBox()
        self.wan_behavior_combo.addItem(self.tr_text("wan_behavior_split", "Split and continue with the last frame (default)"), "split")
        self.wan_behavior_combo.addItem(self.tr_text("wan_behavior_stretch", "Extend by reducing FPS"), "stretch")
        self.wan_behavior_form.addRow(self.tr_text("wan_behavior", "Behavior beyond 81 frames"), self.wan_behavior_combo)
        self.comfyui_video_unet_edit = QLineEdit()
        self.comfyui_video_text_encoder_edit = QLineEdit()
        self.comfyui_video_vae_edit = QLineEdit()
        self.comfyui_video_clip_vision_edit = QLineEdit()
        workflow_help = QLabel(
            f'<a href="{bundled_wan_video_workflow_path().as_uri()}">'
            + self.tr_text(
                "video_storyboard_open_wan_video_workflow",
                "Open bundled Wan 2.2 video workflow JSON",
            )
            + "</a>"
        )
        workflow_help.setOpenExternalLinks(True)
        requirements = QLabel(
            self.tr_text(
                "video_storyboard_wan_requirements",
                "Requires ComfyUI-GGUF plus: wan2.2-i2v-rapid-aio-v10-Q4_K.gguf, umt5_xxl_fp8_e4m3fn_scaled.safetensors, wan_2.1_vae.safetensors and clip_vision_h.safetensors.",
            )
        )
        requirements.setObjectName("helperLabel")
        requirements.setWordWrap(True)
        wan_form.addRow("Wan GGUF", self.comfyui_video_unet_edit)
        wan_form.addRow(
            self.tr_text("video_storyboard_text_encoder", "Text encoder"),
            self.comfyui_video_text_encoder_edit,
        )
        wan_form.addRow(
            self.tr_text("video_storyboard_vae", "VAE"),
            self.comfyui_video_vae_edit,
        )
        wan_form.addRow("CLIP Vision", self.comfyui_video_clip_vision_edit)
        wan_form.addRow(workflow_help)
        wan_form.addRow(requirements)
        self.comfyui_video_profile_stack.addWidget(wan_widget)

        ltx_widget = QFrame()
        ltx_widget.setObjectName("card")
        ltx_layout = QVBoxLayout(ltx_widget)
        self.comfyui_video_ltx_prompt_enhance_checkbox = QCheckBox(
            self.tr_text(
                "video_storyboard_ltx_prompt_enhance",
                "Enable the workflow's LTX prompt enhancer",
            )
        )
        ltx_workflow_help = QLabel(
            f'<a href="{bundled_ltx_video_workflow_path().as_uri()}">'
            + self.tr_text(
                "video_storyboard_open_ltx_video_workflow",
                "Open bundled LTX 2.3 I2V workflow JSON",
            )
            + "</a>"
        )
        ltx_workflow_help.setOpenExternalLinks(True)
        ltx_requirements = QLabel(
            self.tr_text(
                "video_storyboard_ltx_requirements",
                "The workflow owns its LTX checkpoint, text encoder, LoRAs, audio VAE and latent x2 upscaler. Recommended output: 1280 × 720 at 25 FPS.",
            )
        )
        ltx_requirements.setObjectName("helperLabel")
        ltx_requirements.setWordWrap(True)
        ltx_layout.addWidget(self.comfyui_video_ltx_prompt_enhance_checkbox)
        ltx_layout.addWidget(ltx_workflow_help)
        ltx_layout.addWidget(ltx_requirements)
        self.comfyui_video_profile_stack.addWidget(ltx_widget)

        custom_widget = QFrame()
        custom_widget.setObjectName("card")
        custom_form = QFormLayout(custom_widget)
        custom_help = QLabel(
            self.tr_text(
                "video_storyboard_custom_workflow_help",
                "Use placeholders such as {{IMAGE}}, {{PROMPT}}, {{SEED}}, {{WIDTH}}, {{HEIGHT}}, {{DURATION_SECONDS}}, {{FPS}}, {{FRAME_COUNT}} and {{OUTPUT_PREFIX}}. Alternatively bind a value as node-id.input-name below. Blank optional bindings keep the workflow's own value.",
            )
        )
        custom_help.setObjectName("helperLabel")
        custom_help.setWordWrap(True)
        custom_form.addRow(custom_help)
        self.comfyui_video_binding_edits: dict[str, QLineEdit] = {}
        binding_labels = {
            "image": "Image",
            "prompt": "Prompt",
            "seed": "Seed",
            "width": "Width",
            "height": "Height",
            "duration": "Duration",
            "fps": "FPS",
            "frame_count": "Frame count",
            "output_prefix": "Output prefix",
        }
        for name in VIDEO_WORKFLOW_BINDING_KEYS:
            edit = QLineEdit()
            edit.setPlaceholderText("node-id.input-name")
            self.comfyui_video_binding_edits[name] = edit
            custom_form.addRow(binding_labels[name], edit)
        self.comfyui_video_output_node_edit = QLineEdit()
        custom_form.addRow(self.tr_text("storyboard_output_node", "Output node"), self.comfyui_video_output_node_edit)
        map_button = QPushButton(self.tr_text("storyboard_map_inputs", "Choose workflow inputs…"))
        map_button.clicked.connect(lambda: map_workflow(self, self.comfyui_video_workflow_picker, self.comfyui_video_binding_edits, self.comfyui_video_output_node_edit, self.tr_text))
        custom_form.addRow(map_button)
        self.comfyui_video_profile_stack.addWidget(custom_widget)
        outer.addWidget(self.comfyui_video_profile_stack)
        return group

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
                "video_storyboard_scene_maximum_help",
                "The AI proposes the scenes. Longer scenes are divided into shots that do not exceed this maximum duration.",
            )
        )
        explanation.setObjectName("helperLabel")
        explanation.setWordWrap(True)
        self.scene_timing_help_label = explanation
        self.scene_minimum_spin = self._seconds_spin(4, 60)
        self.scene_target_spin = self._seconds_spin(4, 60)
        self.scene_maximum_spin = self._seconds_spin(4, 60)
        form.addRow(explanation)
        # Retain legacy values for saved settings, but they no longer control scene-first analysis.
        self.scene_minimum_spin.setParent(group)
        self.scene_target_spin.setParent(group)
        self.scene_minimum_spin.hide()
        self.scene_target_spin.hide()
        form.addRow(self.tr_text("video_storyboard_maximum", "Maximum scene"), self.scene_maximum_spin)
        return group

    def _build_image_preset_settings(self) -> QGroupBox:
        group = QGroupBox(self.tr_text("video_storyboard_image_preset", "Image preset"))
        form = QFormLayout(group)
        preset_hint = QLabel(
            self.tr_text(
                "video_storyboard_image_basic_help",
                "Choose the image resolution and visual style. Advanced sampling settings are at the bottom of this page.",
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
        self.style_mode_list = QListWidget()
        self.style_mode_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.style_mode_list.setMinimumWidth(260)
        for style in storyboard_styles():
            item = QListWidgetItem(style.name)
            item.setData(Qt.ItemDataRole.UserRole, style.id)
            self.style_mode_list.addItem(item)
        custom_item = QListWidgetItem(self.tr_text("custom", "Custom"))
        custom_item.setData(Qt.ItemDataRole.UserRole, "custom")
        self.style_mode_list.addItem(custom_item)
        row_height = max(24, self.style_mode_list.sizeHintForRow(0))
        self.style_mode_list.setFixedHeight(
            row_height * 10 + self.style_mode_list.frameWidth() * 2
        )
        style_picker = QWidget()
        style_picker_layout = QHBoxLayout(style_picker)
        style_picker_layout.setContentsMargins(0, 0, 0, 0)
        style_picker_layout.setSpacing(16)
        style_picker_layout.addWidget(self.style_mode_list, 1, Qt.AlignmentFlag.AlignTop)
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
        form.addRow(self.tr_text("video_storyboard_style", "Default visual style"), style_picker)
        form.addRow(self.style_prompt_label, self.style_prompt_edit)
        return group

    def _build_video_settings(self) -> QGroupBox:
        group = QGroupBox(self.tr_text("video_storyboard_final_video_output", "Final video output"))
        form = QFormLayout(group)
        hint = QLabel(self.tr_text("video_storyboard_final_video_help", "Settings for the final rendered video, combining all scenes and audiobook audio."))
        hint.setObjectName("helperLabel")
        hint.setWordWrap(True)
        form.addRow(hint)
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
        self.wan_behavior_combo.currentIndexChanged.connect(self._emit_changed)
        self.profile_combo.currentIndexChanged.connect(self._change_profile)
        self.visual_advanced.toggled.connect(self._sync_profile_ui)
        self.runpod_settings.changed.connect(self._emit_changed)
        self.video_provider_combo.currentIndexChanged.connect(self._video_provider_changed)
        self.image_provider_combo.currentIndexChanged.connect(self._sync_image_provider)
        self.image_edit_provider_combo.currentIndexChanged.connect(
            self.image_edit_provider_stack.setCurrentIndex
        )
        self.llm_provider_combo.currentIndexChanged.connect(self.llm_provider_stack.setCurrentIndex)
        self.comfyui_video_profile_combo.currentIndexChanged.connect(
            self._video_workflow_profile_changed
        )
        self.enabled_checkbox.toggled.connect(self._emit_changed)
        for combo in (
            self.image_provider_combo,
            self.image_edit_provider_combo,
            self.llm_provider_combo,
            self.video_transition_combo,
        ):
            combo.currentIndexChanged.connect(self._emit_changed)
        self.style_mode_list.currentItemChanged.connect(
            self._sync_style_mode
        )
        self.style_mode_list.currentItemChanged.connect(self._emit_changed)
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
        self.litellm_image_edit_model_combo.currentIndexChanged.connect(
            lambda: self._sync_litellm_custom_model(
                self.litellm_image_edit_model_combo,
                self.litellm_image_edit_custom_edit,
                self.litellm_image_edit_custom_label,
            )
        )
        self.litellm_image_edit_model_combo.currentIndexChanged.connect(self._emit_changed)
        for combo in (self.comfyui_diffusion_combo, self.comfyui_text_encoder_combo, self.comfyui_vae_combo, self.ollama_model_combo):
            combo.currentTextChanged.connect(self._emit_changed)
        for spin in (
            self.comfyui_timeout_spin, self.litellm_image_timeout_spin,
            self.comfyui_video_timeout_spin,
            self.comfyui_video_width_spin, self.comfyui_video_height_spin,
            self.comfyui_video_fps_spin,
            self.comfyui_image_edit_width_spin,
            self.comfyui_image_edit_height_spin,
            self.comfyui_image_edit_steps_spin,
            self.comfyui_image_edit_cfg_spin,
            self.comfyui_image_edit_camera_strength_spin,
            self.comfyui_image_edit_timeout_spin,
            self.litellm_image_edit_timeout_spin,
            self.ollama_context_spin, self.ollama_timeout_spin,
            self.litellm_timeout_spin, self.litellm_max_output_tokens_spin,
            self.scene_minimum_spin, self.scene_target_spin, self.scene_maximum_spin,
            self.image_width_spin, self.image_height_spin,
            self.video_fps_spin, self.video_transition_seconds_spin,
            self.video_zoom_spin, self.video_crf_spin,
        ):
            spin.valueChanged.connect(self._emit_changed)
        for edit in (
            self.comfyui_output_node_edit, self.comfyui_video_output_node_edit,
            self.comfyui_url_edit, self.comfyui_auth_token_edit, self.litellm_image_url_edit,
            self.comfyui_video_url_edit, self.comfyui_video_unet_edit,
            self.comfyui_video_auth_token_edit,
            self.comfyui_video_text_encoder_edit, self.comfyui_video_vae_edit,
            self.comfyui_video_clip_vision_edit,
            self.comfyui_image_edit_url_edit,
            self.comfyui_image_edit_token_edit,
            self.comfyui_image_edit_unet_edit,
            self.comfyui_image_edit_lora_edit,
            self.comfyui_image_edit_camera_lora_edit,
            self.comfyui_image_edit_text_encoder_edit,
            self.comfyui_image_edit_vae_edit,
            self.litellm_image_edit_url_edit,
            self.litellm_image_edit_custom_edit,
            self.litellm_image_edit_api_key_edit,
            self.litellm_image_custom_model_edit, self.litellm_image_api_key_edit,
            self.ollama_url_edit,
            self.litellm_url_edit, self.litellm_custom_model_edit,
            self.litellm_api_key_edit,
        ):
            edit.textChanged.connect(self._emit_changed)
        for edit in (*self.comfyui_video_binding_edits.values(), *self.comfyui_binding_edits.values()):
            edit.textChanged.connect(self._emit_changed)
        self.comfyui_video_ltx_prompt_enhance_checkbox.toggled.connect(
            self._emit_changed
        )
        self.comfyui_workflow_picker.path_changed.connect(self._emit_changed)
        self.comfyui_video_workflow_picker.path_changed.connect(self._emit_changed)
        for picker in self.comfyui_image_edit_workflow_pickers:
            picker.path_changed.connect(self._emit_changed)
        self.style_prompt_edit.textChanged.connect(self._emit_changed)

    def set_configuration(self, value: dict[str, Any] | None) -> None:
        config = self._merge(deepcopy(DEFAULT_SETTINGS["video_storyboard"]), value if isinstance(value, dict) else {})
        self._restoring = True
        try:
            self._select(self.wan_behavior_combo, config.get("comfyui_video", {}).get("wan_behavior", "split"))
            self._profiles = deepcopy(config.get("profiles", {}))
            self._active_profile = infer_profile(value or config)
            self._video_provider = config.get("video_provider", "comfyui")
            self._select(self.video_provider_combo, self._video_provider)
            self._select(self.profile_combo, self._active_profile)
            self.runpod_settings.set_configuration(config.get("runpod", {}))
            self.enabled_checkbox.setChecked(bool(config.get("enabled", False)))
            self._select(self.image_provider_combo, config.get("image_provider"))
            self._select(
                self.image_edit_provider_combo,
                config.get("image_edit_provider", "disabled"),
            )
            self._sync_image_provider()
            self.comfyui_output_node_edit.setText(str(config["comfyui"].get("output_node") or ""))
            self.comfyui_video_output_node_edit.setText(str(config["comfyui_video"].get("output_node") or ""))
            comfyui = config["comfyui"]
            self.comfyui_auth_token_edit.setText(str(comfyui.get("auth_token") or ""))
            bindings = comfyui.get("bindings", {})
            for name, edit in self.comfyui_binding_edits.items():
                edit.setText(str(bindings.get(name) or "") if isinstance(bindings, dict) else "")
            self.comfyui_url_edit.setText(str(comfyui.get("base_url", "")))
            self._set_combo_text(self.comfyui_diffusion_combo, comfyui.get("diffusion_model"))
            self._set_combo_text(self.comfyui_text_encoder_combo, comfyui.get("text_encoder"))
            self._set_combo_text(self.comfyui_vae_combo, comfyui.get("vae_model"))
            self.comfyui_workflow_picker.set_path(comfyui.get("workflow_path", ""))
            self.comfyui_timeout_spin.setValue(int(comfyui.get("timeout_seconds", 900)))
            comfyui_video = config["comfyui_video"]
            self._select(
                self.comfyui_video_profile_combo,
                comfyui_video.get("workflow_profile", WAN_VIDEO_PROFILE),
            )
            self.comfyui_video_url_edit.setText(
                str(comfyui_video.get("base_url", ""))
            )
            self.comfyui_video_auth_token_edit.setText(
                str(comfyui_video.get("auth_token", ""))
            )
            self.comfyui_video_workflow_picker.set_path(
                comfyui_video.get("workflow_path", "")
            )
            self.comfyui_video_unet_edit.setText(
                str(comfyui_video.get("unet_model", ""))
            )
            self.comfyui_video_text_encoder_edit.setText(
                str(comfyui_video.get("text_encoder", ""))
            )
            self.comfyui_video_vae_edit.setText(
                str(comfyui_video.get("vae_model", ""))
            )
            self.comfyui_video_clip_vision_edit.setText(
                str(comfyui_video.get("clip_vision_model", ""))
            )
            self.comfyui_video_width_spin.setValue(
                int(comfyui_video.get("width", 640))
            )
            self.comfyui_video_height_spin.setValue(
                int(comfyui_video.get("height", 360))
            )
            self.comfyui_video_fps_spin.setValue(
                int(comfyui_video.get("fps", 24))
            )
            self.comfyui_video_ltx_prompt_enhance_checkbox.setChecked(
                bool(comfyui_video.get("ltx_prompt_enhance", False))
            )
            bindings = comfyui_video.get("bindings", {})
            bindings = bindings if isinstance(bindings, dict) else {}
            for name, edit in self.comfyui_video_binding_edits.items():
                edit.setText(str(bindings.get(name) or ""))
            self.comfyui_video_timeout_spin.setValue(
                int(comfyui_video.get("timeout_seconds", 1800))
            )
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
            image_edit = config["comfyui_image_edit"]
            self.comfyui_image_edit_url_edit.setText(str(image_edit.get("base_url", "")))
            self.comfyui_image_edit_token_edit.setText(str(image_edit.get("auth_token", "")))
            for index, picker in enumerate(self.comfyui_image_edit_workflow_pickers, start=1):
                picker.set_path(image_edit.get(f"workflow_path_{index}", ""))
            self.comfyui_image_edit_unet_edit.setText(str(image_edit.get("unet_model", "")))
            self.comfyui_image_edit_lora_edit.setText(str(image_edit.get("lora_model", "")))
            self.comfyui_image_edit_camera_lora_edit.setText(str(image_edit.get("camera_lora_model", CAMERA_LORA_MODEL)))
            self.comfyui_image_edit_camera_strength_spin.setValue(float(image_edit.get("camera_lora_strength", 1.0)))
            self.comfyui_image_edit_text_encoder_edit.setText(str(image_edit.get("text_encoder", "")))
            self.comfyui_image_edit_vae_edit.setText(str(image_edit.get("vae_model", "")))
            self.comfyui_image_edit_width_spin.setValue(int(image_edit.get("width", 1280)))
            self.comfyui_image_edit_height_spin.setValue(int(image_edit.get("height", 720)))
            self.comfyui_image_edit_steps_spin.setValue(int(image_edit.get("steps", 4)))
            self.comfyui_image_edit_cfg_spin.setValue(float(image_edit.get("cfg", 1.0)))
            self.comfyui_image_edit_timeout_spin.setValue(int(image_edit.get("timeout_seconds", 1800)))
            remote_edit = config["litellm_image_edit"]
            self.litellm_image_edit_url_edit.setText(str(remote_edit.get("base_url", "")))
            self._set_litellm_model(
                self.litellm_image_edit_model_combo,
                self.litellm_image_edit_custom_edit,
                self.litellm_image_edit_custom_label,
                remote_edit.get("model", ""),
            )
            self.litellm_image_edit_api_key_edit.setText(str(remote_edit.get("api_key", "")))
            self.litellm_image_edit_timeout_spin.setValue(int(remote_edit.get("timeout_seconds", 600)))
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
            self._image_settings = deepcopy(image)
            style_mode = normalize_storyboard_style_id(
                image.get("style_mode") or DEFAULT_STORYBOARD_STYLE_ID
            )
            self._select_style_mode(style_mode)
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
        self._installation_settings = deepcopy((value or {}).get("installation", {}))
        self._engine_inventory = {}
        self._sync_profile_ui()
        self._refresh_engine_cards()

    def configuration(self) -> dict[str, Any]:
        return {
            "active_profile": self._active_profile,
            "profiles": deepcopy(self._profiles),
            "video_provider": self._video_provider,
            "runpod": self.runpod_settings.configuration(),
            "installation": deepcopy(self._installation_settings),
            "enabled": self.enabled_checkbox.isChecked(),
            "image_provider": self.image_provider_combo.currentData() or "comfyui",
            "image_edit_provider": self.image_edit_provider_combo.currentData() or "disabled",
            "llm_provider": self.llm_provider_combo.currentData() or "ollama",
            "scene": {
                "mode": "semantic_bounded",
                "minimum_seconds": self.scene_minimum_spin.value(),
                "target_seconds": self.scene_target_spin.value(),
                "maximum_seconds": self.scene_maximum_spin.value(),
            },
            "image": {
                **deepcopy(self._image_settings),
                "width": self.image_width_spin.value(), "height": self.image_height_spin.value(),
                "batch_size": 1,
                "style_mode": self._style_mode_value(),
                "style_prompt": (
                    self.style_prompt_edit.toPlainText().strip()
                    if self._style_mode_value() == "custom"
                    else ""
                ),
                "seed_mode": "audiobook_locked",
            },
            "comfyui": {
                "output_node": self.comfyui_output_node_edit.text().strip(),
                "base_url": self.comfyui_url_edit.text().strip(),
                "auth_token": self.comfyui_auth_token_edit.text().strip(),
                "bindings": {name: edit.text().strip() for name, edit in self.comfyui_binding_edits.items()},
                "workflow_path": self.comfyui_workflow_picker.path_edit.text().strip(),
                "diffusion_model": self.comfyui_diffusion_combo.currentText().strip(),
                "text_encoder": self.comfyui_text_encoder_combo.currentText().strip(),
                "vae_model": self.comfyui_vae_combo.currentText().strip(),
                "timeout_seconds": self.comfyui_timeout_spin.value(),
            },
            "comfyui_video": {
                "output_node": self.comfyui_video_output_node_edit.text().strip(),
                "base_url": self.comfyui_video_url_edit.text().strip(),
                "auth_token": self.comfyui_video_auth_token_edit.text().strip(),
                "wan_behavior": self.wan_behavior_combo.currentData() or "split",
                "workflow_profile": (
                    self.comfyui_video_profile_combo.currentData()
                    or WAN_VIDEO_PROFILE
                ),
                "workflow_path": self.comfyui_video_workflow_picker.path_edit.text().strip(),
                "unet_model": self.comfyui_video_unet_edit.text().strip(),
                "text_encoder": self.comfyui_video_text_encoder_edit.text().strip(),
                "vae_model": self.comfyui_video_vae_edit.text().strip(),
                "clip_vision_model": self.comfyui_video_clip_vision_edit.text().strip(),
                "width": self.comfyui_video_width_spin.value(),
                "height": self.comfyui_video_height_spin.value(),
                "frames": 49,
                "steps": 4,
                "fps": self.comfyui_video_fps_spin.value(),
                "ltx_prompt_enhance": (
                    self.comfyui_video_ltx_prompt_enhance_checkbox.isChecked()
                ),
                "bindings": {
                    name: edit.text().strip()
                    for name, edit in self.comfyui_video_binding_edits.items()
                },
                "timeout_seconds": self.comfyui_video_timeout_spin.value(),
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
            "comfyui_image_edit": {
                "base_url": self.comfyui_image_edit_url_edit.text().strip(),
                "auth_token": self.comfyui_image_edit_token_edit.text().strip(),
                **{
                    f"workflow_path_{index}": picker.path_edit.text().strip()
                    for index, picker in enumerate(self.comfyui_image_edit_workflow_pickers, start=1)
                },
                "unet_model": self.comfyui_image_edit_unet_edit.text().strip(),
                "lora_model": self.comfyui_image_edit_lora_edit.text().strip(),
                "camera_lora_model": self.comfyui_image_edit_camera_lora_edit.text().strip(),
                "camera_lora_strength": self.comfyui_image_edit_camera_strength_spin.value(),
                "text_encoder": self.comfyui_image_edit_text_encoder_edit.text().strip(),
                "vae_model": self.comfyui_image_edit_vae_edit.text().strip(),
                "width": self.comfyui_image_edit_width_spin.value(),
                "height": self.comfyui_image_edit_height_spin.value(),
                "steps": self.comfyui_image_edit_steps_spin.value(),
                "cfg": self.comfyui_image_edit_cfg_spin.value(),
                "timeout_seconds": self.comfyui_image_edit_timeout_spin.value(),
            },
            "litellm_image_edit": {
                "base_url": self.litellm_image_edit_url_edit.text().strip(),
                "model": self._litellm_model_value(
                    self.litellm_image_edit_model_combo,
                    self.litellm_image_edit_custom_edit,
                ),
                "api_key": self.litellm_image_edit_api_key_edit.text().strip(),
                "timeout_seconds": self.litellm_image_edit_timeout_spin.value(),
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
        style_mode = self._style_mode_value()
        is_custom = style_mode == "custom"
        self.style_prompt_edit.setEnabled(is_custom)
        self.style_prompt_edit.setVisible(is_custom)
        self.style_prompt_label.setVisible(is_custom)
        sample_path = storyboard_style_sample_path(
            style_mode
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

    def _video_workflow_profile_changed(self, index: int) -> None:
        self.comfyui_video_profile_stack.setCurrentIndex(max(0, index))
        self._sync_profile_ui()
        if not self._restoring:
            profile = self.comfyui_video_profile_combo.currentData()
            if profile == WAN_VIDEO_PROFILE:
                width, height, fps = 640, 360, 24
            elif profile == LTX_VIDEO_PROFILE:
                width, height, fps = 1280, 720, 25
            else:
                width = height = fps = 0
            if width:
                self.comfyui_video_width_spin.setValue(width)
                self.comfyui_video_height_spin.setValue(height)
                self.comfyui_video_fps_spin.setValue(fps)
        self._emit_changed()

    def _style_mode_value(self) -> str:
        item = self.style_mode_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return str(value or DEFAULT_STORYBOARD_STYLE_ID)

    def _select_style_mode(self, value: object) -> None:
        requested = normalize_storyboard_style_id(value)
        for index in range(self.style_mode_list.count()):
            item = self.style_mode_list.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == requested:
                self.style_mode_list.setCurrentItem(item)
                return
        self.style_mode_list.setCurrentRow(0)

    def _video_provider_changed(self, *_args):
        self._video_provider = self.video_provider_combo.currentData()
        self._emit_changed()

    def _change_profile(self, *_args):
        if self._restoring:
            return
        selected = self.profile_combo.currentData()
        if selected == self._active_profile:
            return
        config = switch_profile(self.configuration(), selected, DEFAULT_SETTINGS["video_storyboard"])
        self.set_configuration(config)
        self.settingsChanged.emit()

    def _sync_profile_ui(self, *_args):
        remote = self._active_profile == "runpod"
        self.wan_behavior_panel.setVisible(not remote and self.comfyui_video_profile_combo.currentData() == WAN_VIDEO_PROFILE)
        self.runpod_settings.setVisible(remote)
        advanced = self.visual_advanced.isChecked()
        for role in ("image", "video", "edit"):
            self.engine_sections[role].setVisible(not remote and (advanced or self._active_profile != "local"))
        self.engine_cards_widget.setVisible(not remote)
        self.engine_refresh_button.setVisible(not remote)
        self.visual_advanced.setVisible(not remote)

    def _sync_image_provider(self, *_args) -> None:
        provider = self.image_provider_combo.currentData()
        custom = provider == "custom_comfyui"
        self.image_provider_stack.setCurrentIndex(2 if provider == "runpod" else 1 if provider == "litellm_image" else 0)
        for field in self.comfyui_default_fields:
            self.comfyui_form.setRowVisible(field, not custom)
        self.comfyui_form.setRowVisible(self.comfyui_workflow_picker, custom)
        self.comfyui_form.setRowVisible(self.comfyui_custom_mapping, custom)

    def _start_detection(self, service: str) -> None:
        if service in self.detection_threads:
            return
        url = self.ollama_url_edit.text().strip() if service == "ollama" else self.comfyui_url_edit.text().strip()
        button, status = self._service_widgets(service)
        button.setEnabled(False)
        status.setText(self.tr_text("video_storyboard_checking", "Checking connection and models..."))
        thread = QThread(self)
        worker = VideoStoryboardServiceDetectionWorker(service, url, self.comfyui_auth_token_edit.text().strip() if service == "comfyui" else "")
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
            if self.image_provider_combo.currentData() == "custom_comfyui":
                self.comfyui_status_label.setText(self.tr_text("storyboard_custom_image_connected", "Connected to ComfyUI. Custom workflow nodes are checked before generation."))
                return
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
        return bool(self.detection_threads) or self._engine_thread is not None or self.runpod_settings.thread is not None

    def installation_active(self) -> bool:
        return self._engine_thread is not None and self._engine_role != "probe"

    def _service_widgets(self, service: str) -> tuple[QPushButton, QLabel]:
        return (self.ollama_detect_button, self.ollama_status_label) if service == "ollama" else (self.comfyui_detect_button, self.comfyui_status_label)

    @staticmethod
    def _missing_comfyui_recommendations(result: dict[str, Any]) -> list[str]:
        expected = (("diffusion_models", "z_image_turbo_bf16.safetensors"), ("text_encoders", "qwen_3_4b.safetensors"), ("vae_models", "ae.safetensors"))
        return [filename for key, filename in expected if filename not in {str(value) for value in result.get(key, [])}]

    def _emit_changed(self, *_args: object) -> None:
        if not self._restoring:
            self._refresh_engine_cards()
            self.settingsChanged.emit()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._checked_on_show:
            self._checked_on_show = True
            QTimer.singleShot(0, self._refresh_engine_inventory)

    def _focus_engine_section(self, role: str):
        self.visual_advanced.setChecked(True)
        section = self.engine_sections[role]
        y = section.mapTo(self.settings_content, QPoint(0, 0)).y()
        self.settings_scroll.verticalScrollBar().setValue(max(0, y - 14))
        target = {"llm": self.llm_provider_combo, "image": self.image_provider_combo,
                  "video": self.comfyui_video_url_edit, "edit": self.image_edit_provider_combo}[role]
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _refresh_engine_cards(self):
        config = self.configuration()
        endpoints = tuple((url, tuple(headers.items())) for url, headers in (endpoint_for(role, config) for role in ROLES))
        if endpoints != self._engine_inventory_key:
            self._engine_inventory = {}
            self._engine_inventory_key = endpoints
        models = {
            "llm": config["ollama"]["model"] if config["llm_provider"] == "ollama" else config["litellm"]["model"],
            "image": config["comfyui"]["diffusion_model"] if config["image_provider"] == "comfyui" else "Custom ComfyUI" if config["image_provider"] == "custom_comfyui" else config["litellm_image"]["model"],
            "video": config["comfyui_video"].get("unet_model") if config["comfyui_video"].get("workflow_profile") == WAN_VIDEO_PROFILE else self.comfyui_video_profile_combo.currentText(),
            "edit": config["comfyui_image_edit"]["unet_model"] if config["image_edit_provider"] == "comfyui" else config["litellm_image_edit"]["model"] if config["image_edit_provider"] == "litellm_image" else "",
        }
        for role, card in self.engine_cards.items():
            manifest = load_manifest(role)
            card["model"].setText(str(models[role] or manifest["name"]))
            result = self._engine_inventory.get(role, {})
            state = result.get("state", "unchecked")
            status = {
                "installed": self.tr_text("storyboard_engine_installed", "Default engine installed"),
                "missing": self.tr_text("storyboard_engine_missing", "Default engine: missing components"),
                "unavailable": self.tr_text("storyboard_engine_unavailable", "Service unavailable / not started"),
                "pending": self.tr_text("storyboard_engine_restart", "Files installed · restart ComfyUI"),
                "unchecked": self.tr_text("video_storyboard_not_checked", "Not checked"),
            }.get(state, state)
            card["status"].setText(status)
            card["status"].setToolTip("\n".join(result.get("missing_nodes", []) + result.get("missing_models", [])) or result.get("detail", ""))
            active = self._engine_thread is not None and self._engine_role == role
            card["install"].setText(self.tr_text("storyboard_install_view", "View installation") if active else
                                    self.tr_text("installed", "Installed") if state == "installed" else
                                    self.tr_text("storyboard_install_default", "Install default"))
            card["install"].setToolTip(manifest["name"])
            card["install"].setEnabled(self._engine_thread is None or active)
        self.engine_refresh_button.setEnabled(self._engine_thread is None)

    def _refresh_engine_inventory(self):
        if self._active_profile == "runpod":
            return
        if self._engine_thread is None:
            self._refresh_engine_cards()
            self._engine_probe_key = self._engine_inventory_key
            self._start_engine_worker("probe", self.configuration())

    def _open_engine_install(self, role: str):
        if self._engine_thread is not None:
            if self._engine_dialog and self._engine_role == role:
                self._engine_dialog.show()
                self._engine_dialog.raise_()
            return
        if self._engine_dialog is not None:
            self._engine_dialog.deleteLater()
        dialog = StoryboardInstallDialog(self.tr_text, role, self.configuration(), self)
        dialog.installRequested.connect(self._begin_engine_install)
        dialog.cancelRequested.connect(self._cancel_engine_install)
        self._engine_dialog = dialog
        dialog.open()

    @Slot(object)
    def _begin_engine_install(self, options: dict):
        if self._engine_thread is not None or self._engine_dialog is None:
            return
        self._engine_options = deepcopy(options)
        self._start_engine_worker(self._engine_dialog.role, options)

    def _start_engine_worker(self, role: str, options: dict):
        thread = QThread(self)
        worker = StoryboardInstallWorker(role, options)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._engine_progress)
        worker.finished.connect(self._engine_finished)
        worker.failed.connect(self._engine_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._clear_engine_worker)
        thread.finished.connect(thread.deleteLater)
        self._engine_thread, self._engine_worker, self._engine_role = thread, worker, role
        self._refresh_engine_cards()
        thread.start()

    @Slot(object)
    def _engine_progress(self, event):
        if self._engine_dialog:
            self._engine_dialog.set_progress(event)

    @Slot(object)
    def _engine_finished(self, result):
        if "inventory" in result:
            if self._engine_probe_key == self._engine_inventory_key:
                self._engine_inventory = result["inventory"]
        else:
            role = result["role"]
            config = self.configuration()
            if role != "llm":
                config["installation"] = {"comfy_root": result["comfy_root"], "comfy_python": result["python"]}
            if self._engine_options.get("use_defaults"):
                self._apply_installed_engine(config, role, result)
            self.set_configuration(config)
            self.settingsChanged.emit()
            ready = result["state"] == "installed"
            self._engine_inventory[role] = {**result, "state": "installed" if ready else "pending"}
            if self._engine_dialog:
                self._engine_dialog.finish_install(
                    self.tr_text("storyboard_install_success", "Installation verified. The default engine is ready.") if ready else
                    self.tr_text("storyboard_install_pending", "Files installed. Restart ComfyUI and use Check engines to validate the new nodes and models."), ready)
        self._refresh_engine_cards()

    def _apply_installed_engine(self, config, role, result):
        defaults = DEFAULT_SETTINGS["video_storyboard"]
        key = {"llm": "ollama", "image": "comfyui", "video": "comfyui_video", "edit": "comfyui_image_edit"}[role]
        config[key] = deepcopy(defaults[key])
        config[key]["base_url"] = result["url"]
        if "auth_token" in config[key]:
            config[key]["auth_token"] = str(self._engine_options.get("headers", {}).get("Authorization", "")).removeprefix("Bearer ")
        if role == "llm":
            config["llm_provider"] = "ollama"
        elif role == "image":
            config["image_provider"] = "comfyui"
        elif role == "edit":
            config["image_edit_provider"] = "comfyui"
        # Use the exact names advertised by ComfyUI, including subdirectories.
        matched = result.get("matched_models", {})
        for field, value in list(config[key].items()):
            if isinstance(value, str) and value in matched:
                config[key][field] = matched[value]

    @Slot(str)
    def _engine_failed(self, message):
        if self._engine_dialog and self._engine_role != "probe":
            self._engine_dialog.finish_install(message, False)

    @Slot()
    def _cancel_engine_install(self):
        if self._engine_worker:
            self._engine_worker.cancel()

    @Slot()
    def _clear_engine_worker(self):
        self._engine_thread = self._engine_worker = None
        self._engine_role = ""
        self._refresh_engine_cards()

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
