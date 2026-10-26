"""UI adapters only: all synthesis, ASR and catalog writes run on the host."""
from dataclasses import fields
import time

from PySide6.QtCore import QObject, Signal, Slot
from app.tts.voice_gallery_manager import GalleryVoice


def request_preview(client, text, config):
    payload = dict(engine_id=config["engine"], model=config.get("model"),
                   language=config["language"], sample_text=text,
                   description=config.get("instruction") or config.get("instruct", ""),
                   generation_parameters=config)
    job = client.request_json("POST", "/voices/design/preview", payload)
    while job["status"] not in {"complete", "failed", "cancelled"}:
        time.sleep(0.5)
        job = client.request_json("GET", "/jobs/" + job["job_id"])
    result = job.get("result", {})
    if result.get("status") != "preview":
        message = result.get("message") or job.get("error_message") or job["status"]
        if "asr_transcript" in result:
            message += "\nWhisper: " + result["asr_transcript"]
        raise ValueError(message)
    return {**result, "draft_id": job["job_id"]}


class ServerDesignPreviewWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, client, config, text):
        super().__init__()
        self.client, self.config, self.text = client, config, text
        self.result = None

    @Slot()
    def run(self):
        try:
            self.result = request_preview(self.client, self.text, self.config)
            self.finished.emit(self.result["output_wav"])
        except Exception as exc:
            self.failed.emit(str(exc))


class ServerDesignGallery:
    """Preserve the existing rename/overwrite UI, forwarding writes to the server."""
    def __init__(self, client, gallery, draft_id):
        self.client, self.gallery, self.draft_id = client, gallery, draft_id

    def list_voices(self, engine):
        return self.gallery.list_voices(engine)

    def _save(self, metadata, replace_voice_id=None):
        result = self.client.request_json("POST", f"/voices/design/previews/{self.draft_id}/save",
            dict(name=metadata["name"], replace_voice_id=replace_voice_id, labels=metadata))
        allowed = {field.name for field in fields(GalleryVoice)}
        return GalleryVoice(**{k: v for k, v in result["voice"].items() if k in allowed})

    def import_reference_voice(self, engine, source, **metadata):
        return self._save(metadata)

    def update_reference_voice(self, voice_id, source=None, **metadata):
        return self._save(metadata, voice_id)
