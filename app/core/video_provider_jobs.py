"""Shared submit/poll/download/recovery lifecycle for direct video APIs."""
import hashlib
import json
import random
import shutil
import threading
import time
from urllib.parse import quote, urlsplit

import httpx

from app.core.storyboard_generation_errors import redact_generation_error
from app.core.video_provider_protocol import error_detail

_locks = {}
_locks_guard = threading.Lock()


def _write_record(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(path)


def _response(response, provider):
    try:
        data = response.json()
    except ValueError:
        raise ValueError(f"{provider} returned invalid JSON (HTTP {response.status_code}).") from None
    if not isinstance(data, dict):
        raise ValueError(f"{provider} returned an invalid API response.")
    if response.is_error or data.get("code") or (data.get("error") and not data.get("status")):
        raise ValueError(f"{provider} HTTP {response.status_code}: {error_detail(data)}")
    return data


def run_job(request, adapter, config, scene, settings, target, *, prompt, frame_role, status=None, cancelled=None):
    """Confirmed jobs resume; ambiguous submissions never create another paid job."""
    from app.core.video_storyboard_video_comfyui import VideoStoryboardVideoError
    identity = request.identity or {"root": request.root, "endpoint": request.submit_path,
                                   "payload": request.payload, "scene": scene.get("scene_id", scene.get("id")),
                                   "accepted_video": scene.get("video_path", ""),
                                   "account": hashlib.sha256(config["api_key"].strip().encode()).hexdigest()}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    jobs = target.parent / (request.provider + "_jobs")
    lock_key = str((jobs / fingerprint).resolve())
    with _locks_guard:
        lock = _locks.setdefault(lock_key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise VideoStoryboardVideoError("This video request is already running.")
    try:
        return _run_job(request, adapter, config, scene, settings, target, jobs, fingerprint,
                        prompt, frame_role, status, cancelled)
    except Exception as exc:
        raise VideoStoryboardVideoError(redact_generation_error(str(exc), settings)) from None
    finally:
        lock.release()


def _run_job(request, adapter, config, scene, settings, target, jobs, fingerprint, prompt, frame_role, status, cancelled):
    from app.core.video_storyboard_video_comfyui import _probe_media_duration, _retime_video
    jobs.mkdir(parents=True, exist_ok=True)
    record_path = jobs / (fingerprint + ".json")
    source = jobs / (fingerprint + ".mp4")
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    if not isinstance(record, dict):
        raise ValueError(f"Invalid video recovery record: {record_path}")
    if record.get("consumed") or record.get("failed"):
        record_path.replace(jobs / f"{fingerprint}-history-{time.time_ns()}.json")
        source.unlink(missing_ok=True)
        record = {}
    deadline = time.monotonic() + float(config["timeout_seconds"])
    label = f"{request.provider} · {request.model}"

    def check():
        if cancelled and cancelled():
            raise ValueError("Video generation cancelled locally. Generate again to resume the remote task.")
        if time.monotonic() >= deadline:
            raise ValueError("Video generation timed out. Generate again to resume the remote task.")

    def report(message):
        if status:
            status(redact_generation_error(label + " · " + message, settings))

    headers = {"Authorization": "Bearer " + config["api_key"].strip()}
    with httpx.Client(timeout=60) as client:
        check()
        if not record.get("id"):
            if record.get("submitting"):
                raise ValueError("Previous video submission has no confirmed task ID. Check the provider console before resubmitting. Recovery record: " + str(record_path))
            record = {"submitting": True}
            _write_record(record_path, record)
            report("Submitting video")
            response = client.post(request.root + request.submit_path,
                                   headers={**headers, **request.headers}, json=request.payload)
            if 400 <= response.status_code < 500 and response.status_code != 408:
                record["failed"] = True
                _write_record(record_path, record)
            data = _response(response, request.provider)
            task_id = adapter.submitted_id(data)
            if not isinstance(task_id, (str, int)) or not str(task_id).strip():
                raise ValueError("Provider returned no confirmed task ID. Check its console before resubmitting.")
            record.update(id=str(task_id), submitting=False)
            _write_record(record_path, record)
        while not source.exists():
            check()
            url = request.root + request.poll_path.format(id=quote(str(record["id"]), safe=""))
            response = client.get(url, headers=headers)
            if response.status_code == 404:
                raise ValueError("The saved video task is missing or expired. Check the provider console; no new generation was submitted. Recovery record: " + str(record_path))
            result = adapter.status(_response(response, request.provider))
            report(result.detail or result.state)
            if result.state == "failed":
                record["failed"] = True
                _write_record(record_path, record)
                raise ValueError(f"{label}: {result.detail or 'Video generation failed.'}")
            if result.state == "succeeded":
                parsed = urlsplit(result.video_url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError("Provider returned an invalid video download URL.")
                partial = source.with_suffix(".part")
                report("Downloading video")
                try:
                    # Signed result URLs must never receive the provider API key.
                    with client.stream("GET", result.video_url, follow_redirects=True) as response:
                        response.raise_for_status()
                        with partial.open("wb") as handle:
                            for chunk in response.iter_bytes():
                                check()
                                handle.write(chunk)
                    partial.replace(source)
                finally:
                    partial.unlink(missing_ok=True)
                break
            delay_end = time.monotonic() + random.uniform(5, 6)
            while time.monotonic() < delay_end:
                check()
                time.sleep(min(.25, max(0, delay_end - time.monotonic())))
    check()
    measured = _probe_media_duration(source, settings)
    if not measured:
        source.unlink(missing_ok=True)
        raise ValueError("Provider returned an unreadable video. Retry to download the same task again.")
    shutil.copyfile(source, target)
    duration = max(.1, float(scene.get("duration_seconds") or measured))
    report("Fitting video to scene")
    size = {"frame_size": request.frame_size} if request.frame_size else {}
    _retime_video(target, duration, settings, source_duration_seconds=measured, preserve_audio=True, **size)
    check()
    record["consumed"] = True
    _write_record(record_path, record)
    source.unlink(missing_ok=True)
    return {"provider": request.provider, "model": request.model,
            "scene_id": str(scene.get("scene_id") or scene.get("id") or ""),
            "video_path": str(target), "video_prompt": prompt, "video_frame_role": frame_role,
            "video_duration_seconds": duration, "generated_duration_seconds": measured,
            "workflow_profile": request.model}
