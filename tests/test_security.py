from io import BytesIO

import pytest

from bangla_ocr.application import create_application
from bangla_ocr.cli import _validate_bind_host


def test_non_loopback_bind_requires_explicit_opt_in():
    _validate_bind_host("127.0.0.1", False)
    _validate_bind_host("localhost", False)
    _validate_bind_host("0.0.0.0", True)

    with pytest.raises(ValueError, match="Refusing to expose"):
        _validate_bind_host("0.0.0.0", False)


def test_invalid_pdf_upload_does_not_leave_source_copy(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    source_root = tmp_path / "sources"
    class DummyRegistry:
        def __init__(self, *args, **kwargs):
            pass

        def statuses(self):
            return {
                "surya": {"available": True, "reason": "ready"},
                "easyocr": {"available": False, "reason": "not installed"},
                "tesseract": {"available": False, "reason": "not installed"},
                "embedded": {"available": True, "reason": "ready"},
            }

    monkeypatch.setattr("bangla_ocr.application.EngineRegistry", DummyRegistry)
    monkeypatch.setattr(
        "bangla_ocr.application.storage_preflight",
        lambda *args, **kwargs: {
            "ready": True,
            "warnings": [],
            "errors": [],
            "snapshot": {
                "disks": {
                    "system": {"free": "100 GiB", "free_bytes": 100 * 1024**3},
                    "output": {"free": "100 GiB", "free_bytes": 100 * 1024**3},
                },
                "memory": {
                    "physical_available": "8 GiB",
                    "commit_used": "4 GiB",
                },
            },
            "estimated_workspace_bytes": 0,
            "estimated_workspace": "0 B",
        },
    )
    app = create_application(output_root=output_root, source_root=source_root)
    app.config.update(TESTING=True)

    response = app.test_client().post(
        "/import",
        data={
            "title": "Invalid upload",
            "author": "Test Author",
            "ocr_engine": "surya",
            "page_mode": "all",
            "pdf": (BytesIO(b"not a PDF"), "broken.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Cannot open the PDF" in response.data
    assert list(source_root.iterdir()) == []
