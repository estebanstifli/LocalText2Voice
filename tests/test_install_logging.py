from __future__ import annotations

import subprocess
import sys
import unittest

from app.tts.install_logging import (
    communicate_with_live_output,
    detailed_pip_args,
    readable_process_line,
)


class InstallLoggingTests(unittest.TestCase):
    def test_process_output_is_streamed_from_stdout_and_stderr(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-c",
                (
                    "import sys; "
                    "print('Collecting package', flush=True); "
                    "sys.stderr.write('Downloading 10%\\rDownloading 50%\\r'); "
                    "sys.stderr.flush(); "
                    "print('Installed package', flush=True)"
                ),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        events: list[tuple[str, str]] = []

        stdout, stderr = communicate_with_live_output(
            process,
            lambda: None,
            lambda source, line: events.append((source, line)),
        )

        self.assertIn(b"Collecting package", stdout)
        self.assertIn(b"Downloading 50%", stderr)
        self.assertIn(("stdout", "Collecting package"), events)
        self.assertIn(("stderr", "Downloading 10%"), events)
        self.assertIn(("stderr", "Downloading 50%"), events)

    def test_structured_worker_timing_is_readable(self) -> None:
        self.assertEqual(
            readable_process_line(
                '{"type":"timing","label":"model load","elapsed":2.345}'
            ),
            "model load: 2.35 s",
        )

    def test_pip_install_enables_verbose_download_progress(self) -> None:
        args = detailed_pip_args(["install", "torch==2.8.0"])

        self.assertEqual(args[0], "install")
        self.assertIn("--verbose", args)
        self.assertIn("--progress-bar", args)
        self.assertIn("on", args)


if __name__ == "__main__":
    unittest.main()
