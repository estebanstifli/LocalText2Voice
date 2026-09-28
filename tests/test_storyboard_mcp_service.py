import json
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.core.audiobook_store import AudiobookStore
from app.core.settings_manager import DEFAULT_SETTINGS
from app.server.storyboard_service import StoryboardService


@pytest.fixture
def service(tmp_path):
    store = AudiobookStore(tmp_path / "library.sqlite")
    store.root_dir = tmp_path / "projects"
    settings = deepcopy(DEFAULT_SETTINGS)
    settings["video_storyboard"]["image"].update(width=160, height=96)
    settings["video_storyboard"]["video"].update(fps=12, transition_seconds=0)
    instance = StoryboardService(SimpleNamespace(settings=settings), store)
    yield instance
    instance.close()


def project(service):
    return service.sb_create_project(title="MCP test", text="A clean shirt.")


def mutate(service, p, tool, **args):
    p = service.sb_get_project(str(p["project_id"]))
    return service.call(tool, dict(project_id=str(p["project_id"]), expected_revision=p["revision"], **args))


def wait(service, p, job):
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        result = service.sb_get_job(str(p["project_id"]), job["job_id"])
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(.02)
    pytest.fail("Job did not finish")


def test_crud_styles_conflicts_and_atomic_batch(service):
    p = project(service)
    mutate(service, p, "sb_set_style", style_id="custom", custom_prompt="Graphite drawing")
    with pytest.raises(OSError):
        service.sb_update_project(str(p["project_id"]), p["revision"], {"title": "stale"})
    mutate(service, p, "sb_create_entity", entity_type="character", entity={"id": "ventura", "name": "Ventura", "states": [{"id": "clean", "description": "clean white shirt"}]})
    mutate(service, p, "sb_create_scene", scene={"scene_id": "a", "start_seconds": 0, "duration_seconds": 2, "prompt": "A man", "characters": ["clean"]})
    compiled = service.sb_compile_scene_prompt(str(p["project_id"]), "a")["prompt"]
    assert "Graphite drawing" in compiled
    assert "clean white shirt" in compiled
    with pytest.raises(ValueError):
        mutate(service, p, "sb_delete_entity", entity_id="ventura")
    with pytest.raises(ValueError):
        mutate(service, p, "sb_batch_update_scenes", operations=[{"operation": "create", "scene": {"scene_id": "b", "start_seconds": 2, "duration_seconds": 2}}, {"operation": "update", "scene_id": "missing", "scene": {"prompt": "bad"}}])
    assert service.sb_list_scenes(str(p["project_id"]))["total"] == 1
    mutate(service, p, "sb_set_style", style_id="custom", custom_prompt="Watercolor", scene_ids=["a"])
    assert "Watercolor" in service.sb_compile_scene_prompt(str(p["project_id"]), "a")["prompt"]


def test_complete_prompt_and_explicit_references(service, tmp_path):
    p = project(service)
    clean = tmp_path / "clean.png"
    bloody = tmp_path / "bloody.png"
    Image.new("RGB", (32, 32)).save(clean)
    Image.new("RGB", (32, 32)).save(bloody)
    mutate(service, p, "sb_create_entity", entity_type="character", entity={"id": "ventura", "name": "Ventura", "reference_image_path": str(bloody), "states": [{"id": "clean", "description": "clean", "reference_image_path": str(clean)}]})
    mutate(service, p, "sb_create_scene", scene={"scene_id": "a", "start_seconds": 0, "duration_seconds": 1, "prompt": "Ventura mentioned in a letter", "characters": []})
    assert service.sb_compile_scene_prompt(str(p["project_id"]), "a")["references"] == []
    mutate(service, p, "sb_set_scene_entities", scene_id="a", assignments={"characters": ["clean"]})
    assert service.sb_compile_scene_prompt(str(p["project_id"]), "a")["references"][0]["path"] == str(clean)
    mutate(service, p, "sb_update_scene", scene_id="a", changes={"generation_overrides": {"prompt_mode": "complete", "raw_prompt": "EXACT"}})
    assert service.sb_compile_scene_prompt(str(p["project_id"]), "a")["prompt"] == "EXACT"


