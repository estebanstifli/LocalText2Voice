import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from app.ui.storyboard_timeline_navigation import TimelineScrollBar, TimelineTimeEdit, parse_time
from app.ui.video_storyboard_page import VideoStoryboardPage


@pytest.mark.parametrize("text,seconds", [("01:23",83), ("1:02:03.5",3723.5),("90",90),("10,250",10.25),("90:00",5400)])
def test_time_formats(text, seconds):
    assert parse_time(text) == seconds


@pytest.mark.parametrize("text", ["1:60", "1:99:00", "no", "-10", "1:2:3:4"])
def test_invalid_time(text):
    with pytest.raises(ValueError):
        parse_time(text)


def test_quarter_grip_scrolls_full_range_after_resize():
    app = QApplication.instance() or QApplication([])
    bar = TimelineScrollBar()
    bar.resize(800, 18)
    bar.setRange(0, 100000)
    bar.show()
    app.processEvents()
    assert bar.handle_rect().width() == 200
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=bar.handle_rect().center())
    QTest.mouseMove(bar, QPoint(799, 8))
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=QPoint(799, 8))
    assert bar.value() == bar.maximum()
    QTest.keyClick(bar, Qt.Key.Key_Home)
    assert bar.value() == bar.minimum()
    bar.resize(1200, 18)
    app.processEvents()
    assert bar.handle_rect().width() == 300
    bar.close()


def test_edit_time_survives_playback_updates_and_commits_or_cancels():
    app = QApplication.instance() or QApplication([])
    field = TimelineTimeEdit(lambda k,d: d)
    jumps = []
    field.seekRequested.connect(jumps.append)
    field.set_time(20, 600)
    field._begin()
    field.selectAll()
    QTest.keyClicks(field, "02:30")
    field.set_time(25, 600)
    assert field.text() == "02:30"
    QTest.keyClick(field, Qt.Key.Key_Return)
    assert jumps == [150]
    field._begin()
    field.selectAll()
    QTest.keyClicks(field, "08:00")
    QTest.keyClick(field, Qt.Key.Key_Escape)
    assert jumps == [150]
    field._begin()
    field._commit()  # Viewing the time without editing must not seek.
    assert jumps == [150]
    field.close()


def test_ten_percent_zoom_and_goto_without_audio():
    app = QApplication.instance() or QApplication([])
    page = VideoStoryboardPage(lambda k,d,**v: d.format(**v))
    page.set_scenes([{"id":str(i),"duration":15,"prompt":"Scene"} for i in range(20)])
    while page.timeline_canvas.zoom_index:
        page._zoom_out()
    assert page.zoom_label.text() == "10%"
    assert page.timeline_canvas.pixels_per_second == 4.5
    assert not page.zoom_out_button.isEnabled()
    page._goto_preview_time(160)
    assert page.timeline_canvas.playhead_seconds == 160
    assert "02:40" in page.preview_time_label.text()
    page._goto_preview_time(500)
    assert page.timeline_canvas.playhead_seconds == 300
    page.close()
