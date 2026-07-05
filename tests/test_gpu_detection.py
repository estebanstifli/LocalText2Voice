from __future__ import annotations

import subprocess
import unittest

from app.utils.gpu_detection import detect_gpus, format_gpu_detection


class GPUDetectionTests(unittest.TestCase):
    def test_nvidia_smi_query_reports_model_memory_driver_and_compute(self) -> None:
        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if "--query-gpu=index,name,memory.total,driver_version,compute_cap" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "0, NVIDIA GeForce RTX 3070, 8192, 571.96, 8.6\n",
                    "",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                "NVIDIA-SMI 571.96    Driver Version: 571.96    CUDA Version: 12.8",
                "",
            )

        result = detect_gpus(runner, "nvidia-smi")
        summary = format_gpu_detection(result)

        self.assertTrue(result.has_nvidia_gpu)
        self.assertEqual(result.gpus[0].name, "NVIDIA GeForce RTX 3070")
        self.assertEqual(result.gpus[0].memory_total_mb, 8192)
        self.assertEqual(result.gpus[0].driver_version, "571.96")
        self.assertEqual(result.gpus[0].compute_capability, "8.6")
        self.assertEqual(result.cuda_driver_version, "12.8")
        self.assertIn("8.0 GB VRAM", summary)
        self.assertIn("compute 8.6", summary)

    def test_windows_fallback_can_report_non_nvidia_gpu(self) -> None:
        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command,
                0,
                (
                    '[{"Name":"Intel UHD Graphics",'
                    '"AdapterRAM":1073741824,'
                    '"DriverVersion":"31.0"}]'
                ),
                "",
            )

        result = detect_gpus(runner, "")
        summary = format_gpu_detection(result)

        self.assertFalse(result.has_nvidia_gpu)
        self.assertIn("Intel UHD Graphics", summary)
        self.assertIn("no NVIDIA CUDA GPU", summary)


if __name__ == "__main__":
    unittest.main()
