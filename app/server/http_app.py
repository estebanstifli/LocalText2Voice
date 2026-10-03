from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app import __version__
from app.core.audio_formats import audio_format_from_path
from app.core.audiobook_store import AudiobookStore
from app.core.settings_manager import SettingsManager
from app.server.job_manager import LocalServerJobManager
from app.server.job_source_editor import JobSourceEditor
from app.server.engine_host_config import internal_engine_host_url
from app.server.ltv_service import LocalText2VoiceService, public_settings_snapshot


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON object expected.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object expected.")
    return payload


def create_http_app(
    settings_manager: SettingsManager | None = None,
    job_manager: LocalServerJobManager | None = None,
    shutdown_callback: Callable[[], None] | None = None,
    audiobook_store: AudiobookStore | None = None,
) -> FastAPI:
    settings_manager = settings_manager or SettingsManager()
    service = LocalText2VoiceService(settings_manager, keep_engines_alive=True)
    server_settings = _server_settings(settings_manager)
    base_url = _base_url_from_settings(settings_manager.settings)
    manager = job_manager or LocalServerJobManager(
        service,
        max_parallel_jobs=int(server_settings.get("max_parallel_jobs", 1) or 1),
    )
    source_editor = JobSourceEditor(manager, audiobook_store)
    from app.server.storyboard_service import StoryboardService
    storyboard = StoryboardService(settings_manager, audiobook_store)
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            storyboard.close()
            manager.shutdown()
            service.close()

    app = FastAPI(
        title="LocalText2Voice Local Server",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.job_manager = manager
    app.state.service = service

    @app.middleware("http")
    async def token_guard(request: Request, call_next):
        path = request.url.path.rstrip("/")
        if path not in {"", "/health"} and not _authorized(request, settings_manager):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "name": "LocalText2Voice",
            "mcp_transport": "stdio",
            "storyboard_active_jobs": len(storyboard.events),
        }

    @app.post("/shutdown")
    def shutdown_server() -> dict[str, Any]:
        if storyboard.events:
            raise HTTPException(status_code=409, detail="Storyboard jobs are active. Cancel them explicitly before stopping the shared host.")
        if shutdown_callback is None:
            raise HTTPException(
                status_code=409,
                detail="This server is managed by its parent process.",
            )
        shutdown_callback()
        return {"status": "shutting_down"}

    @app.get("/info")
    def info() -> dict[str, Any]:
        return {**service.server_info(), "settings": public_settings_snapshot(settings_manager.settings), "mcp_transport": "stdio"}

    @app.get("/engines")
    def http_list_engines() -> list[dict[str, Any]]:
        return service.list_engines()

    @app.get("/engines/memory")
    def http_engine_memory() -> dict[str, dict[str, Any]]:
        return service.engine_status()

    @app.post("/engines/{engine_id}/preload")
    async def preload_engine(engine_id: str, request: Request) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            payload.update(body)
        payload["engine_id"] = engine_id
        try:
            return await run_in_threadpool(service.preload_engine, engine_id, payload)
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/engines/{engine_id}/unload")
    def unload_engine(engine_id: str) -> dict[str, Any]:
        return service.unload_engine(engine_id)

    @app.post("/review/segments/synthesize")
    async def synthesize_review_segment(request: Request) -> dict[str, Any]:
        payload = await _json_object(request)
        voice_config = payload.get("voice_config")
        if not isinstance(voice_config, dict):
            raise HTTPException(status_code=400, detail="voice_config must be an object.")
        try:
            output = service.synthesize_segment(
                str(payload.get("text", "")),
                str(payload.get("output_wav", "")),
                voice_config,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"output_wav": str(output)}

    @app.get("/voices")
    def http_list_voices(
        engine_id: str | None = None,
        installed_only: bool = True,
    ) -> list[dict[str, Any]]:
        return service.list_voices(engine_id, installed_only=installed_only)

    @app.get("/background-music")
    def http_list_background_music() -> list[dict[str, Any]]:
        return service.list_background_music()

    @app.get("/sfx")
    def http_list_sfx() -> list[dict[str, Any]]:
        return service.list_sfx()

    @app.post("/jobs")
    async def create_job(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON object expected.")
        job = manager.submit(payload)
        return _job_response(job, settings_manager, base_url=base_url)

    @app.get("/jobs")
    def list_jobs(status: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        return [
            _job_response(
                job,
                settings_manager,
                base_url=base_url,
                include_logs=False,
            )
            for job in manager.list_jobs(status=status, limit=limit)
        ]

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        job = manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return _job_response(job, settings_manager, base_url=base_url)

    @app.get("/jobs/{job_id}/source")
    def read_job_source_http(
        job_id: str,
        page: int = 1,
        page_size_chars: int = 12000,
        page_count: int = 1,
        read_all: bool = False,
    ) -> dict[str, Any]:
        try:
            return source_editor.read(
                job_id,
                page=page,
                page_size_chars=page_size_chars,
                page_count=page_count,
                read_all=read_all,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/jobs/{job_id}/source")
    async def write_job_source_http(job_id: str, request: Request) -> dict[str, Any]:
        payload = await _json_object(request)
        if "text" not in payload or not isinstance(payload["text"], str):
            raise HTTPException(status_code=400, detail="text must be a string.")
        try:
            return source_editor.write(
                job_id,
                payload["text"],
                payload.get("expected_sha256"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/jobs/{job_id}/source/search")
    async def search_job_source_http(job_id: str, request: Request) -> dict[str, Any]:
        payload = await _json_object(request)
        try:
            return source_editor.search(
                job_id,
                str(payload.get("query", "")),
                regex=bool(payload.get("regex", False)),
                case_sensitive=bool(payload.get("case_sensitive", False)),
                result_offset=int(payload.get("result_offset", 0)),
                max_results=int(payload.get("max_results", 25)),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/jobs/{job_id}/source/edit")
    async def edit_job_source_http(job_id: str, request: Request) -> dict[str, Any]:
        payload = await _json_object(request)
        try:
            return source_editor.edit(
                job_id,
                str(payload.get("operation", "")),
                int(payload.get("start_offset", 0)),
                end_offset=(
                    None
                    if payload.get("end_offset") is None
                    else int(payload["end_offset"])
                ),
                text=str(payload.get("text", "")),
                expected_sha256=payload.get("expected_sha256"),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/jobs/{job_id}/source/replace")
    async def replace_job_source_http(job_id: str, request: Request) -> dict[str, Any]:
        payload = await _json_object(request)
        try:
            return source_editor.replace_text(
                job_id,
                str(payload.get("search", "")),
                str(payload.get("replacement", "")),
                replace_all=bool(payload.get("replace_all", False)),
                occurrence=int(payload.get("occurrence", 1)),
                regex=bool(payload.get("regex", False)),
                case_sensitive=bool(payload.get("case_sensitive", True)),
                expected_sha256=payload.get("expected_sha256"),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> dict[str, Any]:
        job = manager.cancel_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return _job_response(job, settings_manager, base_url=base_url)

    @app.get("/files/jobs/{job_id}/{kind}")
    def job_file(job_id: str, kind: str) -> FileResponse:
        job = manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        mix_kinds = {"mix", "mix.audio", "mix.mp3"}
        clean_kinds = {
            "clean",
            "clean.audio",
            "clean.mp3",
            "voice",
            "voice.audio",
            "voice.mp3",
        }
        path_text = job.mix_audio_path if kind in mix_kinds else job.clean_audio_path
        if kind not in mix_kinds | clean_kinds:
            raise HTTPException(status_code=404, detail="Unknown file kind.")
        path = Path(path_text)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="File is not ready.")
        format_spec = audio_format_from_path(path)
        return FileResponse(
            path,
            media_type=format_spec.mime_type if format_spec is not None else None,
            filename=path.name,
        )

    @app.post("/storyboard/{operation}")
    def storyboard_operation(operation: str, payload: dict[str, Any]) -> Any:
        try:
            return storyboard.call(operation, payload)
        except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    app.state.storyboard_service = storyboard
    return app


def _server_settings(settings_manager: SettingsManager) -> dict[str, Any]:
    value = settings_manager.settings.get("local_server", {})
    return dict(value) if isinstance(value, dict) else {}


def _authorized(request: Request, settings_manager: SettingsManager) -> bool:
    token = str(_server_settings(settings_manager).get("auth_token", "") or "").strip()
    if not token:
        return True
    header = request.headers.get("Authorization", "")
    if header.casefold().startswith("bearer "):
        if header.split(" ", 1)[1].strip() == token:
            return True
    return request.query_params.get("token", "") == token


def _base_url_from_settings(settings: dict[str, Any]) -> str:
    return internal_engine_host_url(settings)


def _job_response(
    job,
    settings_manager: SettingsManager,
    base_url: str | None = None,
    include_logs: bool = True,
) -> dict[str, Any]:
    payload = job.to_dict(include_logs=include_logs)
    token = str(_server_settings(settings_manager).get("auth_token", "") or "").strip()
    suffix = f"?token={token}" if token else ""
    base = base_url or _base_url_from_settings(settings_manager.settings)
    if payload.get("clean_audio_path"):
        payload["clean_audio_url"] = f"{base}/files/jobs/{job.job_id}/clean{suffix}"
        payload["clean_mp3_url"] = f"{base}/files/jobs/{job.job_id}/clean{suffix}"
    if payload.get("mix_audio_path"):
        payload["mix_audio_url"] = f"{base}/files/jobs/{job.job_id}/mix{suffix}"
        payload["mix_mp3_url"] = f"{base}/files/jobs/{job.job_id}/mix{suffix}"
    result = payload.get("result", {})
    if isinstance(result, dict):
        project = result.get("project")
        if isinstance(project, dict) and project:
            payload["project"] = project
        project_message = str(result.get("project_edit_message", "") or "")
        if project_message:
            payload["message"] = project_message
    return payload
