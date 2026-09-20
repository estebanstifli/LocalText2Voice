from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import secrets
import threading

from PySide6.QtCore import QThread
from PIL import Image, ImageOps

from app.core.video_storyboard_comfyui import generate_storyboard_frame, prepare_image_runtime


class CharacterImageWorker(QThread):
    """Generate a reference without blocking the entity editor."""

    def __init__(self, prompt, settings, target, parent=None):
        super().__init__(parent)
        self.prompt = prompt
        self.settings = deepcopy(settings)
        self.target = Path(target)
        self.cancelled = threading.Event()
        self.error = ""

    def run(self):
        try:
            self.settings.setdefault("image", {}).update(width=1280, height=720)
            if self.cancelled.is_set():
                return
            self.target.parent.mkdir(parents=True, exist_ok=True)
            prepare_image_runtime(self.settings)
            if self.cancelled.is_set():
                return
            generate_storyboard_frame(
                {"generation_overrides": {"raw_prompt": self.prompt}},
                {"base_seed": secrets.randbelow(2**31)},
                self.settings, self.target, cancelled=self.cancelled.is_set,
            )
            # Providers may return their own supported size. Preserve every panel.
            with Image.open(self.target) as source:
                normalized = ImageOps.pad(
                    ImageOps.exif_transpose(source).convert("RGB"), (1280, 720),
                    method=Image.Resampling.LANCZOS, color=(235, 235, 235),
                )
            normalized.save(self.target, "PNG")
        except Exception as exc:
            self.error = str(exc)
