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
                    "cancel_job",
                }.issubset(names)
                resources = await session.list_resources()
                resource_uris = {str(resource.uri) for resource in resources.resources}
                assert "localtext2voice://docs/markup" in resource_uris
                assert "localtext2voice://docs/markup/examples" in resource_uris
                assert "localtext2voice://docs/engines" in resource_uris

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
