"""Resolve scene references consistently for preview and generation."""
from copy import deepcopy
from pathlib import Path
from app.core.storyboard_entity_names import mentioned_records, resolve


def scene_with_references(scene, plan, settings=None):
    result = deepcopy(scene)
    overrides = result.setdefault("generation_overrides", {})
    references = list(overrides.get("reference_images") or [])
    paths = {str(Path(r["path"]).resolve()).casefold() for r in references if isinstance(r, dict) and r.get("path")}
    text = " ".join(str(v or "") for v in (scene.get("prompt"), scene.get("shot"), overrides.get("raw_prompt")))
    continuity = plan.get("continuity") or {}
    automatic = (settings or {}).get("auto_character_references", True)
    from app.core.runpod_image_models import image_model_id
    collections = ["characters", "objects"]
    if ((settings or {}).get("image_provider") == "runpod"
            and image_model_id((settings or {}).get("runpod", {})) == "qwen-image-edit-2511"):
        collections.append("locations")
    for collection in collections:
        if collection == "characters" and not automatic:
            continue
        selected = {str(v).casefold() for v in scene.get(collection, [])}
        if collection == "objects":
            selected.update(str(v).casefold() for v in overrides.get("object_state_ids", []))
        records = [r for r in continuity.get(collection, []) if isinstance(r, dict)]
        mentioned = mentioned_records(text, records)
        assigned = [resolve(value, records) for value in selected]
        for record in records:
            if record not in mentioned and record not in assigned:
                continue
            path = str(record.get("reference_image_path") or "")
            if not path or not Path(path).is_file():
                continue
            key = str(Path(path).resolve()).casefold()
            if key in paths:
                continue
            references.append({"path": path, "label": str(record.get("name") or record.get("id")), "kind": collection[:-1]})
            paths.add(key)
    overrides["reference_images"] = references
    return result


def referenced_entity(record, scene):
    refs = (scene.get("generation_overrides") or {}).get("reference_images") or []
    name = str(record.get("name") or "").casefold()
    path = str(record.get("reference_image_path") or "")
    return any(isinstance(ref, dict) and (
        str(ref.get("label") or "").casefold() == name
        or (path and str(Path(str(ref.get("path") or "")).resolve()).casefold() == str(Path(path).resolve()).casefold())
    ) for ref in refs)
