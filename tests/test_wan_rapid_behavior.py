import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor
from app.core import video_storyboard_video_comfyui as vc
from app.ui.video_storyboard_page import VideoStoryboardPage, StoryboardScene
from app.ui.video_storyboard_image_edit_dialog import VideoStoryboardImageEditDialog

app = QApplication.instance() or QApplication([])
tr = lambda key, fallback, **values: fallback.format(**values)

@pytest.mark.parametrize("behavior,duration,frames,seconds", [
    ("split", 8, 81, 3.375), ("stretch", 8, 81, 8),
    ("split", 2, 49, 2), ("stretch", 2, 49, 2),
    ("split", 3.375, 81, 3.375),
])
def test_metrics(behavior, duration, frames, seconds):
    config = {"workflow_profile": "wan22_rapid", "fps": 24, "wan_behavior": behavior}
    assert vc.video_generation_metrics(config, duration) == (frames, seconds)
    built = vc.build_video_workflow({"comfyui_video": config}, prompt="motion", image_name="frame.png", seed=0, prefix="test", duration_seconds=duration)
    assert built.generated_frames == frames
    fps = next(node["inputs"]["fps"] for node in built.workflow.values() if node.get("class_type") == "CreateVideo")
    assert fps == (frames / seconds if behavior == "stretch" else 24)


def test_split_preserves_timing_short_remainder_and_undo(tmp_path):
    frame = tmp_path / "last.png"
    image = QImage(16, 9, QImage.Format.Format_RGB32); image.fill(QColor("red")); image.save(str(frame))
    page = VideoStoryboardPage(tr)
    page.set_scenes([{"id": "001", "duration": 3.5}, {"id": "002", "duration": 5}])
    with patch("PySide6.QtMultimedia.QMediaPlayer.setSource"):
        page.set_scene_video("001", str(tmp_path / "clip.mp4"), "move", "start", 3.375, continuation_frame=str(frame))
    scenes = page.scenes()
    assert len(scenes) == 3
    assert scenes[1]["duration_seconds"] == .125
    assert scenes[1]["image_path"] == str(frame) and not scenes[1]["video_path"]
    assert scenes[2]["start_seconds"] == 3.5
    assert StoryboardScene.from_value(scenes[1], 1).duration_seconds == .125
    page._undo_scene_edit()
    assert len(page.scenes()) == 2 and page.scenes()[0]["duration_seconds"] == 3.5
    page.close()


def test_apply_only_after_actual_image_change(tmp_path):
    source = tmp_path / "source.png"
    image = QImage(16, 9, QImage.Format.Format_RGB32); image.fill(QColor("red")); image.setPixelColor(0, 0, QColor("blue")); image.save(str(source))
    dialog = VideoStoryboardImageEditDialog(tr, "001", str(source), configuration={})
    assert dialog.accept_button.isHidden()
    dialog.flip_horizontal_button.click()
    assert not dialog.accept_button.isHidden()
    dialog.reset_button.click()
    assert dialog.accept_button.isHidden()
    candidate = tmp_path / "candidate.png"
    image.mirrored(True, False).save(str(candidate))
    dialog.set_edit_candidate(str(candidate))
    assert not dialog.accept_button.isHidden()
    dialog._discard_ai_candidate()
    assert dialog.accept_button.isHidden()
    dialog.close()


