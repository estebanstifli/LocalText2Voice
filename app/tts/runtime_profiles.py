from __future__ import annotations

from dataclasses import dataclass

from app.utils.gpu_detection import GPUDetectionResult, GPUInfo


def parse_compute_capability(value: str) -> tuple[int, int] | None:
    parts = value.strip().split(".", 1)
    if not parts or not parts[0].isdigit():
        return None
    minor = parts[1] if len(parts) > 1 else "0"
    if not minor.isdigit():
        return None
    return int(parts[0]), int(minor)


@dataclass(frozen=True)
class TorchRuntimeProfile:
    """Pinned PyTorch environment selected for a hardware generation."""

    profile_id: str
    display_name: str
    backend: str
    torch_version: str
    torch_index_url: str
    cuda_build: str = ""
    priority: int = 0
    minimum_compute_capability: tuple[int, int] | None = None
    maximum_compute_capability: tuple[int, int] | None = None
    gpu_name_markers: tuple[str, ...] = ()

    def supports_gpu(self, gpu: GPUInfo) -> bool:
        if self.backend != "cuda" or not gpu.is_nvidia:
            return False
        capability = parse_compute_capability(gpu.compute_capability)
        if capability is not None and (
            self.minimum_compute_capability is not None
            or self.maximum_compute_capability is not None
        ):
            meets_minimum = (
                self.minimum_compute_capability is None
                or capability >= self.minimum_compute_capability
            )
            meets_maximum = (
                self.maximum_compute_capability is None
                or capability <= self.maximum_compute_capability
            )
            if meets_minimum and meets_maximum:
                return True
        normalized_name = gpu.name.casefold()
        if capability is None and any(
            marker.casefold() in normalized_name
            for marker in self.gpu_name_markers
        ):
            return True
        return (
            self.minimum_compute_capability is None
            and self.maximum_compute_capability is None
            and not self.gpu_name_markers
        )

    def manifest_data(self) -> dict[str, object]:
        return {
            "id": self.profile_id,
            "display_name": self.display_name,
            "backend": self.backend,
            "torch_version": self.torch_version,
            "torch_index_url": self.torch_index_url,
            "cuda_build": self.cuda_build,
            "minimum_compute_capability": self.minimum_compute_capability,
            "maximum_compute_capability": self.maximum_compute_capability,
        }


def select_torch_runtime_profile(
    gpu_detection: GPUDetectionResult,
    cpu_profile: TorchRuntimeProfile,
    cuda_profiles: tuple[TorchRuntimeProfile, ...],
) -> TorchRuntimeProfile:
    """Select the highest-priority CUDA match, or the pinned CPU fallback."""

    nvidia_gpus = [gpu for gpu in gpu_detection.gpus if gpu.is_nvidia]
    if not nvidia_gpus:
        return cpu_profile
    for profile in sorted(
        cuda_profiles,
        key=lambda candidate: candidate.priority,
        reverse=True,
    ):
        if any(profile.supports_gpu(gpu) for gpu in nvidia_gpus):
            return profile
    return cpu_profile
