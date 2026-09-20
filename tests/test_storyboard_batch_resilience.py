import json
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from app.core.storyboard_generation_errors import generation_error_details
from app.core.storyboard_illustration_context import illustration_context, contextualize_prompt
from app.workers.video_storyboard_frame_worker import VideoStoryboardFrameWorker
from app.ui.video_storyboard_page import VideoStoryboardPage

APP = QApplication.instance() or QApplication([])
MODULE = "app.workers.video_storyboard_frame_worker."
BLOCK = json.dumps({"error": {"code": "moderation_blocked", "type": "image_generation_user_error",
    "message": "Request rejected req_abc", "moderation_details": {"moderation_stage": "output", "categories": ["other"]}}})


def run_worker(tmp_path, generate, *, plan=None, scenes=None, candidate=False):
    worker = VideoStoryboardFrameWorker({"plan": plan or {}, "scenes": scenes or [
        {"scene_id": "001", "prompt": "A boy in a doorway"}, {"scene_id": "002", "prompt": "A garden"}]},
        {"image_provider": "litellm_image", "litellm_image": {"model": "openai/gpt-image-2"}}, tmp_path, candidate=candidate)
    ready, failed, finished, terminal = [], [], [], []
    worker.frameReady.connect(ready.append)
    worker.frameFailed.connect(failed.append)
    worker.finished.connect(finished.append)
    worker.failed.connect(terminal.append)
    with patch(MODULE + "prepare_image_runtime", return_value={}), patch(MODULE + "generate_storyboard_frame", side_effect=generate) as call, patch.object(worker._cancel_event, "wait", return_value=False):
        worker.run()
    return worker, ready, failed, finished, terminal, call.call_count


def write_image(scene, plan, settings, target, **kwargs):
    target.write_bytes(b"image")
    return {"scene_id": scene["scene_id"]}


def test_moderation_continues_batch_without_retry_and_keeps_original(tmp_path):
    original = tmp_path / "scene-001-001.png"
    original.write_bytes(b"original")
    def generate(scene, plan, settings, target, **kwargs):
        if scene["scene_id"] == "001":
            target.write_bytes(b"partial")
            raise RuntimeError(BLOCK)
        return write_image(scene, plan, settings, target)
    worker, ready, failed, finished, terminal, count = run_worker(tmp_path, generate)
    assert count == 2 and not terminal
    assert len(ready) == len(failed) == 1
    assert failed[0]["moderation_stage"] == "output"
    assert failed[0]["request_id"] == "req_abc"
    assert failed[0]["provider"] == "LiteLLM · openai/gpt-image-2"
    assert [x["status"] for x in finished[0]] == ["failed", "generated"]
    assert original.read_bytes() == b"original"
    assert not list(tmp_path.glob(".*.png"))


def test_invalid_scene_reference_does_not_abort_other_scenes(tmp_path):
    def resolve(scene, *args):
        if scene["scene_id"] == "001":
            raise ValueError("Too many references")
        return scene
    with patch(MODULE + "scene_with_references", side_effect=resolve):
        _, ready, failed, finished, terminal, count = run_worker(tmp_path, write_image)
    assert count == 1 and len(failed) == len(ready) == 1 and finished and not terminal


def test_transient_retry_is_bounded(tmp_path):
    _, _, failed, finished, terminal, count = run_worker(tmp_path, RuntimeError("HTTP 503 unavailable"))
    assert count == 6 and len(failed) == 2 and finished and not terminal


def test_transient_retry_can_recover(tmp_path):
    calls = []
    def generate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("HTTP 503 unavailable")
        return write_image(*args, **kwargs)
    _, ready, failed, finished, terminal, count = run_worker(tmp_path, generate)
    assert count == 3 and len(ready) == 2 and not failed and not terminal


def test_failed_scene_with_existing_image_is_retried(tmp_path):
    original = tmp_path / "old.png"
    original.write_bytes(b"old")
    _, ready, failed, _, _, count = run_worker(tmp_path, write_image,
        plan={"frame_generation_errors": {"001": {"code": "moderation_blocked"}}},
        scenes=[{"scene_id": "001", "image_path": str(original)}])
    assert count == 1 and len(ready) == 1 and not failed


