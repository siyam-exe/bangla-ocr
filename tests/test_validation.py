import json

from bangla_ocr.validation import text_anomalies, validate_book


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _book(tmp_path, text, status="human_verified"):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "validation-book",
            "title": "পরীক্ষা",
            "author": "রকিব হাসান",
            "source_page_count": 1,
        },
    )
    page = tmp_path / "pages" / "0001"
    _write_json(
        page / "page.json",
        {
            "page_number": 1,
            "processing_complete": True,
            "decision": {
                "status": "draft",
                "include": True,
                "page_role": "story",
                "reasons": [],
            },
            "manual": {
                "status": status,
                "include": True,
                "page_role": "story",
                "reviewer": "Test Reviewer",
                "reviewed_utc": "2026-01-01T00:00:00Z",
            },
        },
    )
    (page / "final.txt").write_text(text, encoding="utf-8")


def _multi_page_book(tmp_path, texts, candidates=None):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "structural-book",
            "title": "কাঠামো পরীক্ষা",
            "author": "রকিব হাসান",
            "source_page_count": len(texts),
        },
    )
    candidates = candidates or [[] for _ in texts]
    for number, (text, page_candidates) in enumerate(
        zip(texts, candidates), start=1
    ):
        page = tmp_path / "pages" / f"{number:04d}"
        _write_json(
            page / "page.json",
            {
                "page_number": number,
                "processing_complete": True,
                "preprocessing": {},
                "candidates": page_candidates,
                "decision": {
                    "status": "draft",
                    "include": True,
                    "page_role": "story",
                    "selected_candidate": 0,
                    "reasons": [],
                },
                "manual": {
                    "status": "human_verified",
                    "include": True,
                    "page_role": "story",
                    "heading": "",
                    "reviewer": "Test Reviewer",
                    "reviewed_utc": "2026-01-01T00:00:00Z",
                },
            },
        )
        (page / "final.txt").write_text(text, encoding="utf-8")


def test_validation_blocks_unreviewed_included_page(tmp_path):
    _book(tmp_path, "একটি স্বাভাবিক বাক্য।", status="unreviewed")
    report = validate_book(tmp_path)
    assert report["complete"] is False
    assert any(error["code"] == "not_human_verified" for error in report["errors"])


def test_validation_blocks_unreviewed_exclusion(tmp_path):
    _book(tmp_path, "প্রচ্ছদ", status="unreviewed")
    page_path = tmp_path / "pages" / "0001" / "page.json"
    page = json.loads(page_path.read_text(encoding="utf-8"))
    page["manual"]["include"] = False
    page["manual"]["page_role"] = "front_matter"
    _write_json(page_path, page)

    report = validate_book(tmp_path)

    assert any(
        error["code"] == "not_human_verified"
        and "exclusion" in error["message"]
        for error in report["errors"]
    )


def test_validation_passes_human_verified_page(tmp_path):
    _book(tmp_path, "একটি স্বাভাবিক বাক্য।")
    report = validate_book(tmp_path)
    assert report["complete"] is True
    assert report["statistics"]["human_verified_pages"] == 1
    assert report["schema_version"] == 2


def test_text_anomaly_finds_duplicate_paragraph_and_quotes():
    paragraph = "এই অনুচ্ছেদটি পরীক্ষার জন্য যথেষ্ট দীর্ঘ এবং একইভাবে আবার আছে।"
    anomalies = text_anomalies(f"‘অসমাপ্ত\n\n{paragraph}\n\n{paragraph}")
    codes = {value["code"] for value in anomalies}
    assert "unbalanced_quotes" in codes
    assert "duplicate_paragraph" in codes


def test_decided_ai_patch_is_not_reported_as_pending(tmp_path):
    _book(tmp_path, "একটি স্বাভাবিক বাক্য।")
    evidence = tmp_path / "pages" / "0001" / "evidence"
    evidence.mkdir()
    events = [
        {
            "request_id": "request",
            "status": "proposed",
            "changes": [{"original": "একটি", "replacement": "একটা"}],
        },
        {
            "event": "rejected",
            "request_id": "request",
            "change_index": 0,
        },
    ]
    (evidence / "openrouter-proposals.jsonl").write_text(
        "\n".join(json.dumps(value, ensure_ascii=False) for value in events),
        encoding="utf-8",
    )

    report = validate_book(tmp_path)

    assert not any(
        warning["code"] == "pending_ai_proposals"
        for warning in report["warnings"]
    )


def test_layout_heading_and_visible_end_marker_omissions_are_flagged(tmp_path):
    body = "এটি গল্পের একটি স্বাভাবিক অনুচ্ছেদ।"
    candidates = [
        [{"lines": [{"label": "Text", "text": body}]}]
        for _ in range(4)
    ] + [
        [
            {
                "lines": [
                    {"label": "SectionHeader", "text": "ষোলো"},
                    {"label": "Text", "text": body},
                    {"label": "SectionHeader", "text": "শেষ"},
                ]
            }
        ]
    ]
    _multi_page_book(tmp_path, [body] * 5, candidates)

    report = validate_book(tmp_path)
    codes = {warning["code"] for warning in report["warnings"]}

    assert "layout_heading_missing_from_text" in codes
    assert "visible_end_marker_missing_from_text" in codes
    assert "terminal_marker_not_detected" in codes
    assert report["statistics"]["structural_review_items"] >= 3


def test_heading_gap_is_added_to_page_review_queue(tmp_path):
    _multi_page_book(
        tmp_path,
        ["এক\nপ্রথম অধ্যায়ের পাঠ্য।", "তিন\nতৃতীয় অধ্যায়ের পাঠ্য।"],
    )

    report = validate_book(tmp_path)
    gap = next(
        warning
        for warning in report["warnings"]
        if warning["code"] == "heading_sequence_gap"
    )

    assert gap["page_number"] == 2
    assert gap["related_pages"] == [1, 2]


def test_split_word_and_adjacent_page_overlap_are_flagged(tmp_path):
    repeated = " ".join(
        [
            "কিশোর",
            "মুসা",
            "রবিন",
            "রহস্য",
            "সমাধান",
            "করতে",
            "পুরনো",
            "বাড়ির",
            "দিকে",
            "এগিয়ে",
            "গেল",
            "ধীরে",
            "ধীরে",
            "রাতে",
        ]
    )
    _multi_page_book(
        tmp_path,
        [f"শুরুর পাঠ্য {repeated}-", f"{repeated} তারপর নতুন পাঠ্য।"],
    )

    report = validate_book(tmp_path)
    codes = {warning["code"] for warning in report["warnings"]}

    assert "possible_split_word_across_pages" in codes
    assert codes & {"cross_page_text_overlap", "possible_adjacent_page_duplicate"}


def test_high_confidence_on_degraded_scan_is_not_trusted(tmp_path):
    _multi_page_book(
        tmp_path,
        ["একটি স্বাভাবিক কিন্তু ঝাপসা পাতার পাঠ্য।"],
        [[{"confidence": 0.98, "lines": []}]],
    )
    page_path = tmp_path / "pages" / "0001" / "page.json"
    page = json.loads(page_path.read_text(encoding="utf-8"))
    page["preprocessing"] = {
        "metrics": {
            "sharpness_laplacian_variance": 12,
            "grayscale_stddev": 18,
        }
    }
    _write_json(page_path, page)

    report = validate_book(tmp_path)

    assert any(
        warning["code"] == "high_confidence_degraded_scan"
        for warning in report["warnings"]
    )
