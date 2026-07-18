from __future__ import annotations

from pathlib import Path


INSTALLER_SCRIPT = (
    Path(__file__).resolve().parents[1] / "installer" / "LocalText2Voice.iss"
)


def test_uninstaller_offers_optional_ai_data_cleanup() -> None:
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")

    assert "function InitializeUninstall(): Boolean;" in script
    assert '#define UserDataDir "{localappdata}\\LocalText2Voice"' in script
    assert "DownloadedAIDataExists()" in script
    assert "MB_YESNO or MB_DEFBUTTON1" in script
    assert "UninstallSilent()" in script
    assert "downloaded AI data will be preserved" in script
    assert (
        "DirExists(ExpandConstant('{app}\\runtimes\\python311\\engine-deps'))"
        in script
    )


def test_uninstaller_cleanup_preserves_projects_and_exports() -> None:
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")

    for directory in ("models", "runtimes", "voice-gallery"):
        assert f"RemoveDataTree(Root + '\\{directory}');" in script
    assert (
        "ExpandConstant('{app}\\runtimes\\python311\\engine-deps')" in script
    )
    assert "RemoveDataTree(ExpandConstant('{app}\\voices'));" in script
    assert "RemoveDataTree(Root + '\\projects');" not in script
    assert "DelTree(Root, True, True, True)" not in script
    assert "Audiobook projects, exported audio, and settings will be kept." in script


def test_installer_writes_current_settings_schema() -> None:
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")

    assert "'  \"settings_schema_version\": 16,'" in script
