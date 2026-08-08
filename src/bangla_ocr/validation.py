from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from rapidfuzz import fuzz

from .utils import nfc, read_json, write_json
from .workflow import is_human_verified, normalize_page_state


WORD_RE = re.compile(r"[\u0980-\u09ffA-Za-z]+(?:[-’'][\u0980-\u09ffA-Za-z]+)*")
REPEATED_TOKEN_RE = re.compile(r"(?i)\b([\u0980-\u09ffA-Za-z]{2,})\s+\1\b")
REPEATED_GLYPH_RE = re.compile(r"([\u0980-\u09ff])\1{4,}")
PAGE_ROLES = {
    "body_text",
    "section_heading",
    "story",
    "chapter_heading",
    "table",
    "caption",
    "references",
    "other_content",
    "front_matter",
    "advertisement_or_uploader_page",
    "blank_or_visual",
    "non_story_or_uncertain",
    "unreadable",
}
CONTENT_ROLES = {
    "body_text",
    "section_heading",
    "story",
    "chapter_heading",
    "table",
    "caption",
    "references",
    "other_content",
}
TERMINAL_MARKERS = ("শেষ", "সমাপ্ত", "সমাপ্তি")
UPLOADER_MARKERS = ("bangla book", "direct link", "www.", "facebook.com")
BENGALI_NUMBER_WORDS = {
    "শূন্য": 0,
    "এক": 1,
    "দুই": 2,
    "তিন": 3,
    "চার": 4,
    "পাঁচ": 5,
    "ছয়": 6,
    "ছয়": 6,
    "সাত": 7,
    "আট": 8,
    "নয়": 9,
    "নয়": 9,
    "দশ": 10,
    "এগারো": 11,
    "বারো": 12,
    "তেরো": 13,
    "চৌদ্দ": 14,
    "পনেরো": 15,
    "ষোলো": 16,
    "সতেরো": 17,
    "আঠারো": 18,
    "উনিশ": 19,
    "বিশ": 20,
    "একুশ": 21,
    "বাইশ": 22,
    "তেইশ": 23,
    "চব্বিশ": 24,
    "পঁচিশ": 25,
    "ছাব্বিশ": 26,
    "সাতাশ": 27,
    "আঠাশ": 28,
    "ঊনত্রিশ": 29,
    "ত্রিশ": 30,
}
BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
TERMINAL_PUNCTUATION = tuple("।!?…’”\"')]}»")


def _finding(
    code: str,
    message: str,
    *,
    category: str = "structure",
    related_pages: list[int] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "code": code,
        "category": category,
        "message": message,
    }
    if related_pages:
        value["related_pages"] = related_pages
    if evidence:
        value["evidence"] = evidence
    return value


def _selected_candidate(page: dict[str, Any]) -> dict[str, Any]:
    candidates = page.get("candidates", [])
    if not candidates:
        return {}
    selected = page.get("decision", {}).get("selected_candidate")
    if not isinstance(selected, int) or selected < 0 or selected >= len(candidates):
        selected = 0
    return candidates[selected]


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", nfc(text)).strip()


def _heading_number(text: str) -> int | None:
    value = nfc(text).strip()
    value = re.sub(r"^(?:অধ্যায়|অধ্যায়|পরিচ্ছেদ|পর্ব)\s*[-:–—]?\s*", "", value)
    value = re.sub(r"[\s।:;,.\-–—()\[\]]+", "", value)
    if value in BENGALI_NUMBER_WORDS:
        return BENGALI_NUMBER_WORDS[value]
    translated = value.translate(BENGALI_DIGITS)
    return int(translated) if translated.isdigit() and len(translated) <= 3 else None


