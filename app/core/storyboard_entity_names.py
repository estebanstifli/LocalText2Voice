"""Conservative identity matching; never choose the first fuzzy candidate."""
import re
import unicodedata


def key(value):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", str(value)).casefold()))


def base_name(value):
    return re.sub(r"(?:\s*\([^()]*\))+\s*$", "", str(value)).strip()


def variants(record):
    values = [record.get("name", ""), *record.get("aliases", [])]
    for value in list(values):
        values.append(base_name(value))
    for value in list(values):
        if value.lower().startswith("the "):
            values.append(value[4:])
    return list(dict.fromkeys(v for v in values if v))


def candidates(reference, records):
    literal = str(reference).strip().casefold()
    exact = [r for r in records if literal in {
        str(v).strip().casefold() for v in [r.get("id", ""), r.get("name", ""), *r.get("aliases", []),
                                           *(s.get("id", "") for s in r.get("states", []))]}]
    if exact:
        return exact
    normalized = key(reference).removeprefix("the ")
    return [r for r in records if normalized and normalized in {key(v).removeprefix("the ") for v in variants(r)}]


def resolve(reference, records):
    matches = candidates(reference, records)
    return matches[0] if len(matches) == 1 else None


def clean_names(records):
    """Clean only unambiguous display names, keeping IDs and original aliases."""
    bases = [key(base_name(r["name"])) for r in records]
    for record, base in zip(records, bases):
        old = record["name"]
        short = base_name(old)
        if short and short != old and bases.count(base) == 1:
            record["name"] = short
            record["aliases"] = list(dict.fromkeys([*record.get("aliases", []), old]))
            record.setdefault("name_qualifier", old[len(short):].strip())


def mentioned_records(text, records):
    matches = []
    for record in records:
        for value in variants(record):
            if resolve(value, records) is not record:
                continue
            # Whitespace/punctuation are flexible, words themselves are not fuzzy.
            words = re.findall(r"\w+", value)
            if not words:
                continue
            pattern = r"(?<!\w)" + r"[\W_]+".join(map(re.escape, words)) + r"(?!\w)"
            for match in re.finditer(pattern, text, re.I):
                matches.append((match.start(), match.end(), record))
    occupied, result = [], []
    for start, end, record in sorted(matches, key=lambda m: -(m[1] - m[0])):
        if any(start < b and end > a for a, b in occupied):
            continue
        occupied.append((start, end))
        if record not in result:
            result.append(record)
    return result


def resolve_references(visuals, assignments, continuity, request, warn, check):
    """Batch only unresolved references. Never cache contextual pronouns as aliases."""
    pending = []
    for index, visual in enumerate(visuals):
        for collection in ("characters", "locations"):
            resolved = []
            for reference in visual.get(collection, []):
                record = resolve(reference, continuity[collection])
                if record:
                    resolved.append(record["id"])
                    continue
                options = candidates(reference, continuity[collection]) or continuity[collection]
                warn(f"Frame {index + 1}: unresolved {collection} reference '{reference}'.")
                if not options or len(options) > 24:
                    warn(f"Reference '{reference}' needs manual review; no safe small candidate set.")
                    continue
                pending.append({"position": len(pending), "frame": index, "collection": collection,
                    "reference": reference, "narration": assignments[index]["narration"],
                    "candidates": [{"id": r["id"], "name": r["name"]} for r in options]})
            visual[collection] = list(dict.fromkeys(resolved))
    for start in range(0, len(pending), 6):
        check()
        batch = pending[start:start + 6]
        ids = sorted({r["id"] for item in batch for r in item["candidates"]})
        schema = {"type": "object", "additionalProperties": False, "required": ["bindings"], "properties": {
            "bindings": {"type": "array", "minItems": len(batch), "maxItems": len(batch), "items": {
                "type": "object", "additionalProperties": False, "required": ["position", "entity_id"],
                "properties": {"position": {"type": "integer", "enum": [b["position"] for b in batch]},
                               "entity_id": {"type": "string", "enum": ["", *ids]}}}}}}
        result = request(schema,
            "Match each reference to one supplied candidate using ONLY its narration. "
            "Return an empty entity_id if unclear or none applies. Do not invent identities or change profiles.",
            [{k: v for k, v in b.items() if k not in ("frame", "collection")} for b in batch],
            "conversation: resolve ambiguous entity references")
        rows = result.get("bindings", []) if isinstance(result, dict) else []
        for item in batch:
            found = [r for r in rows if isinstance(r, dict) and r.get("position") == item["position"]]
            selected = found[0].get("entity_id") if len(found) == 1 else ""
            if selected and selected in {r["id"] for r in item["candidates"]}:
                target = visuals[item["frame"]][item["collection"]]
                if selected not in target:
                    target.append(selected)
                warn(f"Reference '{item['reference']}' resolved by LLM as '{selected}' for this frame only.")
            else:
                warn(f"Reference '{item['reference']}' remains unresolved; manual review required.")
