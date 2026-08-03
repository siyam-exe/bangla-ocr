from __future__ import annotations

from PIL import Image

from ..models import OCRCandidate
from ..utils import nfc, text_counts
from .base import OCREngine


class EmbeddedTextEngine(OCREngine):
    name = "embedded"
    supports_preprocessed_variant = False

    def available(self) -> tuple[bool, str]:
        return True, "PDF embedded-text extraction is available"

    def recognize(
        self,
        image: Image.Image,
        variant: str,
        *,
        embedded_text: str = "",
    ) -> OCRCandidate:
        text = nfc(embedded_text.strip())
        counts = text_counts(text)
        return OCRCandidate(
            engine=self.name,
            variant="pdf",
            text=text,
            confidence=None,
            diagnostics={"character_counts": counts},
            raw={"text": text},
        )