def test_timed_text_all_pages_and_snapshot(service):
    p = project(service)
    cues = [{"cue_id": str(i), "start_seconds": i, "end_seconds": i+1, "text": f"Line {i}", "timing_ready": True} for i in range(1005)]
    mutate(service, p, "sb_import_timed_text", cues=cues)
    result = service.sb_get_timed_text(str(p["project_id"]), offset=1000, limit=10)
    assert len(result["cues"]) == 5
    assert len(service.sb_export_timed_text(str(p["project_id"]))["cues"]) == 1005
    assert service.sb_search_timed_text(str(p["project_id"]), "Line 1004")["total"] == 1
    snap = service.sb_create_snapshot(str(p["project_id"]))
    mutate(service, p, "sb_set_style", style_id="custom", custom_prompt="Ink")
    mutate(service, p, "sb_restore_snapshot", snapshot_id=snap["snapshot_id"])
    assert service.sb_get_project(str(p["project_id"]))["state"]["plan"]["style"] == {}


def test_jobs_idempotency_partial_retry_and_preview(service, monkeypatch):
    p = project(service)
    for i in ("a", "b"):
        mutate(service, p, "sb_create_scene", scene={"scene_id": i, "start_seconds": 0 if i == "a" else 1, "duration_seconds": 1, "prompt": i})
    calls = []
    def execute(op, scene, state, config, args, target, status, event):
        calls.append(scene["scene_id"])
        if scene["scene_id"] == "b" and calls.count("b") == 1:
            raise ValueError("Simulated provider failure")
        Image.new("RGB", (64, 64), "blue").save(target)
        return {"image_path": str(target)}
    monkeypatch.setattr(service, "execute_item", execute)
    job = mutate(service, p, "sb_generate_frames", scene_ids=["a", "b"], idempotency_key="one")
    result = wait(service, p, job)
    assert result["status"] == "partial", result
    same = mutate(service, p, "sb_generate_frames", scene_ids=["a", "b"], idempotency_key="one")
    assert same["job_id"] == job["job_id"]
    retry = mutate(service, p, "sb_retry_job", job_id=job["job_id"])
    retried = wait(service, p, retry)
    assert retried["status"] == "complete", retried
    assert calls == ["a", "b", "b"]
    preview = service.sb_preview(str(p["project_id"]))
    assert preview["mime_type"] == "image/jpeg" and preview["image_base64"]


def test_generation_does_not_assign_after_concurrent_edit(service, monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    p = project(service)
    mutate(service, p, "sb_create_scene", scene={"scene_id": "a", "start_seconds": 0, "duration_seconds": 1, "prompt": "old"})
    def execute(op, scene, state, config, args, target, status, event):
        entered.set()
        assert release.wait(5)
        Image.new("RGB", (32, 32)).save(target)
        return {}
    monkeypatch.setattr(service, "execute_item", execute)
    job = mutate(service, p, "sb_generate_frames", scene_ids=["a"])
    assert entered.wait(5)
    mutate(service, p, "sb_update_scene", scene_id="a", changes={"prompt": "new"})
    release.set()
    result = wait(service, p, job)
    assert not result["results"][0]["assigned"]
    assert not service.sb_get_scene(str(p["project_id"]), "a")["image_path"]


def test_real_render_and_video_edit_without_ui(service, tmp_path):
    import wave
    audio = tmp_path / "voice.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0"*24000)
    image = tmp_path / "frame.png"
    Image.new("RGB", (160, 96), "green").save(image)
    p = project(service)
    mutate(service, p, "sb_set_source", source={"audio_path": str(audio), "duration_seconds": 1})
    mutate(service, p, "sb_create_scene", scene={"scene_id": "a", "start_seconds": 0, "duration_seconds": 1, "image_path": str(image)})
    job = mutate(service, p, "sb_render")
    result = wait(service, p, job)
    assert result["status"] == "complete", result
    asset = result["results"][0]
    assert Path(asset["path"]).stat().st_size > 100
    mutate(service, p, "sb_assign_asset", asset_id=asset["asset_id"], scene_id="a")
    extracted = wait(service, p, mutate(service, p, "sb_extract_video_frame", scene_id="a", seconds=.2))
    assert extracted["status"] == "complete", extracted
    edited = wait(service, p, mutate(service, p, "sb_edit_video", scene_id="a", ranges=[[.1, .6]]))
    assert edited["status"] == "complete", edited


