from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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
                        "runtime_version": manager.RUNTIME_VERSION,
                    }
                ),
                encoding="utf-8",
            )

            self.assertTrue(manager.is_installed())


if __name__ == "__main__":
    unittest.main()