def test_candidate_error_remains_terminal(tmp_path):
    _, ready, failed, finished, terminal, count = run_worker(tmp_path, RuntimeError(BLOCK), candidate=True)
    assert count == 1 and terminal and not failed and not finished and not ready


def test_cancel_stops_instead_of_reporting_scene_failure(tmp_path):
    worker = VideoStoryboardFrameWorker({"scenes": [{"scene_id": "001"}, {"scene_id": "002"}]}, {}, tmp_path)
    terminal, failed, finished = [], [], []
    worker.failed.connect(terminal.append)
    worker.frameFailed.connect(failed.append)
    worker.finished.connect(finished.append)
    def generate(*args, **kwargs):
        worker.cancel()
        raise RuntimeError("cancelled")
    with patch(MODULE + "prepare_image_runtime", return_value={}), patch(MODULE + "generate_storyboard_frame", side_effect=generate) as call:
        worker.run()
    assert call.call_count == 1 and terminal and not failed and not finished


@pytest.mark.parametrize("error", [BLOCK, "HTTP 429 insufficient_quota", "HTTP 401 invalid_api_key", "unsupported image size"])
def test_non_transient_errors_are_not_retried(error):
    assert not generation_error_details(RuntimeError(error))["retryable"]


def tr(key, default, **kwargs):
    return default.format(**kwargs)


def test_warning_persists_and_success_clears_it(tmp_path):
    page = VideoStoryboardPage(tr)
    page.set_scenes([{"scene_id": "001", "prompt": "A door", "start": 0, "end": 2}])
    failure = {"scene_id": "001", "code": "moderation_blocked", "status": "failed", "provider": "LiteLLM", "error": BLOCK}
    page.set_frame_generation_scene_failed(failure)
    assert not page.frame_errors_button.isHidden()
    assert "0 ready, 1 failed" in page.set_frame_generation_finished([failure])
    state = page.project_state()
    assert "001" in state["plan"]["frame_generation_errors"]
    restored = VideoStoryboardPage(tr)
    restored.restore_project_state(state)
    assert not restored.frame_errors_button.isHidden()
    restored.set_frame_generated("001", str(tmp_path / "success.png"))
    assert restored.frame_errors_button.isHidden()
    assert not restored.project_state()["plan"]["frame_generation_errors"]
    page.close()
    restored.close()


def test_family_context_is_editable_and_not_duplicated():
    plan = {"continuity": {"characters": [{"name": "Jack"}], "locations": [{"name": "Beanstalk"}]}}
    context = illustration_context(plan)
    assert "family-friendly" in context
    once = contextualize_prompt("A door", plan)
    assert contextualize_prompt(once, plan) == once
    page = VideoStoryboardPage(tr)
    page.set_analysis_result({**plan, "scenes": []})
    assert page.illustration_context_edit.toPlainText() == context
    page.illustration_context_edit.setPlainText("A gentle picture book")
    assert page.project_overrides()["narrative"]["illustration_context"] == "A gentle picture book"
    page.set_analysis_result({**plan, "scenes": []})
    assert page.illustration_context_edit.toPlainText() == "A gentle picture book"
    plan["narrative_context"] = {"illustration_context": ""}
    assert contextualize_prompt("A door", plan) == "A door"
    page.close()


