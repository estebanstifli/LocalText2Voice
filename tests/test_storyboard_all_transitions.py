import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QComboBox, QDialog
from app.ui.video_storyboard_page import VideoStoryboardPage

APP = QApplication.instance() or QApplication([])


def test_all_transitions_accept_cancel_and_single_undo():
    page = VideoStoryboardPage(lambda key, default, **kw: default.format(**kw))
    page.set_scenes([{'id': '1', 'duration': 2, 'transition': 'fade'}, {'id': '2', 'duration': 3, 'transition': 'wipeleft', 'video_path': 'clip.mp4'}, {'id': '3', 'duration': 4, 'transition': 'dissolve'}])
    page._select_transition(0)
    original = page.scenes()
    events = []
    page.scenesChanged.connect(events.append)
    def choose(dialog):
        combo = dialog.findChild(QComboBox, 'allTransitionsCombo')
        assert combo.currentData() == 'none'
        combo.setCurrentIndex(combo.findData('none'))
        return QDialog.DialogCode.Accepted
    with patch.object(QDialog, 'exec', choose):
        page.request_all_transitions()
    assert [s['transition'] for s in page.scenes()] == ['fade', 'none', 'none']
    assert page.transition_combo.currentData() == 'none'
    assert len(events) == 1
    page.undo_button.click()
    assert page.scenes() == original
    page.redo_button.click()
    assert [s['transition'] for s in page.scenes()][1:] == ['none', 'none']
    undo_count = len(page._undo_stack)
    page._apply_all_transitions('none')
    assert len(page._undo_stack) == undo_count
    def cancel(dialog):
        combo = dialog.findChild(QComboBox, 'allTransitionsCombo')
        combo.setCurrentIndex(combo.findData('fade'))
        return QDialog.DialogCode.Rejected
    with patch.object(QDialog, 'exec', cancel):
        page.request_all_transitions()
    assert [s['transition'] for s in page.scenes()][1:] == ['none', 'none']
    page.close()
