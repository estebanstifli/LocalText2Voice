"""Combined free reports, followed by append-only character discovery."""
import math
import re


def balanced_passages(text, limit):
    count = max(1, math.ceil(len(text) / limit))
    if count == 1:
        return [text]
    paragraphs = [m.end() for m in re.finditer(r"\n\s*\n", text)]
    sentences = [m.end() for m in re.finditer(r"(?<=[.!?])\s+", text)]
    words = [m.end() for m in re.finditer(r"\s+", text)]
    cuts = [0]
    for i in range(1, count):
        lo = max(cuts[-1] + 1, len(text) - (count - i) * limit)
        hi = min(cuts[-1] + limit, len(text) - (count - i))
        ideal = len(text) * i / count
        chosen = None
        for boundaries in (paragraphs, sentences, words):
            options = [b for b in boundaries if lo <= b <= hi]
            if options:
                chosen = min(options, key=lambda b: abs(b - ideal))
                break
        cuts.append(chosen if chosen is not None else min(hi, max(lo, round(ideal))))
    cuts.append(len(text))
    return [text[a:b] for a, b in zip(cuts, cuts[1:])]


def split_report(answer, warn):
    sections = {"characters": [], "changes": [], "summary": []}
    aliases = {"personajes": "characters", "cambios": "changes", "resumen": "summary"}
    active = None
    for line in answer.splitlines(keepends=True):
        heading = line.strip().strip("#* :").lower().split("(")[0].rstrip(" :")
        heading = aliases.get(heading, heading)
        if heading in sections:
            active = heading
        elif active:
            sections[active].append(line)
    result = {k: "".join(v).strip() for k, v in sections.items()}
    if not result["characters"]:
        warn("Combined report has no Characters section; preserving the complete answer for review.")
        result["characters"] = answer
    if not result["summary"]:
        warn("Combined report has no story summary section; review the saved report.")
    return result


def new_character_text(answer):
    # Keep provider replies verbatim in logs/files, but don't turn an explicit
    # 'none' response or explanatory Note into a character during conversion.
    clean = answer.strip().strip("*")
    if re.match(r"(?i)^(?:none\b|no new characters\b|there are no new characters\b)", clean):
        return ""
    return re.split(r"(?im)^\s*\**(?:note|nota)\**\s*:", answer)[0].strip()
