from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class GPUInfo:
    name: str
    index: int | None = None
    memory_total_mb: int | None = None
    driver_version: str = ""
    compute_capability: str = ""
    source: str = ""
    is_nvidia: bool = True

    @property
    def memory_total_gb(self) -> float | None:
        if self.memory_total_mb is None:
            return None
        return self.memory_total_mb / 1024


@dataclass(frozen=True)
class GPUDetectionResult:
    gpus: list[GPUInfo] = field(default_factory=list)
    method: str = ""
    nvidia_smi_path: str = ""
    cuda_driver_version: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def has_nvidia_gpu(self) -> bool:
        return any(gpu.is_nvidia for gpu in self.gpus)


def detect_gpus(
    command_runner: CommandRunner | None = None,
    nvidia_smi_path: str | None = None,
) -> GPUDetectionResult:
    runner = command_runner or _run_command
    smi_path = _find_nvidia_smi() if nvidia_smi_path is None else nvidia_smi_path
    warnings: list[str] = []
    if smi_path:
        try:
            gpus = _query_nvidia_smi(smi_path, runner)
            cuda_driver_version = _query_nvidia_smi_cuda_version(smi_path, runner)
            return GPUDetectionResult(
                gpus=gpus,
                method="nvidia-smi",
                nvidia_smi_path=smi_path,
                cuda_driver_version=cuda_driver_version,
            )
        except Exception as exc:
            warnings.append(f"nvidia-smi failed: {exc}")

    windows_result = _query_windows_video_controllers(runner)
    if windows_result.gpus:
        return GPUDetectionResult(
            gpus=windows_result.gpus,
            method=windows_result.method,
            error=windows_result.error,
            warnings=[*warnings, *windows_result.warnings],
        )
    return GPUDetectionResult(
        method=windows_result.method or "none",
        error=windows_result.error or "No NVIDIA GPU was detected.",
        warnings=[*warnings, *windows_result.warnings],
    )


def format_gpu_detection(result: GPUDetectionResult) -> str:
    lines: list[str] = []
    if result.has_nvidia_gpu:
        lines.append("System GPU: NVIDIA CUDA-capable hardware detected.")
        for gpu in result.gpus:
            if not gpu.is_nvidia:
                continue
            parts = [gpu.name]
            if gpu.memory_total_gb is not None:
                parts.append(f"{gpu.memory_total_gb:.1f} GB VRAM")
            if gpu.compute_capability:
                parts.append(f"compute {gpu.compute_capability}")
            if gpu.driver_version:
                parts.append(f"driver {gpu.driver_version}")
            lines.append(f"- {', '.join(parts)}")
        if result.cuda_driver_version:
            lines.append(f"NVIDIA driver CUDA support: {result.cuda_driver_version}")
        if result.method == "nvidia-smi" and result.nvidia_smi_path:
            lines.append(f"Detector: nvidia-smi ({result.nvidia_smi_path})")
    elif result.gpus:
        lines.append("System GPU: no NVIDIA CUDA GPU detected.")
        for gpu in result.gpus:
            parts = [gpu.name]
            if gpu.memory_total_gb is not None:
                parts.append(f"{gpu.memory_total_gb:.1f} GB VRAM")
            if gpu.driver_version:
                parts.append(f"driver {gpu.driver_version}")
            lines.append(f"- {', '.join(parts)}")
    else:
        lines.append("System GPU: no compatible GPU detected.")
        if result.error:
            lines.append(result.error)

    if result.warnings:
        lines.append("Warnings: " + " | ".join(result.warnings))
    return "\n".join(lines)


def format_runtime_cuda_info(
    info: dict[str, object],
    engine_name: str = "Chatterbox",
) -> str:
    if not info:
        return f"Runtime CUDA: not available until the {engine_name} runtime is installed."
    if info.get("error"):
        return f"Runtime CUDA: could not test PyTorch ({info.get('error')})."
    torch_version = str(info.get("torch_version", "unknown"))
    torch_cuda = str(info.get("torch_cuda_version") or "CPU build")
    available = bool(info.get("cuda_available"))
    device_count = int(info.get("device_count") or 0)
    lines = [
        "Runtime CUDA: "
        + ("available" if available else "not available to PyTorch"),
        f"PyTorch: {torch_version}, CUDA build: {torch_cuda}",
    ]
    devices = info.get("devices")
    if isinstance(devices, list) and devices:
        for device in devices:
            if not isinstance(device, dict):
                continue
            name = str(device.get("name", "Unknown GPU"))
            total_memory_gb = device.get("total_memory_gb")
            capability = str(device.get("capability", ""))
            parts = [name]
            if isinstance(total_memory_gb, (int, float)):
                parts.append(f"{total_memory_gb:.1f} GB VRAM")
            if capability:
                parts.append(f"compute {capability}")
            lines.append(f"- {', '.join(parts)}")
    elif device_count == 0:
        lines.append("PyTorch device count: 0")
    return "\n".join(lines)


def _find_nvidia_smi() -> str:
    path = shutil.which("nvidia-smi")
    if path:
        return path
    candidates = [
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "nvidia-smi.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "NVIDIA Corporation"
        / "NVSMI"
        / "nvidia-smi.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def _query_nvidia_smi(path: str, runner: CommandRunner) -> list[GPUInfo]:
    completed = runner(
        [
            path,
            "--query-gpu=index,name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ]
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "nvidia-smi returned an error")
    gpus: list[GPUInfo] = []
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) < 5:
            continue
        index = _parse_int(row[0])
        memory_total_mb = _parse_int(row[2])
        gpus.append(
            GPUInfo(
                index=index,
                name=row[1].strip(),
                memory_total_mb=memory_total_mb,
                driver_version=row[3].strip(),
                compute_capability=row[4].strip(),
                source="nvidia-smi",
                is_nvidia=True,
            )
        )
    return gpus


def _query_nvidia_smi_cuda_version(path: str, runner: CommandRunner) -> str:
    completed = runner([path])
    if completed.returncode != 0:
        return ""
    match = re.search(r"CUDA Version:\s*([0-9.]+)", completed.stdout)
    return match.group(1) if match else ""


def _query_windows_video_controllers(
    runner: CommandRunner,
) -> GPUDetectionResult:
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        (
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name,AdapterRAM,DriverVersion | "
            "ConvertTo-Json -Compress"
        ),
    ]
    try:
        completed = runner(command)
    except Exception as exc:
        return GPUDetectionResult(method="windows-cim", error=str(exc))
    if completed.returncode != 0:
        return GPUDetectionResult(
            method="windows-cim",
            error=completed.stderr.strip(),
        )
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return GPUDetectionResult(method="windows-cim", error=str(exc))
    entries = raw if isinstance(raw, list) else [raw]
    gpus: list[GPUInfo] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("Name", "")).strip()
        if not name:
            continue
        adapter_ram = _parse_int(str(entry.get("AdapterRAM", "")))
        memory_mb = (
            round(adapter_ram / (1024 * 1024))
            if adapter_ram is not None and adapter_ram > 0
            else None
        )
        gpus.append(
            GPUInfo(
                index=index,
                name=name,
                memory_total_mb=memory_mb,
                driver_version=str(entry.get("DriverVersion", "")).strip(),
                source="windows-cim",
                is_nvidia="nvidia" in name.lower(),
            )
        )
    return GPUDetectionResult(gpus=gpus, method="windows-cim")


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
        creationflags=(
            subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        ),
    )


def _parse_int(value: str) -> int | None:
    cleaned = value.strip().replace("MiB", "").replace("MB", "").strip()
    try:
        return int(float(cleaned))
    except (TypeError, ValueError):
        return None
