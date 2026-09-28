from __future__ import annotations

from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_stdio_bridge import _with_file_uris


def test_stdio_bridge_exposes_expected_tools():
    async def run_client() -> None:
        repo_root = Path(__file__).resolve().parents[1]
        server = StdioServerParameters(
            command=str(repo_root / ".venv" / "Scripts" / "python.exe"),
            args=[str(repo_root / "mcp_stdio_bridge.py")],
            cwd=str(repo_root),
        )
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {
                    "server_info",
                    "list_engines",
                    "engine_memory",
                    "preload_engine",
                    "unload_engine",
                    "get_markup_help",
                    "list_voices",
                    "list_background_music",
                    "list_sfx",
                    "create_audiobook",
                    "generate_audio",
                    "get_job",
                    "get_jobs",
                    "read_job_source",
                    "write_job_source",
                    "search_job_source",
                    "edit_job_source",
                    "replace_job_source_text",
                    "cancel_job",
                    "sb_list_styles",
                    "sb_set_style",
                    "sb_get_timed_text",
                    "sb_create_entity",
                    "sb_create_state",
                    "sb_batch_update_scenes",
                    "sb_generate_frames",
                    "sb_generate_videos",
                    "sb_render",
                }.issubset(names)
                resources = await session.list_resources()
                resource_uris = {str(resource.uri) for resource in resources.resources}
                assert "localtext2voice://docs/markup" in resource_uris
                assert "localtext2voice://docs/markup/examples" in resource_uris
                assert "localtext2voice://docs/engines" in resource_uris
                assert "localtext2voice://docs/storyboard" in resource_uris
                assert not any(name.startswith("sb_analy") for name in names)

    anyio.run(run_client)


def test_stdio_bridge_adds_project_file_uris(tmp_path):
    project_dir = tmp_path / "project"
    payload = {
        "project": {
            "project_dir": str(project_dir),
            "manifest_path": str(project_dir / "project.localtext2voice.json"),
        }
    }

    enriched = _with_file_uris(payload)

    assert enriched["project"]["project_dir_uri"].startswith("file:")
    assert enriched["project"]["manifest_file_uri"].startswith("file:")


def test_storyboard_cold_start_stdio_without_ui(tmp_path):
    """Real bridge starts a separate real EngineHost on an isolated port/database."""
    import os
    import socket
    import sys
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    repo = Path(__file__).resolve().parents[1]
    script = f'''
import sys
import mcp_stdio_bridge as bridge
bridge._base_url = lambda: "http://127.0.0.1:{port}"
bridge._engine_host_command = lambda: [sys.executable, "engine_host.py", "--port", "{port}"]
assert "app.ui.main_window" not in sys.modules
try:
    bridge.mcp.run(transport="stdio")
finally:
    if bridge._engine_host_process is not None:
        try:
            bridge._request_json("POST", "/shutdown", {{}})
            bridge._engine_host_process.wait(timeout=10)
        except Exception:
            bridge._engine_host_process.terminate()
            bridge._engine_host_process.wait(timeout=10)
'''
    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-c", script], cwd=str(repo), env={**os.environ, "LOCALAPPDATA": str(tmp_path), "XDG_DATA_HOME": str(tmp_path), "LOCALTEXT2VOICE_ASSETS_BASE_DIR": str(tmp_path / "assets")})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                styles = await session.call_tool("sb_list_styles", {})
                assert not styles.isError, styles
                created = await session.call_tool("sb_create_project", {"title": "Headless smoke", "text": "A test."})
                assert not created.isError, created
                projects = await session.call_tool("sb_list_projects", {"query": "Headless smoke"})
                assert not projects.isError, projects
                assert "Headless smoke" in str(projects)
    anyio.run(run)
