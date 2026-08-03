from __future__ import annotations

import statistics

from .models import OCRLine
from .utils import nfc


def _line_height(line: OCRLine) -> float:
    return max(1.0, line.bbox[3] - line.bbox[1])


def lines_to_text(lines: list[OCRLine]) -> str:
    """Reconstruct reading order and paragraphs without rewriting OCR content."""
    usable = [line for line in lines if line.text.strip()]
    if not usable:
        return ""

    # Engines usually return one box per word or line. Group boxes that overlap
    # vertically, then sort within each visual line from left to right.
    usable.sort(key=lambda item: ((item.bbox[1] + item.bbox[3]) / 2, item.bbox[0]))
    visual_lines: list[list[OCRLine]] = []
    for item in usable:
        center_y = (item.bbox[1] + item.bbox[3]) / 2
        placed = False
        for group in reversed(visual_lines[-3:]):
            group_center = statistics.mean(
                (entry.bbox[1] + entry.bbox[3]) / 2 for entry in group
            )
            tolerance = max(
                statistics.mean(_line_height(entry) for entry in group),
                _line_height(item),
            ) * 0.45
            if abs(center_y - group_center) <= tolerance:
                group.append(item)
                placed = True
                break
        if not placed:
            visual_lines.append([item])

    merged: list[OCRLine] = []
    for group in visual_lines:
        group.sort(key=lambda entry: entry.bbox[0])
        text = " ".join(entry.text.strip() for entry in group if entry.text.strip())
        merged.append(
            OCRLine(
                text=nfc(text),
                bbox=[
                    min(entry.bbox[0] for entry in group),
                    min(entry.bbox[1] for entry in group),
                    max(entry.bbox[2] for entry in group),
                    max(entry.bbox[3] for entry in group),
                ],
                confidence=statistics.mean(
                    entry.confidence
                    for entry in group
                    if entry.confidence is not None
                )
                if any(entry.confidence is not None for entry in group)
                else None,
            )
        )

    merged.sort(key=lambda item: (item.bbox[1], item.bbox[0]))
    heights = [_line_height(item) for item in merged]
    median_height = statistics.median(heights) if heights else 1.0
    left_edges = [item.bbox[0] for item in merged]
    normal_left = statistics.median(left_edges) if left_edges else 0.0

    paragraphs: list[list[str]] = []
    current: list[str] = []
    previous: OCRLine | None = None
    for item in merged:
        if previous is not None:
            gap = item.bbox[1] - previous.bbox[3]
            indented = item.bbox[0] - normal_left > median_height * 0.85
            previous_terminal = previous.text.rstrip().endswith(
                ("।", "?", "!", "’", "”", "\"", "'", ":")
            )
            if gap > median_height * 0.75 or (indented and previous_terminal):
                if current:
                    paragraphs.append(current)
                current = []
        current.append(item.text.strip())
        previous = item
    if current:
        paragraphs.append(current)

    return "\n\n".join(" ".join(lines).strip() for lines in paragraphs if lines)
