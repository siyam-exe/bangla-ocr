import json
import time
from io import BytesIO

import pytest
from pypdf import PdfWriter

from bangla_ocr.application import JobRegistry, _job_progress, create_application
from bangla_ocr.engines.surya_policy import SuryaBackendDecision
from bangla_ocr.engines.surya_runtime import (
    SURYA_RUNTIME_DEFINITIONS,
    SuryaRuntimeSelection,
)
from bangla_ocr.processor import RequiredEngineFailedError
from bangla_ocr.review import create_review_app


@pytest.fixture(autouse=True)
def _stable_application_storage(monkeypatch):
    """Keep UI tests independent from the workstation's current free space."""
    snapshot = {
        "disks": {
            "system": {"free": "100.0 GiB", "free_bytes": 100 * 1024**3},
            "output": {"free": "100.0 GiB", "free_bytes": 100 * 1024**3},
        },
        "memory": {"physical_available": "8.0 GiB", "commit_used": "4.0 GiB"},
    }
    monkeypatch.setattr(
        "bangla_ocr.application.storage_preflight",
        lambda *args, estimated_workspace_bytes=0, **kwargs: {
            "ready": True,
            "warnings": [],
            "errors": [],
            "snapshot": snapshot,
            "estimated_workspace_bytes": estimated_workspace_bytes,
            "estimated_workspace": "2.0 MiB",
            "system_warning_bytes": 15 * 1024**3,
            "system_block_bytes": 12 * 1024**3,
        },
    )


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False),
        encoding="utf-8",
    )


def _runtime_report():
    return {
        "generated_utc": "2026-08-30T10:00:00+00:00",
        "display_adapters": {
            "status": "ready",
            "reason": "Windows reported 1 display adapter(s)",
            "adapters": [
                {
                    "name": "Test NVIDIA GPU",
                    "vendor": "nvidia",
                    "driver_version": "600.1",
                    "windows_reported_adapter_ram_bytes": 8 * 1024**3,
                }
            ],
        },
        "runtimes": {
            "cpu": {
                "backend": "cpu",
                "display_name": "CPU",
                "binary": "C:/test/llama.cpp-cpu/llama-server.exe",
                "installed": True,
                "usable": True,
                "status": "ready",
                "reason": "The CPU runtime started successfully",
                "version": "b10107",
                "devices": [],
                "duration_ms": 10,
                "diagnostic_output": "",
            },
            "cuda": {
                "backend": "cuda",
                "display_name": "CUDA",
                "binary": "C:/test/llama.cpp-cuda/llama-server.exe",
                "installed": True,
                "usable": True,
                "status": "ready",
                "reason": "The CUDA runtime reported 1 usable device(s)",
                "version": "b10107",
                "devices": [
                    {"identifier": "CUDA0", "description": "Test NVIDIA GPU"}
                ],
                "duration_ms": 20,
                "diagnostic_output": "Available devices: CUDA0",
            },
            "vulkan": {
                "backend": "vulkan",
                "display_name": "Vulkan",
                "binary": "C:/test/llama.cpp-vulkan/llama-server.exe",
                "installed": False,
                "usable": False,
                "status": "missing",
                "reason": "The Vulkan runtime is not installed",
                "version": None,
                "devices": [],
                "duration_ms": 0,
                "diagnostic_output": "",
            },
        },
    }


def _runtime_decision(root, *, requested="auto", hardware_report=None):
    report = hardware_report or _runtime_report()
    requested_backend, _, requested_device = requested.partition("/")
    selected_backend = "cuda" if requested_backend == "auto" else requested_backend
    runtime = report["runtimes"].get(selected_backend, {})
    selection = None
    if runtime.get("usable"):
        definition = SURYA_RUNTIME_DEFINITIONS[selected_backend]
        selection = SuryaRuntimeSelection(
            definition=definition,
            binary=definition.binary_path(root),
            source="bundled",
            device_identifier=(
                requested_device
                or next(
                    (
                        str(device["identifier"])
                        for device in runtime.get("devices", [])
                    ),
                    None,
                )
            ),
        )
    return SuryaBackendDecision(
        requested=requested,
        selected=selection,
        automatic=requested == "auto",
        cpu_fallback=requested == "auto" and selected_backend == "cpu",
        reason=(
            f"Selected {selected_backend}"
            if selection
            else str(runtime.get("reason", "Runtime unavailable"))
        ),
        attempted_backends=(selected_backend,),
        runtime_statuses=report["runtimes"],
        requested_backend=requested_backend,
        requested_device=requested_device or None,
    )


