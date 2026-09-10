"""Optional managed reference uploads. No Cloudflare or Runpod keys leave the PC."""
from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.core.storyboard_credentials import reveal
from app.core.installation_identity import installation_token, storage_consent

DEFAULT_SERVICE_URL = "https://localtext2voice-temporary-assets.estebandezafra.workers.dev"
MAX_BYTES = 15 * 1024 * 1024


class TemporaryStorageError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the beta credential to another origin.


def service_url(config):
    value = str(config.get("temporary_storage_url") or DEFAULT_SERVICE_URL).strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise TemporaryStorageError("Temporary storage requires an HTTPS service address without a path.")
    return value


def access_code(config):
    # Existing beta credentials remain usable; new installations need no code.
    value = reveal(config.get("temporary_storage_token_encrypted", "")).strip() or installation_token()
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise TemporaryStorageError("Cannot read this installation's temporary storage identity.")
    return value


def credential_id(config):
    return hashlib.sha256(access_code(config).encode()).hexdigest()


def storage_mode(config):
    mode = config.get("reference_storage", "auto")
    return ("s3" if config.get("s3_endpoint") else "managed") if mode == "auto" else mode


def register(config):
    if not storage_consent(service_url(config)):
        raise TemporaryStorageError("Accept temporary image storage in Settings > Video Storyboard > Runpod before uploading references.")
    # Idempotent and revocation-aware. No Runpod API key or machine information.
    return request(config, "/v1/register", method="POST", data=b'{"consent_version":1}')


def request(config, path, *, data=None, method="GET", content_type="application/json"):
    req = urllib.request.Request(service_url(config) + path, data=data, method=method, headers={
        "Authorization": "Bearer " + access_code(config), "Content-Type": content_type,
        "Accept": "application/json", "User-Agent": "LocalText2Voice/TemporaryStorage",
    })
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=90) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError("Invalid response")
        return result
    except urllib.error.HTTPError as exc:
        messages = {401: "Invalid or expired temporary storage access code.",
                    403: "Temporary storage access denied.", 413: "The reference image exceeds the temporary storage limits.",
                    415: "Temporary storage did not accept this image.",
                    429: "Temporary storage quota or upload limit reached. Try later or select your own S3 storage.",
                    503: "Temporary storage is paused or unavailable. Try later or select your own S3 storage."}
        raise TemporaryStorageError(messages.get(exc.code, f"Temporary storage returned HTTP {exc.code}.")) from exc
    except (OSError, ValueError) as exc:
        raise TemporaryStorageError("Cannot connect to temporary storage. Try later or select your own S3 storage.") from exc


def normalized_png(path):
    from PIL import Image, ImageOps
    try:
        with Image.open(Path(path)) as original:
            if max(original.size) > 4096 or original.width * original.height > 4096 * 4096:
                raise TemporaryStorageError("Temporary references must be at most 4096 x 4096 pixels. Resize the image or use your own storage.")
            oriented = ImageOps.exif_transpose(original)
            mode = "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
            pixels = oriented.convert(mode)
            # A fresh image strips EXIF, text chunks, filenames and other metadata.
            clean = Image.frombytes(mode, pixels.size, pixels.tobytes())
            stream = io.BytesIO()
            clean.save(stream, format="PNG")
        data = stream.getvalue()
        if len(data) > MAX_BYTES:
            raise TemporaryStorageError("The PNG reference exceeds 15 MB. Resize the image or use your own storage.")
        return data
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise TemporaryStorageError("Cannot prepare the reference image for temporary storage.") from exc


def upload(path, config):
    origin = service_url(config)
    register(config)
    result = request(config, "/v1/assets", data=normalized_png(path), method="POST", content_type="image/png")
    asset_id = result.get("id", "")
    url = result.get("url", "")
    parsed = urllib.parse.urlsplit(url)
    if not re.fullmatch(r"[a-f0-9-]{36}", asset_id) or f"{parsed.scheme}://{parsed.netloc}" != origin or parsed.path != f"/media/{asset_id}":
        raise TemporaryStorageError("Temporary storage returned an invalid image address.")
    return {"id": asset_id, "url": url, "expires": float(result["expires"]),
            "service_url": origin, "credential_id": credential_id(config)}


def delete(asset, config):
    if asset.get("service_url") != service_url(config) or asset.get("credential_id") != credential_id(config):
        return False  # Another profile cannot delete the original user's reference.
    asset_id = str(asset.get("id", ""))
    if not re.fullmatch(r"[a-f0-9-]{36}", asset_id):
        return False
    request(config, "/v1/assets/" + asset_id, method="DELETE")
    return True
