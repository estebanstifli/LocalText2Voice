import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from app.core.video_storyboard_project import save_storyboard_state, load_storyboard_state, storyboard_matches_source
from app.core.storyboard_frame_checkpoint import FrameCheckpoint, ACTIVE_RUNS
from app.workers.video_storyboard_frame_worker import VideoStoryboardFrameWorker
from app.ui.video_storyboard_page import VideoStoryboardPage


def state():
    return {"source": {"text": "Saved story.\n", "project_id": 1}, "plan": {"continuity": {"characters": []}},
            "scenes": [{"scene_id": "001", "prompt": "First", "image_path": ""},
                       {"scene_id": "002", "prompt": "Second", "image_path": ""}]}


def test_source_newline_does_not_hide_saved_work(tmp_path):
    save_storyboard_state(tmp_path, state())
    saved = load_storyboard_state(tmp_path)
    assert storyboard_matches_source(saved, "Saved story.")
    assert storyboard_matches_source(saved, "Saved story.\r\n")
    assert not storyboard_matches_source(saved, "Changed story.")
    assert not storyboard_matches_source(saved, "Savedstory.")


def test_worker_recovers_before_any_gui_delivery_and_keeps_old_images(tmp_path, monkeypatch):
    old = tmp_path / "old.png"
    Image.new("RGB", (8, 8), "red").save(old)
    baseline = state()
    baseline["scenes"][0]["image_path"] = str(old)
    save_storyboard_state(tmp_path, baseline)
    payload = deepcopy(baseline)
    payload["scenes"][0]["image_path"] = ""  # Regenerate, keeping accepted image in snapshot.
    worker = VideoStoryboardFrameWorker(payload, {}, tmp_path / "frames",
        checkpoint_project_dir=tmp_path, checkpoint_state=baseline)
    monkeypatch.setattr("app.workers.video_storyboard_frame_worker.prepare_image_runtime", lambda *a, **k: {})
    calls = []
    def generate(scene, plan, settings, target, **kwargs):
        calls.append(target)
        if len(calls) == 2:
            raise SystemExit("Simulated killed process")
        Image.new("RGB", (8, 8), "blue").save(target)
        return {}
    monkeypatch.setattr("app.workers.video_storyboard_frame_worker.generate_storyboard_frame", generate)
    with pytest.raises(SystemExit):
        worker.run()  # No connected frameReady listener.
    restored = load_storyboard_state(tmp_path)
    recovered = Path(restored["scenes"][0]["image_path"])
    assert recovered.is_file() and recovered != old
    assert Image.open(old).getpixel((0, 0)) == (255, 0, 0)
    assert restored["scenes"][1]["image_path"] == ""
    assert len(restored["scenes"]) == 2
    assert not (tmp_path / "storyboard/frame-checkpoint.json").exists()
    assert load_storyboard_state(tmp_path)["scenes"] == restored["scenes"]


def test_intent_recovers_atomic_image_even_before_completion_receipt(tmp_path):
    baseline = state()
    save_storyboard_state(tmp_path, baseline)
    checkpoint = FrameCheckpoint(tmp_path, baseline)
    image = tmp_path / "complete.png"
    checkpoint.record({"scene_id": "001", "image_path": str(image), "status": "pending"})
    Image.new("RGB", (8, 8)).save(image)
    with checkpoint.events.open("ab") as file:
        file.write(b'{"scene_id": "002",')  # torn final journal line
    ACTIVE_RUNS.discard(checkpoint.run_id)
    restored = load_storyboard_state(tmp_path)
    assert restored["scenes"][0]["image_path"] == str(image)
    assert restored["scenes"][0]["status"] == "generated"


def test_recovery_preserves_changed_prompt_and_existing_image_on_failure(tmp_path):
    baseline = state()
    checkpoint = FrameCheckpoint(tmp_path, baseline)
    image = tmp_path / "old.png"
    Image.new("RGB", (8, 8)).save(image)
    changed = deepcopy(baseline)
    changed["scenes"][0].update(prompt="User's later edit", image_path=str(image))
    save_storyboard_state(tmp_path, changed)
    checkpoint.record({"scene_id": "001", "status": "failed", "error": "Remote error"})
    ACTIVE_RUNS.discard(checkpoint.run_id)
    restored = load_storyboard_state(tmp_path)
    assert restored["scenes"][0]["prompt"] == "User's later edit"
    assert restored["scenes"][0]["image_path"] == str(image)


def test_backup_and_snapshot_fallback(tmp_path):
    baseline = state()
    path = save_storyboard_state(tmp_path, baseline)
    save_storyboard_state(tmp_path, baseline)
    path.write_text('{"torn":', encoding="utf8")
    assert len(load_storyboard_state(tmp_path)["scenes"]) == 2
    path.unlink()
    path.with_suffix(".json.bak").unlink()
    checkpoint = FrameCheckpoint(tmp_path, baseline)
    ACTIVE_RUNS.discard(checkpoint.run_id)
    assert len(load_storyboard_state(tmp_path)["scenes"]) == 2


def test_generate_frames_reopens_same_running_dialog():
    app = QApplication.instance() or QApplication([])
    page = VideoStoryboardPage(lambda k, d, **v: d.format(**v))
    page.set_scenes([{"id": "001", "duration": 4, "prompt": "Scene"}])
    starts = []
    page.generateFramesRequested.connect(starts.append)
    page.generate_frames_button.click()
    dialog = page._frame_batch_dialog
    dialog.start_button.click()
    dialog.set_progress(1, 2, "Working", 50)
    dialog._close_or_hide()
    assert page.generate_frames_button.isEnabled() and not dialog.isVisible()
    page.generate_frames_button.click()
    assert page._frame_batch_dialog is dialog and dialog.isVisible()
    assert dialog.progress_bar.value() == 50 and len(starts) == 1
    dialog.reject()  # Escape must hide, not destroy, a running dialog.
    page.generate_frames_button.click()
    assert page._frame_batch_dialog is dialog and len(starts) == 1
    page.set_frame_generation_finished([])
    dialog.close()
    page.close()
    app.processEvents()


def test_actual_process_exit_keeps_completed_frames(tmp_path):
    import subprocess
    import sys
    script = '''
import os, sys
from pathlib import Path
from PIL import Image
from app.workers import video_storyboard_frame_worker as module
root = Path(sys.argv[1])
payload = {"source": {"text": "A book"}, "plan": {}, "scenes": [
    {"id": "001", "prompt": "One"}, {"id": "002", "prompt": "Two"}]}
module.prepare_image_runtime = lambda *a, **k: {}
def generate(scene, plan, settings, target, **kwargs):
    if scene["id"] == "002":
        os._exit(23)
    Image.new("RGB", (16, 16), "green").save(target)
    return {}
module.generate_storyboard_frame = generate
module.VideoStoryboardFrameWorker(payload, {}, root / "frames", checkpoint_project_dir=root).run()
'''
    result = subprocess.run([sys.executable, "-", str(tmp_path)], input=script, text=True,
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert result.returncode == 23, result.stderr
    recovered = load_storyboard_state(tmp_path)
    assert len(recovered["scenes"]) == 2
    assert Path(recovered["scenes"][0]["image_path"]).is_file()
    assert not recovered["scenes"][1]["image_path"]
