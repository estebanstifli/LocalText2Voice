"""Headless storyboard editing and durable visual jobs. No narrative LLM calls."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.core.audiobook_store import AudiobookStore
from app.core.video_storyboard_project import load_storyboard_state, save_storyboard_state, _atomic_json
from app.core.video_storyboard_source import build_storyboard_narration_timeline
from app.core.video_storyboard_styles import storyboard_styles

COLLECTIONS = {"character": "characters", "location": "locations", "object": "objects", "group": "groups", "era": "eras"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def public(value):
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if not any(word in k.lower() for word in ("secret", "token", "api_key", "password", "encrypted"))}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


class StoryboardService:
    def __init__(self, settings_manager, store=None):
        self.settings_manager = settings_manager
        self.store = store or AudiobookStore()
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="storyboard")
        self.events = {}
        self.audio_hashes = {}

    def close(self):
        for event in self.events.values():
            event.set()
        self.executor.shutdown(wait=True, cancel_futures=False)

    def call(self, operation, arguments):
        if not operation.startswith("sb_") or not callable(getattr(self, operation, None)):
            raise ValueError("Unknown storyboard operation")
        with self.lock:
            return public(getattr(self, operation)(**arguments))

    def book(self, project_id):
        book = self.store.get_audiobook(int(project_id)) if str(project_id).isdigit() else self.store.get_audiobook_by_uuid(str(project_id))
        if book is None:
            raise ValueError(f"Project not found: {project_id}")
        return book

    def read(self, project_id):
        book = self.book(project_id)
        state = load_storyboard_state(book.project_dir, recover_pending=False)
        if state is None:
            raise ValueError("No storyboard. Call sb_create_project first.")
        return book, state

    def revision(self, book):
        path = book.project_dir / "storyboard/storyboard.json"
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""

    def change(self, project_id, expected_revision, edit):
        with self.lock:
            book, state = self.read(project_id)
            before = state["_revision"]
            if not expected_revision or before != expected_revision:
                raise OSError("Storyboard changed. Read sb_get_project and pass its revision before editing.")
            result = edit(state)
            issues = self.check(state)
            if issues:
                raise ValueError("; ".join(issues))
            state["plan"]["mcp_managed"] = True
            save_storyboard_state(book.project_dir, state, expected_revision=before)
            return {"project_id": book.id, "revision": self.revision(book), "result": result}

    def settings(self, state):
        from app.core.storyboard_profiles import switch_profile
        from app.core.settings_manager import DEFAULT_SETTINGS
        if callable(getattr(self.settings_manager, "load", None)):
            self.settings_manager.settings = self.settings_manager.load()
        config = deepcopy(self.settings_manager.settings.get("video_storyboard", {}))
        selected = state["plan"].get("generation_profile")
        if selected:
            config = switch_profile(config, selected, DEFAULT_SETTINGS["video_storyboard"])
        for key, value in state["plan"].get("generation_settings", {}).items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(deepcopy(value))
            else:
                config[key] = deepcopy(value)
        config["video"] = {**config.get("video", {}), **state["plan"].get("timeline", {})}
        config["ffmpeg_path"] = self.settings_manager.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe")
        return config

    def audio_hash(self, path):
        audio = Path(path or "")
        if not audio.is_file():
            return ""
        stat = audio.stat()
        key = (str(audio.resolve()), stat.st_size, stat.st_mtime_ns)
        if key not in self.audio_hashes:
            with audio.open("rb") as stream:
                value = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024*1024), b""):
                    value.update(chunk)
                self.audio_hashes[key] = value.hexdigest()
        return self.audio_hashes[key]

    def sb_list_projects(self, query: str = "", offset: int = 0, limit: int = 50) -> dict:
        """Find saved audiobooks/storyboards, including projects created in the GUI."""
        books = [b for b in self.store.list_audiobooks() if query.casefold() in b.title.casefold()]
        return {"total": len(books), "projects": [{"project_id": b.id, "uuid": b.uuid, "title": b.title, "has_storyboard": (b.project_dir / "storyboard/storyboard.json").is_file()} for b in books[max(0, offset):max(0, offset)+min(200, max(1, limit))]]}

    def sb_create_project(self, project_id: str = "", title: str = "Storyboard", text: str = "", audio_path: str = "", voice_start_offset_seconds: float | None = None) -> dict:
        """Create an empty storyboard in an existing audiobook, or a new project. Never analyze text."""
        if project_id:
            book = self.book(project_id)
        else:
            book = self.store.create_audiobook(text, {}, self.store.root_dir / "exports", "none", "single", title=title)
        if load_storyboard_state(book.project_dir, recover_pending=False) is not None:
            raise ValueError("Storyboard already exists; use editing tools.")
        cues, duration = build_storyboard_narration_timeline(self.store.list_segments(book.id), round((voice_start_offset_seconds or 0)*1000))
        state = {"source": {"title": book.title, "text": text or book.source_text, "audio_path": audio_path or book.mix_audio_path or book.clean_audio_path, "narration_cues": [c.as_dict() for c in cues], "duration_seconds": duration, "voice_start_offset_seconds": voice_start_offset_seconds}, "plan": {"style": {}, "continuity": {key: [] for key in COLLECTIONS.values()}, "mcp_managed": True}, "scenes": []}
        state["plan"].update(voice_start_offset_seconds=voice_start_offset_seconds, source_duration_seconds=duration)
        state["source"]["audio_sha256"] = self.audio_hash(state["source"]["audio_path"])
        save_storyboard_state(book.project_dir, state, expected_revision="")
        return self.sb_get_project(str(book.id))

    def sb_get_project(self, project_id: str) -> dict:
        """Read the complete storyboard and revision required for all mutations."""
        book, state = self.read(project_id)
        return {"project_id": book.id, "revision": state["_revision"], "state": state}

    def sb_update_project(self, project_id: str, expected_revision: str, changes: dict) -> dict:
        """Patch plan fields: title, style, base_seed, narrative_context. No implicit analysis."""
        allowed = {"title", "style", "base_seed", "narrative_context"}
        if set(changes) - allowed:
            raise ValueError(f"Allowed fields: {sorted(allowed)}")
        return self.change(project_id, expected_revision, lambda s: s["plan"].update(deepcopy(changes)))

    def sb_set_source(self, project_id: str, expected_revision: str, source: dict) -> dict:
        """Set text/audio/cues/duration/voice offset explicitly. Existing media are marked stale."""
        def edit(s):
            s["source"].update(deepcopy(source))
            if "voice_start_offset_seconds" in source:
                s["plan"]["voice_start_offset_seconds"] = source["voice_start_offset_seconds"]
            if "audio_path" in source:
                s["source"]["audio_sha256"] = self.audio_hash(source["audio_path"])
            for scene in s["scenes"]:
                scene.setdefault("generation_overrides", {})["stale"] = True
        return self.change(project_id, expected_revision, edit)

    def sb_get_timed_text(self, project_id: str, offset: int = 0, limit: int = 100, start_seconds: float | None = None, end_seconds: float | None = None, voice_start_offset_seconds: float | None = None) -> dict:
        """Read narration cues without AI. Segment times, not word alignment. Supply offset if no saved clock exists."""
        book = self.book(project_id)
        state = load_storyboard_state(book.project_dir, recover_pending=False) or {}
        source = state.get("source", {})
        cues = deepcopy(source.get("narration_cues", []))
        clock_offset = source.get("voice_start_offset_seconds")
        if clock_offset is None:
            clock_offset = voice_start_offset_seconds
        if not cues:
            built, duration = build_storyboard_narration_timeline(self.store.list_segments(book.id), round((clock_offset or 0)*1000))
            cues = [c.as_dict() for c in built]
            ready = True
            for cue in cues:
                ready = ready and cue["timing_ready"]
                cue["timing_ready"] = ready
        else:
            duration = float(source.get("duration_seconds") or max((c.get("end_seconds", 0) for c in cues), default=0))
        for index, cue in enumerate(cues):
            cue.setdefault("cue_id", str(cue.get("segment_id", index)))
            cue.setdefault("end_seconds", cue.get("start_seconds", 0)+cue.get("duration_seconds", 0))
            if clock_offset is None:
                cue["timing_ready"] = False
        selected = [c for c in cues if (start_seconds is None or c["end_seconds"] > start_seconds) and (end_seconds is None or c["start_seconds"] < end_seconds)]
        offset, limit = max(0, offset), min(1000, max(1, limit))
        audio = source.get("audio_path") or book.mix_audio_path or book.clean_audio_path
        audio_hash = self.audio_hash(audio)
        changed = bool(source.get("audio_sha256") and audio_hash != source["audio_sha256"])
        if changed:
            for cue in selected:
                cue["timing_ready"] = False
        return {"project_id": book.id, "source_revision": digest(source or book.source_text), "audio_path": audio, "audio_sha256": audio_hash, "audio_changed": changed, "timebase": "selected_audio" if clock_offset is not None else "unconfirmed_offset", "timing_granularity": "segment", "voice_start_offset_seconds": clock_offset, "duration_seconds": duration, "total": len(selected), "cues": selected[offset:offset+limit], "next_offset": offset+limit if offset+limit < len(selected) else None}

    def sb_search_timed_text(self, project_id: str, query: str, limit: int = 50) -> dict:
        """Search all cues, returning matching text, IDs and timestamps."""
        results, offset = [], 0
        while True:
            page = self.sb_get_timed_text(project_id, offset=offset, limit=1000)
            results.extend(c for c in page["cues"] if query.casefold() in c["text"].casefold())
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        return {"total": len(results), "cues": results[:max(1, min(limit, 1000))]}

    def sb_import_timed_text(self, project_id: str, expected_revision: str, cues: list[dict] | None = None, voice_start_offset_seconds: float = 0, text: str = "", format: str = "json") -> dict:
        """Import JSON cues or SRT/VTT text. All timestamps refer to the selected audio including initial silence."""
        if format not in {"json", "srt", "vtt"}:
            raise ValueError("format must be json, srt or vtt")
        if format != "json":
            cues = []
            def timestamp(value):
                parts = value.replace(",", ".").split(":")
                if len(parts) == 2:
                    parts.insert(0, "0")
                if len(parts) != 3:
                    raise ValueError("Invalid subtitle timestamp")
                hours, minutes, seconds = map(float, parts)
                if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
                    raise ValueError("Invalid subtitle timestamp")
                return hours*3600+minutes*60+seconds
            for block in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
                lines = block.splitlines()
                timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
                if timing is None:
                    if block.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
                        continue
                    raise ValueError("Subtitle block has no timestamps")
                start, end = lines[timing].split("-->", 1)
                cues.append({"cue_id": str(len(cues)+1), "start_seconds": timestamp(start.strip()), "end_seconds": timestamp(end.strip().split()[0]), "text": "\n".join(lines[timing+1:]), "timing_ready": True})
        if cues is None:
            raise ValueError("Supply cues or subtitle text")
        normalized = deepcopy(cues)
        for i, cue in enumerate(normalized):
            cue.setdefault("cue_id", str(i+1))
            cue.setdefault("duration_seconds", cue["end_seconds"]-cue["start_seconds"])
            cue.setdefault("timing_ready", True)
        return self.sb_set_source(project_id, expected_revision, {"narration_cues": normalized, "voice_start_offset_seconds": voice_start_offset_seconds})

    def sb_export_timed_text(self, project_id: str, format: str = "json") -> dict:
        """Export all cues as JSON, TXT, SRT or VTT without inventing word timestamps."""
        cues, offset = [], 0
        while True:
            page = self.sb_get_timed_text(project_id, offset=offset, limit=1000)
            cues.extend(page["cues"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        def stamp(seconds):
            ms = round(seconds*1000)
            return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}{',' if format == 'srt' else '.'}{ms%1000:03}"
        if format == "json":
            return {"cues": cues}
        if format not in {"txt", "srt", "vtt"}:
            raise ValueError("format must be json, txt, srt or vtt")
        text = "WEBVTT\n\n" if format == "vtt" else ""
        for i, c in enumerate(cues, 1):
            if format == "txt":
                text += f"[{stamp(c['start_seconds'])} - {stamp(c['end_seconds'])}] {c['text']}\n"
            else:
                text += f"{i}\n{stamp(c['start_seconds'])} --> {stamp(c['end_seconds'])}\n{c['text']}\n\n"
        return {"format": format, "text": text}

    def sb_list_styles(self) -> list[dict]:
        """List every built-in visual style with its exact prompt. Custom styles also supported."""
        return [asdict(style) for style in storyboard_styles()]

    def sb_set_style(self, project_id: str, expected_revision: str, style_id: str, custom_prompt: str = "", palette: str = "", lighting: str = "", negative: str = "", scene_ids: list[str] | None = None) -> dict:
        """Apply preset/custom image style globally or to selected scenes. Complete prompts remain verbatim."""
        styles = {s.id: s for s in storyboard_styles()}
        if style_id != "custom" and style_id not in styles:
            raise ValueError("Unknown style_id; call sb_list_styles.")
        medium = custom_prompt if style_id == "custom" else styles[style_id].prompt
        if not medium.strip():
            raise ValueError("Custom style needs a prompt")
        style = dict(medium=medium, palette=palette, lighting=lighting, negative=negative)
        def edit(s):
            if scene_ids is None:
                s["plan"].update(style=style, style_mode=style_id)
            else:
                for scene_id in scene_ids:
                    self.scene(s, scene_id).setdefault("generation_overrides", {})["style"] = style
            return {"style": style, "complete_prompts_unchanged": True}
        return self.change(project_id, expected_revision, edit)

    def entities(self, s):
        return [(kind, entity) for kind, key in COLLECTIONS.items() for entity in s["plan"].setdefault("continuity", {}).setdefault(key, [])]

    def entity(self, s, entity_id):
        for kind, entity in self.entities(s):
            if entity["id"] == entity_id:
                return kind, entity
        raise ValueError(f"Entity not found: {entity_id}")

    def sb_list_entities(self, project_id: str, entity_type: str = "") -> list[dict]:
        """Read characters, locations, objects, groups and eras with their states."""
        _, s = self.read(project_id)
        return [{"entity_type": k, **e} for k, e in self.entities(s) if not entity_type or entity_type == k]

    def sb_get_entity(self, project_id: str, entity_id: str) -> dict:
        """Read one entity, states and references."""
        _, s = self.read(project_id)
        k, e = self.entity(s, entity_id)
        return {"entity_type": k, **e}

    def sb_create_entity(self, project_id: str, expected_revision: str, entity_type: str, entity: dict) -> dict:
        """Create character/location/object/group/era; entity includes name, descriptions, optional id and states."""
        if entity_type not in COLLECTIONS:
            raise ValueError("Invalid entity_type")
        entity = deepcopy(entity)
        entity.setdefault("id", uuid.uuid4().hex)
        entity.setdefault("states", [])
        def edit(s):
            s["plan"].setdefault("continuity", {}).setdefault(COLLECTIONS[entity_type], []).append(entity)
            return entity
        return self.change(project_id, expected_revision, edit)

    def sb_update_entity(self, project_id: str, expected_revision: str, entity_id: str, changes: dict) -> dict:
        """Patch an entity's stable identity, aliases, description or group members; preserve id."""
        if "id" in changes:
            raise ValueError("IDs are immutable")
        return self.change(project_id, expected_revision, lambda s: self.entity(s, entity_id)[1].update(deepcopy(changes)))

    def sb_find_entity_usages(self, project_id: str, entity_id: str) -> dict:
        """Find explicit entity and state assignments in scenes."""
        _, s = self.read(project_id)
        _, entity = self.entity(s, entity_id)
        ids = {entity_id, *(x["id"] for x in entity.get("states", []))}
        return {"scene_ids": [c["scene_id"] for c in s["scenes"] if ids.intersection(v for key in COLLECTIONS.values() for v in c.get(key, [])) or c.get("era_state_id") in ids]}

    def sb_delete_entity(self, project_id: str, expected_revision: str, entity_id: str) -> dict:
        """Delete an unused entity. Explicitly unlink scenes first if it is still used."""
        if self.sb_find_entity_usages(project_id, entity_id)["scene_ids"]:
            raise ValueError("Entity still used by scenes")
        def edit(s):
            kind, entity = self.entity(s, entity_id)
            s["plan"]["continuity"][COLLECTIONS[kind]].remove(entity)
        return self.change(project_id, expected_revision, edit)

    def sb_create_state(self, project_id: str, expected_revision: str, entity_id: str, state: dict) -> dict:
        """Create a contextual appearance state with description/evidence/scope and optional reference_image_path."""
        state = deepcopy(state)
        state.setdefault("id", uuid.uuid4().hex)
        def edit(s):
            self.entity(s, entity_id)[1].setdefault("states", []).append(state)
            return state
        return self.change(project_id, expected_revision, edit)

    def state(self, s, entity_id, state_id):
        return next((v for v in self.entity(s, entity_id)[1].get("states", []) if v["id"] == state_id), None)

    def sb_update_state(self, project_id: str, expected_revision: str, entity_id: str, state_id: str, changes: dict) -> dict:
        """Edit a specific contextual state; identity remains separate."""
        def edit(s):
            state = self.state(s, entity_id, state_id)
            if state is None or "id" in changes:
                raise ValueError("Unknown state or immutable id")
            state.update(deepcopy(changes))
        return self.change(project_id, expected_revision, edit)

    def sb_delete_state(self, project_id: str, expected_revision: str, entity_id: str, state_id: str) -> dict:
        """Delete an unused appearance state; linked scenes must be reassigned first."""
        def edit(s):
            if any(state_id in c.get(key, []) or c.get("era_state_id") == state_id for c in s["scenes"] for key in COLLECTIONS.values()):
                raise ValueError("State still used")
            entity = self.entity(s, entity_id)[1]
            state = self.state(s, entity_id, state_id)
            if state is None:
                raise ValueError("Unknown state")
            entity["states"].remove(state)
        return self.change(project_id, expected_revision, edit)

    def sb_set_reference(self, project_id: str, expected_revision: str, entity_id: str, path: str, state_id: str = "") -> dict:
        """Set/clear entity or state reference image. State reference takes precedence in explicit assignments."""
        if path and not Path(path).is_file():
            raise ValueError("Reference file missing")
        def edit(s):
            entity = self.state(s, entity_id, state_id) if state_id else self.entity(s, entity_id)[1]
            if entity is None:
                raise ValueError("Unknown state")
            entity["reference_image_path"] = str(Path(path).resolve()) if path else ""
        return self.change(project_id, expected_revision, edit)

    def scene(self, s, scene_id):
        for c in s["scenes"]:
            if str(c.get("scene_id") or c.get("id")) == scene_id:
                return c
        raise ValueError(f"Scene not found: {scene_id}")

    def sb_list_scenes(self, project_id: str, offset: int = 0, limit: int = 100) -> dict:
        """Read scenes with their timing, prompts, assignments and media."""
        _, s = self.read(project_id)
        return {"total": len(s["scenes"]), "scenes": s["scenes"][max(0, offset):max(0, offset)+min(500, max(1, limit))]}

    def sb_get_scene(self, project_id: str, scene_id: str) -> dict:
        """Read a scene by stable ID."""
        return self.scene(self.read(project_id)[1], scene_id)

    def sb_create_scene(self, project_id: str, expected_revision: str, scene: dict) -> dict:
        """Create scene: scene_id(optional), start_seconds, duration_seconds, prompt, characters/state IDs, generation_overrides."""
        return self.sb_batch_update_scenes(project_id, expected_revision, [{"operation": "create", "scene": scene}])

    def sb_update_scene(self, project_id: str, expected_revision: str, scene_id: str, changes: dict) -> dict:
        """Patch a scene including prompts, camera, times and generation_overrides; IDs immutable."""
        return self.sb_batch_update_scenes(project_id, expected_revision, [{"operation": "update", "scene_id": scene_id, "scene": changes}])

    def sb_batch_update_scenes(self, project_id: str, expected_revision: str, operations: list[dict]) -> dict:
        """Atomically create/update/delete scenes. Each operation has operation, scene_id and scene fields."""
        def edit(s):
            ids = []
            for op in operations:
                fields = deepcopy(op.get("scene", {}))
                if op["operation"] == "create":
                    fields.setdefault("scene_id", uuid.uuid4().hex)
                    fields.setdefault("generation_overrides", {})["explicit_entities"] = True
                    s["scenes"].append(fields)
                    ids.append(fields["scene_id"])
                elif op["operation"] == "update":
                    if "scene_id" in fields or "id" in fields:
                        raise ValueError("Scene IDs are immutable")
                    self.scene(s, op["scene_id"]).update(fields)
                    ids.append(op["scene_id"])
                elif op["operation"] == "delete":
                    s["scenes"].remove(self.scene(s, op["scene_id"]))
                else:
                    raise ValueError("Unknown batch operation")
            return {"scene_ids": ids}
        return self.change(project_id, expected_revision, edit)

    def sb_delete_scene(self, project_id: str, expected_revision: str, scene_id: str) -> dict:
        """Delete a scene; validate resulting timeline coverage before render."""
        return self.sb_batch_update_scenes(project_id, expected_revision, [{"operation": "delete", "scene_id": scene_id}])

    def sb_set_scene_entities(self, project_id: str, expected_revision: str, scene_id: str, assignments: dict) -> dict:
        """Assign explicit characters/locations/objects/groups (entity or state IDs), era_state_id, and mentioned_entities separately."""
        if set(assignments) - {*COLLECTIONS.values(), "era_state_id", "mentioned_entities"}:
            raise ValueError("Unsupported assignment field")
        def edit(s):
            c = self.scene(s, scene_id)
            c.update(deepcopy(assignments))
            c.setdefault("generation_overrides", {})["explicit_entities"] = True
        return self.change(project_id, expected_revision, edit)

    def sb_split_scene(self, project_id: str, expected_revision: str, scene_id: str, at_seconds: float) -> dict:
        """Split inside a scene, keeping total duration and copied media/prompts."""
        def edit(s):
            c = self.scene(s, scene_id)
            end = c["start_seconds"]+c["duration_seconds"]
            if not c["start_seconds"] < at_seconds < end:
                raise ValueError("Split must be inside scene")
            other = deepcopy(c)
            other.update(scene_id=uuid.uuid4().hex, start_seconds=at_seconds, duration_seconds=end-at_seconds)
            c["duration_seconds"] = at_seconds-c["start_seconds"]
            s["scenes"].insert(s["scenes"].index(c)+1, other)
            return other
        return self.change(project_id, expected_revision, edit)

    def sb_merge_scenes(self, project_id: str, expected_revision: str, scene_ids: list[str]) -> dict:
        """Merge contiguous scenes; retain first scene's media and prompt, concatenate narration."""
        def edit(s):
            selected = [self.scene(s, i) for i in scene_ids]
            if len(selected) < 2:
                raise ValueError("Select at least two contiguous scenes")
            for a, b in zip(selected, selected[1:]):
                if abs(a["start_seconds"]+a["duration_seconds"]-b["start_seconds"]) > .01:
                    raise ValueError("Scenes are not contiguous")
            selected[0]["duration_seconds"] = sum(c["duration_seconds"] for c in selected)
            selected[0]["narration"] = " ".join(c.get("narration") or c.get("text", "") for c in selected)
            for c in selected[1:]:
                s["scenes"].remove(c)
        return self.change(project_id, expected_revision, edit)

    def sb_reorder_scenes(self, project_id: str, expected_revision: str, scene_ids: list[str]) -> dict:
        """Reorder ALL scenes and recalculate visual starts from zero; source audio remains unchanged."""
        def edit(s):
            if len(set(scene_ids)) != len(s["scenes"]) or set(scene_ids) != {c["scene_id"] for c in s["scenes"]}:
                raise ValueError("Include every scene exactly once")
            s["scenes"] = [self.scene(s, i) for i in scene_ids]
            cursor = 0
            for c in s["scenes"]:
                c["start_seconds"] = cursor
                cursor += c["duration_seconds"]
        return self.change(project_id, expected_revision, edit)

    def sb_update_timeline(self, project_id: str, expected_revision: str, settings: dict) -> dict:
        """Patch renderer video settings (fps, size, transition, zoom, clip audio options)."""
        return self.change(project_id, expected_revision, lambda s: s["plan"].setdefault("timeline", {}).update(deepcopy(settings)))

    def sb_compile_scene_prompt(self, project_id: str, scene_id: str) -> dict:
        """Inspect exact effective image prompt and references without calling a model."""
        from app.core.storyboard_reference_images import scene_with_references
        from app.core.video_storyboard_comfyui import compile_effective_scene_prompt, compile_reference_edit_prompt
        _, s = self.read(project_id)
        scene = scene_with_references(self.scene(s, scene_id), s["plan"], self.settings(s))
        refs = scene.get("generation_overrides", {}).get("reference_images", [])
        prompt = compile_effective_scene_prompt(s["plan"], scene)
        return {"prompt": compile_reference_edit_prompt(prompt, refs) if refs else prompt, "references": refs}

    def sb_import_prompts(self, project_id: str, expected_revision: str, scenes: list[dict], replace: bool = False) -> dict:
        """Import timed scene dictionaries; replace only when explicitly true. Supports complete raw_prompt overrides."""
        operations = []
        if replace:
            operations = [{"operation": "delete", "scene_id": c["scene_id"]} for c in self.read(project_id)[1]["scenes"]]
        return self.sb_batch_update_scenes(project_id, expected_revision, operations+[{"operation": "create", "scene": c} for c in scenes])

    def sb_export_prompts(self, project_id: str) -> dict:
        """Export all timed scenes with prompts for external editing."""
        return {"scenes": self.read(project_id)[1]["scenes"]}

    def sb_get_capabilities(self, project_id: str = "") -> dict:
        """Read configured visual providers/profiles/settings without credentials; query styles separately."""
        from app.core.storyboard_profiles import PROFILE_IDS
        state = self.read(project_id)[1] if project_id else {"plan": {}}
        return {"profiles": list(PROFILE_IDS), "settings": public(self.settings(state)), "analysis_required": False}

    def sb_set_generation_profile(self, project_id: str, expected_revision: str, profile: str, overrides: dict | None = None) -> dict:
        """Select saved local/custom_comfyui/litellm/runpod profile and project-only provider settings overrides."""
        from app.core.storyboard_profiles import PROFILE_IDS
        if profile not in PROFILE_IDS:
            raise ValueError("Unknown generation profile")
        if public(overrides or {}) != (overrides or {}):
            raise ValueError("Configure credentials in application settings, not storyboard documents")
        return self.change(project_id, expected_revision, lambda s: s["plan"].update(generation_profile=profile, generation_settings=deepcopy(overrides or {})))

    def check(self, s):
        errors, ids = [], set()
        collection_ids = {key: set() for key in COLLECTIONS.values()}
        for kind, e in self.entities(s):
            for item in [e]+e.get("states", []):
                if not item.get("id") or item["id"] in ids:
                    errors.append("Entity/state IDs must be present and globally unique")
                ids.add(item.get("id"))
                collection_ids[COLLECTIONS[kind]].add(item.get("id"))
        scenes = set()
        for c in s["scenes"]:
            key = c.get("scene_id") or c.get("id")
            if not key or key in scenes:
                errors.append("Scene IDs must be unique")
            scenes.add(key)
            for field in ("start_seconds", "duration_seconds"):
                v = c.get(field, 0)
                if not isinstance(v, (int, float)) or not math.isfinite(v) or (v <= 0 if field == "duration_seconds" else v < 0):
                    errors.append(f"Invalid {field} in {key}")
            if c.get("generation_overrides", {}).get("explicit_entities"):
                for collection in COLLECTIONS.values():
                    if any(v not in collection_ids[collection] for v in c.get(collection, [])):
                        errors.append(f"Unknown entity/state in {key}/{collection}")
                if c.get("era_state_id") and c["era_state_id"] not in collection_ids["eras"]:
                    errors.append(f"Unknown era in {key}")
        for cue in s["source"].get("narration_cues", []):
            start, end = cue.get("start_seconds", -1), cue.get("end_seconds", -1)
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or not math.isfinite(start+end) or start < 0 or end < start:
                errors.append("Invalid cue timing")
        return errors

    def sb_validate(self, project_id: str) -> dict:
        """Deterministic structural/render readiness checks; no narrative or visual AI analysis."""
        _, s = self.read(project_id)
        errors = self.check(s)
        cursor = 0
        for c in s["scenes"]:
            if abs(c["start_seconds"]-cursor) > .05:
                errors.append(f"Timeline gap/overlap before {c['scene_id']}")
            cursor = c["start_seconds"]+c["duration_seconds"]
            if not Path(c.get("image_path") or "").is_file():
                errors.append(f"Missing frame: {c['scene_id']}")
        if not s["scenes"]:
            errors.append("No scenes")
        if not Path(s["source"].get("audio_path") or "").is_file():
            errors.append("Missing narration audio")
        else:
            from app.core.storyboard_clip_audio import probe_clip
            from app.utils.ffmpeg_utils import find_ffmpeg
            _, audio_duration = probe_clip(find_ffmpeg(self.settings(s)["ffmpeg_path"]), Path(s["source"]["audio_path"]))
            if audio_duration and abs(cursor-audio_duration) > .15:
                errors.append(f"Scene duration {cursor:.3f}s does not cover audio duration {audio_duration:.3f}s")
        return {"valid": not errors, "errors": errors}

    def sb_get_change_impact(self, project_id: str, entity_id: str = "") -> dict:
        """List scenes depending on an entity, or all scenes for a project-level change; final export needs rerender."""
        ids = self.sb_find_entity_usages(project_id, entity_id)["scene_ids"] if entity_id else [c["scene_id"] for c in self.read(project_id)[1]["scenes"]]
        return {"scene_ids": ids, "frames_and_clips_require_review": ids, "render_stale": True}

    def sb_create_snapshot(self, project_id: str) -> dict:
        """Save a recoverable storyboard snapshot, preserving media files."""
        book, s = self.read(project_id)
        snapshot_id = uuid.uuid4().hex
        _atomic_json(book.project_dir / "storyboard/snapshots" / f"{snapshot_id}.json", s)
        return {"snapshot_id": snapshot_id}

    def sb_restore_snapshot(self, project_id: str, expected_revision: str, snapshot_id: str) -> dict:
        """Restore snapshot as a new revision; keep generated files."""
        if not snapshot_id.isalnum():
            raise ValueError("Invalid snapshot id")
        book = self.book(project_id)
        snapshot = json.loads((book.project_dir / "storyboard/snapshots" / f"{snapshot_id}.json").read_text(encoding="utf-8"))
        return self.change(project_id, expected_revision, lambda s: (s.clear(), s.update(snapshot)) and None)

    def asset(self, s, asset_id):
        for asset in s["plan"].get("assets", []):
            if asset["asset_id"] == asset_id:
                return asset
        raise ValueError("Unknown asset")

    def sb_import_asset(self, project_id: str, expected_revision: str, path: str, kind: str = "image", scene_id: str = "") -> dict:
        """Copy an existing image/video/audio into the project asset library; does not assign it."""
        if kind not in {"image", "video", "audio"} or not Path(path).is_file():
            raise ValueError("Invalid kind or missing file")
        asset_id = uuid.uuid4().hex
        def edit(s):
            book = self.book(project_id)
            target = book.project_dir / "storyboard/assets" / (asset_id+Path(path).suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            a = {"asset_id": asset_id, "kind": kind, "path": str(target.resolve()), "scene_id": scene_id}
            s["plan"].setdefault("assets", []).append(a)
            return a
        return self.change(project_id, expected_revision, edit)

    def sb_list_assets(self, project_id: str, scene_id: str = "") -> list[dict]:
        """List generated/imported candidates and previously assigned scene media."""
        _, s = self.read(project_id)
        assets = deepcopy(s["plan"].get("assets", []))
        paths = {a["path"] for a in assets}
        for c in s["scenes"]:
            for kind in ("image", "video"):
                path = c.get(kind+"_path")
                if path and path not in paths:
                    assets.append({"asset_id": f"existing:{c['scene_id']}:{kind}", "scene_id": c["scene_id"], "kind": kind, "path": path})
        return [a for a in assets if not scene_id or a.get("scene_id") == scene_id]

    def sb_assign_asset(self, project_id: str, expected_revision: str, asset_id: str, scene_id: str) -> dict:
        """Accept an image/video candidate for a scene or audio candidate as source."""
        asset = next((a for a in self.sb_list_assets(project_id) if a["asset_id"] == asset_id), None)
        if asset is None or not Path(asset["path"]).is_file():
            raise ValueError("Missing asset")
        def edit(s):
            if asset["kind"] == "audio":
                s["source"]["audio_path"] = asset["path"]
            else:
                self.scene(s, scene_id)[asset["kind"]+"_path"] = asset["path"]
        return self.change(project_id, expected_revision, edit)

    def sb_preview(self, project_id: str, scene_ids: list[str] | None = None, asset_id: str = "") -> dict:
        """Return a contact sheet as inline MCP image; optional asset_id selects a candidate image."""
        import base64
        import io
        from PIL import Image, ImageDraw, ImageOps
        _, s = self.read(project_id)
        selected = [(c["scene_id"], c.get("image_path", "")) for c in s["scenes"] if scene_ids is None or c["scene_id"] in scene_ids]
        if asset_id:
            a = next((a for a in self.sb_list_assets(project_id) if a["asset_id"] == asset_id), None)
            if a is None or a["kind"] != "image":
                raise ValueError("Select an image asset or extract a video frame first")
            selected = [(asset_id, a["path"])]
        selected = selected[:24]
        if not selected:
            raise ValueError("No images selected")
        sheet = Image.new("RGB", (960, 206*math.ceil(len(selected)/3)), "#202020")
        draw = ImageDraw.Draw(sheet)
        for i, (label, path) in enumerate(selected):
            x, y = (i%3)*320, (i//3)*206
            if Path(path).is_file():
                with Image.open(path) as image:
                    sheet.paste(ImageOps.contain(image.convert("RGB"), (316, 178)), (x, y))
            draw.text((x+3, y+181), label[:45], fill="white")
        output = io.BytesIO()
        sheet.save(output, "JPEG", quality=80)
        return {"mime_type": "image/jpeg", "image_base64": base64.b64encode(output.getvalue()).decode(), "count": len(selected)}

    def job_path(self, project_id, job_id):
        if not job_id.isalnum():
            raise ValueError("Invalid job id")
        return self.book(project_id).project_dir / "storyboard/jobs" / f"{job_id}.json"

    def submit(self, project_id, expected_revision, operation, arguments, idempotency_key):
        book, state = self.read(project_id)
        fingerprint = digest({"operation": operation, "arguments": arguments})
        if idempotency_key:
            for job in self.sb_list_jobs(project_id):
                if job.get("idempotency_key") == idempotency_key:
                    if job.get("fingerprint") != fingerprint:
                        raise ValueError("Idempotency key already used for different arguments")
                    return job
        if state["_revision"] != expected_revision:
            raise OSError("Revision conflict; read project before generating")
        if "scene_ids" in arguments:
            selected = arguments["scene_ids"]
            if not selected or len(selected) != len(set(selected)):
                raise ValueError("Select at least one scene; IDs must be unique")
            for scene_id in selected:
                scene = self.scene(state, scene_id)
                required = "image_path" if operation == "edit_frames" else "video_path" if operation in {"extract", "edit_video"} else ""
                if required and not Path(scene.get(required) or "").is_file():
                    raise ValueError(f"Missing {required} for scene {scene_id}")
        if "entity_id" in arguments:
            entity = self.entity(state, arguments["entity_id"])[1]
            if arguments.get("state_id"):
                entity = self.state(state, arguments["entity_id"], arguments["state_id"])
                if entity is None:
                    raise ValueError("Unknown reference state")
            if operation == "edit_reference" and not Path(entity.get("reference_image_path") or "").is_file():
                raise ValueError("Entity/state has no reference to edit")
        if operation in {"edit_frames", "edit_reference"} and not arguments.get("instruction", "").strip():
            raise ValueError("An editing instruction is required")
        if operation == "reference" and not arguments.get("prompt", "").strip():
            raise ValueError("A reference prompt is required")
        job_id = uuid.uuid4().hex
        job = dict(job_id=job_id, project_id=str(project_id), status="queued", operation=operation, arguments=arguments, idempotency_key=idempotency_key, fingerprint=fingerprint, source_revision=expected_revision, results=[], errors=[])
        _atomic_json(self.job_path(project_id, job_id), job)
        event = threading.Event()
        self.events[job_id] = event
        future = self.executor.submit(self.run_job, job, deepcopy(state), event)
        def finished(future):
            error = future.exception()
            if error is not None:
                from app.core.storyboard_generation_errors import redact_generation_error
                job["status"] = "failed"
                job["errors"].append({"error": redact_generation_error(str(error), self.settings_manager.settings)})
                _atomic_json(self.job_path(project_id, job_id), public(job))
                self.events.pop(job_id, None)
        future.add_done_callback(finished)
        return job

    def sb_get_job(self, project_id: str, job_id: str) -> dict:
        """Read persistent visual job progress, per-item results and errors. Interrupted jobs need explicit retry."""
        job = json.loads(self.job_path(project_id, job_id).read_text(encoding="utf-8"))
        if job["status"] in {"queued", "running"} and job_id not in self.events:
            job["status"] = "interrupted"
        return job

    def sb_list_jobs(self, project_id: str) -> list[dict]:
        """List persistent visual jobs for a project."""
        root = self.book(project_id).project_dir / "storyboard/jobs"
        return [self.sb_get_job(project_id, p.stem) for p in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)]

    def sb_cancel_job(self, project_id: str, job_id: str) -> dict:
        """Request cooperative cancellation. Already completed assets remain available."""
        job = self.sb_get_job(project_id, job_id)
        if job_id in self.events:
            self.events[job_id].set()
            job["cancellation_requested"] = True
        return job

    def sb_retry_job(self, project_id: str, expected_revision: str, job_id: str, idempotency_key: str = "") -> dict:
        """Retry failed/interrupted items only; remote requests interrupted before a result may incur another charge."""
        job = self.sb_get_job(project_id, job_id)
        if job["status"] not in {"failed", "partial", "cancelled", "interrupted"}:
            raise ValueError("Only failed/partial/cancelled/interrupted jobs can be retried")
        args = deepcopy(job["arguments"])
        if "scene_ids" in args:
            done = {r.get("scene_id") for r in job["results"]}
            args["scene_ids"] = [i for i in args["scene_ids"] if i not in done]
            if not args["scene_ids"]:
                raise ValueError("No pending scenes")
        return self.submit(project_id, expected_revision, job["operation"], args, idempotency_key)

    def sb_generate_frames(self, project_id: str, expected_revision: str, scene_ids: list[str], assign: bool = True, idempotency_key: str = "") -> dict:
        """Queue image generation for explicit scenes using project style/profile and scene overrides."""
        for i in scene_ids:
            self.scene(self.read(project_id)[1], i)
        return self.submit(project_id, expected_revision, "frames", dict(scene_ids=scene_ids, assign=assign), idempotency_key)

    def sb_edit_frames(self, project_id: str, expected_revision: str, scene_ids: list[str], instruction: str, assign: bool = False, idempotency_key: str = "") -> dict:
        """Queue edits of existing frames with the configured image-edit provider."""
        return self.submit(project_id, expected_revision, "edit_frames", dict(scene_ids=scene_ids, instruction=instruction, assign=assign), idempotency_key)

    def sb_generate_videos(self, project_id: str, expected_revision: str, scene_ids: list[str], assign: bool = True, idempotency_key: str = "") -> dict:
        """Queue scene clips using configured video provider, video_prompt and frame role."""
        return self.submit(project_id, expected_revision, "videos", dict(scene_ids=scene_ids, assign=assign), idempotency_key)

    def sb_generate_reference(self, project_id: str, expected_revision: str, entity_id: str, prompt: str, state_id: str = "", assign: bool = True, idempotency_key: str = "") -> dict:
        """Generate entity/state reference with agent prompt plus project style; queued without UI."""
        return self.submit(project_id, expected_revision, "reference", dict(entity_id=entity_id, state_id=state_id, prompt=prompt, assign=assign), idempotency_key)

    def sb_edit_reference(self, project_id: str, expected_revision: str, entity_id: str, instruction: str, state_id: str = "", assign: bool = True, idempotency_key: str = "") -> dict:
        """Edit an existing reference, e.g. clean clothing while retaining identity."""
        return self.submit(project_id, expected_revision, "edit_reference", dict(entity_id=entity_id, state_id=state_id, instruction=instruction, assign=assign), idempotency_key)

    def sb_render(self, project_id: str, expected_revision: str, idempotency_key: str = "") -> dict:
        """Queue final MP4 with narration and configured transitions/clip audio. Require contiguous scenes and existing frames."""
        validation = self.sb_validate(project_id)
        if not validation["valid"]:
            raise ValueError("; ".join(validation["errors"]))
        return self.submit(project_id, expected_revision, "render", {}, idempotency_key)

    def sb_extract_video_frame(self, project_id: str, expected_revision: str, scene_id: str, seconds: float = 0, idempotency_key: str = "") -> dict:
        """Extract a clip frame as an image candidate for inspection or reuse."""
        if seconds < 0 or not math.isfinite(seconds):
            raise ValueError("Invalid time")
        return self.submit(project_id, expected_revision, "extract", dict(scene_ids=[scene_id], seconds=seconds, assign=False), idempotency_key)

    def sb_edit_video(self, project_id: str, expected_revision: str, scene_id: str, ranges: list[list[float]], assign: bool = False, idempotency_key: str = "") -> dict:
        """Trim/reorder/concatenate ranges [start,end] from one scene clip, retaining its audio."""
        return self.submit(project_id, expected_revision, "edit_video", dict(scene_ids=[scene_id], ranges=ranges, assign=assign), idempotency_key)

    def run_job(self, job, state, event):
        from app.core.storyboard_generation_errors import redact_generation_error
        path = self.job_path(job["project_id"], job["job_id"])
        config = self.settings(state)
        book = self.book(job["project_id"])
        output = book.project_dir / "storyboard/assets" / job["job_id"]
        output.mkdir(parents=True, exist_ok=True)
        args = job["arguments"]
        op = job["operation"]
        revision = job["source_revision"]
        def status(message, *unused):
            job["stage"] = str(message)
            _atomic_json(path, public(job))
        try:
            job["status"] = "running"
            status("preparing")
            items = args.get("scene_ids", [args.get("entity_id", "render")])
            for index, item_id in enumerate(items):
                if event.is_set():
                    break
                scene = self.scene(state, item_id) if "scene_ids" in args else None
                target = output / f"{index:04d}{'.mp4' if op in {'videos', 'render', 'edit_video'} else '.png'}"
                try:
                    result = self.execute_item(op, scene, state, config, args, target, status, event)
                    if not target.is_file():
                        raise RuntimeError("Generator returned no output file")
                    asset = {"asset_id": uuid.uuid4().hex, "kind": "video" if target.suffix == ".mp4" else "image", "path": str(target.resolve()), "scene_id": item_id if scene else "", "entity_id": args.get("entity_id", ""), "state_id": args.get("state_id", ""), "job_id": job["job_id"], "source_revision": job["source_revision"], "provider_result": public(result)}
                    with self.lock:
                        latest_book, latest = self.read(job["project_id"])
                        current = latest["_revision"]
                        unchanged = current == revision
                        latest["plan"].setdefault("assets", []).append(asset)
                        asset["assigned"] = bool(unchanged and args.get("assign", False) and not event.is_set())
                        if asset["assigned"]:
                            if scene:
                                current_scene = self.scene(latest, item_id)
                                current_scene[asset["kind"]+"_path"] = asset["path"]
                                if asset["kind"] == "video" and result.get("video_duration_seconds"):
                                    current_scene["video_duration_seconds"] = result["video_duration_seconds"]
                            else:
                                entity = self.state(latest, args["entity_id"], args["state_id"]) if args.get("state_id") else self.entity(latest, args["entity_id"])[1]
                                entity["reference_image_path"] = asset["path"]
                        if op == "render" and unchanged:
                            latest["plan"]["rendered_output_path"] = asset["path"]
                        save_storyboard_state(book.project_dir, latest, expected_revision=current)
                        if unchanged:
                            revision = self.revision(book)
                    job["results"].append(asset)
                except Exception as exc:
                    job["errors"].append({"item_id": item_id, "error": redact_generation_error(str(exc), config), "output_path": str(target) if target.exists() else ""})
                job["completed_items"] = index+1
                _atomic_json(path, public(job))
            job["status"] = "cancelled" if event.is_set() else ("partial" if job["results"] else "failed") if job["errors"] else "complete"
        except Exception as exc:
            job["status"] = "failed"
            job["errors"].append({"error": redact_generation_error(str(exc), config)})
        finally:
            _atomic_json(path, public(job))
            self.events.pop(job["job_id"], None)

    def execute_item(self, op, scene, state, config, args, target, status, event):
        if op in {"frames", "reference"}:
            from app.core.video_storyboard_comfyui import generate_storyboard_frame, prepare_image_runtime
            prepare_image_runtime(config, status=status)
            if op == "reference":
                scene = {"scene_id": args["entity_id"], "prompt": args["prompt"], "generation_overrides": {"explicit_entities": True}}
            return generate_storyboard_frame(scene, state["plan"], config, target, status=status, cancelled=event.is_set)
        if op in {"edit_frames", "edit_reference"}:
            from app.core.video_storyboard_image_edit import generate_edited_storyboard_image, prepare_image_edit_runtime
            prepare_image_edit_runtime(config, status=status)
            if scene:
                reference = scene.get("image_path", "")
            else:
                entity = self.state(state, args["entity_id"], args["state_id"]) if args.get("state_id") else self.entity(state, args["entity_id"])[1]
                reference = entity.get("reference_image_path", "")
            return generate_edited_storyboard_image([reference], args["instruction"], config, target, status=status, cancelled=event.is_set)
        if op == "videos":
            from app.core.video_storyboard_video_comfyui import generate_storyboard_scene_video, prepare_video_runtime
            prepare_video_runtime(config, status=status)
            return generate_storyboard_scene_video(scene, state["plan"], config, target, prompt=scene.get("video_prompt", ""), frame_role=scene.get("video_frame_role", "start"), status=status, cancelled=event.is_set)
        if op == "render":
            from app.core.video_storyboard_renderer import render_storyboard_video
            return render_storyboard_video(state["scenes"], Path(state["source"]["audio_path"]), target, config, progress=status, cancelled=event.is_set)
        from app.utils.ffmpeg_utils import FFmpegRunner, find_ffmpeg
        executable = find_ffmpeg(config.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"))
        runner = FFmpegRunner(executable)
        source = scene.get("video_path", "")
        if not Path(source).is_file():
            raise ValueError("Scene video missing")
        if op == "extract":
            command = ["-y", "-ss", str(args["seconds"]), "-i", source, "-frames:v", "1", str(target)]
        elif op == "edit_video":
            from app.core.storyboard_video_edit import export_arguments
            from app.core.storyboard_clip_audio import probe_clip
            has_audio, duration = probe_clip(executable, Path(source))
            if any(r[1] > duration+.1 for r in args["ranges"]):
                raise ValueError("Edit extends beyond clip duration")
            command = export_arguments(source, str(target), [tuple(r) for r in args["ranges"]], has_audio)
        else:
            raise ValueError("Unknown job operation")
        done = threading.Event()
        def watch():
            while not done.wait(.1):
                if event.is_set():
                    runner.cancel_current()
                    return
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            runner.run(command)
        finally:
            done.set()
            watcher.join()
        return {"path": str(target)}
