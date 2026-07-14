from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.settings_manager import SettingsManager
from app.server.http_app import create_http_app
from app.server.job_manager import LocalServerJobManager, wait_for_job
from app.server.ltv_service import LocalText2VoiceService


class FakeGenerationService:
    def server_info(self):
        return {"name": "LocalText2Voice", "engines": []}

    def list_engines(self):
        return []

    def list_voices(self, engine_id=None, installed_only=True):
        return []

    def list_background_music(self):
        return []

    def generate_audio(self, request, progress_callback=None, log_callback=None, on_pipeline=None):
        if log_callback is not None:
            log_callback("fake generation started")
        if progress_callback is not None:
            progress_callback(1, 1, "done")
        return {
            "audiobook_id": 123,
            "outputs": [str(Path("output") / "podcast1.mp3")],
            "clean_mp3": str(Path("output") / "podcast1.mp3"),
            "mix_mp3": "",
        }


def _settings(tmp_path: Path) -> SettingsManager:
    manager = SettingsManager(tmp_path / "config.json")
    manager.settings["local_server"] = {
        "enabled": False,
        "auto_start": False,
        "host": "127.0.0.1",
        "port": 8765,
        "auth_token": "secret-token",
        "allow_lan": False,
        "serve_files": True,
        "max_parallel_jobs": 1,
    }
    return manager


def test_http_server_protects_non_health_endpoints(tmp_path):
    settings = _settings(tmp_path)
    manager = LocalServerJobManager(
        service=FakeGenerationService(),
        db_path=tmp_path / "jobs.sqlite3",
    )
    app = create_http_app(settings, job_manager=manager)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/info").status_code == 401
        response = client.get(
            "/info",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "LocalText2Voice"


def test_job_manager_runs_generation_job(tmp_path):
    manager = LocalServerJobManager(
        service=FakeGenerationService(),
        db_path=tmp_path / "jobs.sqlite3",
    )
    try:
        job = manager.submit({"text": "Hello world.", "title": "Tiny test"})
        finished = wait_for_job(manager, job.job_id, timeout_seconds=5)
        assert finished is not None
        assert finished.status == "complete"
        payload = finished.to_dict()
        assert payload["audiobook_id"] == 123
        assert payload["progress"]["percent"] == 100.0
        assert "fake generation started" in "\n".join(payload["logs"])
    finally:
        manager.shutdown()


def test_service_reloads_ui_settings_and_keeps_project_id(tmp_path):
    settings = SettingsManager(tmp_path / "config.json")
    settings.settings["tts_engine"] = "piper"
    settings.settings["speed"] = 1.1
    settings.settings["output_dir"] = str(tmp_path / "output")
    settings.save()
    service = LocalText2VoiceService(settings)

    updated = SettingsManager(settings.path)
    updated.settings["speed"] = 1.35
    updated.settings["paragraph_pause_min_ms"] = 725
    updated.save()

    service.refresh_settings()
    options = service._generation_options(
        {"project_audiobook_id": 42},
        {"engine": "piper", "speed": 1.35},
    )
    assert service.settings["speed"] == 1.35
    assert options.paragraph_pause_min_ms == 725
    assert options.project_audiobook_id == 42


def test_external_play_forces_whisper_review_and_postproduction_mix(tmp_path):
    settings = SettingsManager(tmp_path / "config.json")
    settings.settings["output_dir"] = str(tmp_path / "output")
    settings.save()
    service = LocalText2VoiceService(settings)
    service.faster_whisper_manager.is_installed = MagicMock(return_value=True)
    clean = tmp_path / "output" / "podcast1.mp3"
    mixed = tmp_path / "output" / "podcast1_mix.mp3"
    pipeline = MagicMock()
    pipeline.generate.return_value = [clean]
    pipeline._active_audiobook = SimpleNamespace(id=7)

    with (
        patch("app.server.ltv_service.AudioPipeline", return_value=pipeline),
        patch.object(service, "_get_tts_engine", return_value=MagicMock()),
        patch.object(
            service,
            "_run_automatic_review",
            return_value=({"enabled": True}, clean),
        ) as review,
        patch.object(service, "_render_reviewed_mix", return_value=mixed) as render,
    ):
        result = service.generate_audio(
            {
                "text": 'Hola {{play "door.mp3" volume=-6db}} mundo.',
                "review_policy": "off",
                "mix_policy": "clean_only",
            }
        )

    review.assert_called_once()
    render.assert_called_once()
    assert result["clean_mp3"] == str(clean)
    assert result["mix_mp3"] == str(mixed)