def test_review_dashboard_can_finalize_verified_pages(tmp_path):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "review-test",
            "title": "পরীক্ষার বই",
            "author": "রকিব হাসান",
        },
    )
    page_root = tmp_path / "pages" / "0001"
    _write_json(
        page_root / "page.json",
            {
                "page_number": 1,
                "source_image": "source.webp",
                "selected_image": "source.webp",
                "processing_complete": True,
                "preprocessing": {},
            "candidates": [
                {"engine": "surya", "score": 0.98, "confidence": 0.98}
            ],
            "decision": {
                "status": "draft",
                "include": True,
                "page_role": "story",
                "selected_engine": "surya",
                "reasons": [],
            },
            "manual": {
                "status": "human_verified",
                "include": True,
                "page_role": "story",
                "heading": "",
                "break_before": False,
                "reviewer": "tester",
            },
            "errors": [],
        },
    )
    (page_root / "draft.txt").write_text("খসড়া।", encoding="utf-8")
    (page_root / "final.txt").write_text("যাচাই করা পাঠ্য।", encoding="utf-8")

    app = create_review_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()

    dashboard = client.get("/", follow_redirects=True)
    assert dashboard.status_code == 200
    assert "Download verified text".encode() in dashboard.data
    assert b"disabled" not in dashboard.data

    finalized = client.post(
        "/books/review-test/finalize", follow_redirects=True
    )
    assert finalized.status_code == 200
    assert "attachment" in finalized.headers["Content-Disposition"]
    assert "verified.txt" in finalized.headers["Content-Disposition"]
    assert (tmp_path / "book.txt").exists()
    assert (tmp_path / "audit" / "processing.json").exists()
    assert "যাচাই করা পাঠ্য।" in (
        tmp_path / "book.txt"
    ).read_text(encoding="utf-8")

    markdown = client.post(
        "/books/review-test/finalize",
        data={"format": "markdown"},
    )
    assert markdown.status_code == 200
    assert "verified.md" in markdown.headers["Content-Disposition"]
    assert markdown.mimetype == "text/markdown"
    assert "# পরীক্ষার বই".encode() in markdown.data
    assert "**Author: রকিব হাসান**".encode() in markdown.data


def test_unverified_book_downloads_preview_but_locks_verified_export(
    tmp_path,
):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "draft-review",
            "title": "খসড়া বই",
            "author": "রকিব হাসান",
            "source_page_count": 1,
        },
    )
    page_root = tmp_path / "pages" / "0001"
    _write_json(
        page_root / "page.json",
        {
            "page_number": 1,
            "source_image": "source.webp",
            "selected_image": "source.webp",
            "preprocessing": {},
            "candidates": [],
            "decision": {
                "status": "draft",
                "include": True,
                "page_role": "story",
                "selected_engine": "surya",
                "reasons": [],
            },
            "manual": {
                "status": "unreviewed",
                "include": True,
                "page_role": "story",
                "heading": "",
                "break_before": False,
                "reviewer": "",
            },
            "errors": [],
        },
    )
    (page_root / "draft.txt").write_text(
        "এটি যাচাই না করা খসড়া।",
        encoding="utf-8",
    )

    app = create_review_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()

    dashboard = client.get("/", follow_redirects=True)
    assert dashboard.status_code == 200
    assert b"Download preview" in dashboard.data
    assert b"disabled" in dashboard.data
    assert b"Verified export is locked" in dashboard.data

    preview = client.post("/books/draft-review/preview")

    assert preview.status_code == 200
    assert "attachment" in preview.headers["Content-Disposition"]
    assert "UNVERIFIED-preview.txt" in preview.headers["Content-Disposition"]
    assert "এটি যাচাই না করা খসড়া।".encode() in preview.data
    assert not (tmp_path / "book.txt").exists()

    markdown = client.post(
        "/books/draft-review/preview",
        data={"format": "markdown"},
    )
    assert markdown.status_code == 200
    assert "UNVERIFIED-preview.md" in markdown.headers["Content-Disposition"]
    assert markdown.mimetype == "text/markdown"
    assert "# খসড়া বই".encode() in markdown.data


def test_structural_review_queue_links_dashboard_to_flagged_page(tmp_path):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "structural-ui",
            "title": "কাঠামো পরীক্ষা",
            "author": "রকিব হাসান",
            "source_page_count": 1,
        },
    )
    page_root = tmp_path / "pages" / "0001"
    _write_json(
        page_root / "page.json",
        {
            "page_number": 1,
            "source_image": "source.webp",
            "selected_image": "source.webp",
            "processing_complete": True,
            "preprocessing": {},
            "candidates": [],
            "decision": {
                "status": "draft",
                "include": True,
                "page_role": "story",
                "selected_engine": "surya",
                "reasons": [],
            },
            "manual": {
                "status": "human_verified",
                "include": True,
                "page_role": "story",
                "heading": "",
                "break_before": False,
                "reviewer": "tester",
            },
        },
    )
    (page_root / "final.txt").write_text("শেষ পাতার পাঠ্য।", encoding="utf-8")
    finding = {
        "code": "layout_heading_missing_from_text",
        "category": "structure",
        "message": "A visible layout heading is absent from the reviewed text.",
    }
    _write_json(
        tmp_path / "structural-validation.json",
        {
            "schema_version": 2,
            "complete": True,
            "errors": [],
            "warnings": [{"page_number": 1, **finding}],
            "review_queue": [
                {"severity": "warning", "page_number": 1, **finding}
            ],
            "pages": [
                {
                    "page_number": 1,
                    "errors": [],
                    "warnings": [finding],
                }
            ],
            "statistics": {"flagged_pages": 1},
        },
    )

    app = create_review_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()

    dashboard = client.get("/", follow_redirects=True)
    page = client.get("/books/structural-ui/pages/1")

    assert dashboard.status_code == 200
    assert b"Review 1 structural finding" in dashboard.data
    assert b"PDF page 1" in dashboard.data
    assert page.status_code == 200
    assert b"Layout Heading Missing From Text" in page.data


