from __future__ import annotations

import math
import re
from typing import Any

from rapidfuzz.fuzz import ratio

from .models import OCRCandidate, PageDecision
from .utils import text_counts


PUBLICATION_PATTERNS = (
    "প্রকাশক",
    "প্রকাশনী",
    "প্রথম প্রকাশ",
    "মুদ্রণ",
    "স্বত্ব",
    "সর্বস্বত্ব",
    "আইএসবিএন",
    "isbn",
    "মূল্য",
    "প্রচ্ছদ",
)
AD_PATTERNS = (
    "facebook.com",
    "www.",
    ".com",
    "ডাউনলোড",
    "আমারবই",
    "amarboi",
    "বইলাভার",
    "boilovers",
)
STORY_PUNCTUATION = ("।", "‘", "’", "“", "”", "?", "!")


def _candidate_quality(
    candidate: OCRCandidate,
    maximum_length: int,
    config: dict[str, Any],
) -> float:
    counts = text_counts(candidate.text)
    script_total = counts["bengali"] + counts["latin"]
    bengali_ratio = counts["bengali"] / max(1, script_total)
    coverage = min(1.0, counts["non_whitespace"] / max(1, maximum_length))
    confidence = (
        max(0.0, min(1.0, candidate.confidence))
        if candidate.confidence is not None
        else 0.55
    )
    punctuation_count = sum(candidate.text.count(char) for char in STORY_PUNCTUATION)
    punctuation_score = min(1.0, punctuation_count / max(1.0, counts["bengali"] / 120))
    replacement_penalty = min(0.25, counts["replacement"] * 0.03)
    engine_prior = {
        "surya": 0.05,
        "easyocr": 0.025,
        "tesseract": 0.0,
        "embedded": 0.0,
    }.get(candidate.engine, 0.0)
    score = (
        0.38 * bengali_ratio
        + 0.27 * confidence
        + 0.22 * coverage
        + 0.08 * punctuation_score
        + engine_prior
        - replacement_penalty
    )
    candidate.diagnostics.update(
        {
            "character_counts": counts,
            "bengali_ratio": round(bengali_ratio, 5),
            "coverage": round(coverage, 5),
            "punctuation_score": round(punctuation_score, 5),
        }
    )
    return max(0.0, min(1.0, score))


def score_candidates(
    candidates: list[OCRCandidate],
    config: dict[str, Any],
) -> tuple[list[OCRCandidate], float]:
    maximum_length = max(
        (text_counts(candidate.text)["non_whitespace"] for candidate in candidates),
        default=1,
    )
    for candidate in candidates:
        candidate.score = _candidate_quality(candidate, maximum_length, config)
    candidates.sort(key=lambda item: item.score, reverse=True)

    useful = [item for item in candidates if len(item.text.strip()) >= 20]
    if len(useful) < 2:
        disagreement = 0.0
    else:
        comparisons: list[float] = []
        best = useful[0].text
        for item in useful[1:4]:
            comparisons.append(1.0 - ratio(best, item.text) / 100.0)
        disagreement = max(comparisons, default=0.0)
    return candidates, disagreement


