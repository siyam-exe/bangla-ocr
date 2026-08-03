import json

from PIL import Image
from pypdf import PdfWriter
import pytest

from bangla_ocr.config import load_config
from bangla_ocr.models import OCRCandidate
from bangla_ocr.processor import (
    RequiredEngineFailedError,
    _run_engine,
    process_book,
)


class _RecoverableEngine:
    name = "surya"
    supports_recovery = True

    def __init__(self, failures: int, recovery_fails: bool = False):
        self.failures = failures
        self.recovery_fails = recovery_fails
        self.calls = 0
        self.recoveries = 0

    def recognize(self, image, variant, *, embedded_text=""):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(f"synthetic engine failure {self.calls}")
        return OCRCandidate(
            engine=self.name,
            variant=variant,
            text="পরীক্ষার সফল পাঠ্য",
            confidence=0.99,
            raw=[{"text": "পরীক্ষার সফল পাঠ্য"}],
        )

    def retry_reason(self, candidate):
        return None

    def recovery_diagnostics(self):
        return {"calls": self.calls, "healthy": False}

    def recover_after_failure(self):
        self.recoveries += 1
        if self.recovery_fails:
            raise RuntimeError("synthetic recovery failure")
        return {"recovered": True}


def test_recoverable_engine_retries_once_and_preserves_attempt_evidence(tmp_path):
    engine = _RecoverableEngine(failures=1)
    messages = []

    result = _run_engine(
        engine,
        Image.new("RGB", (20, 20), "white"),
        "original",
        "",
        tmp_path,
        progress=messages.append,
        progress_prefix="[1/1] Page 1",
    )

    assert result.candidate is not None
    assert result.candidate.text == "পরীক্ষার সফল পাঠ্য"
    assert result.errors == []
    assert [attempt["status"] for attempt in result.attempts] == [
        "failed",
        "succeeded",
    ]
    assert engine.calls == 2
    assert engine.recoveries == 1
    assert "retrying this page once" in messages[0]
    failure = json.loads(
        (tmp_path / "surya-original-attempt-1-failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["diagnostics"]["calls"] == 1
    assert "synthetic engine failure" in failure["traceback"]


def test_recoverable_engine_stops_after_one_retry(tmp_path):
    engine = _RecoverableEngine(failures=2)

    result = _run_engine(
        engine,
        Image.new("RGB", (20, 20), "white"),
        "original",
        "",
        tmp_path,
    )

    assert result.candidate is None
    assert len(result.attempts) == 2
    assert len(result.errors) == 1
    assert result.errors[0]["attempt"] == 2
    assert engine.calls == 2
    assert engine.recoveries == 1


def test_failed_recovery_does_not_enter_a_retry_loop(tmp_path):
    engine = _RecoverableEngine(failures=2, recovery_fails=True)

    result = _run_engine(
        engine,
        Image.new("RGB", (20, 20), "white"),
        "original",
        "",
        tmp_path,
    )

    assert result.candidate is None
    assert len(result.attempts) == 1
    assert result.errors[0]["recovery_error"] == "synthetic recovery failure"
    assert engine.calls == 1
    assert engine.recoveries == 1


def test_required_engine_failure_writes_resumable_failed_page(
    monkeypatch, tmp_path
):
    source_pdf = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with source_pdf.open("wb") as stream:
        writer.write(stream)

    class FailingPrimary:
        name = "surya"
        supports_recovery = False
        supports_preprocessed_variant = False

        def recognize(self, image, variant, *, embedded_text=""):
            raise RuntimeError("synthetic terminal OCR failure")

    primary = FailingPrimary()

    class FakeRegistry:
        def __init__(self, config, working_root):
            pass

        def required_primary(self, requested):
            return "surya", {
                "surya": {"available": True, "reason": "test engine"}
            }

        def available(self, requested, statuses=None):
            return [primary]

    monkeypatch.setattr("bangla_ocr.processor.EngineRegistry", FakeRegistry)

    with pytest.raises(RequiredEngineFailedError, match="No fallback"):
        process_book(
            source_pdf=source_pdf,
            title="Failure test",
            author="Tester",
            output_root=tmp_path / "output",
            page_indexes=[0],
            config=load_config(),
            requested_engines=["surya", "embedded"],
            progress=lambda message: None,
        )

    book_root = next((tmp_path / "output").iterdir())
    manifest = json.loads((book_root / "book.json").read_text(encoding="utf-8"))
    page = json.loads(
        (book_root / "pages" / "0001" / "page.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["processed_pages"] == []
    assert manifest["requested_pages"] == [1]
    assert manifest["ocr_plan"]["automatic_fallback_enabled"] is False
    assert page["processing_complete"] is False
    assert page["workflow"]["overall"] == "failed"
    assert page["decision"]["selected_engine"] is None
    assert len(page["ocr_attempts"]) == 1


def test_resume_skips_pages_already_completed(monkeypatch, tmp_path):
    source_pdf = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with source_pdf.open("wb") as stream:
        writer.write(stream)

    class SuccessfulPrimary:
        name = "surya"
        supports_recovery = False
        supports_preprocessed_variant = False

        def __init__(self):
            self.calls = 0

        def recognize(self, image, variant, *, embedded_text=""):
            self.calls += 1
            return OCRCandidate(
                engine=self.name,
                variant=variant,
                text="একটি সম্পূর্ণ পরীক্ষার পৃষ্ঠা।",
                confidence=0.99,
            )

        def retry_reason(self, candidate):
            return None

    primary = SuccessfulPrimary()

    class FakeRegistry:
        def __init__(self, config, working_root):
            pass

        def required_primary(self, requested):
            return "surya", {
                "surya": {"available": True, "reason": "test engine"}
            }

        def available(self, requested, statuses=None):
            return [primary]

    monkeypatch.setattr("bangla_ocr.processor.EngineRegistry", FakeRegistry)
    kwargs = {
        "source_pdf": source_pdf,
        "title": "Resume test",
        "author": "Tester",
        "output_root": tmp_path / "output",
        "page_indexes": [0],
        "config": load_config(),
        "requested_engines": ["surya", "embedded"],
        "progress": lambda message: None,
    }

    book_root = process_book(**kwargs)
    assert primary.calls == 1
    first_page = json.loads(
        (book_root / "pages" / "0001" / "page.json").read_text(
            encoding="utf-8"
        )
    )
    assert first_page["timings"]["render_seconds"] >= 0
    assert first_page["timings"]["preprocessing_seconds"] >= 0
    assert first_page["timings"]["image_encoding_seconds"] >= 0
    assert first_page["timings"]["ocr_seconds"] >= 0
    assert first_page["timings"]["page_save_seconds"] >= 0
    processing = json.loads(
        (book_root / "audit" / "processing.json").read_text(encoding="utf-8")
    )
    assert processing["runs"][-1]["prefetch_pages"] == 1
    assert processing["runs"][-1]["timing"]["pages_completed"] == 1
    resumed_root = process_book(**kwargs)

    assert resumed_root == book_root
    assert primary.calls == 1
    manifest = json.loads((book_root / "book.json").read_text(encoding="utf-8"))
    assert manifest["processed_pages"] == [1]


def test_next_page_preparation_starts_before_current_page_ocr(
    monkeypatch, tmp_path
):
    source_pdf = tmp_path / "two-pages.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    with source_pdf.open("wb") as stream:
        writer.write(stream)
    events = []

    class SuccessfulPrimary:
        name = "surya"
        supports_recovery = False
        supports_preprocessed_variant = False

        def __init__(self):
            self.calls = 0

        def recognize(self, image, variant, *, embedded_text=""):
            self.calls += 1
            events.append(f"ocr-{self.calls}")
            return OCRCandidate(
                engine=self.name,
                variant=variant,
                text=f"পরীক্ষার পৃষ্ঠা {self.calls}",
                confidence=0.99,
            )

        def retry_reason(self, candidate):
            return None

    primary = SuccessfulPrimary()

    class FakeRegistry:
        def __init__(self, config, working_root):
            pass

        def required_primary(self, requested):
            return "surya", {
                "surya": {"available": True, "reason": "test engine"}
            }

        def available(self, requested, statuses=None):
            return [primary]

    from bangla_ocr import processor as processor_module

    original_preparation = processor_module.PagePreparation

    class TrackingPreparation(original_preparation):
        def __init__(self, source_pdf, page_index, page_root, config):
            events.append(f"prepare-{page_index + 1}")
            super().__init__(source_pdf, page_index, page_root, config)

    monkeypatch.setattr("bangla_ocr.processor.EngineRegistry", FakeRegistry)
    monkeypatch.setattr(
        "bangla_ocr.processor.PagePreparation", TrackingPreparation
    )
    config = load_config()
    config["render"]["dpi"] = 72

    process_book(
        source_pdf=source_pdf,
        title="Prefetch test",
        author="Tester",
        output_root=tmp_path / "output",
        page_indexes=[0, 1],
        config=config,
        requested_engines=["surya", "embedded"],
        progress=lambda message: None,
    )

    assert events.index("prepare-2") < events.index("ocr-1")
    assert primary.calls == 2
