from __future__ import annotations

from typing import Any


WORKFLOW_SCHEMA_VERSION = 4
OCR_STATUSES = {"pending", "running", "complete", "failed"}
AUTOMATED_STATUSES = {"pending", "passed", "needs_review", "failed"}
HUMAN_STATUSES = {
    "unreviewed",
    "in_review",
    "human_verified",
    "unresolved",
}


def _legacy_human_status(page: dict[str, Any]) -> str:
    manual = page.get("manual", {})
    legacy = str(manual.get("status", "unreviewed"))
    if legacy == "human_verified":
        return "human_verified"
    if legacy == "reviewed_unverified":
        return "in_review"
    if legacy != "verified":
        return "unreviewed"

    reviewer = str(manual.get("reviewer", "")).casefold()
    reviewed_utc = str(manual.get("reviewed_utc", "")).strip()
    automated_markers = (
        "automated",
        "multi-source evidence gate",
        "consensus",
        "codex",
        "qwen",
    )
    if any(marker in reviewer for marker in automated_markers):
        return "unreviewed"
    if reviewer and reviewed_utc:
        return "human_verified"
    return "unreviewed"


def normalize_page_state(page: dict[str, Any]) -> dict[str, Any]:
    """Return a page state with explicit OCR, automated, and human statuses."""
    workflow = page.setdefault("workflow", {})
    previous_schema_version = int(workflow.get("schema_version", 0) or 0)
    workflow["schema_version"] = WORKFLOW_SCHEMA_VERSION
    manual = page.setdefault("manual", {})

    ocr = workflow.setdefault("ocr", {})
    if ocr.get("status") not in OCR_STATUSES:
        ocr["status"] = (
            "complete" if page.get("processing_complete") else "pending"
        )

    automated = workflow.setdefault("automated", {})
    if automated.get("status") not in AUTOMATED_STATUSES:
        decision_status = str(page.get("decision", {}).get("status", ""))
        automated["status"] = (
            "passed" if decision_status == "draft" else "needs_review"
        )
    automated.setdefault(
        "reasons",
        list(page.get("decision", {}).get("reasons", [])),
    )

    human = workflow.setdefault("human", {})
    legacy_needs_recheck = (
        previous_schema_version < WORKFLOW_SCHEMA_VERSION
        and str(manual.get("status", "")) == "verified"
    )
    if human.get("status") not in HUMAN_STATUSES or legacy_needs_recheck:
        migrated_status = _legacy_human_status(page)
        human["status"] = migrated_status
        if legacy_needs_recheck and migrated_status == "unreviewed":
            workflow["migration"] = {
                "legacy_status": "verified",
                "legacy_reviewer": str(manual.get("reviewer", "")),
                "legacy_reviewed_utc": manual.get("reviewed_utc"),
                "reason": "automated verification is not human verification",
            }
            human["reviewer"] = ""
            human["reviewed_utc"] = None
            manual["status"] = "unreviewed"
            manual["reviewer"] = ""
            manual["reviewed_utc"] = None
    manual.setdefault(
        "include", bool(page.get("decision", {}).get("include", False))
    )
    manual.setdefault(
        "page_role",
        page.get("decision", {}).get(
            "page_role", "story" if manual["include"] else "non_story_or_uncertain"
        ),
    )
    manual.setdefault("heading", "")
    manual.setdefault("break_before", False)
    manual.setdefault("join_without_space", False)
    manual.setdefault("preserve_trailing_hyphen", False)
    human.setdefault("reviewer", str(manual.get("reviewer", "")))
    human.setdefault("reviewed_utc", manual.get("reviewed_utc"))

    ai = workflow.setdefault("ai", {})
    ai.setdefault("status", "not_requested")
    ai.setdefault("proposal_count", 0)
    ai.setdefault("accepted_count", 0)

    workflow["overall"] = overall_status(page)
    return page


def overall_status(page: dict[str, Any]) -> str:
    workflow = page.get("workflow", {})
    ocr_status = workflow.get("ocr", {}).get("status", "pending")
    automated_status = workflow.get("automated", {}).get("status", "pending")
    human_status = workflow.get("human", {}).get("status", "unreviewed")
    include = bool(
        page.get("manual", {}).get(
            "include", page.get("decision", {}).get("include", False)
        )
    )
    if ocr_status == "failed" or automated_status == "failed":
        return "failed"
    if ocr_status != "complete":
        return "processing"
    if human_status == "human_verified":
        return "human_verified" if include else "excluded_verified"
    if human_status == "unresolved":
        return "unresolved"
    if human_status == "in_review":
        return "in_review"
    if automated_status == "needs_review":
        return "needs_review"
    return "automated_checks_passed"


def is_human_verified(page: dict[str, Any]) -> bool:
    normalize_page_state(page)
    return (
        page["workflow"]["human"]["status"] == "human_verified"
    )


def set_human_status(
    page: dict[str, Any],
    status: str,
    *,
    reviewer: str,
    reviewed_utc: str,
) -> None:
    if status not in HUMAN_STATUSES:
        raise ValueError(f"Invalid human status: {status}")
    normalize_page_state(page)
    human = page["workflow"]["human"]
    human.update(
        {
            "status": status,
            "reviewer": reviewer,
            "reviewed_utc": reviewed_utc,
        }
    )
    manual = page.setdefault("manual", {})
    manual["status"] = status
    manual["reviewer"] = reviewer
    manual["reviewed_utc"] = reviewed_utc
    page["workflow"]["overall"] = overall_status(page)


def workflow_summary(pages: list[dict[str, Any]]) -> dict[str, int]:
    counters = {
        "total": len(pages),
        "processing": 0,
        "automated_checks_passed": 0,
        "needs_review": 0,
        "in_review": 0,
        "human_verified": 0,
        "excluded_verified": 0,
        "unresolved": 0,
        "failed": 0,
        "included": 0,
    }
    for page in pages:
        normalize_page_state(page)
        status = page["workflow"]["overall"]
        if status in counters:
            counters[status] += 1
        if bool(
            page.get("manual", {}).get(
                "include", page.get("decision", {}).get("include", False)
            )
        ):
            counters["included"] += 1
    counters["pending_human_review"] = (
        counters["total"]
        - counters["human_verified"]
        - counters["excluded_verified"]
    )
    return counters
