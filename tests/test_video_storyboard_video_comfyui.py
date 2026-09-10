from __future__ import annotations

from pathlib import Path
import json

from app.core.comfyui_http import COMFYUI_USER_AGENT
from app.core import video_storyboard_video_comfyui as video_comfyui
from app.core.video_storyboard_comfyui import _http_json


def _settings() -> dict:
    return {
        "comfyui": {"base_url": "http://127.0.0.1:8188"},
        "comfyui_video": {
            "base_url": "http://127.0.0.1:8188",
            "workflow_path": "",
            "unet_model": "wan.gguf",
            "text_encoder": "umt5.safetensors",
            "vae_model": "wan.vae.safetensors",
            "clip_vision_model": "clip-vision.safetensors",
            "width": 640,
            "height": 360,
            "frames": 49,
            "steps": 4,
            "fps": 24,
            "timeout_seconds": 1800,
        },
    }


def test_remote_comfyui_requests_share_browser_identity_and_bearer_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured = []
    responses = [
        b'{"ok": true}',
        b'{"name": "uploaded.png", "subfolder": "ltv"}',
        b"video-bytes",
    ]

    class Response:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return self.payload

    def fake_urlopen(request, timeout):
        captured.append((request, timeout))
        return Response(responses.pop(0))

    monkeypatch.setattr(video_comfyui.urllib.request, "urlopen", fake_urlopen)
    auth = {"Authorization": "Bearer remote-secret"}
    assert _http_json(
        "https://example.test/object_info",
        timeout=60,
        headers=auth,
    ) == {"ok": True}
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")
    assert video_comfyui._upload_image(
        "https://example.test",
        frame,
        60,
        headers=auth,
    ) == "ltv/uploaded.png"
    video_comfyui._download_video(
        "https://example.test/view?filename=clip.mp4",
        tmp_path / "clip.mp4",
        60,
        headers=auth,
    )

    assert len(captured) == 3
    for request, _timeout in captured:
        assert request.get_header("User-agent") == COMFYUI_USER_AGENT
        assert request.get_header("Authorization") == "Bearer remote-secret"
    assert captured[0][0].get_header("Accept") == (
        "application/json, text/plain, */*"
    )
    assert captured[1][0].get_header("Content-type").startswith(
        "multipart/form-data; boundary="
    )
    assert captured[2][0].get_header("Accept") == "video/*,*/*"


def test_bundled_wan_workflow_is_customized_for_the_scene() -> None:
    workflow = video_comfyui.build_wan_video_workflow(
        _settings(),
        prompt="A child runs through a sunny square.",
        image_name="ltv/input.png",
        seed=4512,
        prefix="LocalText2Voice/video/test",
    )

    assert workflow["9"]["inputs"]["image"] == "ltv/input.png"
    assert workflow["5"]["inputs"]["text"] == "A child runs through a sunny square."
    assert workflow["3"]["inputs"]["seed"] == 4512
    assert workflow["3"]["inputs"]["steps"] == 4
    assert workflow["11"]["inputs"]["width"] == 640
    assert workflow["11"]["inputs"]["height"] == 360
    assert workflow["11"]["inputs"]["length"] == 49
    assert workflow["13"]["inputs"]["fps"] == 24.0
    assert workflow["14"]["inputs"]["format"] == "mp4"


def test_wan_workflow_caps_generation_at_81_frames() -> None:
    workflow = video_comfyui.build_wan_video_workflow(
        _settings(),
        prompt="A child runs through a sunny square.",
        image_name="ltv/input.png",
        seed=4512,
        prefix="LocalText2Voice/video/test",
        duration_seconds=7.8,
    )

    # Wan generation is limited to its coherent 81-frame window.
    assert workflow["11"]["inputs"]["length"] == 81
    assert video_comfyui.wan_video_frame_count(7.8, 24) == 189


def test_ltx_profile_binds_generic_scene_parameters() -> None:
    settings = _settings()
    settings["comfyui_video"].update(
        {
            "workflow_profile": "ltx23_i2v",
            "width": 1280,
            "height": 720,
            "fps": 25,
            "ltx_prompt_enhance": True,
        }
    )

    built = video_comfyui.build_video_workflow(
        settings,
        prompt="The girl walks through the meadow at normal speed.",
        image_name="ltv/frame.png",
        seed=9821,
        prefix="LocalText2Voice/video/ltx-test",
        duration_seconds=7.8,
    )

    workflow = built.workflow
    assert built.profile == "ltx23_i2v"
    assert built.generated_frames == 201
    assert built.generated_duration_seconds == 201 / 25
    assert workflow["269"]["inputs"]["image"] == "ltv/frame.png"
    assert workflow["320:319"]["inputs"]["value"].startswith("The girl walks")
    assert workflow["320:277"]["inputs"]["noise_seed"] == 9821
    assert workflow["320:276"]["inputs"]["noise_seed"] == 42
    assert workflow["320:312"]["inputs"]["value"] == 1280
    assert workflow["320:299"]["inputs"]["value"] == 720
    assert workflow["320:301"]["inputs"]["value"] == 8
    assert workflow["320:300"]["inputs"]["value"] == 25
    assert workflow["75"]["inputs"]["filename_prefix"].endswith("ltx-test")
    assert workflow["320:328"]["inputs"]["value"] is True


