"""Runpod public image/edit/video endpoints with resumable asynchronous jobs.

Model contracts: docs.runpod.io/public-endpoints/models/{z-image-turbo,
qwen-image-edit-2511,wan-2-6-i2v}. Endpoint overrides must use the same contract.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from contextvars import ContextVar
from pathlib import Path

from app.core.storyboard_credentials import reveal
from app.core.storyboard_profiles import RUNPOD_DEFAULTS
from app.core.runpod_video_models import VIDEO_MODELS, duration_for, model_id

API_ROOT = "https://api.runpod.ai/v2"
TERMINAL = {"FAILED", "CANCELLED", "TIMED_OUT"}
_temporary_uploads = ContextVar("runpod_temporary_uploads", default=None)
logger = logging.getLogger(__name__)
from app.core.runpod_image_models import IMAGE_MODELS, image_model_id

PUBLIC_ENDPOINTS = frozenset(RUNPOD_DEFAULTS[f"{role}_endpoint"] for role in ("image", "edit", "video")) | VIDEO_MODELS.keys() | IMAGE_MODELS.keys() | {"qwen-image-edit-2511-lora"}


class RunpodError(RuntimeError):
    status_code = None
    response_detail = ""


def _response_detail(response, key):
    try:
        raw = response.read(8192).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if isinstance(body, dict):
        # Error fields only: do not log echoed input objects, headers or images.
        detail = next((body.get(name) for name in ("detail", "error", "message", "title") if isinstance(body.get(name), str) and body[name]), "")
    else:
        detail = raw if "<html" not in raw.lower() and "<!doctype" not in raw.lower() else "Non-JSON error response"
    return _safe_detail(detail, key)


def _safe_detail(detail, key):
    if key:
        detail = detail.replace(key, "[REDACTED]")
    detail = re.sub(r"\b(?:rpa_|rps_)[A-Za-z0-9_-]+", "[REDACTED]", detail)
    detail = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", detail)
    detail = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[REDACTED]", detail)
    return " ".join(detail.split())[:600]


def job_error(record, config):
    detail = record.get("error")
    if isinstance(detail, dict):
        detail = next((detail[name] for name in ("detail", "message", "error", "title") if isinstance(detail.get(name), str) and detail[name]), "")
    if not isinstance(detail, str) or not detail:
        output = record.get("output")
        detail = next((output[name] for name in ("error", "message", "detail") if isinstance(output.get(name), str) and output[name]), "") if isinstance(output, dict) else ""
    detail = _safe_detail(detail, api_key(config))
    message = f"Runpod job {record.get('id', '?')} ended as {record.get('status', 'FAILED')}."
    if detail:
        message += " " + detail
    message += " Use Settings > Runpod > Jobs to allow a new generation."
    logger.warning("Runpod job %s | %s | %s | %s", record.get("id"), record.get("endpoint"), record.get("status"), detail or "No error detail returned")
    error = RunpodError(message)
    error.response_detail = detail
    return error


def configuration(settings):
    return {**RUNPOD_DEFAULTS, **settings.get("runpod", {})}


def api_key(config):
    try:
        key = str(config.get("api_key") or reveal(config.get("api_key_encrypted", "")) or os.environ.get("RUNPOD_API_KEY", "")).strip()
    except (ValueError, RuntimeError) as exc:
        raise RunpodError(str(exc)) from exc
    if not key:
        raise RunpodError("Enter your Runpod API key in Settings > Video Storyboard > Runpod.")
    return key


def endpoint_id(value):
    value = str(value).strip().rstrip("/")
    if value.startswith(API_ROOT + "/"):
        value = value[len(API_ROOT) + 1:].split("/")[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise RunpodError("Enter a Runpod endpoint ID or an api.runpod.ai/v2 endpoint URL.")
    return value


def request(config, endpoint, operation, payload=None, *, expected_error=None):
    endpoint = endpoint_id(endpoint)
    url = f"{API_ROOT}/{endpoint}/{operation}"
    key = api_key(config)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "User-Agent": "LocalText2Voice/Runpod", "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = _response_detail(exc, key)
        messages = {401: "Runpod rejected authentication or access to this operation.", 403: "Runpod access denied.", 404: "Runpod endpoint or job not found; the result may have expired.", 429: "Runpod rate limit reached. Try again shortly."}
        message = messages.get(exc.code, "Runpod request failed.")
        error = RunpodError(f"{message} HTTP {exc.code} · {req.get_method()} {endpoint}/{operation}" + (f" · {detail}" if detail else ""))
        error.status_code = exc.code
        error.response_detail = detail
        if expected_error != (exc.code, detail.lower()):
            logger.warning("Runpod HTTP %s | %s %s | %s", exc.code, req.get_method(), url, detail or "No error detail returned")
        raise error from exc
    except (OSError, ValueError) as exc:
        raise RunpodError("Runpod connection interrupted or invalid response. Your recorded job can be resumed.") from exc
    if not isinstance(result, dict):
        raise RunpodError("Runpod returned an unexpected response.")
    return result


def prepare(settings, role):
    config = configuration(settings)
    api_key(config)
    endpoint = endpoint_id(config[f"{role}_endpoint"])
    return {"provider": "runpod", "model": endpoint}


def check_connection(settings):
    config = configuration(settings)
    results = {}
    for role in ("image", "edit", "video"):
        endpoint = endpoint_id(config[f"{role}_endpoint"])
        if endpoint not in PUBLIC_ENDPOINTS:
            results[role] = request(config, endpoint, "health")
            continue
        # Public model consumers cannot inspect the owner's worker statistics:
        # /health returns 401 even for valid API keys. A GET for a fresh random
        # job ID verifies authentication without submitting any billable job.
        # Accept only the specific authenticated "job not found" response;
        # an unknown endpoint or a generic routing 404 is not a successful check.
        try:
            request(config, endpoint, f"status/{uuid.uuid4()}-u1", expected_error=(404, "job not found"))
        except RunpodError as exc:
            if exc.status_code != 404 or exc.response_detail.lower() != "job not found":
                raise
        results[role] = {"authenticated": True, "probe": "status", "generation_verified": False}
        logger.info("Runpod authentication verified | %s | public status probe; no generation submitted", endpoint)
    return results


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _read(path):
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunpodError(f"Cannot read Runpod job record: {path}") from exc


def file_digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def image_digest(path):
    # The editor re-encodes PNGs. Match actual pixels so an unchanged frame
    # retains its remote URL through copies, metadata changes and acceptance.
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as image:
            pixels = ImageOps.exif_transpose(image).convert("RGBA")
            return hashlib.sha256(str(pixels.size).encode() + pixels.tobytes()).hexdigest()
    except (OSError, ValueError) as exc:
        raise RunpodError("Cannot read the reference image.") from exc


def _asset_index():
    from app.utils.paths import large_assets_root
    return large_assets_root() / "runpod" / "assets"


def jobs_directory():
    from app.utils.paths import large_assets_root
    return large_assets_root() / "runpod" / "jobs"


def remember_asset(path, url, expires=None):
    _write(_asset_index() / (image_digest(path) + ".json"), {"url": url, "expires": expires or time.time() + 6 * 86400})


def source_url(path, config):
    path = Path(path)
    if not path.is_file():
        raise RunpodError("The source image does not exist.")
    digest = image_digest(path)
    cached = _read(_asset_index() / (digest + ".json"))
    if cached.get("url") and float(cached.get("expires", 0)) > time.time():
        return cached["url"]
    from app.core.storyboard_temporary_storage import storage_mode
    mode = storage_mode(config)
    if mode == "managed":
        from app.core import storyboard_temporary_storage as storage
        uploads = _temporary_uploads.get()
        if uploads is not None:
            existing = next((asset for asset in uploads if asset.get("digest") == digest), None)
            if existing:
                return existing["url"]
        try:
            asset = storage.upload(path, config)
        except (storage.TemporaryStorageError, ValueError, RuntimeError) as exc:
            raise RunpodError(str(exc)) from exc
        asset["digest"] = digest
        if uploads is not None:
            uploads.append(asset)
        # Each job owns its uploads. A different concurrent job must not lose its
        # reference when the first job completes and deletes its objects.
        return asset["url"]
    if mode == "disabled":
        raise RunpodError("This local image needs a temporary URL. Select free temporary storage or your own S3 storage in the Runpod profile.")
    if not all(config.get(k) for k in ("s3_endpoint", "s3_bucket", "s3_access_key", "s3_secret_encrypted")):
        raise RunpodError("This local image needs a temporary URL. Select free temporary storage or configure your own S3 storage in the Runpod profile. Local references are uploaded only when generating.")
    try:
        import boto3
        from botocore.config import Config
        client = boto3.client("s3", endpoint_url=config["s3_endpoint"], region_name=config.get("s3_region") or "auto",
            aws_access_key_id=config["s3_access_key"], aws_secret_access_key=reveal(config["s3_secret_encrypted"]),
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, connect_timeout=15, read_timeout=60, retries={"max_attempts": 2}))
        key = f"localtext2voice/references/{digest}{path.suffix.lower()}"
        client.upload_file(str(path), config["s3_bucket"], key, ExtraArgs={"ContentType": mimetypes.guess_type(path.name)[0] or "image/png"})
        url = client.generate_presigned_url("get_object", Params={"Bucket": config["s3_bucket"], "Key": key}, ExpiresIn=86400)
    except ImportError as exc:
        raise RunpodError("S3 support requires boto3. Install the application dependencies.") from exc
    except Exception as exc:
        raise RunpodError("Cannot upload the reference image. Check the S3 endpoint, bucket and credentials.") from exc
    _write(_asset_index() / (digest + ".json"), {"url": url, "expires": time.time() + 23 * 3600})
    return url


def _release_temporary(record, config, path=None):
    from app.core import storyboard_temporary_storage as storage
    for asset in record.get("temporary_assets", []):
        if asset.get("deleted"):
            continue
        try:
            asset["deleted"] = storage.delete(asset, config)
        except Exception:
            # Cleanup must not discard a paid result. Server expiry is the
            # fallback if the PC goes offline or the access code changes.
            asset["deleted"] = False
    if path is not None:
        _write(path, record)


def output_media_url(output, role):
    """Accept the documented model field and the live public endpoint envelope."""
    field = "video_url" if role == "video" else "image_url"
    # The live Z-Image endpoint returns {"result": "https://...", "cost": ...}.
    # Public endpoints can use this same envelope for image edits and videos.
    # Do not recursively pick arbitrary URLs (inputs or previews) from a result.
    for value in (output.get(field), output.get("result")):
        if not isinstance(value, str):
            continue
        try:
            parsed = urllib.parse.urlsplit(value)
            if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
                return value
        except ValueError:
            pass
    fields = ", ".join(str(key)[:40] for key in list(output)[:12]) or "none"
    logger.warning("Runpod completed without a supported media URL | role=%s | output fields=%s", role, fields)
    raise RunpodError(f"Runpod completed the job but returned no supported HTTPS media URL ({field} or result). Output fields: {fields}. The saved job can be recovered without submitting it again.")


def download(url, target, cancelled=None):
    if not isinstance(url, str) or not url.startswith("https://"):
        raise RunpodError("Runpod did not return an HTTPS media URL.")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    try:
        # Media hosts never receive the Runpod API key.
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "LocalText2Voice"}), timeout=120) as response, temp.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                if cancelled and cancelled():
                    raise RunpodError("Download cancelled. The completed job is saved for recovery.")
                handle.write(chunk)
        if not temp.stat().st_size:
            raise RunpodError("Runpod returned an empty media file.")
        if target.suffix.lower() == ".png":
            from PIL import Image
            with Image.open(temp) as img:
                img.verify()
        temp.replace(target)
    except RunpodError:
        raise
    except Exception as exc:
        raise RunpodError("Cannot download the generated media. Retry to recover the same job without generating again.") from exc
    finally:
        temp.unlink(missing_ok=True)


def execute(settings, role, payload, target, *, identity=None, status=None, cancelled=None):
    config = configuration(settings)
    endpoint = endpoint_id(config[f"{role}_endpoint"])
    account = hashlib.sha256(api_key(config).encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps([str(Path(target).parent.resolve()), endpoint, account, identity or payload], sort_keys=True).encode()).hexdigest()
    record_path = jobs_directory() / (fingerprint + ".json")
    record = _read(record_path)
    if cancelled and cancelled():
        raise RunpodError("Runpod generation cancelled.")
    if record.get("status") == "SUBMITTING" and not record.get("id"):
        raise RunpodError("Runpod submission outcome is unknown. Check your Runpod requests, then use Settings > Runpod > Jobs to allow a new submission.")
    if record.get("status") in TERMINAL:
        _release_temporary(record, config, record_path)
        raise job_error(record, config)
    if not record.get("id"):
        uploads = []
        scope = _temporary_uploads.set(uploads)
        try:
            payload = payload() if callable(payload) else payload
            if cancelled and cancelled():
                raise RunpodError("Runpod generation cancelled before submission.")
        except Exception:
            _release_temporary({"temporary_assets": uploads}, config)
            raise
        finally:
            _temporary_uploads.reset(scope)
        record = {"status": "SUBMITTING", "endpoint": endpoint, "role": role, "created": time.time(), "target": str(target), "account": account, "input_snapshot": identity or payload,
                  "temporary_assets": [{key: value for key, value in asset.items() if key != "url"} for asset in uploads]}
        _write(record_path, record)
        try:
            result = request(config, endpoint, "run", {"input": payload, "policy": {"executionTimeout": int(config["timeout_seconds"]) * 1000, "ttl": max(86400000, int(config["timeout_seconds"]) * 2000)}})
        except RunpodError as exc:
            if exc.status_code in {400, 401, 403, 404, 422, 429}:
                record["status"] = "REJECTED"
                _release_temporary(record, config)
                _write(record_path, record)
            raise
        record.update(result)
        _write(record_path, record)
        if not record.get("id"):
            raise RunpodError("Runpod accepted no job ID. Check your requests before retrying.")
    deadline = time.monotonic() + int(config["timeout_seconds"])
    while record.get("status") != "COMPLETED":
        if record.get("status") in TERMINAL:
            _release_temporary(record, config, record_path)
            raise job_error(record, config)
        if cancelled and cancelled():
            request(config, endpoint, f"cancel/{record['id']}", {})
            record["status"] = "CANCELLED"
            _release_temporary(record, config)
            _write(record_path, record)
            raise RunpodError("Runpod job cancelled.")
        if time.monotonic() > deadline:
            raise RunpodError("Stopped waiting for Runpod. The job may still be running; retry to resume it or cancel it in Runpod.")
        if status:
            status("Runpod · " + ("In queue" if record.get("status") == "IN_QUEUE" else "Generating"))
        record.update(request(config, endpoint, f"status/{record['id']}"))
        _write(record_path, record)
        if record.get("status") in TERMINAL:
            _release_temporary(record, config, record_path)
            raise job_error(record, config)
        if record.get("status") != "COMPLETED":
            for _ in range(10):
                if cancelled and cancelled():
                    break
                time.sleep(0.25)
    _release_temporary(record, config, record_path)
    output = record.get("output", {})
    if not isinstance(output, dict):
        raise RunpodError("This endpoint does not use the expected public model response format.")
    url = output_media_url(output, role)
    if status:
        status("Runpod · Downloading")
    download(url, target, cancelled)
    if role != "video":
        remember_asset(target, url, float(record.get("created", time.time())) + 6 * 86400)
    return {"provider": "runpod", "prompt_id": record["id"], "model": endpoint,
            "cost_usd": output.get("cost"), "remote_url": url, "job_record": str(record_path)}


def manage_job(settings, path, action):
    config = configuration(settings)
    record = _read(path)
    account = hashlib.sha256(api_key(config).encode()).hexdigest()
    if record.get("account") != account:
        raise RunpodError("This job belongs to a different Runpod API key.")
    if action == "check":
        if not record.get("id"):
            raise RunpodError("No job ID was received. Check requests in the Runpod console.")
        record.update(request(config, record["endpoint"], f"status/{record['id']}"))
        _write(path, record)
    elif action == "cancel":
        if not record.get("id") or record.get("status") in TERMINAL | {"COMPLETED"}:
            raise RunpodError("There is no active job to cancel.")
        request(config, record["endpoint"], f"cancel/{record['id']}", {})
        record["status"] = "CANCELLED"
        _write(path, record)
    if record.get("status") in TERMINAL | {"COMPLETED"}:
        _release_temporary(record, config, path)
    return record


def video_duration(seconds, config=None):
    return duration_for(seconds, config or RUNPOD_DEFAULTS)


def estimate_cost(settings, *, images=0, edits=0, durations=()):
    config = configuration(settings)
    image_rate = IMAGE_MODELS.get(image_model_id(config), {}).get("price")
    if (images and image_rate is None) or (edits and config["edit_endpoint"] != RUNPOD_DEFAULTS["edit_endpoint"]):
        return None
    rate = VIDEO_MODELS.get(model_id(config), {}).get("rates", {}).get(config["video_size"])
    if durations and rate is None:
        return None
    return images * (image_rate or 0) + edits * 0.02 + sum(video_duration(d, config) for d in durations) * (rate or 0)
