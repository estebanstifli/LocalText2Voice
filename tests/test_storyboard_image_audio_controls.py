import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.ui.video_storyboard_page import VideoStoryboardPage
from app.ui.storyboard_entity_scenes import SceneImagePreview

APP = QApplication.instance() or QApplication([])


def tr(key, default, **values):
    return default.format(**values)


def test_double_click_and_context_menu_share_large_viewer(tmp_path):
    frame = tmp_path / 'frame.png'
    image = QPixmap(640, 360)
    image.fill(QColor('red'))
    image.save(str(frame))
    page = VideoStoryboardPage(tr)
    page.resize(1280, 900)
    page.set_scenes([{'scene_id': '001', 'duration_seconds': 10, 'image_path': str(frame)}])
    page.show()
    APP.processEvents()

    def check_viewer():
        dialogs = page.findChildren(SceneImagePreview)
        active = [d for d in dialogs if d.isVisible()]
        assert len(active) == 1
        assert active[0].pixmap.size() == image.size()
        assert active[0].isModal()
        active[0].close()
        APP.processEvents()

    QTest.mouseDClick(page.timeline_canvas, Qt.MouseButton.LeftButton, pos=QPoint(30, 70))
    check_viewer()
    QTest.mouseDClick(page.current_preview, Qt.MouseButton.LeftButton)
    check_viewer()
    for menu in (page._build_frame_context_menu('001'), page._build_video_context_menu(page.selected_scene())):
        action = next(a for a in menu.actions() if a.text() == 'View Image')
        assert action.isEnabled()
        action.trigger()
        check_viewer()
    frame.unlink()
    assert not page._build_frame_context_menu('001').actions()[0].isEnabled()
    page.close()
    page.deleteLater()


def test_audio_controls_persist_and_update_preview(tmp_path):
    page = VideoStoryboardPage(tr)
    page.set_scenes([{'scene_id': '001', 'duration_seconds': 10}])
    page._select_scene('001')
    page._scene_video_pause_on_first_frame = False
    page.clip_audio_volume.setValue(50)
    page.soundtrack_volume.setValue(30)
    page.scene_audio_volume.setValue(40)
    assert abs(page.scene_video_audio_output.volume() - .2) < .001
    assert abs(page.preview_audio_output.volume() - .3) < .001
    page.scene_audio_check.setChecked(False)
    assert page.scene_video_audio_output.isMuted()
    state = page.project_state()
    assert state['scenes'][0]['video_audio_enabled'] is False
    assert state['scenes'][0]['video_audio_volume'] == .4
    page.restore_project_state(state)
    assert page.clip_audio_volume.value() == 50
    assert page.soundtrack_volume.value() == 30
    assert not page.scene_audio_check.isChecked()
    page.scene_audio_check.setChecked(True)
    assert not page.scene_video_audio_output.isMuted()
    page.clip_audio_check.setChecked(False)
    assert page.scene_video_audio_output.isMuted()
    page.restore_project_state(page.project_state())
    assert not page.clip_audio_check.isChecked()
    page.scene_audio_volume.setValue(350)
    page.clip_audio_volume.setValue(400)
    page.restore_project_state(page.project_state())
    assert page.scene_audio_volume.value() == 350
    assert page.clip_audio_volume.value() == 400
    page.deleteLater()


def test_export_dispatches_mixer_controls_with_existing_full_mix(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    from app.ui.main_window import MainWindow

    mix = tmp_path / 'full-mix.wav'
    mix.write_bytes(b'audio')
    audiobook = SimpleNamespace(mix_audio_path=str(mix), clean_audio_path='',
                                output_dir=tmp_path, project_dir=tmp_path, title='Test')
    page = VideoStoryboardPage(tr)
    page.clip_audio_check.setChecked(False)
    page.clip_audio_volume.setValue(25)
    page.soundtrack_volume.setValue(60)
    holder = Mock()
    holder.video_storyboard_render_thread = None
    holder.video_storyboard_video_thread = None
    holder.video_storyboard_image_edit_thread = None
    holder.current_audiobook_id = 1
    holder.audiobook_store.get_audiobook.return_value = audiobook
    holder.video_storyboard_page = page
    holder.settings = {}
    holder.tr = tr
    holder._safe_project_folder_name.return_value = 'Test'
    with patch('app.ui.main_window.QThread'), patch('app.ui.main_window.VideoStoryboardRenderWorker') as worker:
        MainWindow._start_video_storyboard_render(holder, [])
        args = worker.call_args.args
        assert args[1] == mix
        assert args[3]['video']['clip_audio_enabled'] is False
        assert args[3]['video']['clip_audio_volume'] == .25
        assert args[3]['video']['soundtrack_volume'] == .6
    page.deleteLater()
