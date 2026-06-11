from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseLLMProvider(ABC):
    """Contract for optional course-text generation providers."""

    @abstractmethod
    def generate_course_text(self, topic: str, options: dict[str, Any]) -> str:
        raise NotImplementedError
