from __future__ import annotations

from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader


class PDFSource:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self._document = pdfium.PdfDocument(str(self.path))
        self.page_count = len(self._document)
        self._reader: PdfReader | None = None

    def close(self) -> None:
        self._document.close()

    def __enter__(self) -> "PDFSource":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def render_page(self, page_index: int, dpi: int) -> Image.Image:
        page = self._document[page_index]
        bitmap = page.render(scale=dpi / 72.0, rev_byteorder=True)
        image = bitmap.to_pil().convert("RGB")
        bitmap.close()
        page.close()
        return image

    def embedded_text(self, page_index: int) -> str:
        if self._reader is None:
            self._reader = PdfReader(str(self.path), strict=False)
        try:
            return self._reader.pages[page_index].extract_text() or ""
        except Exception:
            return ""
