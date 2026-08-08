from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np
from PIL import Image


@dataclass
class ImageMetrics:
    width: int
    height: int
    grayscale_stddev: float
    sharpness_laplacian_variance: float
    foreground_ratio: float
    estimated_skew_degrees: float
    content_bbox: list[int]
    crop_savings_ratio: float
    border_ink_ratio: float


@dataclass
class PreprocessResult:
    original: Image.Image
    selected: Image.Image
    selected_name: str
    operations: list[dict[str, Any]]
    metrics: ImageMetrics

    def diagnostics(self) -> dict[str, Any]:
        return {
            "selected_name": self.selected_name,
            "operations": self.operations,
            "metrics": asdict(self.metrics),
        }


def _pil_to_bgr(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _bgr_to_pil(image: np.ndarray) -> Image.Image:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def _foreground_mask(gray: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]


def _estimate_skew(mask: np.ndarray) -> float:
    coordinates = np.column_stack(np.where(mask > 0))
    if len(coordinates) < 500:
        return 0.0
    angle = cv2.minAreaRect(coordinates[:, ::-1].astype(np.float32))[-1]
    if angle > 45:
        angle -= 90
    if angle < -45:
        angle += 90
    return float(angle)


def _content_bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    height, width = mask.shape
    kernel_width = max(15, width // 35)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 3))
    joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    coordinates = cv2.findNonZero(joined)
    if coordinates is None:
        return (0, 0, width, height)
    x, y, w, h = cv2.boundingRect(coordinates)
    return (x, y, x + w, y + h)


def _border_ink_ratio(mask: np.ndarray) -> float:
    height, width = mask.shape
    edge_y = max(1, int(height * 0.025))
    edge_x = max(1, int(width * 0.025))
    border = np.concatenate(
        (
            mask[:edge_y, :].ravel(),
            mask[-edge_y:, :].ravel(),
            mask[:, :edge_x].ravel(),
            mask[:, -edge_x:].ravel(),
        )
    )
    return float(np.count_nonzero(border) / max(1, border.size))


def _rotate_bound(image: np.ndarray, angle: float) -> np.ndarray:
    height, width = image.shape[:2]
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos = abs(matrix[0, 0])
    sin = abs(matrix[0, 1])
    new_width = int((height * sin) + (width * cos))
    new_height = int((height * cos) + (width * sin))
    matrix[0, 2] += new_width / 2 - center[0]
    matrix[1, 2] += new_height / 2 - center[1]
    return cv2.warpAffine(
        image,
        matrix,
        (new_width, new_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def preprocess_page(
    image: Image.Image, config: dict[str, Any]
) -> PreprocessResult:
    """Apply only conservative, evidence-based transforms to a page copy."""
    original = image.convert("RGB")
    bgr = _pil_to_bgr(original)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mask = _foreground_mask(gray)
    height, width = gray.shape
    skew = _estimate_skew(mask)
    x0, y0, x1, y1 = _content_bbox(mask)
    content_area = max(1, (x1 - x0) * (y1 - y0))
    crop_savings = 1.0 - (content_area / max(1, width * height))

    metrics = ImageMetrics(
        width=width,
        height=height,
        grayscale_stddev=float(np.std(gray)),
        sharpness_laplacian_variance=float(
            cv2.Laplacian(gray, cv2.CV_64F).var()
        ),
        foreground_ratio=float(np.count_nonzero(mask) / max(1, mask.size)),
        estimated_skew_degrees=skew,
        content_bbox=[x0, y0, x1, y1],
        crop_savings_ratio=float(crop_savings),
        border_ink_ratio=_border_ink_ratio(mask),
    )

    if not config.get("enabled", True):
        return PreprocessResult(original, original.copy(), "original", [], metrics)

    selected = bgr.copy()
    operations: list[dict[str, Any]] = []

    maximum_skew = float(config["maximum_deskew_degrees"])
    minimum_skew = float(config["minimum_deskew_degrees"])
    if minimum_skew <= abs(skew) <= maximum_skew:
        selected = _rotate_bound(selected, skew)
        operations.append(
            {
                "name": "deskew",
                "angle_degrees": round(skew, 4),
                "reason": "small, measurable text-line skew",
            }
        )

    selected_gray = cv2.cvtColor(selected, cv2.COLOR_BGR2GRAY)
    if float(np.std(selected_gray)) < float(config["minimum_contrast_stddev"]):
        lab = cv2.cvtColor(selected, cv2.COLOR_BGR2LAB)
        lightness, channel_a, channel_b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8))
        selected = cv2.cvtColor(
            cv2.merge((clahe.apply(lightness), channel_a, channel_b)),
            cv2.COLOR_LAB2BGR,
        )
        operations.append(
            {
                "name": "local_contrast",
                "reason": "page contrast was below the configured threshold",
            }
        )

    # Crop only when every edge is safe and enough space is removed.
    padding_ratio = float(config["crop_padding_ratio"])
    maximum_edge = float(config["maximum_crop_per_edge_ratio"])
    left_ratio = x0 / width
    top_ratio = y0 / height
    right_ratio = (width - x1) / width
    bottom_ratio = (height - y1) / height
    edges_are_safe = all(
        value <= maximum_edge
        for value in (left_ratio, top_ratio, right_ratio, bottom_ratio)
    )
    enough_savings = crop_savings >= float(config["minimum_crop_savings_ratio"])
    if not operations and edges_are_safe and enough_savings:
        pad_x = int(width * padding_ratio)
        pad_y = int(height * padding_ratio)
        crop_x0 = max(0, x0 - pad_x)
        crop_y0 = max(0, y0 - pad_y)
        crop_x1 = min(width, x1 + pad_x)
        crop_y1 = min(height, y1 + pad_y)
        if crop_x1 > crop_x0 and crop_y1 > crop_y0:
            selected = selected[crop_y0:crop_y1, crop_x0:crop_x1]
            operations.append(
                {
                    "name": "conservative_crop",
                    "bbox": [crop_x0, crop_y0, crop_x1, crop_y1],
                    "reason": "blank border was detected with protected padding",
                }
            )

    selected_name = (
        "+".join(operation["name"] for operation in operations)
        if operations
        else "original"
    )
    selected_image = _bgr_to_pil(selected) if operations else original.copy()
    return PreprocessResult(
        original=original,
        selected=selected_image,
        selected_name=selected_name,
        operations=operations,
        metrics=metrics,
    )
