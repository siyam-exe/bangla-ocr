import json

from bangla_ocr.assemble import finalize_book


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False),
        encoding="utf-8",
    )


def test_finalize_uses_only_verified_included_pages(tmp_path):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "test-book",
            "title": "পরীক্ষার বই",
            "author": "রকিব হাসান",
        },
    )
    first = tmp_path / "pages" / "0001"
    second = tmp_path / "pages" / "0002"
    for root, status in ((first, "human_verified"), (second, "unreviewed")):
        _write_json(
            root / "page.json",
            {
                "page_number": int(root.name),
                "decision": {"include": True},
                "manual": {
                    "include": True,
                    "status": status,
                    "heading": "",
                    "break_before": False,
                },
            },
        )
    (first / "final.txt").write_text("প্রথম পৃষ্ঠা।", encoding="utf-8")
    (first / "draft.txt").write_text("অগ্রাহ্য খসড়া।", encoding="utf-8")
    (second / "draft.txt").write_text("দ্বিতীয় পৃষ্ঠা।", encoding="utf-8")

    report = finalize_book(tmp_path)

    assert report["included_pages"] == [1]
    assert report["skipped_unreviewed_pages"] == [2]
    assert report["complete"] is False
    assert not (tmp_path / "book.txt").exists()
    final_text = (tmp_path / "book.preview.txt").read_text(encoding="utf-8")
    assert "প্রথম পৃষ্ঠা।" in final_text
    assert "অগ্রাহ্য খসড়া।" not in final_text
    assert "দ্বিতীয় পৃষ্ঠা।" not in final_text


def test_draft_export_is_never_marked_verified_complete(tmp_path):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "draft-book",
            "title": "খসড়া বই",
            "author": "রকিব হাসান",
        },
    )
    page_root = tmp_path / "pages" / "0001"
    _write_json(
        page_root / "page.json",
        {
            "page_number": 1,
            "decision": {"include": True},
            "manual": {
                "include": True,
                "status": "unreviewed",
                "heading": "",
                "break_before": False,
            },
        },
    )
    (page_root / "draft.txt").write_text("অযাচাইকৃত পাঠ্য।", encoding="utf-8")

    report = finalize_book(tmp_path, allow_draft=True)

    assert report["assembled"] is True
    assert report["draft_pages_used"] == [1]
    assert report["complete"] is False


def test_page_split_word_can_join_without_space(tmp_path):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "split-word",
            "title": "পরীক্ষা",
            "author": "রকিব হাসান",
        },
    )
    for number, text, join_without_space in (
        (1, "রবিন, প্রতি-", False),
        (2, "দিনই সেখানে যায়।", True),
    ):
        page_root = tmp_path / "pages" / f"{number:04d}"
        _write_json(
            page_root / "page.json",
            {
                "page_number": number,
                "decision": {"include": True},
                "manual": {
                    "include": True,
                    "status": "human_verified",
                    "heading": "",
                    "break_before": False,
                    "join_without_space": join_without_space,
                },
            },
        )
        (page_root / "final.txt").write_text(text, encoding="utf-8")

    report = finalize_book(tmp_path)
    output = (tmp_path / "book.txt").read_text(encoding="utf-8")

    assert report["complete"] is True
    assert "প্রতিদিনই" in output
    assert "প্রতি-দিনই" not in output


def test_plain_text_heading_has_no_markdown_marker(tmp_path):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "plain-heading",
            "title": "পরীক্ষা",
            "author": "রকিব হাসান",
        },
    )
    page_root = tmp_path / "pages" / "0001"
    _write_json(
        page_root / "page.json",
        {
            "page_number": 1,
            "decision": {"include": True},
            "manual": {
                "include": True,
                "status": "human_verified",
                "heading": "এক",
                "break_before": False,
                "join_without_space": False,
            },
        },
    )
    (page_root / "final.txt").write_text("গল্পের শুরু।", encoding="utf-8")

    report = finalize_book(tmp_path)
    plain = (tmp_path / "book.txt").read_text(encoding="utf-8")
    markdown = (tmp_path / "book.md").read_text(encoding="utf-8")

    assert report["complete"] is True
    assert "\nএক\n" in plain
    assert "## এক" not in plain
    assert "## এক" in markdown


def test_existing_mid_page_heading_is_not_moved_or_duplicated(tmp_path):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "inline-heading",
            "title": "পরীক্ষা",
            "author": "রকিব হাসান",
        },
    )
    page_root = tmp_path / "pages" / "0001"
    _write_json(
        page_root / "page.json",
        {
            "page_number": 1,
            "decision": {"include": True},
            "manual": {
                "include": True,
                "status": "human_verified",
                "heading": "দুই",
                "break_before": False,
                "join_without_space": False,
            },
        },
    )
    (page_root / "final.txt").write_text(
        "আগের অধ্যায়ের শেষ।\n\nদুই\n\nনতুন অধ্যায়।",
        encoding="utf-8",
    )

    report = finalize_book(tmp_path)
    plain = (tmp_path / "book.txt").read_text(encoding="utf-8")
    markdown = (tmp_path / "book.md").read_text(encoding="utf-8")

    assert report["complete"] is True
    assert report["inline_heading_pages"] == [1]
    assert plain.count("\nদুই\n") == 1
    assert markdown.count("## দুই") == 1
    assert plain.index("আগের অধ্যায়ের শেষ।") < plain.index("\nদুই\n")
    assert plain.index("\nদুই\n") < plain.index("নতুন অধ্যায়।")


def test_lexical_hyphen_can_be_preserved_across_page_boundary(tmp_path):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "lexical-hyphen",
            "title": "পরীক্ষা",
            "author": "রকিব হাসান",
        },
    )
    for number, text, join_without_space, preserve in (
        (1, "তার মুখ-", False, False),
        (2, "মাথা পেঁচিয়ে ধরেছে।", True, True),
    ):
        page_root = tmp_path / "pages" / f"{number:04d}"
        _write_json(
            page_root / "page.json",
            {
                "page_number": number,
                "decision": {"include": True},
                "manual": {
                    "include": True,
                    "status": "human_verified",
                    "heading": "",
                    "break_before": False,
                    "join_without_space": join_without_space,
                    "preserve_trailing_hyphen": preserve,
                },
            },
        )
        (page_root / "final.txt").write_text(text, encoding="utf-8")

    report = finalize_book(tmp_path)
    output = (tmp_path / "book.txt").read_text(encoding="utf-8")

    assert report["complete"] is True
    assert "মুখ-মাথা" in output
    assert "মুখমাথা" not in output
    assert "মুখ- মাথা" not in output
