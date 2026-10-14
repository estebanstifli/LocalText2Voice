import json
import os
import wave
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from app.core.audiobook_store import AudiobookStore
from app.core.audio_pipeline import AudioGroup, AudioPipeline, AudioGenerationOptions
from app.core.text_processor import TextChunk
from app.tts.indextts_config import DEFAULTS as INDEX_DEFAULTS
from app.ui.segment_detail_dialog import SegmentDetailDialog
from app.workers.verification_worker import EngineHostSegmentRegenerationWorker


@pytest.fixture
def application():
    return QApplication.instance() or QApplication([])


def tr(key, default, **kwargs):
    return default.format(**kwargs)


def test_index_edit_persists_snapshot_and_worker_uses_it(application, tmp_path):
    config = {**deepcopy(INDEX_DEFAULTS), "engine": "indextts", "reference_audio_path": "old.wav",
              "emotion_mode": "vector", "emotion_source": "custom", "emotion_prompt": "Happy",
              "emotion_revision": "old", "emo_vector": [.6, 0, 0, 0, 0, 0, 0, 0]}
    store = AudiobookStore(tmp_path / "books.db")
    book = store.create_audiobook("Hello.", config, tmp_path / "exports", "safe_chunks", "single", "Book", {}, tmp_path / "project")
    mapping = store.replace_segments(book, [AudioGroup("Chapter", (TextChunk("Hello.", True), TextChunk("Untouched.", True)))])
    segment_id = mapping[(1, 1)]
    old_audio = tmp_path / "original.wav"
    old_audio.write_bytes(b"original audio")
    store.mark_segment_rendering(segment_id, config)
    store.mark_segment_rendered(segment_id, old_audio, 100, 10)
    other = store.get_segment(mapping[(1, 2)])
    dialog = SegmentDetailDialog("Hello.", config, [("New voice", {"reference_audio_path": "new.wav", "reference_text": "New transcript"})], tr)
    assert dialog.fields["emo_text"].text() == "Happy"
    dialog.text_edit.setPlainText("Edited line.")
    dialog.voice_combo.setCurrentIndex(1)
    dialog.fields["emo_text"].setText("Sad and calm")
    dialog.fields["emo_alpha"].setValue(.35)
    text, edited = dialog.values()
    assert edited["emotion_mode"] == "text"
    assert "emotion_revision" not in edited
    store.update_segment_details(segment_id, text, edited)
    restored = AudiobookStore(tmp_path / "books.db").get_segment(segment_id)
    assert restored.status == "edited"
    assert restored.wav_path == str(old_audio) and old_audio.read_bytes() == b"original audio"
    assert store.get_segment(other.id) == other
    assert json.loads(store.get_audiobook(book.id).engine_config_json) == config
    snapshot = json.loads(restored.engine_config_json)
    assert snapshot["reference_audio_path"] == "new.wav"
    assert snapshot["emo_text"] == "Sad and calm"
    assert json.loads(restored.markup_state_json)["emotion"]["emo_alpha"] == .35
    assert "Sad and calm" in (tmp_path / "project" / "project.json").read_text(encoding="utf-8")
    client = Mock()
    def synthesize(payload):
        with wave.open(payload["output_wav"], "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
            wav.writeframes(b"\0\0" * 240)
        return {"output_wav": payload["output_wav"]}
    client.synthesize_review_segment.side_effect = synthesize
    candidate = tmp_path / "candidate.wav"
    worker = EngineHostSegmentRegenerationWorker(client, segment_id, restored.source_text, snapshot, candidate)
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    assert not errors and candidate.is_file()
    payload = client.synthesize_review_segment.call_args.args[0]
    assert payload["text"] == "Edited line."
    assert payload["voice_config"]["emo_alpha"] == .35
    assert old_audio.read_bytes() == b"original audio"
    dialog.deleteLater()


def test_chatterbox_legacy_direction_becomes_editable_and_can_be_removed(application):
    dialog = SegmentDetailDialog("Hello.", {"engine": "chatterbox", "model": "turbo", "chatterbox_emotion": "happy"}, [], tr)
    assert dialog.text_edit.toPlainText() == "[happy] Hello."
    dialog.text_edit.setPlainText("[angry] Hello [laugh].")
    text, config = dialog.values()
    from app.tts.chatterbox_tags import prepare_text
    assert "chatterbox_emotion" not in config
    assert prepare_text(text, config, Mock()) == text
    dialog.text_edit.setPlainText("Hello.")
    assert prepare_text(*dialog.values(), Mock()) == "Hello."
    dialog.deleteLater()


def test_direct_emotion_survives_normalization_and_long_text_chunks(tmp_path):
    from app.core.text_normalization import TextNormalizationStore
    store = TextNormalizationStore(tmp_path / "normalization.db")
    store.add_entry("en", "custom", "happy", "spoken happy")
    options = AudioGenerationOptions(output_dir=tmp_path, voice_config={"engine": "chatterbox", "model": "turbo"},
        ffmpeg_path="ffmpeg", chunk_size=100, text_normalization_enabled=True,
        text_normalization_language="en", text_normalization_db_path=store.db_path)
    groups = AudioPipeline(Mock())._prepare_groups("[happy] " + "This is a long sentence. " * 30, options)
    chunks = [chunk for group in groups for chunk in group.chunks]
    assert len(chunks) > 1
    assert all(chunk.text.startswith("[happy]") for chunk in chunks)
    assert not any("spoken happy" in chunk.text for chunk in chunks)


def test_review_comparison_does_not_expect_chatterbox_tags_to_be_spoken():
    from types import SimpleNamespace
    from app.workers.verification_worker import SegmentVerificationWorker
    worker = SimpleNamespace(
        _voice_config_for_segment=lambda segment: {"engine": "chatterbox"},
        _uses_russian_silero_stress=lambda *args: False,
        comparison_normalizer=None,
    )
    segment = SimpleNamespace(source_text="[happy] Hello [laugh] there.")
    source, transcript, *_ = SegmentVerificationWorker._comparison_values(worker, segment, "Hello there.", [], "en")
    assert "[" not in source and "happy" not in source and "laugh" not in source


def test_invalid_index_values_do_not_accept_and_cancel_preserves_original(application):
    original = {**deepcopy(INDEX_DEFAULTS), "engine": "indextts"}
    dialog = SegmentDetailDialog("Hello.", original, [], tr)
    dialog.fields["emo_alpha"].setValue(1.5)
    with pytest.raises(ValueError):
        dialog.values()
    dialog.reject()
    assert original["emo_alpha"] == INDEX_DEFAULTS["emo_alpha"]
    dialog.deleteLater()


def test_review_button_saves_details_and_regeneration_uses_saved_engine(application, tmp_path, monkeypatch):
    from app.core.settings_manager import SettingsManager
    from app.ui.main_window import MainWindow
    from PySide6.QtWidgets import QDialog
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("LOCALTEXT2VOICE_ASSETS_BASE_DIR", str(tmp_path / "assets"))
    settings = SettingsManager(tmp_path / "config.json")
    monkeypatch.setattr("app.ui.main_window.SettingsManager", lambda: settings)
    monkeypatch.setattr("app.ui.main_window.QTimer.singleShot", lambda *args: None)
    monkeypatch.setattr(MainWindow, "_refresh_wav_cache_stats", lambda *args, **kwargs: None)
    window = MainWindow()
    try:
        store = AudiobookStore(tmp_path / "book.db")
        config = {"engine": "indextts", **deepcopy(INDEX_DEFAULTS)}
        book = store.create_audiobook("Hello", config, tmp_path, "safe_chunks", "single", "Book", {}, tmp_path / "project")
        segment_id = store.replace_segments(book, [AudioGroup("Chapter", (TextChunk("Hello", True),))])[(1, 1)]
        window.audiobook_store = store
        monkeypatch.setattr(window, "_selected_review_segment", lambda: store.get_segment(segment_id))
        monkeypatch.setattr(window, "_refresh_review_page", Mock())
        original_text = "Not yet saved in the review editor"
        window.review_source_detail.setPlainText(original_text)
        changed = {**config, "emotion_mode": "vector", "emo_alpha": .4, "emo_vector": [0, 0, .7, 0, 0, 0, 0, 0]}
        dialog = Mock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.values.return_value = ("Changed in modal", changed)
        factory = Mock(return_value=dialog)
        monkeypatch.setattr("app.ui.segment_detail_dialog.SegmentDetailDialog", factory)
        window.review_segment_detail_button.setEnabled(True)
        window.review_segment_detail_button.click()
        assert factory.call_args.args[0] == original_text
        assert factory.call_args.args[1]["engine"] == "indextts"
        assert json.loads(store.get_segment(segment_id).engine_config_json)["emo_alpha"] == .4
        current_config = Mock(side_effect=AssertionError("Must use segment config, not the active engine"))
        monkeypatch.setattr(window, "_current_voice_config", current_config)
        monkeypatch.setattr(window, "_show_review_regeneration_dialog", Mock())
        monkeypatch.setattr("app.ui.main_window.QThread", Mock())
        worker = Mock()
        worker_factory = Mock(return_value=worker)
        monkeypatch.setattr("app.ui.main_window.EngineHostSegmentRegenerationWorker", worker_factory)
        window._regenerate_review_segment(segment_id)
        assert worker_factory.call_args.args[2] == "Changed in modal"
        assert worker_factory.call_args.args[3] == changed
        current_config.assert_not_called()
        window.segment_regeneration_thread = window.segment_regeneration_worker = None
    finally:
        window.deleteLater()


def test_breeze_markup_normalization_snapshot_review_and_regeneration(application, tmp_path):
    from app.tts.audiocpp_config import BREEZE_MODEL, model_config
    from app.core.text_normalization import TextNormalizationStore
    from app.core.ltv_markup import LTVMarkupCompiler
    normalizer = TextNormalizationStore(tmp_path / "normalization.db")
    normalizer.add_entry("en", "custom", "throat", "WRONG")
    config = {**model_config({"model": BREEZE_MODEL}), "engine": "audiocpp"}
    options = AudioGenerationOptions(output_dir=tmp_path, voice_config=config,
        ffmpeg_path="ffmpeg", chunk_size=100, text_normalization_enabled=True,
        text_normalization_language="en", text_normalization_db_path=normalizer.db_path)
    pipeline = AudioPipeline(Mock())
    source = '{{emotion custom "Speak softly with sadness."}}' + "Hello (clears throat). " * 30
    source += '{{emotion off}}Neutral. {{emotion happy}}Cheerful (laugh).'
    groups = pipeline._prepare_groups(source, options)
    chunks = [c for g in groups for c in g.chunks]
    assert len(chunks) > 3
    for chunk in chunks[:-2]:
        assert '(clears throat)' in chunk.text and 'WRONG' not in chunk.text
        assert '{{' not in chunk.text
        assert pipeline._voice_config_for_chunk(config, chunk)["instruction"] == "Speak softly with sadness."
    assert pipeline._voice_config_for_chunk(config, chunks[-2])["instruction"] == "Speak clearly and naturally."
    assert pipeline._voice_config_for_chunk(config, chunks[-1])["instruction"] == "Speak with a happy and cheerful tone."
    # A shared audio.cpp engine ID must not enable Breeze instructions on Higgs/Fish.
    other = LTVMarkupCompiler.compile('{{emotion happy}}Hello.', 'audiocpp', model='fish_audio_s2_pro_q8_0')
    assert other.ignored_commands
    snapshot = pipeline._voice_config_for_chunk(config, chunks[-1])
    store = AudiobookStore(tmp_path / "books.db")
    book = store.create_audiobook(source, config, tmp_path / "exports", "safe_chunks", "single", "Breeze", {}, tmp_path / "project")
    mapping = store.replace_segments(book, [AudioGroup("Chapter", (chunks[-1],))])
    segment_id = mapping[(1, 1)]
    store.mark_segment_rendering(segment_id, snapshot)
    dialog = SegmentDetailDialog(chunks[-1].text, snapshot, [], tr)
    assert dialog.tabs.count() == 3
    assert dialog.fields["instruction"].toPlainText() == snapshot["instruction"]
    dialog.fields["instruction"].setPlainText("Speak slowly in a whisper.")
    text, edited = dialog.values()
    store.update_segment_details(segment_id, text, edited)
    restored = store.get_segment(segment_id)
    saved = json.loads(restored.engine_config_json)
    assert saved["instruction"] == "Speak slowly in a whisper."
    assert config["instruction"] == "Speak clearly and naturally."
    client = Mock()
    def synthesize(payload):
        with wave.open(payload["output_wav"], "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
            wav.writeframes(b"\0\0" * 240)
        return {"output_wav": payload["output_wav"]}
    client.synthesize_review_segment.side_effect = synthesize
    worker = EngineHostSegmentRegenerationWorker(client, segment_id, restored.source_text, saved, tmp_path / "candidate.wav")
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    assert not errors
    payload = client.synthesize_review_segment.call_args.args[0]
    assert payload["voice_config"]["instruction"] == "Speak slowly in a whisper."
    assert payload["text"] == "Cheerful (laugh)."
    dialog.deleteLater()


def test_breeze_verification_preserves_ordinary_parentheses():
    from types import SimpleNamespace
    from app.workers.verification_worker import SegmentVerificationWorker
    worker = SimpleNamespace(
        _voice_config_for_segment=lambda segment: {"engine": "audiocpp", "model": "breeze_tts_2_q8_0"},
        _uses_russian_silero_stress=lambda *args: False,
        comparison_normalizer=None,
    )
    segment = SimpleNamespace(source_text="Hello (laugh) (clears throat) [笑] (a normal aside).")
    source, *_ = SegmentVerificationWorker._comparison_values(worker, segment, "Hello a normal aside.", [], "en")
    assert '(a normal aside)' in source
    assert '(laugh)' not in source and '(clears throat)' not in source and '[笑]' not in source


def test_breeze_explicit_instruction_overrides_pending_emotion(tmp_path):
    from app.tts.audiocpp_config import BREEZE_MODEL
    pipeline = AudioPipeline(Mock())
    config = {"engine": "audiocpp", "model": BREEZE_MODEL}
    options = AudioGenerationOptions(output_dir=tmp_path, voice_config=config, ffmpeg_path="ffmpeg")
    groups = pipeline._prepare_groups('{{emotion happy}}{{cmd "instruction": "Speak softly."}}Hello.', options)
    assert pipeline._voice_config_for_chunk(config, groups[0].chunks[0])["instruction"] == "Speak softly."


@pytest.mark.parametrize("model_id", ["voxcpm2_q8_0", "voxcpm2_bf16", "voxcpm2_orig"])
def test_voxcpm_markup_chunks_and_review_snapshot(application, tmp_path, model_id):
    from app.tts.audiocpp_config import VOXCPM_MODEL, model_config
    config = {**model_config({"model": model_id, "instruction": "global style"}), "engine": "audiocpp"}
    pipeline = AudioPipeline(Mock())
    options = AudioGenerationOptions(output_dir=tmp_path, voice_config=config, ffmpeg_path="ffmpeg", chunk_size=40)
    source = '{{emotion custom "voz alegre y pausada"}}' + "Una historia muy bonita. " * 10
    source += '{{emotion off}}Neutral. {{emotion happy}}Feliz. {{reset}}Normal.'
    chunks = [c for group in pipeline._prepare_groups(source, options) for c in group.chunks]
    assert len(chunks) > 5
    for chunk in chunks[:-3]:
        assert "{{" not in chunk.text and "voz alegre" not in chunk.text
        assert pipeline._voice_config_for_chunk(config, chunk)["instruction"] == "voz alegre y pausada"
    assert pipeline._voice_config_for_chunk(config, chunks[-3])["instruction"] == ""
    assert pipeline._voice_config_for_chunk(config, chunks[-2])["instruction"] == "happy and cheerful tone"
    assert pipeline._voice_config_for_chunk(config, chunks[-1])["instruction"] == "global style"
    groups = pipeline._prepare_groups('{{emotion happy}}{{cmd "instruction": "softly"}}Hola.', options)
    assert pipeline._voice_config_for_chunk(config, groups[0].chunks[0])["instruction"] == "softly"
    snapshot = pipeline._voice_config_for_chunk(config, chunks[-2])
    dialog = SegmentDetailDialog(chunks[-2].text, snapshot, [], tr)
    assert dialog.tabs.count() == 3
    assert "num_inference_steps" in dialog.fields
    assert not {"temperature", "top_p", "top_k", "depth_temperature"} & dialog.fields.keys()
    dialog.breeze_preset_combo.setCurrentIndex(1)  # Off
    assert dialog.values()[1]["instruction"] == ""
    dialog.fields["instruction"].setPlainText("whispering with sadness")
    dialog.fields["num_inference_steps"].setValue(15)
    text, edited = dialog.values()
    store = AudiobookStore(tmp_path / "books.db")
    book = store.create_audiobook(source, config, tmp_path, "safe_chunks", "single", "VoxCPM", {}, tmp_path / "project")
    segment_id = store.replace_segments(book, [AudioGroup("Chapter", (chunks[-2],))])[(1, 1)]
    store.mark_segment_rendering(segment_id, snapshot)
    store.update_segment_details(segment_id, text, edited)
    segment = store.get_segment(segment_id)
    saved = json.loads(segment.engine_config_json)
    assert segment.source_text == "Feliz."
    assert saved["instruction"] == "whispering with sadness" and saved["num_inference_steps"] == 15
    assert config["instruction"] == "global style"
    dialog.deleteLater()
