from __future__ import annotations

import importlib.util
import statistics
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..layout import lines_to_text
from ..models import OCRCandidate, OCRLine
from .base import OCREngine


class EasyOCREngine(OCREngine):
    name = "easyocr"
    _reader: Any = None

    def available(self) -> tuple[bool, str]:
        if importlib.util.find_spec("easyocr") is None:
            return False, "Python package easyocr is not installed"
        return True, "EasyOCR Bengali model is available"

    def _get_reader(self) -> Any:
        if self._reader is None:
            import easyocr

            model_directory = self.working_root / "models" / "easyocr"
            model_directory.mkdir(parents=True, exist_ok=True)
            self._reader = easyocr.Reader(
                ["bn", "en"],
                gpu=bool(self.config.get("easyocr_gpu", True)),
                model_storage_directory=str(model_directory),
                download_enabled=True,
                verbose=False,
            )
        return self._reader

    def recognize(
        self,
        image: Image.Image,
        variant: str,
        *,
        embedded_text: str = "",
    ) -> OCRCandidate:
        reader = self._get_reader()
        raw_results = reader.readtext(
            np.asarray(image.convert("RGB")),
            detail=1,
            paragraph=False,
            batch_size=1,
            workers=0,
            decoder="beamsearch",
            beamWidth=5,
            rotation_info=None,
        )
        lines: list[OCRLine] = []
        serializable_raw: list[dict[str, Any]] = []
        for polygon, text, confidence in raw_results:
            xs = [float(point[0]) for point in polygon]
            ys = [float(point[1]) for point in polygon]
            bbox = [min(xs), min(ys), max(xs), max(ys)]
            line = OCRLine(
                text=str(text),
                bbox=bbox,
                confidence=float(confidence),
            )
            lines.append(line)
            serializable_raw.append(
                {
                    "polygon": [[float(x), float(y)] for x, y in polygon],
                    "text": str(text),
                    "confidence": float(confidence),
                }
            )
        confidence = (
            statistics.mean(
                line.confidence for line in lines if line.confidence is not None
            )
            if lines
            else 0.0
        )
        return OCRCandidate(
            engine=self.name,
            variant=variant,
            text=lines_to_text(lines),
            lines=lines,
            confidence=confidence,
            raw=serializable_raw,
        )
