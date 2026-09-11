from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from PIL import Image

from ..models import OCRCandidate


class OCREngine(ABC):
    name = "base"
    expensive = False
    supports_preprocessed_variant = True
    supports_recovery = False

    def __init__(self, config: dict[str, Any], working_root: Path):
        self.config = config
        self.working_root = working_root

    def retry_reason(self, candidate: OCRCandidate) -> str | None:
        """Return a reason to discard and retry a technically returned result."""
        return None

    def recovery_diagnostics(self) -> dict[str, Any]:
        """Return bounded diagnostics suitable for a page-attempt audit."""
        return {}

    def recover_after_failure(self) -> dict[str, Any]:
        """Reset a failed engine before one bounded retry."""
        raise RuntimeError(f"OCR engine {self.name!r} does not support recovery")

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    @abstractmethod
    def recognize(
        self,
        image: Image.Image,
        variant: str,
        *,
        embedded_text: str = "",
    ) -> OCRCandidate:
        raise NotImplementedError
