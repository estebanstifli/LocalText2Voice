"""Phase-local progress, with bounded notification traffic and honest ETA scope."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class GenerationProgress:
    def __init__(
        self,
        legacy: Callable[[int, int, str], None],
        detailed: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.legacy = legacy
        self.detailed = detailed
        self.clock = clock
        self.stage = ""
        self.started = 0.0
        self.last_emit = float("-inf")

    def emit(
        self,
        stage: str,
        current: int,
        total: int,
        message: str,
        *,
        unit: str = "segments",
        force: bool = False,
    ) -> None:
        now = self.clock()
        if stage != self.stage:
            self.stage, self.started = stage, now
            force = True
        current, total = max(0, current), max(0, total)
        if total:
            current = min(current, total)
        if not force and current != total and now - self.last_emit < 0.25:
            return
        self.last_emit = now
        elapsed = now - self.started
        eta = None
        if total and current and elapsed >= 1.0:
            eta = max(0, round(elapsed / current * (total - current)))
        payload = {
            "stage": stage,
            "current": current,
            "total": total,
            "message": message,
            "unit": unit,
            "stage_elapsed_seconds": elapsed,
            "stage_eta_seconds": eta,
            "overall_eta_seconds": None,
            "percent": round(current / total * 100, 2) if total else None,
        }
        if self.detailed is not None:
            self.detailed(payload)
        else:
            self.legacy(current, total, message)
