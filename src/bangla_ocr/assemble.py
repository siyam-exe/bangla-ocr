from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import nfc, read_json, write_json, write_text
from .validation import validate_book
from .workflow import is_human_verified, normalize_page_state


def _render_inline_heading(text: str, heading: str) -> tuple[str, bool, bool]:
    """Render an existing standalone heading without moving or duplicating it."""
    if not heading:
        return text, False, False
    lines = text.splitlines()
    indexes = [
        index for index, line in enumerate(lines) if line.strip() == heading
    ]
    if not indexes:
        return text, False, False
    markdown_lines = [
        f"## {heading}" if index in indexes else line
        for index, line in enumerate(lines)
    ]
    first_content_index = next(
        (index for index, line in enumerate(lines) if line.strip()),
        -1,
    )
    return (
        "\n".join(markdown_lines),
        True,
        bool(indexes and indexes[0] == first_content_index),
    )


def finalize_book(
    book_root: Path,
    *,
    allow_draft: bool = False,
) -> dict[str, Any]:
    book_root = book_root.resolve()
    manifest = read_json(book_root / "book.json")
    page_paths = sorted((book_root / "pages").glob("*/page.json"))
    markdown_body = ""
    plain_body = ""
    included_pages: list[int] = []
    draft_pages_used: list[int] = []
    skipped_unreviewed: list[int] = []
    missing_pages: list[int] = []
    inline_heading_pages: list[int] = []

    for page_state_path in page_paths:
        page = normalize_page_state(read_json(page_state_path))
        page_number = int(page["page_number"])
        manual = page.get("manual", {})
        include = bool(manual.get("include", page["decision"]["include"]))
        if not include:
            continue
        final_path = page_state_path.parent / "final.txt"
        draft_path = page_state_path.parent / "draft.txt"
        if is_human_verified(page) and final_path.exists():
            text = final_path.read_text(encoding="utf-8")
        elif allow_draft and draft_path.exists():
            text = draft_path.read_text(encoding="utf-8")
            draft_pages_used.append(page_number)
        else:
            skipped_unreviewed.append(page_number)
            continue
        text = nfc(text.strip())
        if not text:
            missing_pages.append(page_number)
            continue

        heading = nfc(str(manual.get("heading", "")).strip())
        break_before = bool(manual.get("break_before", False))
        join_without_space = bool(manual.get("join_without_space", False))
        preserve_trailing_hyphen = bool(
            manual.get("preserve_trailing_hyphen", False)
        )
        markdown_text, heading_is_inline, heading_at_start = (
            _render_inline_heading(text, heading)
        )
        if heading_is_inline:
            inline_heading_pages.append(page_number)
        if heading and not heading_is_inline:
            markdown_body = markdown_body.rstrip() + f"\n\n## {heading}\n\n"
            plain_body = plain_body.rstrip() + f"\n\n{heading}\n\n"
        elif markdown_body:
            if join_without_space:
                markdown_body = markdown_body.rstrip()
                plain_body = plain_body.rstrip()
                if (
                    not preserve_trailing_hyphen
                    and markdown_body.endswith(("-", "‐", "‑"))
                ):
                    markdown_body = markdown_body[:-1]
                if (
                    not preserve_trailing_hyphen
                    and plain_body.endswith(("-", "‐", "‑"))
                ):
                    plain_body = plain_body[:-1]
            else:
                separator = (
                    "\n\n"
                    if break_before or heading_at_start
                    else " "
                )
                markdown_body = markdown_body.rstrip() + separator
                plain_body = plain_body.rstrip() + separator
        markdown_body += markdown_text
        plain_body += text
        included_pages.append(page_number)

    title = nfc(str(manifest["title"]).strip())
    author = nfc(str(manifest["author"]).strip())
    if author in {"", "লেখক অজ্ঞাত"}:
        author = "Unknown author"
    markdown = (
        f"# {title}\n\n**Author: {author}**\n\n{markdown_body.strip()}\n"
    )
    plain = f"{title}\n\nAuthor: {author}\n\n{plain_body.strip()}\n"
    structural = validate_book(book_root)
    complete = (
        not skipped_unreviewed
        and not missing_pages
        and not draft_pages_used
        and structural["complete"]
    )
    if complete:
        write_text(book_root / "book.md", markdown)
        write_text(book_root / "book.txt", plain)
        exported_files = ["book.md", "book.txt"]
    else:
        write_text(book_root / "book.preview.md", markdown)
        write_text(book_root / "book.preview.txt", plain)
        exported_files = ["book.preview.md", "book.preview.txt"]
    report = {
        "book_id": manifest["book_id"],
        "included_pages": included_pages,
        "draft_pages_used": draft_pages_used,
        "skipped_unreviewed_pages": skipped_unreviewed,
        "empty_included_pages": missing_pages,
        "inline_heading_pages": inline_heading_pages,
        "allow_draft": allow_draft,
        "assembled": not skipped_unreviewed and not missing_pages,
        "complete": complete,
        "exported_files": exported_files,
    }
    report["structural_validation"] = {
        "complete": structural["complete"],
        "error_count": len(structural["errors"]),
        "warning_count": len(structural["warnings"]),
        "path": "structural-validation.json",
    }
    report["complete"] = report["complete"] and structural["complete"]
    write_json(book_root / "finalization-report.json", report)
    processing_path = book_root / "audit" / "processing.json"
    processing = read_json(processing_path) if processing_path.exists() else {}
    processing["last_finalization"] = report
    write_json(processing_path, processing)
    return report
