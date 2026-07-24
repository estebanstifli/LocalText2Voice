from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tts.python_runtime_manager import PythonRuntimeManager


class PythonRuntimeManagerTests(unittest.TestCase):
    def test_enable_site_packages_uncomments_import_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            python_dir = Path(temporary)
            pth = python_dir / "python311._pth"
            pth.write_text(
                "python311.zip\n.\n# Uncomment to run site.main() automatically\n#import site\n",
                encoding="utf-8",
            )

            PythonRuntimeManager._enable_site_packages(python_dir)

            self.assertIn("import site", pth.read_text(encoding="utf-8").splitlines())

    def test_is_installed_requires_manifest_python_and_pip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = PythonRuntimeManager(Path(temporary))
            manager.python_exe.parent.mkdir(parents=True)
            manager.python_exe.write_text("", encoding="utf-8")
            manager.pip_module_path.mkdir(parents=True)
            manager.manifest_path.write_text(
                json.dumps(
                    {
                        "state": "installed",
                        "runtime_version": manager.runtime_version,
                    }
                ),
                encoding="utf-8",
            )

            self.assertTrue(manager.is_installed())

    def test_non_windows_install_uses_venv_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("app.tts.python_runtime_manager.sys.platform", "linux"):
                manager = PythonRuntimeManager(Path(temporary))

            def simulate_process(command, _cancel_token, cwd, **_kwargs) -> str:
                if command[1:3] == ["-m", "venv"]:
                    staging = Path(command[3])
                    (staging / "bin").mkdir(parents=True)
                    (staging / "bin" / "python").write_text("", encoding="utf-8")
                    (
                        staging
                        / "lib"
                        / "python3.12"
                        / "site-packages"
                        / "pip"
                    ).mkdir(parents=True)
                return ""

            with patch.object(manager, "_run_process", side_effect=simulate_process):
                manager.install()

            self.assertEqual(manager.python_exe, manager.python_dir / "bin" / "python")
            self.assertTrue(manager.is_installed())


if __name__ == "__main__":
    unittest.main()