def test_multiscale_alternatives_require_human_choice_and_page_text_can_copy(
    tmp_path,
):
    _write_json(
        tmp_path / "book.json",
        {
            "book_id": "crop-review",
            "title": "Crop review",
            "author": "Tester",
            "source_page_count": 1,
        },
    )
    page_root = tmp_path / "pages" / "0001"
    evidence = page_root / "evidence"
    evidence.mkdir(parents=True)
    (page_root / "source.webp").write_bytes(b"source")
    (evidence / "crop-one.webp").write_bytes(b"crop")
    _write_json(
        page_root / "page.json",
        {
            "page_number": 1,
            "source_image": "source.webp",
            "selected_image": "source.webp",
            "processing_complete": True,
            "preprocessing": {},
            "candidates": [],
            "decision": {
                "status": "needs_review",
                "include": True,
                "page_role": "story",
                "selected_engine": "surya",
                "reasons": ["high-resolution crop disagreement"],
            },
            "manual": {
                "status": "unreviewed",
                "include": True,
                "page_role": "story",
                "heading": "",
                "break_before": False,
                "reviewer": "",
            },
            "multiscale": {
                "policy": "human_choice_only",
                "automatic_correction": False,
                "pending_count": 1,
                "regions": [
                    {
                        "region_id": "r01-test",
                        "status": "pending",
                        "crop_image": "evidence/crop-one.webp",
                        "source_bbox": [1, 2, 3, 4],
                        "original": "পূর্ণ পাতার পাঠ্য",
                        "alternative": "উচ্চ রেজোলিউশনের পাঠ্য",
                        "reasons": ["small text region"],
                        "comparison": {"disagreement": 0.25},
                    }
                ],
            },
        },
    )
    (page_root / "draft.txt").write_text(
        "শুরু। পূর্ণ পাতার পাঠ্য। শেষ।", encoding="utf-8"
    )

    app = create_review_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()

    review = client.get("/books/crop-review/pages/1")
    assert review.status_code == 200
    assert b"High-resolution scan checks" in review.data
    assert b"Copy page text" in review.data
    assert b"Use high-resolution reading" in review.data
    assert b"Nothing is applied without your choice" in review.data

    applied = client.post(
        "/books/crop-review/pages/1/multiscale/r01-test",
        data={"action": "use_crop"},
        follow_redirects=True,
    )

    assert applied.status_code == 200
    assert (page_root / "final.txt").read_text(encoding="utf-8") == (
        "শুরু। উচ্চ রেজোলিউশনের পাঠ্য। শেষ।"
    )
    state = json.loads((page_root / "page.json").read_text(encoding="utf-8"))
    assert state["multiscale"]["regions"][0]["status"] == "accepted"
    assert state["workflow"]["human"]["status"] == "in_review"
    assert list((page_root / "revisions").glob("*.txt"))
    assert (tmp_path / "audit" / "multiscale-decisions.jsonl").exists()


def test_job_registry_persists_and_marks_interrupted_work(tmp_path):
    jobs = JobRegistry(tmp_path / "jobs")
    jobs.put(
        "abc",
        status="running",
        title="পরীক্ষা",
        completed_page_count=1,
        progress_current=3,
        progress_total=3,
        progress_percent=100,
    )

    restored = JobRegistry(tmp_path / "jobs").get("abc")

    assert restored is not None
    assert restored["status"] == "interrupted"
    assert "resume" in restored["message"]
    assert restored["progress_current"] == 1
    assert restored["progress_percent"] == 33


def test_interrupted_job_recounts_completed_pages_from_its_workspace(tmp_path):
    storage = tmp_path / "_app" / "jobs"
    book_root = tmp_path / "recovery-book"
    (book_root / "audit").mkdir(parents=True)
    (book_root / "audit" / "processing.json").write_text(
        json.dumps({"all_processed_pages": [9, 10]}),
        encoding="utf-8",
    )
    jobs = JobRegistry(storage)
    jobs.put(
        "abc",
        status="running",
        workspace_book_id="recovery-book",
        page_indexes=[8, 9, 10],
        completed_page_count=1,
        progress_current=3,
        progress_total=3,
        progress_percent=100,
    )

    restored = JobRegistry(storage).get("abc")

    assert restored is not None
    assert restored["completed_page_count"] == 2
    assert restored["progress_current"] == 2
    assert restored["progress_percent"] == 67


def test_job_registry_reports_stalled_stage_without_losing_job(tmp_path):
    jobs = JobRegistry(tmp_path / "jobs", stall_seconds=1)
    old = "2000-01-01T00:00:00+00:00"
    jobs.put(
        "stale",
        status="running",
        title="পরীক্ষা",
        last_progress_utc=old,
        heartbeat_utc=old,
    )

    stalled = jobs.get("stale")

    assert stalled is not None
    assert stalled["status"] == "stalled"
    assert "preserved" in stalled["message"]
    assert jobs.running() is True


def test_pipeline_version_matches_package_metadata():
    from bangla_ocr import __version__

    assert __version__ == "1.1.0"


def test_whole_document_ai_review_route_is_not_exposed(tmp_path):
    app = create_application(
        output_root=tmp_path / "output",
        source_root=tmp_path / "sources",
    )
    app.config["TESTING"] = True

    response = app.test_client().get("/books/anything/document-review")

    assert response.status_code == 404


def test_job_progress_extracts_recent_speed_and_eta():
    value = _job_progress(
        "[12/100] Page 12: draft, surya "
        "| metrics page=24.500 average=23.250 eta=2046.0"
    )

    assert value["progress_current"] == 12
    assert value["progress_percent"] == 12
    assert value["last_page_seconds"] == 24.5
    assert value["average_page_seconds"] == 23.25
    assert value["eta"] == "34m 6s"
    assert value["progress_message"] == "[12/100] Page 12: draft, surya"


