from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from PIL import Image
from rapidfuzz import fuzz

from .models import OCRCandidate, OCRLine
from .utils import nfc


REPEATED_TOKEN_RE = re.compile(r"(?i)\b([\u0980-\u09ffA-Za-z]{2,})\s+\1\b")
REPEATED_GLYPH_RE = re.compile(r"([\u0980-\u09ff])\1{4,}")


@dataclass(frozen=True)
class RegionPlan:
    region_id: str
    line_index: int
    bbox: list[float]
    original_text: str
    reasons: list[str]
    risk_score: float
    metrics: dict[str, float]


def _clean_bbox(bbox: list[float], width: int, height: int) -> list[float] | None:
    if len(bbox) != 4:
        return None
    x0, y0, x1, y1 = (float(value) for value in bbox)
    x0 = max(0.0, min(float(width), x0))
    y0 = max(0.0, min(float(height), y0))
    x1 = max(0.0, min(float(width), x1))
    y1 = max(0.0, min(float(height), y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return [x0, y0, x1, y1]


def _image_metrics(image: Image.Image, bbox: list[float]) -> dict[str, float]:
    x0, y0, x1, y1 = (int(round(value)) for value in bbox)
    gray = np.asarray(image.convert("L"))[y0:y1, x0:x1]
    if gray.size == 0:
        return {"contrast_stddev": 0.0, "sharpness": 0.0}
    return {
        "contrast_stddev": round(float(np.std(gray)), 4),
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 4),
    }


def _hard_text_signals(text: str) -> list[str]:
    signals: list[str] = []
    if "\ufffd" in text:
        signals.append("replacement character")
    if "[অস্পষ্ট]" in text:
        signals.append("explicit unreadable marker")
    if REPEATED_TOKEN_RE.search(text):
        signals.append("repeated token")
    if REPEATED_GLYPH_RE.search(text):
        signals.append("repeated Bengali glyph")
    if any(ord(character) < 32 and character not in "\n\t\r" for character in text):
        signals.append("control character")
    return signals


def select_regions(
    candidate: OCRCandidate,
    image: Image.Image,
    config: dict[str, Any],
    *,
    page_needs_review: bool = False,
) -> list[RegionPlan]:
    """Select bounded OCR blocks using image/text evidence only.

    This intentionally contains no vocabulary, spelling, or language-model logic.
    """
    if not config.get("enabled", True) or candidate.engine != "surya":
        return []
    width, height = image.size
    minimum_characters = max(1, int(config.get("minimum_text_characters", 8)))
    small_ratio = float(config.get("small_region_height_ratio", 0.045))
    confidence_limit = float(config.get("minimum_region_confidence", 0.94))
    contrast_limit = float(config.get("minimum_region_contrast_stddev", 24.0))
    sharpness_limit = float(config.get("minimum_region_sharpness", 35.0))
    values: list[RegionPlan] = []

    for line_index, line in enumerate(candidate.lines):
        text = nfc(str(line.text).strip())
        if len(text) < minimum_characters:
            continue
        bbox = _clean_bbox(list(line.bbox), width, height)
        if bbox is None:
            continue
        region_height_ratio = (bbox[3] - bbox[1]) / max(1, height)
        metrics = _image_metrics(image, bbox)
        hard_signals = _hard_text_signals(text)
        reasons = list(hard_signals)
        risk_score = 0.0
        if hard_signals:
            risk_score += 100.0 + (10.0 * len(hard_signals))
        if line.confidence is not None and line.confidence < confidence_limit:
            reasons.append(f"low Surya confidence ({line.confidence:.3f})")
            risk_score += 70.0 + (confidence_limit - line.confidence) * 100
        if region_height_ratio <= small_ratio:
            reasons.append(
                f"small text region ({region_height_ratio * 100:.1f}% of page height)"
            )
            risk_score += 45.0 + (small_ratio - region_height_ratio) * 100
        if metrics["contrast_stddev"] < contrast_limit:
            reasons.append(
                f"weak local contrast ({metrics['contrast_stddev']:.1f})"
            )
            risk_score += 35.0
        if metrics["sharpness"] < sharpness_limit:
            reasons.append(f"weak local sharpness ({metrics['sharpness']:.1f})")
            risk_score += 35.0
        if page_needs_review and not reasons:
            reasons.append("page-level OCR checks requested closer inspection")
            risk_score += 10.0
        if not reasons:
            continue
        digest = hashlib.sha256(
            f"{line_index}\0{bbox}\0{text}".encode("utf-8")
        ).hexdigest()[:12]
        values.append(
            RegionPlan(
                region_id=f"r{line_index + 1:02d}-{digest}",
                line_index=line_index,
                bbox=bbox,
                original_text=text,
                reasons=reasons,
                risk_score=round(risk_score, 4),
                metrics={
                    **metrics,
                    "height_ratio": round(region_height_ratio, 6),
                    "surya_confidence": (
                        round(float(line.confidence), 6)
                        if line.confidence is not None
                        else -1.0
                    ),
                },
            )
        )

    maximum = max(0, int(config.get("maximum_regions_per_page", 3)))
    return sorted(values, key=lambda value: (-value.risk_score, value.line_index))[
        :maximum
    ]


def map_and_pad_bbox(
    bbox: list[float],
    source_size: tuple[int, int],
    target_size: tuple[int, int],
    padding_ratio: float,
) -> list[int]:
    source_width, source_height = source_size
    target_width, target_height = target_size
    sx = target_width / max(1, source_width)
    sy = target_height / max(1, source_height)
    x0, y0, x1, y1 = (
        bbox[0] * sx,
        bbox[1] * sy,
        bbox[2] * sx,
        bbox[3] * sy,
    )
    pad_x = max(8.0, (x1 - x0) * padding_ratio)
    pad_y = max(6.0, (y1 - y0) * min(padding_ratio, 0.06))
    return [
        max(0, int(round(x0 - pad_x))),
        max(0, int(round(y0 - pad_y))),
        min(target_width, int(round(x1 + pad_x))),
        min(target_height, int(round(y1 + pad_y))),
    ]


def map_bbox_to_original(
    bbox: list[float],
    selected_size: tuple[int, int],
    original_size: tuple[int, int],
    operations: list[dict[str, Any]],
) -> list[float]:
    """Map an OCR-input bbox back to the unchanged low-resolution render."""
    original_width, original_height = original_size
    points = np.array(
        [
            [bbox[0], bbox[1]],
            [bbox[2], bbox[1]],
            [bbox[2], bbox[3]],
            [bbox[0], bbox[3]],
        ],
        dtype=np.float64,
    )
    geometric = [
        operation
        for operation in operations
        if operation.get("name") in {"conservative_crop", "deskew"}
    ]
    if not geometric:
        sx = original_width / max(1, selected_size[0])
        sy = original_height / max(1, selected_size[1])
        points[:, 0] *= sx
        points[:, 1] *= sy
    else:
        for operation in reversed(geometric):
            name = operation.get("name")
            if name == "conservative_crop":
                crop_bbox = operation.get("bbox", [0, 0, 0, 0])
                points[:, 0] += float(crop_bbox[0])
                points[:, 1] += float(crop_bbox[1])
            elif name == "deskew":
                angle = float(operation.get("angle_degrees", 0.0))
                center = (original_width / 2.0, original_height / 2.0)
                matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
                cosine = abs(matrix[0, 0])
                sine = abs(matrix[0, 1])
                rotated_width = int(
                    (original_height * sine) + (original_width * cosine)
                )
                rotated_height = int(
                    (original_height * cosine) + (original_width * sine)
                )
                matrix[0, 2] += rotated_width / 2 - center[0]
                matrix[1, 2] += rotated_height / 2 - center[1]
                inverse = cv2.invertAffineTransform(matrix)
                homogeneous = np.column_stack((points, np.ones(len(points))))
                points = homogeneous @ inverse.T

    x0 = max(0.0, float(np.min(points[:, 0])))
    y0 = max(0.0, float(np.min(points[:, 1])))
    x1 = min(float(original_width), float(np.max(points[:, 0])))
    y1 = min(float(original_height), float(np.max(points[:, 1])))
    return [x0, y0, x1, y1]


def best_crop_reading(candidate: OCRCandidate, original_text: str) -> str:
    readings = [line.text.strip() for line in candidate.lines if line.text.strip()]
    if candidate.text.strip():
        readings.append(candidate.text.strip())
    if not readings:
        return ""
    original = _comparison_text(original_text)
    return max(
        readings,
        key=lambda value: (
            fuzz.ratio(original, _comparison_text(value)),
            -abs(len(value) - len(original_text)),
        ),
    )


def _comparison_text(text: str) -> str:
    return re.sub(r"\s+", " ", nfc(text)).strip()


def compare_readings(
    original_text: str,
    crop_text: str,
    minimum_disagreement: float,
) -> dict[str, Any]:
    original = _comparison_text(original_text)
    alternative = _comparison_text(crop_text)
    similarity = fuzz.ratio(original, alternative) / 100.0 if alternative else 0.0
    disagreement = 1.0 - similarity
    differs = bool(alternative) and original != alternative
    return {
        "similarity": round(similarity, 6),
        "disagreement": round(disagreement, 6),
        "has_reviewable_alternative": (
            differs and disagreement >= float(minimum_disagreement)
        ),
    }
