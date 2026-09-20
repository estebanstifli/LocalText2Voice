"""Classify provider errors without retrying content or configuration failures."""
import json
import re


def generation_error_details(error):
    text = str(error)
    body, status, request_id = {}, None, ""
    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = status or getattr(current, "status_code", None)
        request_id = request_id or getattr(current, "request_id", "")
        value = getattr(current, "body", None)
        if isinstance(value, dict):
            body = value.get("error", value)
        current = current.__cause__ or current.__context__
    if not body:
        for match in re.finditer(r"\{", text):
            try:
                value, _ = json.JSONDecoder().raw_decode(text[match.start():])
                if isinstance(value, dict):
                    body = value.get("error", value)
                    break
            except ValueError:
                continue
    body = body if isinstance(body, dict) else {}
    if not status:
        match = re.search(r"(?i)\b(?:http|status code)\s*[:=]?\s*(\d{3})\b", text)
        status = int(match[1]) if match else None
    code = str(body.get("code") or body.get("status") or "")
    lowered = text.casefold()
    if not code and ("moderation_blocked" in lowered or "rejected by the safety system" in lowered):
        code = "moderation_blocked"
    if re.search(r'(?:finishreason|finish_reason|blockreason|block_reason)["\'\s:]+(?:image_safety|safety|prohibited_content|blocklist|image_prohibited_content)\b', lowered):
        code = "moderation_blocked"
    if not code:
        if any(v in lowered for v in ("insufficient_quota", "quota exceeded", "billing", "credit balance")):
            code = "quota_or_billing"
        elif status == 429 or "resource_exhausted" in lowered or "http 429" in lowered:
            code = "rate_limit"
        elif status in {502, 503, 504} or "overloaded" in lowered or "http 503" in lowered:
            code = "provider_unavailable"
        elif "timeout" in lowered or "timed out" in lowered:
            code = "timeout"
    details = body.get("moderation_details") or {}
    stage = details.get("moderation_stage", "unknown") if isinstance(details, dict) else "unknown"
    if not request_id:
        match = re.search(r"\breq_[a-zA-Z0-9_-]+", text)
        request_id = match.group() if match else ""
    nonretryable = code in {"moderation_blocked", "quota_or_billing"} or body.get("type") == "image_generation_user_error" or any(v in lowered for v in ("insufficient_quota", "quota exceeded", "billing", "invalid_api_key"))
    transient = not nonretryable and (code in {"rate_limit", "provider_unavailable", "timeout"} or status in {429, 500, 502, 503, 504} or bool(re.search(r"\b(?:http|status code)\s*[:=]?\s*(?:429|500|502|503|504)\b", lowered)) or any(v in lowered for v in ("connection reset", "temporarily unavailable", "connection aborted")))
    return {"code": code or "generation_failed", "moderation_stage": stage, "request_id": request_id,
            "retryable": transient, "http_status": status, "error": text[:5000]}


def is_moderation_error(error):
    return generation_error_details(error)["code"] == "moderation_blocked"


def redact_generation_error(text, settings):
    """Do not persist credentials a provider may include in an error message."""
    def secrets(value):
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if isinstance(item, dict):
                yield from secrets(item)
            elif any(token in key.casefold() for token in ("api_key", "password", "secret", "token")):
                if isinstance(item, str) and item:
                    yield item
    for secret in sorted(secrets(settings), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return re.sub(r"(?i)Bearer\s+[a-z0-9._~+/=-]+", "Bearer [redacted]", text)


def missing_image_error(response, message):
    """Keep diagnostic fields from empty provider replies without media or credentials."""
    def clean(value, depth=0):
        if depth > 8:
            return "…"
        if isinstance(value, dict):
            return {str(k): clean(v, depth + 1) for k, v in value.items()
                    if not any(word in str(k).casefold() for word in
                               ("b64", "base64", "imagedata", "inline_data", "inlinedata", "url", "token", "key", "authorization", "prompt", "request"))
                    or str(k) in {"promptFeedback", "prompt_feedback", "request_id"}}
        if isinstance(value, list):
            return [clean(v, depth + 1) for v in value[:20]]
        if isinstance(value, str):
            return value[:1000]
        return value
    detail = json.dumps(clean(response), ensure_ascii=False, default=str)[:4000]
    return message + " Provider response: " + detail
