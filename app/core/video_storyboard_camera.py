"""Camera instructions for dx8152's Qwen Edit 2509 Multiple-angles LoRA.

The axes follow the model's public demo: 45-degree yaw steps, three
elevations and three distances. They describe an AI edit, not FFmpeg motion.
https://huggingface.co/dx8152/Qwen-Edit-2509-Multiple-angles
"""
from __future__ import annotations

from typing import Any

CAMERA_LORA_MODEL = "镜头转换.safetensors"
CAMERA_LORA_URL = "https://huggingface.co/dx8152/Qwen-Edit-2509-Multiple-angles"


def resolve_camera_lora(configured: str, available: list[str]) -> str:
    """Resolve known renamed files, preserving the server's exact path.

    Never substitute an unrelated custom LoRA or guess between duplicates.
    """
    if configured in available:
        return configured
    def normalized(name: str) -> str:
        return name.replace("\\", "/").casefold()
    exact = [name for name in available if normalized(name) == normalized(configured)]
    basename = normalized(configured).rsplit("/", 1)[-1]
    aliases = {CAMERA_LORA_MODEL.casefold(), "qwen-edit-2509-multiple-angles.safetensors"}
    accepted = aliases if basename in aliases else {basename}
    matches = exact or [name for name in available if normalized(name).rsplit("/", 1)[-1] in accepted]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError("Multiple camera LoRA files match. Select the exact filename including its subfolder in Settings > Video Storyboard > Image editing provider.")
    raise ValueError(f"Camera LoRA '{configured}' is not installed on this ComfyUI server. Install the Multiple-angles LoRA, refresh ComfyUI, and select its exact filename in Settings > Video Storyboard > Image editing provider.")


def normalize_camera(value: object) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}

    def snap(key: str, steps: tuple[int, ...]) -> int:
        try:
            number = float(data.get(key, 0))
        except (TypeError, ValueError):
            number = 0
        return min(steps, key=lambda step: abs(step - number))

    return {
        "enabled": data.get("enabled") is True,
        "rotate_deg": snap("rotate_deg", (-90, -45, 0, 45, 90)),
        "vertical_tilt": snap("vertical_tilt", (-1, 0, 1)),
        "move_forward": snap("move_forward", (0, 5, 10)),
        "wideangle": data.get("wideangle") is True,
    }


def camera_prompt(value: object) -> str:
    camera = normalize_camera(value)
    if not camera["enabled"]:
        return ""
    parts: list[str] = []
    rotation = camera["rotate_deg"]
    if rotation:
        direction = "left" if rotation > 0 else "right"
        parts.append(f"Rotate the camera {abs(rotation)} degrees to the {direction}.")
    if camera["move_forward"] == 10:
        parts.append("Turn the camera to a close-up.")
    elif camera["move_forward"] == 5:
        parts.append("Move the camera forward.")
    if camera["vertical_tilt"] == -1:
        parts.append("Turn the camera to a bird's-eye view.")
    elif camera["vertical_tilt"] == 1:
        parts.append("Turn the camera to a worm's-eye view.")
    if camera["wideangle"]:
        parts.append("Turn the camera to a wide-angle lens.")
    return " ".join(parts)


def compose_image_edit_prompt(
    instruction: str, camera: object = None,
    references: list[dict[str, Any]] | None = None,
    *, provider: str = "comfyui",
) -> str:
    parts = [(runpod_camera_prompt(camera) if provider == "runpod" else camera_prompt(camera)), str(instruction or "").strip()]
    prompt = " ".join(part for part in parts if part)
    if references and len(references) > 1:
        roles = ["Image 1 is the frame to edit."]
        for index, reference in enumerate(references[1:], start=2):
            label = str(reference.get("label") or f"reference {index}").strip()
            roles.append(f"Image {index} is the visual reference for {label}.")
        prompt = " ".join(roles) + " " + prompt
    return prompt


RUNPOD_CAMERA_ENDPOINT = "qwen-image-edit-2511-lora"
RUNPOD_CAMERA_LORA = "https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA/resolve/e3066224ab74263f4a5b6179cd1a3b0a15577e44/qwen-image-edit-2511-multiple-angles-lora.safetensors"


def runpod_camera_prompt(value: object) -> str:
    camera = normalize_camera(value)
    if not camera["enabled"]:
        return ""
    azimuth = {-90: "right side view", -45: "front-right quarter view", 0: "front view",
               45: "front-left quarter view", 90: "left side view"}[camera["rotate_deg"]]
    elevation = {-1: "high-angle shot", 0: "eye-level shot", 1: "low-angle shot"}[camera["vertical_tilt"]]
    distance = {0: "wide shot", 5: "medium shot", 10: "close-up"}[camera["move_forward"]]
    return f"<sks> {azimuth} {elevation} {distance}"


def runpod_camera_settings(settings, camera):
    if not normalize_camera(camera)["enabled"]:
        return settings
    return {**settings, "runpod": {**settings.get("runpod", {}), "edit_endpoint": RUNPOD_CAMERA_ENDPOINT}}
