from __future__ import annotations

import os
import subprocess
import unittest
from unittest.mock import patch

from app.utils.gpu_detection import (
    GPUDetectionResult,
    GPUInfo,
    configure_gpu_device,
    detect_gpus,
    format_gpu_detection,
    format_runtime_cuda_info,
    gpu_detection_for_selected_device,
    gpu_runtime_environment,
    selectable_nvidia_gpus,
    selected_nvidia_gpu,
)


class GPUDetectionTests(unittest.TestCase):
    def test_nvidia_smi_query_reports_model_memory_driver_and_compute(self) -> None:
        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if "--query-gpu=index,uuid,name,memory.total,driver_version,compute_cap" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "0, GPU-3070, NVIDIA GeForce RTX 3070, 8192, 571.96, 8.6\n",
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
        self.assertEqual(result.gpus[0].uuid, "GPU-3070")
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

    def test_explicit_gpu_selection_controls_cuda_child_environment(self) -> None:
        base_environment = {
            "PATH": "runtime-path",
            "CUDA_VISIBLE_DEVICES": "0,1",
        }

        selected = gpu_runtime_environment(base_environment, "1")
        automatic = gpu_runtime_environment(base_environment, "auto")

        self.assertEqual(selected["CUDA_VISIBLE_DEVICES"], "1")
        self.assertEqual(selected["LOCALT2VOICE_GPU_DEVICE"], "1")
        self.assertEqual(automatic["CUDA_VISIBLE_DEVICES"], "0,1")
        self.assertEqual(automatic["LOCALT2VOICE_GPU_DEVICE"], "auto")

    def test_selected_gpu_metadata_uses_physical_nvidia_smi_index(self) -> None:
        first = GPUInfo(
            name="NVIDIA RTX 3070",
            index=0,
            memory_total_mb=8192,
            compute_capability="8.6",
            source="nvidia-smi",
        )
        second = GPUInfo(
            name="NVIDIA RTX 5090",
            index=1,
            uuid="GPU-5090",
            memory_total_mb=32768,
            compute_capability="12.0",
            source="nvidia-smi",
        )
        detection = GPUDetectionResult(
            gpus=[first, second],
            method="nvidia-smi",
        )

        self.assertEqual(selectable_nvidia_gpus(detection), [first, second])
        self.assertEqual(selected_nvidia_gpu(detection, "1"), second)
        self.assertEqual(
            gpu_detection_for_selected_device(detection, "1").gpus,
            [second],
        )
        self.assertIsNone(selected_nvidia_gpu(detection, "7"))
        self.assertFalse(
            gpu_detection_for_selected_device(detection, "7").has_nvidia_gpu
        )
        with patch.dict(os.environ, {}, clear=True):
            configure_gpu_device("1", detection)
            self.assertEqual(
                gpu_runtime_environment()["CUDA_VISIBLE_DEVICES"],
                "GPU-5090",
            )
        with patch.dict(
            os.environ,
            {"CUDA_VISIBLE_DEVICES": "GPU-5090"},
            clear=True,
        ):
            self.assertEqual(selected_nvidia_gpu(detection, "auto"), second)

    def test_runtime_cuda_info_includes_selected_profile_and_fallback(self) -> None:
        summary = format_runtime_cuda_info(
            {
                "torch_version": "2.6.0+cpu",
                "torch_cuda_version": None,
                "cuda_available": False,
                "device_count": 0,
                "runtime_profile": "CPU / PyTorch 2.6",
                "runtime_fallback_reason": "CUDA kernel validation failed",
            },
            engine_name="Qwen3 TTS",
        )

        self.assertIn("Runtime profile: CPU / PyTorch 2.6", summary)
        self.assertIn(
            "Runtime fallback: CUDA kernel validation failed",
            summary,
        )


if __name__ == "__main__":
    unittest.main()
