import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal
from PIL import Image

from app.core import storyboard_conversation as c
from app.core import video_storyboard_planner as p
from app.core.storyboard_token_usage import AnalysisTokenUsage
from app.ui.video_storyboard_analysis_dialog import VideoStoryboardAnalysisDialog
from tests.test_storyboard_conversation import SOURCE, fake_backend


def test_objects_discovered_from_source_reviewed_structured_and_bound(monkeypatch):
    fake_backend(monkeypatch)
    free, structured = p._request_free_text, p._request_plan
    calls = []

    def text(*args, **kwargs):
        if "important objects summary" in kwargs["request_label"]:
            calls.append(kwargs["messages"])
            return "Key: a brass key."
        return free(*args, **kwargs)

    def convert(settings, schema, system, user, **kwargs):
        if schema == c.OBJECT_SCHEMA:
            assert "silver" in user  # The user's edited summary wins.
            return {"objects": [{"object": "Key", "visual_description": "Silver key"}]}
        result = structured(settings, schema, system, user, **kwargs)
        if "intervals" in user:
            assert "objects" in schema["properties"]["scenes"]["items"]["required"]
            for row in result["scenes"]:
                row["objects"] = ["Key", "Unknown prop"]
        return result

    def review(draft):
        assert draft["original"]["objects"] == "Key: a brass key."
        draft["edited"]["objects"] = "Key: a silver key."
        return draft

    monkeypatch.setattr(p, "_request_free_text", text)
    monkeypatch.setattr(p, "_request_plan", convert)
    plan = c.plan_conversation(SOURCE, {"analysis_choices": {
        "plan": "scenes", "objects": True, "review": True}}, review=review)
    assert len(calls) == 1 and SOURCE["text"] in calls[0][0]["content"]
    record = plan["continuity"]["objects"][0]
    assert record["states"][0]["description"] == "Silver key"
    assert all(s["generation_overrides"]["object_state_ids"] == [record["states"][0]["id"]]
               for s in plan["scenes"])


def test_disabled_objects_have_no_registry_or_extra_calls(monkeypatch):
    chats, requests = fake_backend(monkeypatch)
    plan = c.plan_conversation(SOURCE, {})
    assert "objects" not in plan["continuity"]
    assert all(c.OBJECTS not in chat[0]["content"] for chat in chats)
    assert all(schema != c.OBJECT_SCHEMA for schema, _, _ in requests)


def test_object_bindings_survive_page_save_and_restore():
    from app.ui.video_storyboard_page import VideoStoryboardPage
    app = QApplication.instance() or QApplication([])
    tr = lambda key, default, **values: default.format(**values)
    page = VideoStoryboardPage(tr)
    plan = {"continuity": {"characters": [], "locations": [], "objects": [
        {"id": "key", "name": "Key", "states": [{"id": "key_1", "description": "Silver key",
        "from_seconds": 0, "to_seconds": 10}]}]}, "scenes": [
        {"id": "001", "duration": 10, "prompt": "A silver key", "generation_overrides": {"object_state_ids": ["key_1"]}}]}
    page.set_analysis_result(plan)
    saved = page.project_state()
    restored = VideoStoryboardPage(tr)
    restored.restore_project_state(saved)
    assert restored.scenes()[0]["generation_overrides"]["object_state_ids"] == ["key_1"]
    assert restored.project_state()["plan"]["continuity"]["objects"][0]["id"] == "key"
    page.deleteLater()
    restored.deleteLater()
    app.processEvents()


def test_usage_counts_reported_input_output_and_partial_independently():
    usage = AnalysisTokenUsage()
    assert usage.display("input") == "—"
    usage.add({"prompt_eval_count": 200, "eval_count": 30})
    usage.add({"usage": {"prompt_tokens": 100, "completion_tokens": 15}})
    usage.add({"usage": {"input_tokens": 50, "output_tokens": 0}})
    usage.add({"usage": {"input_tokens": 10}})
    assert usage.display("input") == "360"
    assert usage.display("output") == "45 *"
    usage.add({})
    assert usage.display("input") == "360 *"


def test_ollama_stream_retains_usage(monkeypatch):
    import io
    response = io.BytesIO(b'{"message":{"content":"ok"},"done":true,"prompt_eval_count":123,"eval_count":7}\n')
    monkeypatch.setattr(p.urllib.request, "urlopen", lambda *a, **k: response)
    result = p._http_ollama_stream("http://localhost/api/chat", {}, 5, lambda e: None, None)
    assert result["prompt_eval_count"] == 123
    assert result["eval_count"] == 7


def test_reference_queue_filters_reuses_saves_thumbnails_and_cancels(tmp_path, monkeypatch):
    import threading
    app = QApplication.instance() or QApplication([])
    workers = []

    class FakeWorker(QObject):
        finished = Signal()

        def __init__(self, prompt, settings, target, parent):
            super().__init__(parent)
            self.prompt, self.target = prompt, target
            self.cancelled = threading.Event()
            self.error = ""
            workers.append(self)

        def start(self):
            self.target.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.workers.character_image_worker.CharacterImageWorker", FakeWorker)
    existing = tmp_path / "existing.png"
    Image.new("RGB", (128, 72)).save(existing)
    records = [{"id": k, "name": k, "states": [{"id": k + "_1", "description": "red hair, blue coat"}]}
               for k in ("one", "two", "three", "four")]
    records[2]["reference_image_path"] = str(existing)
    plan = {"continuity": {"characters": records}, "scenes": [
        {"characters": ["one_1", "two_1", "three_1", "four_1"]},
        {"characters": ["two_1", "three_1", "four_1"]}]}
    d = VideoStoryboardAnalysisDialog(lambda key, default, **v: default.format(**v))
    assert not d.references_checkbox.isChecked()
    d._enter_running(emit_start=False)
    d.update_plan(plan, final=True)
    d.set_finished(True, "Complete")
    d.configure_references(plan, {}, tmp_path)
    saved = []
    d.referenceReady.connect(saved.append)
    d.start_references()
    assert len(workers) == 1 and "two" in workers[0].prompt
    Image.new("RGB", (128, 72)).save(workers[0].target)
    workers[0].finished.emit()
    assert len(saved) == 1 and saved[0]["id"] == "two"
    assert len(workers) == 2 and "four" in workers[1].prompt
    assert any(d.character_summary.cellWidget(row, 2) for row in range(4))
    d._request_cancel()
    assert workers[1].cancelled.is_set()
    workers[1].finished.emit()
    assert len(saved) == 1 and d._reference_worker is None
    d.close()
    app.processEvents()


def test_dialog_usage_ignores_content_and_validated_duplicate_events():
    app = QApplication.instance() or QApplication([])
    d = VideoStoryboardAnalysisDialog(lambda key, default, **v: default.format(**v))
    d.append_trace({"kind": "request", "provider": "litellm", "model": "test-model"})
    d.append_trace({"kind": "content", "text": "result"})
    d.append_trace({"kind": "raw_response", "raw_response": {"usage": {"input_tokens": 45, "output_tokens": 7}}})
    d.append_trace({"kind": "response", "characters": 6})
    assert d.token_usage.input == 45 and d.token_usage.output == 7
    assert "test-model" in d.engine_label.text()
    assert "45" in d.usage_label.text()
    d.close()
    app.processEvents()