def test_moderation_cannot_trigger_parameter_fallback(tmp_path):
    from app.core.video_storyboard_comfyui import _rejected_image_parameter_message
    from app.core.storyboard_generation_references import litellm_reference_generation
    from app.core.video_storyboard_image_edit import VideoStoryboardImageEditError, _litellm_direct_edit
    message = BLOCK + " HTTP 400 unsupported size response_format"
    assert _rejected_image_parameter_message(message, {"size": "1280x720"}) == ""
    error = VideoStoryboardImageEditError(message)
    with patch("app.core.video_storyboard_image_edit._multipart_json", side_effect=error) as call:
        with pytest.raises(Exception, match="moderation_blocked"):
            litellm_reference_generation([], {"model": "openai/gpt-image-2", "base_url": "https://example.test/v1"}, "A door", 1280, 720)
        assert call.call_count == 1
    path = tmp_path / "reference.png"
    path.write_bytes(b"image")
    with patch("litellm.image_edit", side_effect=error) as call:
        with pytest.raises(VideoStoryboardImageEditError, match="moderation_blocked"):
            _litellm_direct_edit([{"path": str(path)}], "openai/gpt-image-2", "A door", "", 30, 1280, 720)
        assert call.call_count == 1


def test_error_redacts_credentials():
    from app.core.storyboard_generation_errors import redact_generation_error
    text = redact_generation_error("Failed with key abc123 and Bearer another-secret", {"litellm_image": {"api_key": "abc123"}})
    assert "abc123" not in text and "another-secret" not in text

def test_timeline_error_button_and_modal_summary():
    from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit
    page = VideoStoryboardPage(tr)
    page.set_scenes([{"scene_id": "001", "duration": 2}, {"scene_id": "002", "duration": 2}])
    page.set_frame_generation_scene_failed({"scene_id": "002", "error": "Image rejected"})
    page.set_video_generation_failed("002", "Video unavailable")
    page.set_render_failed("Encoder unavailable")
    row = page.timeline_heading.parentWidget().layout().itemAt(0).layout()
    assert row.itemAt(0).widget() is page.timeline_heading
    assert row.itemAt(1).widget() is page.frame_errors_button
    assert not page.frame_errors_button.icon().isNull()
    assert page.frame_errors_button.text() == "View generation errors"
    def inspect(dialog):
        summary = dialog.findChild(QLabel, "storyboardErrorsSummary").text()
        assert summary == "3 errors · 1 failed scenes: 2 (002) · 1 general errors"
        details = dialog.findChild(QPlainTextEdit).toPlainText()
        assert all(message in details for message in ["Image rejected", "Video unavailable", "Encoder unavailable"])
    with patch.object(QDialog, "exec", inspect):
        page.frame_errors_button.click()
    restored = VideoStoryboardPage(tr)
    restored.restore_project_state(page.project_state())
    assert len(restored._storyboard_errors()) == 3
    restored.set_frame_generated("002", "ok.png")
    restored.set_video_candidate("002", "ok.mp4")
    restored.set_render_finished("ok.mp4")
    assert restored.frame_errors_button.isHidden()
    page.close()
    restored.close()


def test_other_storyboard_errors_are_redacted_and_deleted_scenes_are_ignored():
    page = VideoStoryboardPage(tr)
    page.set_configuration({"litellm_image": {"api_key": "secret-value"}})
    page.set_scenes([{"scene_id": "001", "duration": 2}])
    page.set_image_edit_failed("001", "Failed secret-value")
    page.set_frame_generation_failed("Failed Bearer private-token")
    page.set_analysis_failed("Analysis unavailable")
    page.set_frame_replacement_failed("Image unreadable")
    assert len(page._storyboard_errors()) == 4
    state = json.dumps(page.project_state())
    assert "secret-value" not in state and "private-token" not in state
    page.set_scenes([])
    assert len(page._storyboard_errors()) == 3
    assert "0 failed scenes" in page._storyboard_error_summary()
    page.close()

def test_preview_errors_share_the_button_and_clear_on_playback():
    from PySide6.QtMultimedia import QMediaPlayer
    page = VideoStoryboardPage(tr)
    page.set_scenes([{"scene_id": "001", "duration": 2}])
    page._on_scene_video_error(QMediaPlayer.Error.ResourceError, "Video unreadable")
    page._on_preview_error(QMediaPlayer.Error.ResourceError, "Audio unreadable")
    assert len(page._storyboard_errors()) == 2
    page.scene_video_player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    page.preview_player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    assert page.frame_errors_button.isHidden()
    page.set_video_generation_failed("001", "Cancelled")
    assert page.frame_errors_button.isHidden()
    page.close()