def _page_headings(page: dict[str, Any], text: str) -> list[dict[str, Any]]:
    values: list[tuple[str, str]] = []
    manual_heading = str(page.get("manual", {}).get("heading", "")).strip()
    if manual_heading:
        values.append((manual_heading, "manual heading"))
    for line in _selected_candidate(page).get("lines", []):
        label = str(line.get("label", ""))
        line_text = _compact_text(str(line.get("text", "")))
        if label == "SectionHeader" and line_text:
            values.append((line_text, "layout heading"))
    first_lines = [line.strip() for line in text.splitlines() if line.strip()][:5]
    values.extend((line, "page opening") for line in first_lines)
    headings: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for value, source in values:
        number = _heading_number(value)
        if number is None:
            continue
        key = (_compact_text(value), number)
        if key in seen:
            continue
        seen.add(key)
        headings.append({"text": value, "number": number, "source": source})
    return headings


def _layout_heading_findings(page: dict[str, Any], text: str) -> list[dict[str, Any]]:
    normalized_text = re.sub(r"[^\u0980-\u09ffA-Za-z0-9]", "", text).casefold()
    findings: list[dict[str, Any]] = []
    for line in _selected_candidate(page).get("lines", []):
        if str(line.get("label", "")) != "SectionHeader":
            continue
        heading = _compact_text(str(line.get("text", "")))
        compact = re.sub(r"[^\u0980-\u09ffA-Za-z0-9]", "", heading).casefold()
        if (
            not compact
            or len(compact) > 100
            or any(marker in heading.casefold() for marker in UPLOADER_MARKERS)
        ):
            continue
        if compact not in normalized_text:
            findings.append(
                _finding(
                    "layout_heading_missing_from_text",
                    f"A visible layout heading is absent from the reviewed text: {heading!r}.",
                    evidence={"heading": heading, "bbox": line.get("bbox")},
                )
            )
    return findings