def test_custom_profile_supports_typed_placeholders_and_node_bindings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "custom-video-api.json"
    path.write_text(
        json.dumps(
            {
                "load": {
                    "class_type": "LoadImage",
                    "inputs": {"image": "hardcoded.png"},
                },
                "parameters": {
                    "class_type": "CustomVideoNode",
                    "inputs": {
                        "text": "unchanged",
                        "seed": "{{SEED}}",
                        "width": "{{WIDTH}}",
                        "duration": "{{DURATION_SECONDS}}",
                        "frames": "{{FRAME_COUNT}}",
                    },
                },
                "save": {
                    "class_type": "SaveVideo",
                    "inputs": {"filename_prefix": "video/default"},
                },
            }
        ),
        encoding="utf-8",
    )
    settings = _settings()
    settings["comfyui_video"].update(
        {
            "workflow_profile": "custom",
            "workflow_path": str(path),
            "width": 960,
            "height": 540,
            "fps": 30,
            "bindings": {"prompt": "parameters.text"},
        }
    )

    built = video_comfyui.build_video_workflow(
        settings,
        prompt="A smooth tracking shot.",
        image_name="remote/input.png",
        seed=77,
        prefix="video/custom/output",
        duration_seconds=4.2,
    )

    assert built.profile == "custom"
    assert built.generated_frames == 126
    assert built.workflow["load"]["inputs"]["image"] == "remote/input.png"
    assert built.workflow["parameters"]["inputs"] == {
        "text": "A smooth tracking shot.",
        "seed": 77,
        "width": 960,
        "duration": 4.2,
        "frames": 126,
    }
    assert built.workflow["save"]["inputs"]["filename_prefix"] == "video/custom/output"


def test_end_reference_role_reverses_downloaded_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    frame = tmp_path / "frame.png"
    target = tmp_path / "candidate.mp4"
    frame.write_bytes(b"image")
    events: list[object] = []
    monkeypatch.setattr(video_comfyui, "_upload_image", lambda *_args: "input.png")
    monkeypatch.setattr(
        video_comfyui,
        "_http_json",
        lambda *_args, **_kwargs: {"prompt_id": "job-1"},
    )
    monkeypatch.setattr(
        video_comfyui,
        "_wait_for_video_history",
        lambda *_args: {"outputs": {}},
    )
    monkeypatch.setattr(
        video_comfyui,
        "_find_output_video",
        lambda _history, output_node="": {
            "filename": "clip.mp4",
            "subfolder": "video",
            "type": "output",
        },
    )
    monkeypatch.setattr(
        video_comfyui,
        "_download_video",
        lambda _url, path, _timeout: path.write_bytes(b"forward"),
    )
    monkeypatch.setattr(
        video_comfyui,
        "_reverse_video",
        lambda source, output, _settings: (
            events.append((source, output)),
            output.write_bytes(b"reversed"),
        ),
    )
    monkeypatch.setattr(
        video_comfyui,
        "_probe_media_duration",
        lambda _source, _settings: 7.92,
    )
    monkeypatch.setattr(
        video_comfyui,
        "_retime_video",
        lambda source, duration, _settings, **_kwargs: events.append(
            ("retime", source, duration)
        ),
    )

    result = video_comfyui.generate_storyboard_scene_video(
        {
            "scene_id": "001",
            "image_path": str(frame),
            "duration_seconds": 8.0,
        },
        {"base_seed": 99},
        {**_settings(), "comfyui_video": {**_settings()["comfyui_video"], "wan_behavior": "stretch"}},
        target,
        prompt="Subtle natural movement",
        frame_role="end",
    )

    assert target.read_bytes() == b"reversed"
    assert result["video_frame_role"] == "end"
    assert result["video_path"] == str(target)
    assert result["video_duration_seconds"] == 8.0
    assert result["generated_frames"] == 81
    assert result["generated_duration_seconds"] == 7.92
    assert events


def test_output_video_is_read_from_save_video_node() -> None:
    result = video_comfyui._find_output_video(
        {
            "outputs": {
                "14": {
                    "videos": [
                        {
                            "filename": "WAN/result.mp4",
                            "subfolder": "video",
                            "type": "output",
                        }
                    ]
                }
            }
        }
    )

    assert result["filename"] == "WAN/result.mp4"
