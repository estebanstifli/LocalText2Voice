"""Verify audible samples, timing and mute controls using real FFmpeg."""
import array
import math
import subprocess
import wave
from pathlib import Path

import pytest

from app.core.storyboard_clip_audio import probe_clip
from app.core.video_storyboard_renderer import render_storyboard_video
from app.core.video_storyboard_video_comfyui import _retime_video, _reverse_video

FFMPEG = Path('ffmpeg/ffmpeg.exe').resolve()
pytestmark = pytest.mark.skipif(not FFMPEG.is_file(), reason='Bundled FFmpeg required')


def make_clip(path, sound=True):
    args = ['-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=160x90:r=20:d=0.5']
    if sound:
        args += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=0.5', '-c:a', 'aac']
    subprocess.run([str(FFMPEG), *args, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)], check=True, capture_output=True)


def samples(path):
    result = subprocess.run([str(FFMPEG), '-v', 'error', '-i', str(path), '-map', '0:a:0',
                             '-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1'], check=True, capture_output=True)
    return array.array('f', result.stdout)


def rms(values, start, end):
    window = values[round(start * 48000):round(end * 48000)]
    return math.sqrt(sum(x * x for x in window) / len(window))


@pytest.mark.parametrize('sound', [True, False])
def test_retime_and_reverse_preserve_optional_audio(tmp_path, sound):
    clip = tmp_path / 'clip.mp4'
    make_clip(clip, sound)
    settings = {'ffmpeg_path': str(FFMPEG), 'comfyui_video': {'fps': 20}}
    _retime_video(clip, 1, settings, source_duration_seconds=0.5)
    has_audio, duration = probe_clip(FFMPEG, clip)
    assert has_audio is sound
    assert duration == pytest.approx(1, abs=.05)
    if sound:
        assert rms(samples(clip), .1, .85) > .03
    reversed_clip = tmp_path / 'reversed.mp4'
    _reverse_video(clip, reversed_clip, settings)
    assert probe_clip(FFMPEG, reversed_clip)[0] is sound


def test_render_clip_layer_timing_volume_and_mutes(tmp_path):
    clip = tmp_path / 'clip.mp4'
    make_clip(clip)
    frame = tmp_path / 'frame.ppm'
    frame.write_bytes(b'P6\n64 36\n255\n' + bytes((0, 128, 128)) * 64 * 36)
    base = tmp_path / 'base.wav'
    with wave.open(str(base), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b'\0\0' * 96000)
    scenes = [dict(image_path=str(frame), duration_seconds=d, transition='fade') for d in (.5, 1, .5)]
    # No stored media duration: both video and audio must discover the same 0.5s source.
    scenes[1]['video_path'] = str(clip)
    settings = {'ffmpeg_path': str(FFMPEG), 'image': {'width': 160, 'height': 90},
                'video': {'fps': 20, 'supersample': 1, 'preset': 'ultrafast', 'transition_seconds': .1}}

    def render(name):
        output = tmp_path / (name + '.mp4')
        render_storyboard_video(scenes, base, output, settings)
        return samples(output)

    full = render('full')
    assert rms(full, .1, .4) < .0001
    assert rms(full, .7, 1.3) > .03
    assert rms(full, 1.6, 1.9) < .0001
    scenes[1]['video_audio_volume'] = 2
    amplified = render('amplified')
    assert rms(amplified, .7, 1.3) / rms(full, .7, 1.3) == pytest.approx(2, abs=.1)
    scenes[1]['video_audio_volume'] = .5
    settings['video']['clip_audio_volume'] = .5
    quiet = render('quiet')
    assert rms(quiet, .7, 1.3) / rms(full, .7, 1.3) == pytest.approx(.25, abs=.03)
    scenes[1]['video_audio_enabled'] = False
    assert rms(render('scene-muted'), .1, 1.9) < .0001
    scenes[1]['video_audio_enabled'] = True
    settings['video']['clip_audio_enabled'] = False
    assert rms(render('track-muted'), .1, 1.9) < .0001

    # Muting the audiobook leaves the clip audible and keeps other scenes silent.
    with wave.open(str(base), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(array.array('h', [int(2000 * math.sin(i * 2 * math.pi * 220 / 48000)) for i in range(96000)]).tobytes())
    settings['video'].update(clip_audio_enabled=True, soundtrack_volume=0)
    clip_only = render('clip-only')
    assert rms(clip_only, .1, .4) < .0001
    assert rms(clip_only, .7, 1.3) > .01
    scenes[2]['video_path'] = str(clip)
    multiple = render('two-clips')
    assert rms(multiple, .1, .4) < .0001
    assert rms(multiple, .7, 1.3) > .01
    assert rms(multiple, 1.6, 1.9) > .02


def test_amplified_preview_is_async_cached_and_preserves_position(tmp_path):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QObject, Signal, QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from app.ui.storyboard_audio_preview import AmplifiedVideoPreview

    app = QApplication.instance() or QApplication([])
    clip = tmp_path / 'clip.mp4'
    make_clip(clip)

    class Player(QObject):
        mediaStatusChanged = Signal(object)
        def __init__(self):
            super().__init__()
            self.url = QUrl.fromLocalFile(str(clip))
            self.pos, self.rate, self.playing = 120, 1.25, True
        def source(self): return self.url
        def position(self): return self.pos
        def playbackRate(self): return self.rate
        def playbackState(self): return QMediaPlayer.PlaybackState.PlayingState if self.playing else QMediaPlayer.PlaybackState.PausedState
        def setSource(self, url):
            self.url, self.pos, self.rate, self.playing = url, 0, 1, False
            self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)
        def setPosition(self, value): self.pos = value
        def setPlaybackRate(self, value): self.rate = value
        def play(self): self.playing = True
        def pause(self): self.playing = False

    player = Player()
    output = QAudioOutput()
    preview = AmplifiedVideoPreview(player, output)
    errors = []
    preview.failed.connect(errors.append)
    preview.apply(clip, 4, FFMPEG, tmp_path / 'cache')
    assert player.source().toLocalFile() == clip.as_posix()  # no blocking encode
    for _ in range(500):
        QTest.qWait(10)
        if player.source().toLocalFile() != clip.as_posix() or errors:
            break
    assert not errors
    proxy = Path(player.source().toLocalFile())
    assert proxy != clip and proxy.is_file()
    assert player.pos == 120 and player.rate == 1.25 and player.playing
    assert rms(samples(proxy), .1, .4) / rms(samples(clip), .1, .4) == pytest.approx(4, abs=.1)
    preview.apply(clip, .5, FFMPEG, tmp_path / 'cache')
    assert player.source().toLocalFile() == clip.as_posix()
    assert output.volume() == pytest.approx(.5)
    preview.apply(clip, 4, FFMPEG, tmp_path / 'cache')
    QTest.qWait(300)
    assert Path(player.source().toLocalFile()) == proxy
    assert preview._process is None
    preview.reset()
    preview.deleteLater()
