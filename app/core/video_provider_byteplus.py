"""Seedance via BytePlus LAS content generation tasks (Johor region)."""
from app.core.direct_video_models import api_root, frame_size, generation_seconds
from app.core.video_provider_media import reference_paths, image_uri
from app.core.video_provider_protocol import VideoRequest, VideoStatus, error_detail


class BytePlusAdapter:
    def build(self, scene, config, prompt, frame_role):
        paths = reference_paths(scene, frame_role, "byteplus", config)
        role = "first_frame" if frame_role == "start" else "reference_image"
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": image_uri(path, "byteplus")}, "role": role} for path in paths]
        payload = {"model": config["model"], "content": content, "resolution": config["resolution"],
                   "duration": generation_seconds(scene, "byteplus", config), "ratio": "16:9",
                   "generate_audio": bool(config["audio"]), "watermark": False}
        endpoint = "/contents/generations/tasks"
        return VideoRequest("byteplus", config["model"], api_root(config), endpoint, endpoint + "/{id}",
                            payload, frame_size=frame_size(config))

    def submitted_id(self, data):
        return data.get("id", "")

    def status(self, data):
        state = data.get("status")
        if state == "succeeded":
            return VideoStatus("succeeded", data.get("content", {}).get("video_url", ""))
        if state in {"failed", "cancelled", "expired"}:
            return VideoStatus("failed", detail=error_detail(data))
        if state not in {"queued", "running"}:
            raise ValueError("BytePlus returned an unknown job status.")
        return VideoStatus("pending", detail=state)
