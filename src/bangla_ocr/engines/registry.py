from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import OCREngine
from .easyocr_engine import EasyOCREngine
from .embedded import EmbeddedTextEngine
from .surya_engine import SuryaEngine
from .tesseract_engine import TesseractEngine


ENGINE_TYPES: dict[str, type[OCREngine]] = {
    "surya": SuryaEngine,
    "easyocr": EasyOCREngine,
    "tesseract": TesseractEngine,
    "embedded": EmbeddedTextEngine,
}


class RequiredEngineUnavailableError(RuntimeError):
    """Raised when OCR would otherwise silently use a fallback engine."""


class EngineRegistry:
    def __init__(self, config: dict[str, Any], working_root: Path):
        self.config = config
        self.working_root = working_root
        self._engines = {
            name: engine_type(config, working_root)
            for name, engine_type in ENGINE_TYPES.items()
        }

    def statuses(self) -> dict[str, dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {}
        for name, engine in self._engines.items():
            available, reason = engine.available()
            values[name] = {"available": available, "reason": reason}
        return values

    def required_primary(
        self,
        requested: list[str] | None = None,
    ) -> tuple[str, dict[str, dict[str, Any]]]:
        statuses = self.statuses()
        if requested is None:
            primary_name = str(
                self.config.get("preferred_primary_engine", "surya")
            )
        else:
            primary_name = next(
                (name for name in requested if name != "embedded"),
                "embedded",
            )
        if primary_name not in statuses:
            raise ValueError(f"Unknown OCR engine: {primary_name}")
        primary_status = statuses[primary_name]
        if not primary_status["available"]:
            reason = str(primary_status["reason"])
            raise RequiredEngineUnavailableError(
                f"Required OCR engine '{primary_name}' is unavailable: {reason}. "
                "OCR was not started; automatic fallback is disabled."
            )
        return primary_name, statuses

    def available(
        self,
        requested: list[str] | None = None,
        statuses: dict[str, dict[str, Any]] | None = None,
    ) -> list[OCREngine]:
        if requested is None:
            primary = str(self.config.get("preferred_primary_engine", "surya"))
            order = [primary]
            if primary != "embedded":
                order.append("embedded")
        else:
            order = list(requested)
        order = list(dict.fromkeys(order))
        known_statuses = statuses or self.statuses()
        engines: list[OCREngine] = []
        for name in order:
            if name not in self._engines:
                raise ValueError(f"Unknown OCR engine: {name}")
            engine = self._engines[name]
            if known_statuses.get(name, {}).get("available"):
                engines.append(engine)
        return engines
