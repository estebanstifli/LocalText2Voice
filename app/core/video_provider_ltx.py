"""LTX asynchronous V2 API. https://docs.ltx.io/async-jobs"""
from app.core.direct_video_models import api_root, frame_size, generation_seconds
from app.core.video_provider_media import reference_paths, image_uri
from app.core.video_provider_protocol import VideoRequest, VideoStatus, error_detail


class LtxAdapter:
    def build(self, scene, config, prompt, frame_role):
        if len(prompt) > 5000:
            raise ValueError("LTX accepts video prompts up to 5,000 characters.")
        paths = reference_paths(scene, frame_role, "ltx", config)
        endpoint = "/v2/image-to-video" if paths else "/v2/text-to-video"
        width, height = frame_size(config)
        payload = {"model": config["model"], "prompt": prompt,
                   "duration": generation_seconds(scene, "ltx", config),
                   "resolution": f"{width}x{height}", "fps": 24, "generate_audio": bool(config["audio"])}
        if paths:
            payload["image_uri"] = image_uri(paths[0], "ltx")
        return VideoRequest("ltx", config["model"], api_root(config), endpoint, endpoint + "/{id}", payload,
                            frame_size=(width, height))

    def submitted_id(self, data):
        return data.get("id", "")

    def status(self, data):
        state = data.get("status")
        if state == "completed":
            return VideoStatus("succeeded", data.get("result", {}).get("video_url", ""))
        if state == "failed":
            return VideoStatus("failed", detail=error_detail(data))
        if state not in {"pending", "processing"}:
            raise ValueError("LTX returned an unknown job status.")
        return VideoStatus("pending", detail=state)