@pytest.mark.parametrize("behavior", ["split", "stretch"])
def test_real_ffmpeg_output_without_gpu(tmp_path, monkeypatch, behavior):
    from PIL import Image
    settings = {"comfyui_video": {"workflow_profile": "wan22_rapid", "wan_behavior": behavior, "fps": 24}}
    source = tmp_path / "source.mp4"
    runner = vc.FFmpegRunner(vc.find_ffmpeg("ffmpeg/ffmpeg.exe"))
    runner.run(["-y", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=24", "-frames:v", "81", "-c:v", "libx264", str(source)])
    frame = tmp_path / "frame.png"; Image.new("RGB", (64, 64)).save(frame)
    monkeypatch.setattr(vc, "_upload_image", lambda *a, **k: "input.png")
    monkeypatch.setattr(vc, "_http_json", lambda *a, **k: {"prompt_id": "test"})
    monkeypatch.setattr(vc, "_wait_for_video_history", lambda *a, **k: {})
    monkeypatch.setattr(vc, "_find_output_video", lambda *a, **k: {"filename": "video.mp4"})
    monkeypatch.setattr(vc, "_download_video", lambda url, target, *a, **k: target.write_bytes(source.read_bytes()))
    target = tmp_path / "candidate.mp4"
    result = vc.generate_storyboard_scene_video({"scene_id": "001", "image_path": str(frame), "duration_seconds": 8}, {}, settings, target, prompt="motion")
    expected_duration = 3.375 if behavior == "split" else 8
    assert result["video_duration_seconds"] == expected_duration
    assert abs(vc._probe_media_duration(target, settings) - expected_duration) < .02
    if behavior == "split":
        assert json.loads(target.with_suffix(".continuation.json").read_text())["duration_seconds"] == 3.375
        expected = tmp_path / "expected.png"
        runner.run(["-y", "-i", str(source), "-vf", "select=eq(n\\,80)", "-frames:v", "1", str(expected)])
        with Image.open(expected) as a, Image.open(target.with_suffix(".continuation.png")) as b:
            assert a.tobytes() == b.tobytes()
    else:
        assert not target.with_suffix(".continuation.json").exists()


def test_profile_order_behavior_persistence_and_clean_inspector():
    from app.ui.video_storyboard_settings import VideoStoryboardSettingsWidget
    widget = VideoStoryboardSettingsWidget(tr)
    assert [widget.profile_combo.itemData(i) for i in range(4)] == ["local", "runpod", "custom_comfyui", "litellm"]
    assert not widget.wan_behavior_panel.isHidden()
    assert widget.wan_behavior_combo.currentData() == "split"
    widget.wan_behavior_combo.setCurrentIndex(1)
    widget.set_configuration(widget.configuration())
    assert widget.configuration()["comfyui_video"]["wan_behavior"] == "stretch"
    widget.profile_combo.setCurrentIndex(1)
    assert widget.wan_behavior_panel.isHidden()
    widget.profile_combo.setCurrentIndex(0)
    assert widget.wan_behavior_combo.currentData() == "stretch"
    page = VideoStoryboardPage(tr)
    page.set_scenes([{"id": "005", "duration": 8, "narration": "Text"}])
    assert page.narration_edit.isHidden() and page.inspector_heading.isHidden()
    assert "Scene 1" in page.scene_meta_label.text()
    assert page.scene_meta_label.objectName() == "sectionTitle"
    widget.close(); page.close()


def test_accept_continuation_moves_image_to_permanent_assets(tmp_path):
    from types import SimpleNamespace
    from app.ui.main_window import MainWindow
    candidates = tmp_path / "candidates"; candidates.mkdir()
    source = candidates / "clip.mp4"; source.write_bytes(b"video")
    image = QImage(16, 9, QImage.Format.Format_RGB32); image.fill(QColor("red")); image.save(str(source.with_suffix(".continuation.png")))
    source.with_suffix(".continuation.json").write_text(json.dumps({"duration_seconds": 3.375}))
    accepted = []
    page = SimpleNamespace(scenes=lambda: [{"scene_id": "001"}], set_scene_video=lambda *a, **k: accepted.append((a, k)), set_video_generation_failed=lambda *a: pytest.fail(str(a)))
    holder = SimpleNamespace(video_storyboard_page=page, _video_storyboard_video_output_dir=lambda candidate=False: candidates if candidate else tmp_path / "clips", tr=tr)
    MainWindow._accept_video_storyboard_video_candidate(holder, {"scene_id": "001", "video_path": str(source), "video_duration_seconds": 8})
    args, kwargs = accepted[0]
    assert args[4] == 3.375
    assert Path(kwargs["continuation_frame"]).parent == tmp_path / "clips"
    assert Path(kwargs["continuation_frame"]).is_file()
    assert not list(candidates.iterdir())