def test_completed_page_count_uses_processing_audit(tmp_path):
    from bangla_ocr.application import _completed_page_count

    book_root = tmp_path / "book"
    _write_json(
        book_root / "audit" / "processing.json",
        {"all_processed_pages": [1, 2, 2, "3", -1]},
    )

    assert _completed_page_count(book_root) == 2
    assert _completed_page_count(tmp_path / "missing") == 0


def test_import_dashboard_starts_and_completes_one_book_job(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf_bytes = BytesIO()
    writer.write(pdf_bytes)

    output_root = tmp_path / "output"
    requested_engines = None

    def fake_process(**kwargs):
        nonlocal requested_engines
        requested_engines = kwargs["requested_engines"]
        book_root = output_root / "imported-book"
        _write_json(
            book_root / "book.json",
            {
                "book_id": "imported-book",
                "title": kwargs["title"],
                "author": kwargs["author"],
                "source_page_count": 1,
            },
        )
        return book_root

    app = create_application(
        output_root=output_root,
        source_root=tmp_path / "sources",
        process_function=fake_process,
    )
    app.config["TESTING"] = True
    client = app.test_client()
    response = client.post(
        "/import",
        data={
            "title": "আমদানি পরীক্ষা",
            "author": "রকিব হাসান",
            "pages": "all",
            "ocr_engine": "easyocr",
            "pdf": (BytesIO(pdf_bytes.getvalue()), "tiny.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    job_id = response.headers["Location"].rsplit("/", 1)[-1]

    status = None
    for _ in range(100):
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["status"] in {"complete", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "complete"
    assert status["book_id"] == "imported-book"
    assert requested_engines == ["easyocr", "embedded"]
    assert status["resource_latest"]["stage"] == "post_job_cleanup"
    assert (
        output_root
        / status["workspace_book_id"]
        / "audit"
        / "resources.jsonl"
    ).exists()

    completed_job = client.get(f"/jobs/{job_id}")
    assert completed_job.status_code == 302
    assert completed_job.headers["Location"].endswith("/books/imported-book")


def test_import_dashboard_blocks_silent_engine_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "bangla_ocr.application.EngineRegistry.statuses",
        lambda self: {
            "surya": {"available": False, "reason": "test server missing"},
            "easyocr": {"available": True, "reason": "ready"},
            "tesseract": {"available": False, "reason": "missing"},
            "embedded": {"available": True, "reason": "ready"},
        },
    )
    app = create_application(
        output_root=tmp_path / "output",
        source_root=tmp_path / "sources",
    )
    app.config["TESTING"] = True
    client = app.test_client()

    dashboard = client.get("/")
    blocked = client.post(
        "/import",
        data={
            "title": "অবরুদ্ধ পরীক্ষা",
            "author": "রকিব হাসান",
            "pages": "all",
            "ocr_engine": "surya",
            "pdf": (BytesIO(b"not uploaded"), "blocked.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert dashboard.status_code == 200
    assert b"Surya unavailable" in dashboard.data
    assert b"Surya (unavailable)" in dashboard.data
    assert b"EasyOCR" in dashboard.data
    assert b"ocr-unavailable-dialog" in dashboard.data
    assert b"disabled" in dashboard.data
    assert blocked.status_code == 200
    assert b"Choose another model" in blocked.data
    assert list((tmp_path / "sources").glob("*.pdf")) == []


def test_failed_job_can_be_preserved_then_retry_same_engine(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "bangla_ocr.application.EngineRegistry.statuses",
        lambda self: {
            "surya": {"available": True, "reason": "ready"},
            "easyocr": {"available": True, "reason": "ready"},
            "tesseract": {"available": False, "reason": "missing"},
            "embedded": {"available": True, "reason": "ready"},
        },
    )
    monkeypatch.setattr(
        "bangla_ocr.application.probe_surya_hardware",
        lambda root: _runtime_report(),
    )
    monkeypatch.setattr(
        "bangla_ocr.application.decide_surya_backend",
        _runtime_decision,
    )
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf_bytes = BytesIO()
    writer.write(pdf_bytes)
    calls = []

    def fake_process(**kwargs):
        calls.append(kwargs)
        book_root = kwargs["resume_book_root"]
        _write_json(
            book_root / "book.json",
            {
                "book_id": book_root.name,
                "title": kwargs["title"],
                "author": kwargs["author"],
                "source_page_count": 1,
            },
        )
        if len(calls) == 1:
            raise RuntimeError("Surya stopped during page 1")
        return book_root

    app = create_application(
        output_root=tmp_path / "output",
        source_root=tmp_path / "sources",
        process_function=fake_process,
    )
    app.config["TESTING"] = True
    client = app.test_client()
    response = client.post(
        "/import",
        data={
            "title": "Recovery test",
            "author": "Tester",
            "pages": "all",
            "ocr_engine": "surya",
            "pdf": (BytesIO(pdf_bytes.getvalue()), "recovery.pdf"),
        },
        content_type="multipart/form-data",
    )
    job_id = response.headers["Location"].rsplit("/", 1)[-1]
    for _ in range(100):
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["status"] == "failed":
            break
        time.sleep(0.01)

    failed_page = client.get(f"/jobs/{job_id}")
    assert b"Retry Surya" in failed_page.data
    assert b"Continue with EasyOCR" in failed_page.data
    assert b"Stop and preserve" in failed_page.data
    assert b"Surya could not finish the current page" in failed_page.data
    assert b"Completed pages and failure evidence are preserved" in failed_page.data
    assert b"Surya stopped during page 1" in failed_page.data

    client.post(
        f"/jobs/{job_id}/recover",
        data={"action": "preserve"},
    )
    preserved = client.get(f"/api/jobs/{job_id}").get_json()
    assert preserved["status"] == "preserved"
    assert "preserved" in preserved["message"]

    client.post(
        f"/jobs/{job_id}/recover",
        data={"action": "retry_selected"},
    )
    for _ in range(100):
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["status"] in {"complete", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "complete"
    assert calls[0]["resume_book_root"] == calls[1]["resume_book_root"]
    assert calls[1]["requested_engines"] == ["surya", "embedded"]
    assert calls[1]["processing_reason"] == "retry_surya"


def test_failed_surya_job_can_explicitly_continue_with_easyocr(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "bangla_ocr.application.EngineRegistry.statuses",
        lambda self: {
            "surya": {"available": True, "reason": "ready"},
            "easyocr": {"available": True, "reason": "ready"},
            "tesseract": {"available": False, "reason": "missing"},
            "embedded": {"available": True, "reason": "ready"},
        },
    )
    monkeypatch.setattr(
        "bangla_ocr.application.probe_surya_hardware",
        lambda root: _runtime_report(),
    )
    monkeypatch.setattr(
        "bangla_ocr.application.decide_surya_backend",
        _runtime_decision,
    )
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf_bytes = BytesIO()
    writer.write(pdf_bytes)
    calls = []

    def fake_process(**kwargs):
        calls.append(kwargs)
        book_root = kwargs["resume_book_root"]
        _write_json(
            book_root / "book.json",
            {
                "book_id": book_root.name,
                "title": kwargs["title"],
                "author": kwargs["author"],
                "source_page_count": 1,
            },
        )
        if kwargs["requested_engines"][0] == "surya":
            raise RuntimeError("Surya stopped during page 1")
        return book_root

    app = create_application(
        output_root=tmp_path / "output",
        source_root=tmp_path / "sources",
        process_function=fake_process,
    )
    app.config["TESTING"] = True
    client = app.test_client()
    response = client.post(
        "/import",
        data={
            "title": "Engine switch test",
            "author": "Tester",
            "pages": "all",
            "ocr_engine": "surya",
            "pdf": (BytesIO(pdf_bytes.getvalue()), "switch.pdf"),
        },
        content_type="multipart/form-data",
    )
    job_id = response.headers["Location"].rsplit("/", 1)[-1]
    for _ in range(100):
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["status"] == "failed":
            break
        time.sleep(0.01)

    switched = client.post(
        f"/jobs/{job_id}/recover",
        data={
            "action": "switch_engine",
            "target_engine": "easyocr",
            "confirm_engine_switch": "yes",
        },
    )
    assert switched.status_code == 302
    for _ in range(100):
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["status"] in {"complete", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "complete"
    assert status["ocr_engine"] == "easyocr"
    assert calls[0]["resume_book_root"] == calls[1]["resume_book_root"]
    assert calls[1]["requested_engines"] == ["easyocr", "embedded"]
    assert calls[1]["processing_reason"] == "explicit_switch_surya_to_easyocr"
    recovery_log = (
        calls[1]["resume_book_root"] / "audit" / "job-recovery.jsonl"
    ).read_text(encoding="utf-8")
    assert '"from_engine": "surya"' in recovery_log
    assert '"to_engine": "easyocr"' in recovery_log


def test_failed_surya_job_can_explicitly_switch_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "bangla_ocr.application.EngineRegistry.statuses",
        lambda self: {
            "surya": {"available": True, "reason": "ready"},
            "easyocr": {"available": True, "reason": "ready"},
            "tesseract": {"available": False, "reason": "missing"},
            "embedded": {"available": True, "reason": "ready"},
        },
    )
    runtime_report = {
        "display_adapters": {
            "status": "ready",
            "adapters": [{"name": "NVIDIA GPU", "vendor": "nvidia"}],
        },
        "runtimes": {
            "cpu": {
                "backend": "cpu",
                "display_name": "CPU",
                "usable": True,
                "reason": "ready",
            },
            "cuda": {
                "backend": "cuda",
                "display_name": "CUDA",
                "usable": True,
                "reason": "ready",
            },
            "vulkan": {
                "backend": "vulkan",
                "display_name": "Vulkan",
                "usable": False,
                "reason": "not installed",
            },
        },
    }
    monkeypatch.setattr(
        "bangla_ocr.application.probe_surya_hardware",
        lambda root: runtime_report,
    )

    def decide(root, *, requested="auto", hardware_report=None):
        backend = "cuda" if requested == "auto" else requested
        definition = SURYA_RUNTIME_DEFINITIONS[backend]
        selection = SuryaRuntimeSelection(
            definition=definition,
            binary=definition.binary_path(root),
            source="bundled",
        )
        return SuryaBackendDecision(
            requested=requested,
            selected=selection,
            automatic=requested == "auto",
            cpu_fallback=False,
            reason=f"Selected {backend}",
            attempted_backends=(backend,),
            runtime_statuses=runtime_report["runtimes"],
        )

    monkeypatch.setattr("bangla_ocr.application.decide_surya_backend", decide)
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf_bytes = BytesIO()
    writer.write(pdf_bytes)
    calls = []

    def fake_process(**kwargs):
        calls.append(kwargs)
        book_root = kwargs["resume_book_root"]
        _write_json(
            book_root / "book.json",
            {
                "book_id": book_root.name,
                "title": kwargs["title"],
                "author": kwargs["author"],
                "source_page_count": 1,
            },
        )
        if len(calls) == 1:
            raise RuntimeError("CUDA stopped during page 1")
        return book_root

    app = create_application(
        output_root=tmp_path / "output",
        source_root=tmp_path / "sources",
        process_function=fake_process,
    )
    app.config["TESTING"] = True
    client = app.test_client()
    response = client.post(
        "/import",
        data={
            "title": "Runtime switch test",
            "author": "Tester",
            "pages": "all",
            "ocr_engine": "surya",
            "pdf": (BytesIO(pdf_bytes.getvalue()), "runtime-switch.pdf"),
        },
        content_type="multipart/form-data",
    )
    job_id = response.headers["Location"].rsplit("/", 1)[-1]
    for _ in range(100):
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["status"] == "failed":
            break
        time.sleep(0.01)

    failed_page = client.get(f"/jobs/{job_id}")
    assert b"Continue Surya with CPU" in failed_page.data
    assert b"Surya Vulkan" in failed_page.data
    assert b"not installed" in failed_page.data

    switched = client.post(
        f"/jobs/{job_id}/recover",
        data={
            "action": "switch_surya_backend",
            "target_backend": "cpu",
            "confirm_backend_switch": "yes",
        },
    )
    assert switched.status_code == 302
    for _ in range(100):
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["status"] in {"complete", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "complete"
    assert status["ocr_engine"] == "surya"
    assert status["surya_backend"] == "cpu"
    assert calls[1]["config"]["ocr"]["surya_runtime_backend"] == "cpu"
    assert calls[1]["processing_reason"] == (
        "explicit_backend_switch_cuda_to_cpu"
    )
    recovery_log = (
        calls[1]["resume_book_root"] / "audit" / "job-recovery.jsonl"
    ).read_text(encoding="utf-8")
    assert '"from_surya_backend": "cuda"' in recovery_log
    assert '"to_surya_backend": "cpu"' in recovery_log


def test_automatic_surya_verifies_first_page_then_uses_cpu(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "bangla_ocr.application.EngineRegistry.statuses",
        lambda self: {
            "surya": {"available": True, "reason": "ready"},
            "easyocr": {"available": True, "reason": "ready"},
            "tesseract": {"available": False, "reason": "missing"},
            "embedded": {"available": True, "reason": "ready"},
        },
    )
    monkeypatch.setattr(
        "bangla_ocr.application.probe_surya_hardware",
        lambda root: _runtime_report(),
    )
    monkeypatch.setattr(
        "bangla_ocr.application.decide_surya_backend",
        _runtime_decision,
    )
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf_bytes = BytesIO()
    writer.write(pdf_bytes)
    calls = []

    def fake_process(**kwargs):
        calls.append(kwargs)
        book_root = kwargs["resume_book_root"]
        _write_json(
            book_root / "book.json",
            {
                "book_id": book_root.name,
                "title": kwargs["title"],
                "author": kwargs["author"],
                "source_page_count": 1,
            },
        )
        if len(calls) == 1:
            raise RequiredEngineFailedError(
                "Page 1: Surya CUDA server stopped during inference"
            )
        _write_json(
            book_root / "audit" / "processing.json",
            {"all_processed_pages": [1]},
        )
        kwargs["progress"](
            "[1/1] Page 1: ready, surya "
            "| metrics page=1.000 average=1.000 eta=0.0"
        )
        return book_root

    app = create_application(
        output_root=tmp_path / "output",
        source_root=tmp_path / "sources",
        process_function=fake_process,
    )
    app.config["TESTING"] = True
    client = app.test_client()
    response = client.post(
        "/import",
        data={
            "title": "Automatic recovery test",
            "author": "Tester",
            "pages": "all",
            "ocr_engine": "surya",
            "surya_runtime": "auto",
            "pdf": (BytesIO(pdf_bytes.getvalue()), "automatic-recovery.pdf"),
        },
        content_type="multipart/form-data",
    )
    job_id = response.headers["Location"].rsplit("/", 1)[-1]
    for _ in range(200):
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["status"] in {"complete", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "complete"
    assert len(calls) == 2
    assert calls[0]["config"]["ocr"]["surya_runtime_backend"] == "cuda/CUDA0"
    assert calls[1]["config"]["ocr"]["surya_runtime_backend"] == "cpu"
    assert calls[1]["processing_reason"] == (
        "automatic_runtime_fallback_cuda_to_cpu"
    )
    assert status["surya_backend"] == "cpu"
    assert status["surya_backend_requested"] == "auto"
    assert [item["status"] for item in status["surya_runtime_attempts"]] == [
        "failed",
        "complete",
    ]
    assert status["surya_runtime_attempts"][0]["completed_page_count"] == 0
    recovery_log = (
        calls[1]["resume_book_root"] / "audit" / "runtime-recovery.jsonl"
    ).read_text(encoding="utf-8")
    assert '"action": "automatic_runtime_fallback"' in recovery_log
    assert '"to_request": "cpu"' in recovery_log
    assert '"action": "runtime_verified"' in recovery_log


def test_explicit_surya_runtime_failure_stops_without_automatic_fallback(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "bangla_ocr.application.EngineRegistry.statuses",
        lambda self: {
            "surya": {"available": True, "reason": "ready"},
            "easyocr": {"available": True, "reason": "ready"},
            "tesseract": {"available": False, "reason": "missing"},
            "embedded": {"available": True, "reason": "ready"},
        },
    )
    monkeypatch.setattr(
        "bangla_ocr.application.probe_surya_hardware",
        lambda root: _runtime_report(),
    )
    monkeypatch.setattr(
        "bangla_ocr.application.decide_surya_backend",
        _runtime_decision,
    )
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf_bytes = BytesIO()
    writer.write(pdf_bytes)
    calls = []

    def fake_process(**kwargs):
        calls.append(kwargs)
        raise RequiredEngineFailedError(
            "Page 1: Surya CUDA server stopped during inference"
        )

    app = create_application(
        output_root=tmp_path / "output",
        source_root=tmp_path / "sources",
        process_function=fake_process,
    )
    app.config["TESTING"] = True
    client = app.test_client()
    response = client.post(
        "/import",
        data={
            "title": "Explicit failure test",
            "author": "Tester",
            "pages": "all",
            "ocr_engine": "surya",
            "surya_runtime": "cuda/CUDA0",
            "pdf": (BytesIO(pdf_bytes.getvalue()), "explicit-failure.pdf"),
        },
        content_type="multipart/form-data",
    )
    job_id = response.headers["Location"].rsplit("/", 1)[-1]
    for _ in range(200):
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["status"] in {"complete", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "failed"
    assert len(calls) == 1
    assert status["surya_backend"] == "cuda"
    assert status["surya_backend_requested"] == "cuda/CUDA0"
    assert len(status["surya_runtime_attempts"]) == 1
    assert status["surya_runtime_attempts"][0]["status"] == "failed"
    failed_page = client.get(f"/jobs/{job_id}")
    assert b"Runtime verification" in failed_page.data
    assert b"CUDA0" in failed_page.data


def test_automatic_surya_stops_if_a_page_completed_before_runtime_failure(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "bangla_ocr.application.EngineRegistry.statuses",
        lambda self: {
            "surya": {"available": True, "reason": "ready"},
            "easyocr": {"available": True, "reason": "ready"},
            "tesseract": {"available": False, "reason": "missing"},
            "embedded": {"available": True, "reason": "ready"},
        },
    )
    monkeypatch.setattr(
        "bangla_ocr.application.probe_surya_hardware",
        lambda root: _runtime_report(),
    )
    monkeypatch.setattr(
        "bangla_ocr.application.decide_surya_backend",
        _runtime_decision,
    )
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    pdf_bytes = BytesIO()
    writer.write(pdf_bytes)
    calls = []

    def fake_process(**kwargs):
        calls.append(kwargs)
        book_root = kwargs["resume_book_root"]
        _write_json(
            book_root / "audit" / "processing.json",
            {"all_processed_pages": [1]},
        )
        raise RequiredEngineFailedError(
            "Page 2: Surya CUDA server stopped during inference"
        )

    app = create_application(
        output_root=tmp_path / "output",
        source_root=tmp_path / "sources",
        process_function=fake_process,
    )
    app.config["TESTING"] = True
    client = app.test_client()
    response = client.post(
        "/import",
        data={
            "title": "Mid-document failure test",
            "author": "Tester",
            "pages": "all",
            "ocr_engine": "surya",
            "surya_runtime": "auto",
            "pdf": (BytesIO(pdf_bytes.getvalue()), "mid-document.pdf"),
        },
        content_type="multipart/form-data",
    )
    job_id = response.headers["Location"].rsplit("/", 1)[-1]
    for _ in range(200):
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["status"] in {"complete", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "failed"
    assert status["completed_page_count"] == 1
    assert len(calls) == 1
    assert len(status["surya_runtime_attempts"]) == 1
    assert "At least one page completed" in status[
        "automatic_fallback_stop_reason"
    ]
    failed_page = client.get(f"/jobs/{job_id}")
    assert b"Runtime stayed on CUDA" in failed_page.data


def test_openrouter_profiles_are_isolated_per_browser_session(tmp_path):
    app = create_application(
        output_root=tmp_path / "output",
        source_root=tmp_path / "sources",
    )
    app.config["TESTING"] = True
    first = app.test_client()
    second = app.test_client()

    saved = first.post(
        "/settings/openrouter",
        data={
            "enabled": "on",
            "model": "provider/private-vision-model",
            "daily_request_limit": "7",
            "api_key": "sk-or-test-first-browser",
        },
        follow_redirects=True,
    )
    untouched = second.get("/settings/openrouter")

    assert saved.status_code == 200
    assert b"provider/private-vision-model" in saved.data
    assert b"Key loaded" in saved.data
    assert untouched.status_code == 200
    assert b"provider/private-vision-model" not in untouched.data
    assert b"Key loaded" not in untouched.data


def test_surya_runtime_settings_are_visible_and_persistent(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "bangla_ocr.application.probe_surya_hardware",
        lambda root: _runtime_report(),
    )
    monkeypatch.setattr(
        "bangla_ocr.application.decide_surya_backend",
        _runtime_decision,
    )
    output_root = tmp_path / "output"
    app = create_application(
        output_root=output_root,
        source_root=tmp_path / "sources",
    )
    app.config["TESTING"] = True
    client = app.test_client()

    page = client.get("/settings/openrouter")
    assert page.status_code == 200
    assert b"Test NVIDIA GPU" in page.data
    assert b"Model startup has not been tested" in page.data

    saved = client.post(
        "/settings/surya",
        data={"surya_backend": "cpu"},
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert b"Surya runtime preference saved" in saved.data
    settings = json.loads(
        (output_root / "_app" / "runtime-settings.json").read_text(
            encoding="utf-8"
        )
    )
    assert settings["surya_backend"] == "cpu"

    restarted = create_application(
        output_root=output_root,
        source_root=tmp_path / "sources-restarted",
    )
    restarted.config["TESTING"] = True
    restarted_page = restarted.test_client().get("/settings/openrouter")
    assert b'value="cpu" checked' in restarted_page.data
    assert b"CPU selected" in restarted_page.data


def test_exact_gpu_device_is_visible_and_persistent(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "bangla_ocr.application.probe_surya_hardware",
        lambda root: _runtime_report(),
    )
    monkeypatch.setattr(
        "bangla_ocr.application.decide_surya_backend",
        _runtime_decision,
    )
    output_root = tmp_path / "output"
    app = create_application(
        output_root=output_root,
        source_root=tmp_path / "sources",
    )
    app.config["TESTING"] = True

    response = app.test_client().post(
        "/settings/surya",
        data={"surya_runtime": "cuda/CUDA0"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b'value="cuda/CUDA0" checked' in response.data
    settings = json.loads(
        (output_root / "_app" / "runtime-settings.json").read_text(
            encoding="utf-8"
        )
    )
    assert settings["surya_backend"] == "cuda/CUDA0"


def test_unavailable_surya_runtime_cannot_be_saved(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "bangla_ocr.application.probe_surya_hardware",
        lambda root: _runtime_report(),
    )
    monkeypatch.setattr(
        "bangla_ocr.application.decide_surya_backend",
        _runtime_decision,
    )
    output_root = tmp_path / "output"
    app = create_application(
        output_root=output_root,
        source_root=tmp_path / "sources",
    )
    app.config["TESTING"] = True

    response = app.test_client().post(
        "/settings/surya",
        data={"surya_backend": "vulkan"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Runtime selection was not saved" in response.data
    settings_path = output_root / "_app" / "runtime-settings.json"
    assert not settings_path.exists()


def test_surya_model_startup_validation_runs_in_background(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "bangla_ocr.application.probe_surya_hardware",
        lambda root: _runtime_report(),
    )
    monkeypatch.setattr(
        "bangla_ocr.application.decide_surya_backend",
        _runtime_decision,
    )
    monkeypatch.setattr(
        "bangla_ocr.application.SuryaEngine.validate_startup",
        lambda self: {
            "status": "ready",
            "checked_utc": "2026-08-30T10:01:00+00:00",
            "duration_seconds": 2.5,
            "backend": "cuda",
            "server_healthy": True,
        },
    )
    output_root = tmp_path / "output"
    app = create_application(
        output_root=output_root,
        source_root=tmp_path / "sources",
    )
    app.config["TESTING"] = True
    client = app.test_client()

    started = client.post("/settings/surya/validate")
    assert started.status_code == 302
    for _ in range(100):
        result = client.get("/api/settings/surya-validation").get_json()
        if result["status"] != "running":
            break
        time.sleep(0.01)

    assert result["status"] == "ready"
    assert result["backend"] == "cuda"
    assert result["server_healthy"] is True
    page = client.get("/settings/openrouter")
    assert b"Model startup passed on CUDA" in page.data
    settings = json.loads(
        (output_root / "_app" / "runtime-settings.json").read_text(
            encoding="utf-8"
        )
    )
    assert settings["startup_validation"]["status"] == "ready"


def test_new_surya_job_uses_saved_runtime_preference(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "bangla_ocr.application.probe_surya_hardware",
        lambda root: _runtime_report(),
    )
    monkeypatch.setattr(
        "bangla_ocr.application.decide_surya_backend",
        _runtime_decision,
    )
    monkeypatch.setattr(
        "bangla_ocr.application.EngineRegistry.statuses",
        lambda self: {
            "surya": {"available": True, "reason": "CPU ready"},
            "easyocr": {"available": True, "reason": "ready"},
            "tesseract": {"available": False, "reason": "missing"},
            "embedded": {"available": True, "reason": "ready"},
        },
    )
    output_root = tmp_path / "output"
    _write_json(
        output_root / "_app" / "runtime-settings.json",
        {"surya_backend": "cpu", "startup_validation": {"status": "not_run"}},
    )
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf_bytes = BytesIO()
    writer.write(pdf_bytes)
    calls = []

    def fake_process(**kwargs):
        calls.append(kwargs)
        book_root = kwargs["resume_book_root"]
        _write_json(
            book_root / "book.json",
            {
                "book_id": book_root.name,
                "title": kwargs["title"],
                "author": kwargs["author"],
                "source_page_count": 1,
            },
        )
        return book_root

    app = create_application(
        output_root=output_root,
        source_root=tmp_path / "sources",
        process_function=fake_process,
    )
    app.config["TESTING"] = True
    client = app.test_client()
    response = client.post(
        "/import",
        data={
            "title": "CPU selection test",
            "author": "Tester",
            "pages": "all",
            "ocr_engine": "surya",
            "pdf": (BytesIO(pdf_bytes.getvalue()), "cpu-selection.pdf"),
        },
        content_type="multipart/form-data",
    )
    job_id = response.headers["Location"].rsplit("/", 1)[-1]
    for _ in range(100):
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["status"] == "complete":
            break
        time.sleep(0.01)

    assert status["surya_backend_requested"] == "cpu"
    assert status["surya_backend"] == "cpu"
    assert calls[0]["config"]["ocr"]["surya_runtime_backend"] == "cpu"