def test_http_mcp_removed_and_rest_preserved(service, monkeypatch):
    from fastapi.testclient import TestClient
    from app.server.http_app import create_http_app
    from unittest.mock import MagicMock
    app = create_http_app(service.settings_manager, job_manager=MagicMock(), audiobook_store=service.store)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/mcp").status_code == 404
        result = client.post("/storyboard/sb_list_styles", json={})
        assert result.status_code == 200 and result.json()
        assert client.post("/storyboard/close", json={}).status_code == 400


def test_cross_process_stale_write_rejected(service):
    import subprocess
    import sys
    p = project(service)
    book, state = service.read(str(p["project_id"]))
    script = "from pathlib import Path; from app.core.video_storyboard_project import load_storyboard_state,save_storyboard_state; import sys; p=Path(sys.argv[1]); s=load_storyboard_state(p); s['plan']['title']='external'; save_storyboard_state(p,s)"
    subprocess.run([sys.executable, "-c", script, str(book.project_dir)], check=True)
    from app.core.video_storyboard_project import save_storyboard_state
    with pytest.raises(OSError, match="another client"):
        save_storyboard_state(book.project_dir, state)


def test_gui_roundtrip_preserves_mcp_assignments():
    from app.ui.video_storyboard_page import StoryboardScene
    data = {"scene_id": "a", "duration_seconds": 1, "objects": ["letter"], "groups": ["crowd"], "eras": ["period"], "mentioned_entities": ["ventura"], "generation_overrides": {"explicit_entities": True}}
    scene = StoryboardScene.from_value(data, 0).as_dict()
    for key, value in data.items():
        assert scene[key] == value


def test_mcp_tools_execute_with_typed_arguments_and_inline_image(service):
    import anyio
    from mcp.server.fastmcp import FastMCP
    from app.server.storyboard_mcp import register_storyboard_tools
    server = FastMCP("storyboard-test")
    def request(method, path, arguments, timeout):
        assert method == "POST"
        return service.call(path.rsplit("/", 1)[1], arguments)
    register_storyboard_tools(server, request)
    async def run():
        tools = await server.list_tools()
        assert len(tools) == 57
        tool = next(t for t in tools if t.name == "sb_set_style")
        assert tool.inputSchema["properties"]["scene_ids"]
        content = await server.call_tool("sb_list_styles", {})
        assert content
        content = await server.call_tool("sb_create_project", {"title": "MCP typed"})
        assert content
    anyio.run(run)