def classify_page(
    text: str,
    page_index: int,
    page_count: int,
    config: dict[str, Any],
) -> tuple[str, bool, float, list[str]]:
    lower = text.lower()
    counts = text_counts(text)
    reasons: list[str] = []
    publication_hits = [value for value in PUBLICATION_PATTERNS if value in lower]
    ad_hits = [value for value in AD_PATTERNS if value in lower]
    bengali = counts["bengali"]

    if counts["non_whitespace"] < 35:
        return "blank_or_visual", False, 0.05, ["very little recognizable text"]

    # A dense page of Bengali prose is likely story content. This is deliberately
    # conservative: ambiguous exclusions go to review and are never deleted.
    density_score = min(1.0, math.log1p(bengali) / math.log1p(1200))
    punctuation = sum(text.count(char) for char in STORY_PUNCTUATION)
    prose_score = min(1.0, punctuation / max(2.0, bengali / 100))
    story_score = 0.68 * density_score + 0.32 * prose_score

    if ad_hits:
        story_score -= min(0.7, 0.28 * len(ad_hits))
        reasons.append("advertising or uploader marker: " + ", ".join(ad_hits))
    if publication_hits and page_index < int(config["front_matter_scan_pages"]):
        story_score -= min(0.7, 0.22 * len(publication_hits))
        reasons.append("front-matter marker: " + ", ".join(publication_hits))
    if re.search(r"(?:https?://|www\.|facebook\.com)", lower):
        story_score -= 0.25
    story_score = max(0.0, min(1.0, story_score))

    threshold = float(config["automatic_story_threshold"])
    minimum_bengali = int(config["minimum_story_bengali_characters"])
    if ad_hits and story_score < threshold:
        role = "advertisement_or_uploader_page"
        include = False
    elif (
        publication_hits
        and page_index < int(config["front_matter_scan_pages"])
        and story_score < threshold
    ):
        role = "front_matter"
        include = False
    elif story_score >= threshold and bengali >= minimum_bengali:
        role = "body_text"
        include = True
        reasons.append("dense Bengali prose")
    elif bengali >= minimum_bengali:
        role = "body_text"
        include = True
        reasons.append("Bengali body text present but classification is uncertain")
    else:
        role = "non_story_or_uncertain"
        include = False
        reasons.append("insufficient evidence of story prose")
    return role, include, story_score, reasons


def decide_page(
    candidates: list[OCRCandidate],
    disagreement: float,
    page_index: int,
    page_count: int,
    selection_config: dict[str, Any],
    classification_config: dict[str, Any],
    image_metrics: dict[str, Any] | None = None,
) -> PageDecision:
    if not candidates or not candidates[0].text.strip():
        return PageDecision(
            selected_candidate=None,
            selected_engine=None,
            selected_variant=None,
            status="needs_review",
            include=False,
            page_role="unreadable",
            reasons=["no OCR engine returned usable text"],
            disagreement=disagreement,
            text="",
        )

    selected = candidates[0]
    role, include, story_score, role_reasons = classify_page(
        selected.text,
        page_index,
        page_count,
        classification_config,
    )
    reasons = list(role_reasons)
    status = "draft"
    if selected.score < float(selection_config["minimum_candidate_score"]):
        status = "needs_review"
        reasons.append("selected OCR quality score is low")
    if (
        selected.confidence is not None
        and selected.confidence
        < float(selection_config["minimum_engine_confidence"])
    ):
        status = "needs_review"
        reasons.append("OCR engine confidence is low")
    if disagreement > float(selection_config["maximum_candidate_disagreement"]):
        status = "needs_review"
        reasons.append("OCR engines disagree substantially")
    if image_metrics:
        width = int(image_metrics.get("width", 0) or 0)
        height = int(image_metrics.get("height", 0) or 0)
        minimum_width = int(selection_config.get("minimum_render_width", 0))
        minimum_height = int(selection_config.get("minimum_render_height", 0))
        if (
            (minimum_width and width < minimum_width)
            or (minimum_height and height < minimum_height)
        ):
            status = "needs_review"
            reasons.append(
                "source scan resolution is low "
                f"({width}x{height}; needs careful image comparison)"
            )
        sharpness = float(
            image_metrics.get("sharpness_laplacian_variance", 0.0) or 0.0
        )
        minimum_sharpness = float(
            selection_config.get("minimum_sharpness_laplacian_variance", 0.0)
        )
        if minimum_sharpness and sharpness < minimum_sharpness:
            status = "needs_review"
            reasons.append(
                "source scan appears blurred "
                f"(sharpness score {sharpness:.1f})"
            )
    if role not in {"body_text", "story"}:
        status = "needs_review"
        reasons.append("page inclusion requires confirmation")
    excluded_uploader_blocks = int(
        selected.diagnostics.get("excluded_uploader_blocks", 0) or 0
    )
    if excluded_uploader_blocks:
        reasons.append(
            f"excluded {excluded_uploader_blocks} uploader/watermark text block(s)"
        )
    selected.diagnostics["story_score"] = round(story_score, 5)

    return PageDecision(
        selected_candidate=0,
        selected_engine=selected.engine,
        selected_variant=selected.variant,
        status=status,
        include=include,
        page_role=role,
        reasons=reasons,
        disagreement=round(disagreement, 5),
        text=selected.text,
    )
