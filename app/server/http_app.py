from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.fastmcp import FastMCP

from app.core.settings_manager import SettingsManager
from app.server.job_manager import LocalServerJobManager, wait_for_job
from app.server.ltv_service import LocalText2VoiceService, public_settings_snapshot
from app.utils.paths import application_root


def _read_text_resource(relative_path: str) -> str:
    path = application_root() / relative_path
    if not path.is_file():
        return (
            f"# Missing resource\n\nThe LocalText2Voice resource was not found: "
            f"`{relative_path}`."
        )
    return path.read_text(encoding="utf-8", errors="replace")


def create_http_app(
    settings_manager: SettingsManager | None = None,
    job_manager: LocalServerJobManager | None = None,
) -> FastAPI:
    settings_manager = settings_manager or SettingsManager()
    service = LocalText2VoiceService(settings_manager, keep_engines_alive=True)
    server_settings = _server_settings(settings_manager)
    base_url = _base_url_from_settings(server_settings)
    mcp_url = f"{base_url}/mcp"
    manager = job_manager or LocalServerJobManager(
        service,
        max_parallel_jobs=int(server_settings.get("max_parallel_jobs", 1) or 1),
    )
    mcp = FastMCP(
        "LocalText2Voice",
        instructions=(
            "Create audiobooks and podcast mixes using the local "
            "LocalText2Voice desktop engine."
        ),
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(description="Return server status, enabled engines, and public settings.")
    def server_info() -> dict[str, Any]:
        return {
            **service.server_info(),
            "settings": public_settings_snapshot(settings_manager.settings),
            "mcp_endpoint": mcp_url,
        }

    @mcp.tool(description="List available TTS engines.")
    def list_engines() -> list[dict[str, Any]]:
        return service.list_engines()

    @mcp.tool(description="List voices for the selected or requested TTS engine.")
    def list_voices(
        engine_id: str | None = None,
        installed_only: bool = True,
    ) -> list[dict[str, Any]]:
        return service.list_voices(engine_id, installed_only=installed_only)

    @mcp.tool(description="List background music tracks available for podcast mixes.")
    def list_background_music() -> list[dict[str, Any]]:
        return service.list_background_music()

    @mcp.resource("localtext2voice://docs/markup")
    def markup_documentation() -> str:
        """Return the LocalText2Voice markup manual."""
        return _read_text_resource("docs/LTV_MARKUP.md")

    @mcp.resource("localtext2voice://docs/markup/examples")
    def markup_examples() -> str:
        """Return concise LocalText2Voice markup examples."""
        manual = _read_text_resource("docs/LTV_MARKUP.md")
        marker = "## Examples"
        index = manual.find(marker)
        if index >= 0:
            return manual[index:]
        return manual

    @mcp.resource("localtext2voice://docs/engines")
    def engine_documentation() -> str:
        """Return a compact guide for engine-aware audiobook generation."""
        return (
            "# LocalText2Voice Engines\n\n"
            "Use `list_engines` to see installed engines and `list_voices` to see "
            "compatible voices for an engine. Use `engine_memory` to check which "
            "engines are loaded in the persistent host, and `preload_engine` before "
            "long jobs when using heavy local engines such as Qwen3 TTS, OmniVoice, "
            "or Chatterbox.\n\n"
            "For advanced text control, read `localtext2voice://docs/markup` before "
            "calling `create_audiobook`."
        )

    @mcp.tool(
        description=(
            "Return the LocalText2Voice markup manual. Use this before composing "
            "advanced audiobook scripts with voice, language, pause, speed, volume, "
            "and model parameter commands."
        )
    )
    def get_markup_help(section: str | None = None) -> str:
        manual = _read_text_resource("docs/LTV_MARKUP.md")
        selected = str(section or "").strip().casefold()
        if selected in {"", "all", "manual"}:
            return manual
        heading = f"## {selected}"
        lower_manual = manual.casefold()
        index = lower_manual.find(heading)
        if index < 0:
            return manual
        next_index = lower_manual.find("\n## ", index + 1)
        return manual[index:] if next_index < 0 else manual[index:next_index]

    @mcp.tool(
        description=(
            "Create a complete audiobook job from text or LocalText2Voice markup. "
            "For advanced scripts, first read the localtext2voice://docs/markup "
            "resource or call get_markup_help. Returns immediately with a job id "
            "unless wait_until_complete is true."
        )
    )
    def create_audiobook(
        text: str,
        title: str = "Audiobook",
        engine_id: str | None = None,
        voice: str | None = None,
        language: str | None = None,
        background_music: str | None = None,
        mix_policy: str = "always",
        export_mode: str | None = None,
        split_mode: str | None = None,
        review_policy: str = "default",
        wait_until_complete: bool = False,
        wait_timeout_seconds: int = 0,
    ) -> dict[str, Any]:
        request = {
            "text": text,
            "title": title,
            "engine_id": engine_id,
            "voice": voice,
            "language": language,
            "background_music": background_music,
            "mix_policy": mix_policy,
            "export_mode": export_mode,
            "split_mode": split_mode,
            "review_policy": review_policy,
        }
        job = manager.submit({key: value for key, value in request.items() if value is not None})
        if wait_until_complete:
            job = wait_for_job(
                manager,
                job.job_id,
                timeout_seconds=max(1, int(wait_timeout_seconds or 3600)),
            ) or job
        return _job_response(job, settings_manager, base_url=base_url)

    @mcp.tool(description="Alias of create_audiobook for simpler clients.")
    def generate_audio(
        text: str,
        title: str = "Audiobook",
        engine_id: str | None = None,
        voice: str | None = None,
        language: str | None = None,
        background_music: str | None = None,
        mix_policy: str = "always",
        review_policy: str = "default",
    ) -> dict[str, Any]:
        return create_audiobook(
            text=text,
            title=title,
            engine_id=engine_id,
            voice=voice,
            language=language,
            background_music=background_music,
            mix_policy=mix_policy,
            review_policy=review_policy,
        )

    @mcp.tool(description="Get one generation job by id.")
    def get_job(job_id: str) -> dict[str, Any]:
        job = manager.get_job(job_id)
        if job is None:
            return {"error": f"Job not found: {job_id}"}
        return _job_response(job, settings_manager, base_url=base_url)

    @mcp.tool(description="List recent generation jobs.")
    def get_jobs(status: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        return [
            _job_response(
                job,
                settings_manager,
                base_url=base_url,
                include_logs=False,
            )
            for job in manager.list_jobs(status=status, limit=limit)
        ]

    @mcp.tool(description="Cancel a queued or running generation job.")
    def cancel_job(job_id: str) -> dict[str, Any]:
        job = manager.cancel_job(job_id)
        if job is None:
            return {"error": f"Job not found: {job_id}"}
        return _job_response(job, settings_manager, base_url=base_url)

    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                manager.shutdown()
                service.close()

    app = FastAPI(
        title="LocalText2Voice Local Server",
        version="1.0.0",
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
            "mcp_endpoint": mcp_url,
        }

    @app.get("/info")
    def info() -> dict[str, Any]:
        return server_info()

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
        return service.preload_engine(engine_id, payload)

    @app.post("/engines/{engine_id}/unload")
    def unload_engine(engine_id: str) -> dict[str, Any]:
        return service.unload_engine(engine_id)

    @app.get("/voices")
    def http_list_voices(
        engine_id: str | None = None,
        installed_only: bool = True,
    ) -> list[dict[str, Any]]:
        return service.list_voices(engine_id, installed_only=installed_only)

    @app.get("/background-music")
    def http_list_background_music() -> list[dict[str, Any]]:
        return service.list_background_music()

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
        path_text = job.mix_mp3_path if kind in {"mix", "mix.mp3"} else job.clean_mp3_path
        if kind not in {"mix", "mix.mp3", "clean", "clean.mp3", "voice", "voice.mp3"}:
            raise HTTPException(status_code=404, detail="Unknown file kind.")
        path = Path(path_text)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="File is not ready.")
        return FileResponse(path, media_type="audio/mpeg", filename=path.name)

    app.mount("/mcp", mcp_app)
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
    host = str(settings.get("host", "127.0.0.1") or "127.0.0.1")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{int(settings.get('port', 8765) or 8765)}"


def _mcp_url(settings_manager: SettingsManager) -> str:
    return f"{_base_url_from_settings(_server_settings(settings_manager))}/mcp"


def _job_response(
    job,
    settings_manager: SettingsManager,
    base_url: str | None = None,
    include_logs: bool = True,
) -> dict[str, Any]:
    payload = job.to_dict(include_logs=include_logs)
    token = str(_server_settings(settings_manager).get("auth_token", "") or "").strip()
    suffix = f"?token={token}" if token else ""
    base = base_url or _base_url_from_settings(_server_settings(settings_manager))
    if payload.get("clean_mp3_path"):
        payload["clean_mp3_url"] = f"{base}/files/jobs/{job.job_id}/clean{suffix}"
    if payload.get("mix_mp3_path"):
        payload["mix_mp3_url"] = f"{base}/files/jobs/{job.job_id}/mix{suffix}"
    return payload