def test_provider_adapters_and_reference_jobs(service, monkeypatch):
    from app.core import video_storyboard_comfyui as image_provider
    from app.core import video_storyboard_video_comfyui as video_provider
    from app.core import video_storyboard_image_edit as edit_provider
    p = project(service)
    mutate(service, p, "sb_create_entity", entity_type="character", entity={"id": "person", "name": "Person"})
    monkeypatch.setattr(image_provider, "prepare_image_runtime", lambda *a, **k: {})
    monkeypatch.setattr(edit_provider, "prepare_image_edit_runtime", lambda *a, **k: {})
    monkeypatch.setattr(video_provider, "prepare_video_runtime", lambda *a, **k: {})
    calls = []
    def frame(scene, plan, settings, target, **kwargs):
        calls.append("frame")
        Image.new("RGB", (32, 32)).save(target)
        return {"image_path": str(target)}
    def edit(refs, prompt, settings, target, **kwargs):
        assert Path(refs[0]).is_file()
        calls.append("edit")
        Image.new("RGB", (32, 32)).save(target)
        return {"image_path": str(target)}
    def video(scene, plan, settings, target, **kwargs):
        assert kwargs["prompt"] == "Slow pan"
        calls.append("video")
        target.write_bytes(b"simulated video")
        return {"video_path": str(target), "video_duration_seconds": 1}
    monkeypatch.setattr(image_provider, "generate_storyboard_frame", frame)
    monkeypatch.setattr(edit_provider, "generate_edited_storyboard_image", edit)
    monkeypatch.setattr(video_provider, "generate_storyboard_scene_video", video)
    ref = wait(service, p, mutate(service, p, "sb_generate_reference", entity_id="person", prompt="Portrait"))
    assert ref["status"] == "complete", ref
    edited = wait(service, p, mutate(service, p, "sb_edit_reference", entity_id="person", instruction="Clean clothes"))
    assert edited["status"] == "complete", edited
    mutate(service, p, "sb_create_scene", scene={"scene_id": "a", "start_seconds": 0, "duration_seconds": 1, "video_prompt": "Slow pan"})
    assert wait(service, p, mutate(service, p, "sb_generate_frames", scene_ids=["a"]))["status"] == "complete"
    assert wait(service, p, mutate(service, p, "sb_edit_frames", scene_ids=["a"], instruction="Softer light"))["status"] == "complete"
    assert wait(service, p, mutate(service, p, "sb_generate_videos", scene_ids=["a"]))["status"] == "complete"
    assert calls == ["frame", "edit", "frame", "edit", "video"]


def test_subtitle_import_and_gui_cue_roundtrip(service):
    from app.ui.video_storyboard_page import StoryboardNarrationCue
    p = project(service)
    mutate(service, p, "sb_import_timed_text", format="srt", text="1\n00:00:02,500 --> 00:00:04,750\nA witness.\n")
    cue = service.sb_get_timed_text(str(p["project_id"]))["cues"][0]
    assert cue["start_seconds"] == 2.5 and cue["end_seconds"] == 4.75
    restored = StoryboardNarrationCue.from_value(cue).as_dict()
    assert restored["cue_id"] == cue["cue_id"]
    assert restored["duration_seconds"] == 2.25
    vtt = service.sb_export_timed_text(str(p["project_id"]), format="vtt")["text"]
    mutate(service, p, "sb_import_timed_text", format="vtt", text=vtt)
    assert service.sb_get_timed_text(str(p["project_id"]))["cues"][0]["text"] == "A witness."


def test_shutdown_rejects_active_visual_jobs(service):
    from fastapi.testclient import TestClient
    from app.server.http_app import create_http_app
    from unittest.mock import MagicMock
    import threading
    shutdown = MagicMock()
    app = create_http_app(service.settings_manager, job_manager=MagicMock(), audiobook_store=service.store, shutdown_callback=shutdown)
    with TestClient(app) as client:
        app.state.storyboard_service.events["active"] = threading.Event()
        assert client.get("/health").json()["storyboard_active_jobs"] == 1
        assert client.post("/shutdown").status_code == 409
        shutdown.assert_not_called()
        app.state.storyboard_service.events.clear()
        assert client.post("/shutdown").status_code == 200
        shutdown.assert_called_once()


def test_invalid_reference_does_not_start_provider(service, monkeypatch):
    p = project(service)
    calls = []
    monkeypatch.setattr(service, "execute_item", lambda *a: calls.append(a))
    with pytest.raises(ValueError, match="Entity not found"):
        mutate(service, p, "sb_generate_reference", entity_id="missing", prompt="Portrait")
    assert not calls
