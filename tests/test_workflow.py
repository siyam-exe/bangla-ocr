from bangla_ocr.workflow import (
    is_human_verified,
    normalize_page_state,
    set_human_status,
    workflow_summary,
)


def test_automated_legacy_verified_page_is_not_human_verified():
    page = {
        "processing_complete": True,
        "decision": {"status": "draft", "include": True, "reasons": []},
        "manual": {
            "status": "verified",
            "reviewer": "Codex automated multi-source evidence gate",
            "reviewed_utc": "2026-01-01T00:00:00Z",
        },
    }
    normalize_page_state(page)
    assert page["workflow"]["automated"]["status"] == "passed"
    assert page["workflow"]["human"]["status"] == "unreviewed"
    assert is_human_verified(page) is False


def test_codex_legacy_verification_is_demoted():
    page = {
        "processing_complete": True,
        "decision": {"status": "draft", "include": False, "reasons": []},
        "manual": {
            "status": "verified",
            "reviewer": "Codex (cover metadata only)",
            "reviewed_utc": "2026-01-01T00:00:00Z",
        },
    }
    normalize_page_state(page)
    assert page["workflow"]["human"]["status"] == "unreviewed"
    assert page["workflow"]["human"]["reviewer"] == ""
    assert page["manual"]["reviewer"] == ""
    assert "Codex" in page["workflow"]["migration"]["legacy_reviewer"]


def test_named_legacy_human_review_can_migrate():
    page = {
        "processing_complete": True,
        "decision": {"status": "draft", "include": True, "reasons": []},
        "manual": {
            "status": "verified",
            "reviewer": "Test Reviewer",
            "reviewed_utc": "2026-01-01T00:00:00Z",
        },
    }
    assert is_human_verified(page) is True


def test_set_human_verified_updates_legacy_and_workflow_fields():
    page = {
        "processing_complete": True,
        "decision": {"status": "needs_review", "include": True, "reasons": []},
        "manual": {"include": True},
    }
    set_human_status(
        page,
        "human_verified",
        reviewer="Test Reviewer",
        reviewed_utc="2026-01-01T00:00:00Z",
    )
    assert page["manual"]["status"] == "human_verified"
    assert page["workflow"]["human"]["status"] == "human_verified"
    assert page["workflow"]["overall"] == "human_verified"


def test_summary_does_not_count_automated_pass_as_human_review():
    pages = [
        {
            "processing_complete": True,
            "decision": {"status": "draft", "include": True, "reasons": []},
            "manual": {"status": "unreviewed", "include": True},
        }
    ]
    summary = workflow_summary(pages)
    assert summary["automated_checks_passed"] == 1
    assert summary["human_verified"] == 0
    assert summary["pending_human_review"] == 1