def _candidate_geometry_findings(
    page_root: Path,
    page: dict[str, Any],
    text: str,
    include: bool,
    page_role: str,
) -> list[dict[str, Any]]:
    if not include or page_role not in CONTENT_ROLES:
        return []
    candidate = _selected_candidate(page)
    lines = [line for line in candidate.get("lines", []) if line.get("bbox")]
    metrics = page.get("preprocessing", {}).get("metrics", {})
    width = float(metrics.get("width") or 0)
    height = float(metrics.get("height") or 0)
    findings: list[dict[str, Any]] = []
    border_ink = float(metrics.get("border_ink_ratio") or 0)
    content_bbox = metrics.get("content_bbox") or []
    if width and height and len(content_bbox) == 4 and border_ink >= 0.015:
        x0, y0, x1, y1 = (float(value) for value in content_bbox)
        if x0 <= width * 0.008 or x1 >= width * 0.992:
            findings.append(
                _finding(
                    "possible_side_crop",
                    "Visible ink reaches a side edge; verify that no first/last characters were cropped.",
                    evidence={"content_bbox": content_bbox, "border_ink_ratio": border_ink},
                )
            )
        if y0 <= height * 0.008 or y1 >= height * 0.992:
            findings.append(
                _finding(
                    "possible_top_bottom_crop",
                    "Visible ink reaches the top or bottom edge; verify the first and last printed lines.",
                    evidence={"content_bbox": content_bbox, "border_ink_ratio": border_ink},
                )
            )
    if width and height and lines:
        touching = [
            line
            for line in lines
            if float(line["bbox"][1]) <= height * 0.008
            or float(line["bbox"][3]) >= height * 0.992
        ]
        if touching:
            findings.append(
                _finding(
                    "ocr_region_touches_page_edge",
                    "An OCR text region touches the page edge; inspect it for clipped glyphs.",
                    evidence={"regions": [line.get("bbox") for line in touching[:5]]},
                )
            )
    raw_blocks = int(candidate.get("diagnostics", {}).get("raw_block_count", 0) or 0)
    excluded = int(
        candidate.get("diagnostics", {}).get("excluded_uploader_blocks", 0) or 0
    )
    if raw_blocks and raw_blocks > len(lines) + excluded + 2:
        findings.append(
            _finding(
                "ocr_block_coverage_gap",
                "The scan produced more layout blocks than reader-text regions; verify omitted captions, headings, or final lines.",
                evidence={
                    "raw_blocks": raw_blocks,
                    "reader_text_regions": len(lines),
                    "excluded_uploader_blocks": excluded,
                },
            )
        )
    compact_length = len(re.sub(r"\s+", "", text))
    foreground_ratio = float(metrics.get("foreground_ratio") or 0)
    if foreground_ratio >= 0.04 and compact_length < 80:
        findings.append(
            _finding(
                "suspiciously_sparse_ocr",
                "The page contains substantial visible ink but very little OCR text.",
                evidence={
                    "foreground_ratio": round(foreground_ratio, 4),
                    "text_characters": compact_length,
                },
            )
        )
    confidence = candidate.get("confidence")
    sharpness = float(metrics.get("sharpness_laplacian_variance") or 0)
    contrast = float(metrics.get("grayscale_stddev") or 0)
    if (
        confidence is not None
        and float(confidence) >= 0.9
        and metrics
        and (sharpness < 45 or contrast < 28)
    ):
        findings.append(
            _finding(
                "high_confidence_degraded_scan",
                "OCR confidence is high despite weak scan quality; confidence may be misleading.",
                evidence={
                    "confidence": float(confidence),
                    "sharpness": round(sharpness, 2),
                    "contrast": round(contrast, 2),
                },
            )
        )
    if width and len(lines) >= 4:
        left = [line for line in lines if float(line["bbox"][2]) < width * 0.52]
        right = [line for line in lines if float(line["bbox"][0]) > width * 0.48]
        overlaps = sum(
            not (
                float(a["bbox"][3]) < float(b["bbox"][1])
                or float(b["bbox"][3]) < float(a["bbox"][1])
            )
            for a in left
            for b in right
        )
        if len(left) >= 2 and len(right) >= 2 and overlaps >= 2:
            findings.append(
                _finding(
                    "possible_multi_column_reading_order",
                    "Text regions form overlapping left and right columns; verify reading order.",
                    evidence={"left_regions": len(left), "right_regions": len(right)},
                )
            )
    findings.extend(_visual_coverage_findings(page_root, page, lines))
    return findings


