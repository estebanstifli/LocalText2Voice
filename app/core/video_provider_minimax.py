"""MiniMax H3 V2 API; native audio is always retained."""
from app.core.direct_video_models import api_root, frame_size, generation_seconds
from app.core.video_provider_media import reference_paths, image_uri
from app.core.video_provider_protocol import VideoRequest, VideoStatus, error_detail


class MiniMaxAdapter:
    def build(self, scene, config, prompt, frame_role):
        if len(prompt) > 7000:
            raise ValueError("MiniMax accepts video prompts up to 7,000 characters.")
        paths = reference_paths(scene, frame_role, "minimax", config)
        role = {"start": "first_frame", "end": "last_frame", "none": "reference_image"}[frame_role]
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": image_uri(path, "minimax")}, "role": role} for path in paths]
        payload = {"model": config["model"], "content": content, "resolution": "768P",
                   "duration": generation_seconds(scene, "minimax", config), "ratio": "16:9"}
        return VideoRequest("minimax", config["model"], api_root(config), "/v2/video_generation",
                            "/v2/query/video_generation/{id}", payload, frame_size=frame_size(config))

    def submitted_id(self, data):
        return data.get("task_id", "")

    def status(self, data):
        task = data.get("task", {})
        state = task.get("status")
        if state == "succeeded":
            return VideoStatus("succeeded", task.get("content", {}).get("url", ""))
        if state in {"failed", "cancelled", "canceled", "expired"}:
            return VideoStatus("failed", detail=error_detail(task))
        if state not in {"queued", "pending", "running", "processing"}:
            raise ValueError("MiniMax returned an unknown job status.")
        return VideoStatus("pending", detail=state)
