"""Direct-provider registry and entry point; rendering remains provider-independent."""
import json
from app.core.direct_video_models import configuration, prepare
from app.core.video_provider_ltx import LtxAdapter
from app.core.video_provider_minimax import MiniMaxAdapter
from app.core.video_provider_byteplus import BytePlusAdapter
from app.core.video_provider_jobs import run_job
from app.core.storyboard_generation_errors import redact_generation_error

ADAPTERS = {"ltx": LtxAdapter(), "minimax": MiniMaxAdapter(), "byteplus": BytePlusAdapter()}


def generate(scene, plan, settings, target, *, prompt, frame_role, status=None, cancelled=None):
    from app.core.video_storyboard_video_comfyui import VideoStoryboardVideoError
    try:
        prepare(settings)
        if not prompt.strip():
            raise ValueError("The video prompt is empty.")
        if cancelled and cancelled():
            raise ValueError("Video generation cancelled.")
        provider = settings["video_provider"]
        config = configuration(settings)
        adapter = ADAPTERS[provider]
        request = adapter.build(scene, config, prompt.strip(), frame_role)
        if len(json.dumps(request.payload).encode()) > 64 * 1024 * 1024:
            raise ValueError("The video request exceeds 64 MB. Reduce reference image sizes.")
        return run_job(request, adapter, config, scene, settings, target, prompt=prompt.strip(),
                       frame_role=frame_role, status=status, cancelled=cancelled)
    except VideoStoryboardVideoError:
        raise
    except Exception as exc:
        raise VideoStoryboardVideoError(redact_generation_error(str(exc), settings)) from None
