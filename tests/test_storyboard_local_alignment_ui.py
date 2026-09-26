import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from app.ui.video_storyboard_page import StoryboardScene, VideoStoryboardPage
from app.ui.video_storyboard_analysis_dialog import VideoStoryboardAnalysisDialog


def tr(key, default, **values):
    return default.format(**values)


def test_scene_alignment_provenance_survives_page_save_and_reload():
    app = QApplication.instance() or QApplication([])
    metadata = {"source_excerpt_id": "19", "source_coordinate_basis": "timed_narration",
                "source_proposal_id": "19-2", "semantic_title": "A football match",
                "start_quote": "The football match begins.", "alignment_method": "estimated_sentence_in_cue",
                "alignment_approximate": True, "consolidated_proposal_ids": ["19-3"]}
    scene = StoryboardScene.from_value({"id": "001", "duration": 12, **metadata}, 0)
    assert all(scene.as_dict()[k] == v for k, v in metadata.items())
    page = VideoStoryboardPage(tr)
    page.set_scenes([scene.as_dict()])
    restored = StoryboardScene.from_value(page.scenes()[0], 0)
    assert all(restored.as_dict()[k] == v for k, v in metadata.items())
    assert "≈" in page.scene_meta_label.text()
    assert metadata["start_quote"] in page.scene_meta_label.toolTip()
    page.close()


def test_analysis_checks_remain_visible_when_alignment_fails():
    app = QApplication.instance() or QApplication([])
    dialog = VideoStoryboardAnalysisDialog(tr)
    dialog.update_plan({"scenes": [], "analysis_phase": "alignment_failed", "alignment_debug": {
        "fragments": [{"status": "validated"}, {"status": "failed"}], "warnings": ["Fragment 2 failed local validation."]}})
    assert dialog.tabs.indexOf(dialog.quality_view) >= 0
    assert "1/2" in dialog.quality_view.toPlainText()
    assert "Fragment 2 failed" in dialog.quality_view.toPlainText()
    dialog.set_finished(False, "Cannot align fragment 2")
    assert "Cannot align fragment 2" in dialog.status_label.text()
    dialog.close()
