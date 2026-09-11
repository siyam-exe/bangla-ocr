from __future__ import annotations

import csv
import io
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from ..layout import lines_to_text
from ..models import OCRCandidate, OCRLine
from .base import OCREngine


class TesseractEngine(OCREngine):
    name = "tesseract"

    def available(self) -> tuple[bool, str]:
        executable = shutil.which("tesseract")
        if not executable:
            return False, "tesseract executable was not found"
        result = subprocess.run(
            [executable, "--list-langs"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if "ben" not in result.stdout.split():
            return False, "tesseract is installed but Bengali data (ben) is missing"
        return True, f"Tesseract Bengali is available at {executable}"

    def recognize(
        self,
        image: Image.Image,
        variant: str,
        *,
        embedded_text: str = "",
    ) -> OCRCandidate:
        executable = shutil.which("tesseract")
        if not executable:
            raise RuntimeError("tesseract executable is unavailable")
        with tempfile.TemporaryDirectory(prefix="bangla-ocr-tesseract-") as directory:
            image_path = Path(directory) / "page.png"
            image.save(image_path, format="PNG")
            command = [
                executable,
                str(image_path),
                "stdout",
                "-l",
                str(self.config.get("tesseract_languages", "ben+eng")),
                "--psm",
                str(self.config.get("tesseract_psm", 6)),
                "tsv",
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Tesseract failed")

        lines: list[OCRLine] = []
        raw_rows: list[dict[str, str]] = []
        for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
            raw_rows.append(dict(row))
            if row.get("level") != "5" or not row.get("text", "").strip():
                continue
            confidence = float(row["conf"]) / 100.0
            left = float(row["left"])
            top = float(row["top"])
            width = float(row["width"])
            height = float(row["height"])
            lines.append(
                OCRLine(
                    text=row["text"],
                    bbox=[left, top, left + width, top + height],
                    confidence=max(0.0, confidence),
                )
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
            raw=raw_rows,
        )
