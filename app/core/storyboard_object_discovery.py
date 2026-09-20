"""Append-only object identity discovery across audiobook passages."""
import re


OBJECT_ADDITIONS = (
    "Compare the object summaries below. Return ONLY physical objects in SECOND TEXT that are absent from FIRST TEXT. "
    "Match identity, not wording: a portrait, painting or canvas of the same person can be the same object. "
    "Changes in appearance, condition, location or owner do not create another object. "
    "Do not merge genuinely distinct objects, copies or portraits of different people. "
    "Keep the first canonical name for known objects and do not repeat them. "
    "For each new object give its specific name and stated visual description. "
    "Do not invent details. If there are no new objects, return exactly NONE. No commentary."
)


def object_addition_text(answer):
    clean = str(answer or "").strip().strip("*").strip()
    if re.fullmatch(r"(?i)(?:none|no new objects|there are no new objects|ninguno|ninguna|no hay objetos nuevos)[.!]?", clean):
        return ""
    return re.split(r"(?im)^\s*\**(?:note|nota)\**\s*:", clean)[0].strip()


def unify_object_reports(reports, ask, publish=None):
    """Keep raw reports intact; compare each passage against all accepted objects."""
    summary = ""
    for number, report in enumerate(reports, 1):
        candidate = object_addition_text(report.get("objects", ""))
        if not candidate:
            continue
        addition = candidate if not summary else object_addition_text(ask(
            [], OBJECT_ADDITIONS + "\n\nFIRST TEXT:\n" + summary + "\n\nSECOND TEXT:\n" + candidate,
            f"conversation: new important objects from block {number}/{len(reports)}"))
        if addition:
            summary = "\n\n".join(filter(None, (summary, addition)))
        if publish:
            publish(summary)
    return summary


LOCATION_ADDITIONS = (
    "Compare the location summaries below. Return ONLY places in SECOND TEXT absent from FIRST TEXT. "
    "Match physical identity, not wording: the drawing room, salon and living room of the same house may be the same place. "
    "Changes in lighting, time, decoration, condition or occupants do not create another location. "
    "Do not merge distinct rooms, different buildings or similar places at different addresses. "
    "Keep the first canonical name for known locations and do not repeat them. "
    "For each new location give its specific name and stated visual description. "
    "Do not invent details. If there are no new locations, return exactly NONE. No commentary."
)


def unify_location_reports(reports, ask, publish=None):
    """Reuse the same cumulative identity comparison for physical places."""
    summary = ""
    for number, report in enumerate(reports, 1):
        candidate = location_addition_text(report.get("locations", ""))
        if not candidate:
            continue
        addition = candidate if not summary else location_addition_text(ask(
            [], LOCATION_ADDITIONS + "\n\nFIRST TEXT:\n" + summary + "\n\nSECOND TEXT:\n" + candidate,
            f"conversation: new locations from block {number}/{len(reports)}"))
        if addition:
            summary = "\n\n".join(filter(None, (summary, addition)))
        if publish:
            publish(summary)
    return summary


def location_addition_text(answer):
    clean = str(answer or "").strip().strip("*").strip()
    if re.fullmatch(r"(?i)(?:no new locations|there are no new locations|no hay lugares nuevos)[.!]?", clean):
        return ""
    return object_addition_text(clean)
