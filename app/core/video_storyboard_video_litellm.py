"""Veo through LiteLLM SDK, with optional LiteLLM Gemini pass-through proxy.

The scoped HTTP adapter fills gaps in LiteLLM 1.99's video translation:
referenceImages are instance fields, and completed operations may contain errors.
No SDK globals or installed package files are modified.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import shutil
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from app.core.litellm_video_models import configuration, generation_seconds, reference_mode
from app.core.storyboard_generation_errors import redact_generation_error


def prepare(settings):
    config = configuration(settings)
    if not config["api_key"].strip():
        raise ValueError("Set the Gemini API key (or LiteLLM proxy key) in Settings > Video Storyboard > Video provider > LiteLLM.")
    if not config["model"].startswith("gemini/veo-"):
        raise ValueError("Choose a Gemini Veo video model.")
    return {"provider": "litellm", "model": config["model"]}


def api_root(config):
    root = str(config.get("base_url") or "").strip().rstrip("/")
    if not root:
        return "https://generativelanguage.googleapis.com"
    parsed = urlsplit(root)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.username:
        raise ValueError("Enter a valid LiteLLM proxy base URL without credentials or query parameters.")
    root = root.removesuffix("/v1beta").removesuffix("/v1")
    return root if root.endswith("/gemini") else root + "/gemini"


def _image(path):
    mime = mimetypes.guess_type(path.name)[0]
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError(f"Unsupported Veo reference image: {path.name}. Use PNG, JPEG or WebP.")
    return {"bytesBase64Encoded": base64.b64encode(path.read_bytes()).decode("ascii"), "mimeType": mime}


def _client(instance_fields, parameters):
    from litellm.llms.custom_httpx.http_handler import HTTPHandler

    class VeoHTTPHandler(HTTPHandler):
        operation = None

        def post(self, *args, **kwargs):
            # The client belongs to this generation only. Never patch SDK globals.
            body = kwargs["json"]
            body["instances"][0].update(instance_fields)
            body.setdefault("parameters", {}).update(parameters)
            return super().post(*args, **kwargs)

        def get(self, *args, **kwargs):
            response = super().get(*args, **kwargs)
            self.operation = response.json()
            return response

    return VeoHTTPHandler(timeout=60)


def _write_record(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _download_url(uri, root):
    parsed = urlsplit(uri)
    if parsed.scheme != "https" or parsed.hostname != "generativelanguage.googleapis.com":
        raise ValueError("Veo returned an unexpected video download address.")
    # Proxy keys must never be sent to Google. Download through the same proxy.
    return root + parsed.path + ("?" + parsed.query if parsed.query else "")


def _download(client, url, key, temporary, check):
    origin = urlsplit(url)
    for _ in range(6):
        current = urlsplit(url)
        same_origin = (current.scheme, current.netloc) == (origin.scheme, origin.netloc)
        headers = {"x-goog-api-key": key} if same_origin else {}
        with client.stream("GET", url, headers=headers, timeout=60, follow_redirects=False) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers["location"])
                destination = urlsplit(url)
                if (destination.scheme, destination.netloc) != (origin.scheme, origin.netloc):
                    host = destination.hostname or ""
                    if destination.scheme != "https" or not (host == "storage.googleapis.com" or host.endswith(".googleusercontent.com")):
                        raise ValueError("Unexpected Veo download redirect.")
                continue
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes():
                    check()
                    output.write(chunk)
            return
    raise ValueError("Too many Veo download redirects.")


def generate(scene, plan, settings, target, *, prompt, frame_role, status=None, cancelled=None):
    from app.core.video_storyboard_video_comfyui import (
        VideoStoryboardVideoError, _probe_media_duration, _retime_video,
    )
    client = None
    record = {}
    record_path = None
    try:
        prepare(settings)
        import litellm
        config = configuration(settings)
        paths = reference_mode(scene, frame_role, config)
        if not prompt.strip():
            raise ValueError("The video prompt is empty.")
        root = api_root(config)
        seconds = generation_seconds(scene, config, frame_role == "none" and bool(paths))
        instance = {}
        if paths:
            if frame_role == "start":
                instance["image"] = _image(paths[0])
            else:
                instance["referenceImages"] = [{"image": _image(path), "referenceType": "asset"} for path in paths]
        parameters = {"durationSeconds": seconds, "resolution": config["resolution"], "aspectRatio": config["aspect_ratio"]}
        identity = {"model": config["model"], "root": root, "prompt": prompt, "parameters": parameters,
                    "frame_role": frame_role, "scene_id": scene.get("scene_id", scene.get("id")),
                    "accepted_video": scene.get("video_path", ""),
                    "references": [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        jobs = target.parent / "litellm_jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        record_path = jobs / (fingerprint + ".json")
        source = jobs / (fingerprint + ".mp4")
        if record_path.exists():
            record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("consumed") or record.get("failed"):
            record_path.replace(jobs / f"{fingerprint}-history-{time.time_ns()}.json")
            source.unlink(missing_ok=True)
            record = {}
        deadline = time.monotonic() + float(config["timeout_seconds"])

        def check():
            if cancelled and cancelled():
                raise ValueError("Video generation cancelled locally. Its remote operation can be resumed.")
            if time.monotonic() >= deadline:
                raise ValueError("Veo timed out. Generate again to resume the pending operation.")

        def report(message):
            if status:
                status(message)

        check()
        client = _client(instance, parameters)
        kwargs = {"api_key": config["api_key"], "api_base": root, "client": client, "timeout": 60, "num_retries": 0}
        if not record.get("id"):
            if record.get("submitting"):
                raise ValueError("The previous Veo submission has no confirmed operation ID. Check the provider before retrying to avoid duplicate charges. Recovery record: " + str(record_path))
            record = {"submitting": True, "model": config["model"]}
            _write_record(record_path, record)
            report("LiteLLM · Veo · Submitting video")
            try:
                result = litellm.video_generation(model=config["model"], prompt=prompt.strip(), seconds=str(seconds), **kwargs)
            except Exception as exc:
                # Explicit rejection is safe to retry; an ambiguous timeout is not.
                code = getattr(exc, "status_code", None)
                if isinstance(code, int) and 400 <= code < 500 and code != 408:
                    record["failed"] = True
                    _write_record(record_path, record)
                raise
            record.update(id=result.id, submitting=False)
            _write_record(record_path, record)
        report("LiteLLM · Veo · Waiting for video")
        while not source.exists():
            check()
            # Keep the raw operation: the SDK currently discards error/filter details.
            client.operation = None
            try:
                litellm.video_status(video_id=record["id"], **kwargs)
            except Exception as exc:
                # Filtered completions omit generatedSamples, which older SDKs
                # reject before returning the provider's actual explanation.
                if not (client.operation or {}).get("done"):
                    if getattr(exc, "status_code", None) == 404:
                        record["failed"] = True
                        _write_record(record_path, record)
                    raise
            operation = client.operation or {}
            if operation.get("error"):
                record["failed"] = True
                _write_record(record_path, record)
                raise ValueError("Veo: " + json.dumps(operation["error"], ensure_ascii=False))
            if operation.get("done"):
                response = operation.get("response", {}).get("generateVideoResponse", {})
                samples = response.get("generatedSamples") or []
                if not samples:
                    record["failed"] = True
                    _write_record(record_path, record)
                    raise ValueError("Veo returned no video. " + json.dumps(response, ensure_ascii=False)[:3000])
                report("LiteLLM · Veo · Downloading video")
                url = _download_url(samples[0]["video"]["uri"], root)
                temporary = source.with_suffix(".part")
                try:
                    # Stream to disk, preserving native audio; never hold a clip in RAM.
                    _download(client.client, url, config["api_key"], temporary, check)
                    temporary.replace(source)
                finally:
                    temporary.unlink(missing_ok=True)
                break
            for _ in range(20):
                check()
                time.sleep(0.25)
        measured = _probe_media_duration(source, settings)
        if not measured:
            source.unlink(missing_ok=True)
            raise ValueError("Veo returned an unreadable video.")
        check()
        report("LiteLLM · Veo · Preparing scene clip")
        shutil.copyfile(source, target)
        duration = max(0.1, float(scene.get("duration_seconds") or measured))
        _retime_video(target, duration, settings, source_duration_seconds=measured)
        record["consumed"] = True
        _write_record(record_path, record)
        source.unlink(missing_ok=True)
        return {"provider": "litellm", "model": config["model"], "scene_id": str(scene.get("scene_id") or scene.get("id") or ""),
                "video_path": str(target), "video_prompt": prompt.strip(), "video_frame_role": frame_role,
                "video_duration_seconds": duration, "generated_duration_seconds": measured, "workflow_profile": config["model"]}
    except Exception as exc:
        message = redact_generation_error(str(exc), settings)
        raise VideoStoryboardVideoError(message) from None
    finally:
        if client is not None:
            client.close()