def _visual_coverage_findings(
    page_root: Path,
    page: dict[str, Any],
    lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    image_name = str(page.get("selected_image") or page.get("source_image") or "")
    image_path = page_root / image_name
    if not image_name or not image_path.exists() or not lines:
        return []
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return []
    height, width = image.shape
    mask = cv2.threshold(
        cv2.GaussianBlur(image, (3, 3), 0),
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )[1]
    edge_y = max(1, int(height * 0.02))
    edge_x = max(1, int(width * 0.02))
    mask[:edge_y, :] = 0
    mask[-edge_y:, :] = 0
    mask[:, :edge_x] = 0
    mask[:, -edge_x:] = 0
    foreground = int(np.count_nonzero(mask))
    if foreground < 100:
        return []
    covered = np.zeros_like(mask)
    bottom = 0
    for line in lines:
        try:
            x0, y0, x1, y1 = (int(float(value)) for value in line["bbox"])
        except (TypeError, ValueError):
            continue
        pad = max(3, int(height * 0.004))
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(width, x1 + pad), min(height, y1 + pad)
        covered[y0:y1, x0:x1] = 255
        bottom = max(bottom, y1)
    covered_ink = int(np.count_nonzero(cv2.bitwise_and(mask, covered)))
    uncovered_ratio = 1.0 - covered_ink / foreground
    findings: list[dict[str, Any]] = []
    if uncovered_ratio >= 0.5:
        findings.append(
            _finding(
                "large_unmapped_ink_region",
                "A large share of visible page ink is outside OCR text regions; inspect illustrations, captions, or omitted text.",
                evidence={"unmapped_ink_ratio": round(uncovered_ratio, 3)},
            )
        )
    if bottom and bottom < height * 0.94:
        lower_ink = int(np.count_nonzero(mask[min(height, bottom) :, :]))
        if lower_ink / foreground >= 0.025:
            findings.append(
                _finding(
                    "visible_content_below_last_ocr_region",
                    "Visible content exists below the final OCR region; verify the last line or end marker.",
                    evidence={
                        "lower_unmapped_ink_ratio": round(lower_ink / foreground, 3),
                        "last_ocr_bottom": bottom,
                        "image_height": height,
                    },
                )
            )
    return findings


def _token_overlap(previous: str, current: str, limit: int = 80) -> int:
    previous_tokens = WORD_RE.findall(previous)[-limit:]
    current_tokens = WORD_RE.findall(current)[:limit]
    maximum = min(len(previous_tokens), len(current_tokens))
    for size in range(maximum, 7, -1):
        if previous_tokens[-size:] == current_tokens[:size]:
            return size
    return 0


def text_anomalies(text: str) -> list[dict[str, Any]]:
    value = nfc(text)
    anomalies: list[dict[str, Any]] = []
    if "\ufffd" in value:
        anomalies.append(
            {"code": "replacement_character", "message": "Contains �."}
        )
    for match in REPEATED_TOKEN_RE.finditer(value):
        anomalies.append(
            {
                "code": "repeated_token",
                "message": f"Repeated token: {match.group(0)!r}",
                "start": match.start(),
                "end": match.end(),
            }
        )
    for match in REPEATED_GLYPH_RE.finditer(value):
        anomalies.append(
            {
                "code": "repeated_glyph",
                "message": f"Suspicious repeated glyphs: {match.group(0)!r}",
                "start": match.start(),
                "end": match.end(),
            }
        )
    quote_pairs = (("‘", "’"), ("“", "”"))
    for opening, closing in quote_pairs:
        if value.count(opening) != value.count(closing):
            anomalies.append(
                {
                    "code": "unbalanced_quotes",
                    "message": (
                        f"Unbalanced {opening}{closing} quotes "
                        f"({value.count(opening)} / {value.count(closing)})."
                    ),
                }
            )
    if value.count('"') % 2:
        anomalies.append(
            {"code": "unbalanced_ascii_quotes", "message": 'Unbalanced " quotes.'}
        )
    paragraphs = [
        re.sub(r"\s+", " ", part).strip()
        for part in re.split(r"\n\s*\n", value)
        if part.strip()
    ]
    counts = Counter(paragraphs)
    for paragraph, count in counts.items():
        if count > 1 and len(paragraph) >= 30:
            anomalies.append(
                {
                    "code": "duplicate_paragraph",
                    "message": "The same paragraph occurs more than once.",
                    "excerpt": paragraph[:160],
                    "count": count,
                }
            )
    return anomalies


def _pending_ai_proposals(page_root: Path) -> int:
    proposal_path = page_root / "evidence" / "openrouter-proposals.jsonl"
    if not proposal_path.exists():
        return 0
    events = [
        json.loads(line)
        for line in proposal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    decided = {
        (str(event.get("request_id")), int(event.get("change_index", -1)))
        for event in events
        if event.get("event") in {"accepted", "rejected"}
    }
    return sum(
        (str(event.get("request_id")), index) not in decided
        for event in events
        if event.get("status") == "proposed"
        for index, _change in enumerate(event.get("changes", []))
    )


def _append_page_finding(
    target: list[dict[str, Any]],
    page_findings: list[dict[str, Any]],
    page_number: int,
    finding: dict[str, Any],
) -> None:
    page_findings.append(finding)
    target.append({"page_number": page_number, **finding})


def validate_book(book_root: Path) -> dict[str, Any]:
    book_root = book_root.resolve()
    manifest = read_json(book_root / "book.json")
    page_paths = sorted((book_root / "pages").glob("*/page.json"))
    expected_count = int(manifest.get("source_page_count", len(page_paths)))
    present_numbers = [
        int(read_json(path).get("page_number", path.parent.name))
        for path in page_paths
    ]
    missing_page_states = [
        number for number in range(1, expected_count + 1)
        if number not in present_numbers
    ]
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    page_reports: list[dict[str, Any]] = []

    for field in ("book_id", "title", "author"):
        if not str(manifest.get(field, "")).strip():
            errors.append(
                {
                    "code": f"missing_{field}",
                    "message": f"Book metadata is missing {field}.",
                }
            )
    duplicate_page_numbers = sorted(
        number
        for number, count in Counter(present_numbers).items()
        if count > 1
    )
    if duplicate_page_numbers:
        errors.append(
            {
                "code": "duplicate_page_numbers",
                "message": "More than one page state uses the same page number.",
                "pages": duplicate_page_numbers,
            }
        )
    if missing_page_states:
        errors.append(
            {
                "code": "missing_page_states",
                "message": "Some PDF pages have not been processed.",
                "pages": missing_page_states,
            }
        )

    previous_content: dict[str, Any] | None = None
    included_text_hashes: dict[str, int] = {}
    included_pages: list[dict[str, Any]] = []
    detected_headings: list[dict[str, Any]] = []
    for state_path in page_paths:
        page = normalize_page_state(read_json(state_path))
        page_number = int(page["page_number"])
        manual = page.get("manual", {})
        include = bool(
            manual.get("include", page.get("decision", {}).get("include", False))
        )
        page_role = str(
            manual.get(
                "page_role",
                page.get("decision", {}).get("page_role", ""),
            )
        )
        final_path = state_path.parent / "final.txt"
        draft_path = state_path.parent / "draft.txt"
        text_path = final_path if final_path.exists() else draft_path
        text = text_path.read_text(encoding="utf-8").strip() if text_path.exists() else ""
        page_errors: list[dict[str, Any]] = []
        page_warnings: list[dict[str, Any]] = []

        for anomaly in text_anomalies(text):
            _append_page_finding(warnings, page_warnings, page_number, anomaly)

        if include and not text:
            page_errors.append(
                {"code": "empty_included_page", "message": "Included page has no text."}
            )
        if not is_human_verified(page):
            page_errors.append(
                {
                    "code": "not_human_verified",
                    "message": (
                        "Included page has not been verified against the image."
                        if include
                        else "Page exclusion has not been verified against the image."
                    ),
                }
            )
        if page_role not in PAGE_ROLES:
            page_errors.append(
                {
                    "code": "invalid_page_role",
                    "message": f"Page has an unknown role: {page_role or '(empty)'}.",
                }
            )
        is_content = include and page_role in CONTENT_ROLES
        if is_content:
            included_pages.append(
                {"page_number": page_number, "text": text, "page": page}
            )
        if include and text:
            normalized_text = re.sub(r"\s+", " ", text)
            if len(normalized_text) >= 80:
                duplicate_of = included_text_hashes.get(normalized_text)
                if duplicate_of is not None:
                    page_errors.append(
                        {
                            "code": "duplicate_included_page_text",
                            "message": (
                                "Included text duplicates included page "
                                f"{duplicate_of}."
                            ),
                        }
                    )
                else:
                    included_text_hashes[normalized_text] = page_number
        if "[অস্পষ্ট]" in text:
            page_errors.append(
                {
                    "code": "uncertainty_marker",
                    "message": "Page contains an unresolved uncertainty marker.",
                }
            )
        pending_multiscale = sum(
            region.get("status") == "pending"
            for region in page.get("multiscale", {}).get("regions", [])
        )
        if pending_multiscale:
            page_errors.append(
                _finding(
                    "pending_high_resolution_alternatives",
                    (
                        f"{pending_multiscale} high-resolution crop reading(s) "
                        "still require a human choice."
                    ),
                    category="workflow",
                )
            )
        if is_content and text:
            for finding in _layout_heading_findings(page, text):
                _append_page_finding(
                    warnings, page_warnings, page_number, finding
                )
            for finding in _candidate_geometry_findings(
                state_path.parent, page, text, include, page_role
            ):
                _append_page_finding(
                    warnings, page_warnings, page_number, finding
                )
            for heading in _page_headings(page, text):
                detected_headings.append({"page_number": page_number, **heading})

            candidate_text = _compact_text(
                "\n".join(
                    str(line.get("text", ""))
                    for line in _selected_candidate(page).get("lines", [])
                )
            )
            if any(marker in candidate_text for marker in TERMINAL_MARKERS) and not any(
                marker in text for marker in TERMINAL_MARKERS
            ):
                _append_page_finding(
                    warnings,
                    page_warnings,
                    page_number,
                    _finding(
                        "visible_end_marker_missing_from_text",
                        "A visible end marker is absent from the reviewed page text.",
                        evidence={"markers": list(TERMINAL_MARKERS)},
                    ),
                )

            if previous_content:
                previous_number = int(previous_content["page_number"])
                previous_text = str(previous_content["text"])
                previous_tail = _compact_text(previous_text)[-350:]
                current_head = _compact_text(text)[:350]
                overlap = _token_overlap(previous_text, text)
                similarity = fuzz.ratio(previous_tail, current_head)
                if (
                    previous_tail == current_head
                    and len(current_head) >= 80
                ):
                    page_errors.append(
                        _finding(
                            "cross_page_duplicate",
                            "Page opening exactly duplicates the previous page ending.",
                            category="integrity",
                            related_pages=[previous_number, page_number],
                        )
                    )
                elif similarity >= 93 and min(len(previous_tail), len(current_head)) >= 120:
                    _append_page_finding(
                        warnings,
                        page_warnings,
                        page_number,
                        _finding(
                            "possible_adjacent_page_duplicate",
                            "This page opening closely matches the preceding page ending.",
                            category="integrity",
                            related_pages=[previous_number, page_number],
                            evidence={"similarity": round(similarity, 1)},
                        ),
                    )
                elif overlap >= 12:
                    _append_page_finding(
                        warnings,
                        page_warnings,
                        page_number,
                        _finding(
                            "cross_page_text_overlap",
                            f"{overlap} words repeat across the page boundary.",
                            category="integrity",
                            related_pages=[previous_number, page_number],
                            evidence={"overlapping_words": overlap},
                        ),
                    )

                trailing = previous_text.rstrip()
                leading = text.lstrip()
                if trailing.endswith(("-", "‐", "‑")) and leading:
                    _append_page_finding(
                        warnings,
                        page_warnings,
                        page_number,
                        _finding(
                            "possible_split_word_across_pages",
                            "The preceding page ends with a hyphen; verify the word across this boundary.",
                            related_pages=[previous_number, page_number],
                            evidence={
                                "previous_ending": trailing[-60:],
                                "current_opening": leading[:60],
                            },
                        ),
                    )

            previous_content = {
                "page_number": page_number,
                "text": text,
            }

        unresolved_proposals = _pending_ai_proposals(state_path.parent)
        if unresolved_proposals:
            _append_page_finding(
                warnings,
                page_warnings,
                page_number,
                _finding(
                    "pending_ai_proposals",
                    f"{unresolved_proposals} AI proposal(s) await review.",
                    category="workflow",
                ),
            )

        for value in page_errors:
            errors.append({"page_number": page_number, **value})
        page_reports.append(
            {
                "page_number": page_number,
                "include": include,
                "workflow": page["workflow"],
                "errors": page_errors,
                "warnings": page_warnings,
            }
        )

    if not included_pages:
        errors.append(
            {
                "code": "no_included_content_pages",
                "message": "The document has no included content pages.",
            }
        )

    # Compare numbered headings in release order.
    previous_heading: dict[str, Any] | None = None
    for heading in detected_headings:
        if previous_heading is None:
            previous_heading = heading
            continue
        previous_number = int(previous_heading["number"])
        current_number = int(heading["number"])
        page_number = int(heading["page_number"])
        finding: dict[str, Any] | None = None
        if current_number == previous_number:
            finding = _finding(
                "duplicate_heading_number",
                f"Heading number {current_number} repeats.",
                related_pages=[int(previous_heading["page_number"]), page_number],
                evidence={"previous": previous_heading, "current": heading},
            )
        elif current_number > previous_number + 1:
            finding = _finding(
                "heading_sequence_gap",
                f"Heading sequence jumps from {previous_number} to {current_number}.",
                related_pages=[int(previous_heading["page_number"]), page_number],
                evidence={"previous": previous_heading, "current": heading},
            )
        elif current_number < previous_number:
            finding = _finding(
                "heading_sequence_reversal",
                f"Heading sequence moves backward from {previous_number} to {current_number}.",
                related_pages=[int(previous_heading["page_number"]), page_number],
                evidence={"previous": previous_heading, "current": heading},
            )
        if finding:
            warnings.append({"page_number": page_number, **finding})
            page_report = next(
                value for value in page_reports if value["page_number"] == page_number
            )
            page_report["warnings"].append(finding)
        previous_heading = heading

    if len(included_pages) >= 5:
        last = included_pages[-1]
        last_number = int(last["page_number"])
        last_text = str(last["text"]).rstrip()
        if not any(marker in last_text[-500:] for marker in TERMINAL_MARKERS):
            finding = _finding(
                "terminal_marker_not_detected",
                "No end marker was detected near the end of the final included content page.",
                related_pages=[last_number],
                evidence={"expected_markers": list(TERMINAL_MARKERS)},
            )
            warnings.append({"page_number": last_number, **finding})
            next(
                value for value in page_reports if value["page_number"] == last_number
            )["warnings"].append(finding)
        if last_text and not last_text.endswith(TERMINAL_PUNCTUATION):
            finding = _finding(
                "abrupt_book_ending",
                "The final included text does not end with terminal punctuation.",
                related_pages=[last_number],
                evidence={"ending": last_text[-80:]},
            )
            warnings.append({"page_number": last_number, **finding})
            next(
                value for value in page_reports if value["page_number"] == last_number
            )["warnings"].append(finding)

    review_queue = [
        {
            "severity": "error",
            **finding,
        }
        for finding in errors
    ] + [
        {
            "severity": "warning",
            **finding,
        }
        for finding in warnings
        if finding.get("category") in {"structure", "integrity"}
    ]
    counts_by_code = Counter(
        str(finding.get("code", "unknown")) for finding in errors + warnings
    )
    flagged_pages = {
        int(finding["page_number"])
        for finding in errors + warnings
        if finding.get("page_number") is not None
    }

    report = {
        "schema_version": 2,
        "book_id": manifest["book_id"],
        "source_page_count": expected_count,
        "processed_page_count": len(page_paths),
        "complete": not errors,
        "errors": errors,
        "warnings": warnings,
        "pages": page_reports,
        "review_queue": review_queue,
        "headings": detected_headings,
        "statistics": {
            "error_count": len(errors),
            "warning_count": len(warnings),
            "human_verified_pages": sum(
                report_page["workflow"]["human"]["status"] == "human_verified"
                for report_page in page_reports
            ),
            "structural_review_items": len(review_queue),
            "flagged_pages": len(flagged_pages),
            "counts_by_code": dict(sorted(counts_by_code.items())),
        },
    }
    write_json(book_root / "structural-validation.json", report)
    return report
