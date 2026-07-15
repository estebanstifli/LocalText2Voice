from __future__ import annotations

import math
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from app.core.audio_event_timeline import ResolvedAudioClip, SpeechInterval
from app.core.audio_mix import AudioMixSettings, render_audio_mix
from app.core.waveform_preview import probe_audio_duration


def _write_tone(path: Path, duration_seconds: float, frequency: float) -> None:
    sample_rate = 44100
    samples = array(
        "h",
        (
            round(7000 * math.sin(2 * math.pi * frequency * index / sample_rate))
            for index in range(round(duration_seconds * sample_rate))
        ),
    )
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples.tobytes())


class AudioMixTimelineTests(unittest.TestCase):
    def test_real_ffmpeg_mix_creates_and_reuses_timeline_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            voice = root / "voice.wav"
            effect = root / "effect.wav"
            output = root / "master.wav"
            second_output = root / "master-2.wav"
            trimmed_output = root / "master-trimmed.wav"
            cache = root / "stems"
            _write_tone(voice, 2.0, 220.0)
            _write_tone(effect, 0.5, 880.0)
            clip = ResolvedAudioClip(
                event_uid="audio-event-0001",
                event_id="door",
                track="sfx",
                file_path=str(effect),
                timeline_start_ms=500,
                source_start_ms=100,
                playback_duration_ms=800,
                volume_db=-6.0,
                loop=True,
                fade_in_ms=40,
                fade_out_ms=80,
                pan=0.35,
                duck_db=6.0,
                trim_silence=False,
            )
            settings = AudioMixSettings(
                voice_start_offset_ms=0,
                music_tail_ms=0,
                normalize=False,
                sfx_volume_db=-2.0,
            )
            intervals = (SpeechInterval(350, 750),)

            render_audio_mix(
                voice,
                output,
                "ffmpeg/ffmpeg.exe",
                settings,
                voice_duration_seconds=2.0,
                timeline_clips=(clip,),
                speech_intervals=intervals,
                stem_cache_dir=cache,
            )
            stems = list(cache.glob("sfx_*.wav"))

            self.assertTrue(output.is_file())
            self.assertEqual(len(stems), 1)
            self.assertGreater(stems[0].stat().st_size, 44)
            self.assertTrue((cache / "voice.wav").is_file())
            self.assertTrue((cache / "sfx.wav").is_file())
            self.assertTrue((cache / "master.wav").is_file())
            self.assertEqual(len(list(cache.glob("prepared_*.wav"))), 1)
            self.assertAlmostEqual(
                probe_audio_duration(output, "ffmpeg/ffmpeg.exe"),
                2.0,
                delta=0.08,
            )
            stem_mtime = stems[0].stat().st_mtime_ns

            render_audio_mix(
                voice,
                second_output,
                "ffmpeg/ffmpeg.exe",
                settings,
                voice_duration_seconds=2.0,
                timeline_clips=(clip,),
                speech_intervals=intervals,
                stem_cache_dir=cache,
            )

            self.assertTrue(second_output.is_file())
            self.assertEqual(stems[0].stat().st_mtime_ns, stem_mtime)

            render_audio_mix(
                voice,
                trimmed_output,
                "ffmpeg/ffmpeg.exe",
                AudioMixSettings(
                    voice_start_offset_ms=-500,
                    music_tail_ms=0,
                    normalize=False,
                ),
                voice_duration_seconds=2.0,
                stem_cache_dir=cache,
            )
            self.assertAlmostEqual(
                probe_audio_duration(trimmed_output, "ffmpeg/ffmpeg.exe"),
                1.5,
                delta=0.08,
            )
            self.assertFalse((cache / "sfx.wav").exists())


if __name__ == "__main__":
    unittest.main()
