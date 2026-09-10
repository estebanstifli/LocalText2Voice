from __future__ import annotations

from collections.abc import Mapping


# Runpod's proxy is protected by Cloudflare and rejects Python's default
# ``Python-urllib/x.y`` identity even when the Bearer token is valid. Keep one
# browser-compatible identity for every ComfyUI request made by the app.
COMFYUI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
COMFYUI_JSON_ACCEPT = "application/json, text/plain, */*"


def comfyui_request_headers(
    extra: Mapping[str, str] | None = None,
    *,
    accept: str = COMFYUI_JSON_ACCEPT,
    content_type: str | None = None,
) -> dict[str, str]:
    """Build the common HTTP identity used for local and remote ComfyUI."""
    headers = {
        "User-Agent": COMFYUI_USER_AGENT,
        "Accept": accept,
    }
    if content_type:
        headers["Content-Type"] = content_type
    if extra:
        headers.update({str(key): str(value) for key, value in extra.items()})
    return headers
