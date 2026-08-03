import json

from PIL import Image
from pypdf import PdfWriter

from bangla_ocr.config import load_config
from bangla_ocr.models import OCRCandidate, OCRLine
from bangla_ocr.multiscale import (
    best_crop_reading,
    compare_readings,
    map_and_pad_bbox,
    map_bbox_to_original,
    select_regions,
)
from bangla_ocr.processor import process_book
from bangla_ocr.validation import validate_book


def test_region_selection_is_bounded_and_uses_mechanical_signals():
    image = Image.new("RGB", (1000, 1400), "white")
    candidate = OCRCandidate(
        engine="surya",
        variant="original",
        text="page",
        lines=[
            OCRLine("স্বাভাবিক বড় অনুচ্ছেদ।", [100, 100, 900, 300], 0.99),
            OCRLine("ছোট মুদ্রিত লাইন।", [100, 400, 900, 440], 0.99),
            OCRLine("ভুল ভুল শব্দ।", [100, 500, 900, 620], 0.99),
            OCRLine("কম আস্থার পাঠ্য।", [100, 700, 900, 820], 0.70),
        ],
    )
    config = {
        "enabled": True,
        "maximum_regions_per_page": 2,
        "minimum_text_characters": 8,
        "small_region_height_ratio": 0.04,
        "minimum_region_confidence": 0.94,
        "minimum_region_contrast_stddev": 0,
        "minimum_region_sharpness": 0,
    }

    regions = select_regions(candidate, image, config)

    assert len(regions) == 2
    assert regions[0].line_index == 2
    assert "repeated token" in regions[0].reasons
    assert regions[1].line_index == 3
    assert any("low Surya confidence" in reason for reason in regions[1].reasons)


def test_crop_comparison_preserves_literal_readings_without_correction():
    candidate = OCRCandidate(
        engine="surya",
        variant="crop",
        text="পাসার সেল্ভেজ ইয়ার্ড",
        lines=[
            OCRLine("পাসার সেল্ভেজ ইয়ার্ড", [0, 0, 100, 20], 0.99),
            OCRLine("unrelated", [0, 30, 100, 50], 0.99),
        ],
    )

    reading = best_crop_reading(candidate, "পাশা সেল্ভেজ ইয়ার্ড")
    comparison = compare_readings(
        "পাশা সেল্ভেজ ইয়ার্ড", reading, minimum_disagreement=0.01
    )

    assert reading == "পাসার সেল্ভেজ ইয়ার্ড"
    assert comparison["has_reviewable_alternative"] is True


def test_bbox_mapping_scales_and_pads_within_target():
    mapped = map_and_pad_bbox(
        [100, 200, 300, 260],
        (1000, 1400),
        (2000, 2800),
        0.1,
    )

    assert mapped == [160, 393, 640, 527]


def test_cropped_bbox_maps_back_to_original_render():
    mapped = map_bbox_to_original(
        [10, 20, 110, 60],
        (800, 1100),
        (1000, 1400),
        [{"name": "conservative_crop", "bbox": [100, 150, 900, 1250]}],
    )

    assert mapped == [110.0, 170.0, 210.0, 210.0]


def test_processing_keeps_full_page_surya_text_and_only_proposes_crop(
    monkeypatch, tmp_path
):
    source_pdf = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with source_pdf.open("wb") as stream:
        writer.write(stream)

    class MultiscalePrimary:
        name = "surya"
        supports_recovery = False
        supports_preprocessed_variant = False

        def __init__(self):
            self.calls = 0

        def recognize(self, image, variant, *, embedded_text=""):
            self.calls += 1
            if self.calls == 1:
                return OCRCandidate(
                    engine="surya",
                    variant=variant,
                    text="মূল পূর্ণ পাতার পাঠ্য।",
                    lines=[
                        OCRLine(
                            "মূল পূর্ণ পাতার পাঠ্য।",
                            [5, 5, image.width - 5, 30],
                            0.99,
                        )
                    ],
                    confidence=0.99,
                    raw=[],
                )
            return OCRCandidate(
                engine="surya",
                variant=variant,
                text="বিকল্প উচ্চ রেজোলিউশনের পাঠ্য।",
                lines=[
                    OCRLine(
                        "বিকল্প উচ্চ রেজোলিউশনের পাঠ্য।",
                        [0, 0, image.width, image.height],
                        0.99,
                    )
                ],
                confidence=0.99,
                raw=[],
            )

        def retry_reason(self, candidate):
            return None

    primary = MultiscalePrimary()

    class FakeRegistry:
        def __init__(self, config, working_root):
            pass

        def required_primary(self, requested):
            return "surya", {"surya": {"available": True, "reason": "test"}}

        def available(self, requested, statuses=None):
            return [primary]

    monkeypatch.setattr("bangla_ocr.processor.EngineRegistry", FakeRegistry)
    config = load_config()
    config["render"]["dpi"] = 72
    config["multiscale"].update(
        {
            "high_resolution_dpi": 100,
            "maximum_regions_per_page": 1,
            "small_region_height_ratio": 1.0,
            "minimum_region_contrast_stddev": 0,
            "minimum_region_sharpness": 0,
        }
    )

    book_root = process_book(
        source_pdf=source_pdf,
        title="Multiscale test",
        author="Tester",
        output_root=tmp_path / "output",
        page_indexes=[0],
        config=config,
        requested_engines=["surya"],
        progress=lambda _message: None,
    )

    page_root = book_root / "pages" / "0001"
    page = json.loads((page_root / "page.json").read_text(encoding="utf-8"))
    assert primary.calls == 2
    assert (page_root / "draft.txt").read_text(encoding="utf-8") == (
        "মূল পূর্ণ পাতার পাঠ্য।"
    )
    assert not (page_root / "final.txt").exists()
    assert page["multiscale"]["policy"] == "human_choice_only"
    assert page["multiscale"]["automatic_correction"] is False
    assert page["multiscale"]["regions"][0]["status"] == "pending"
    assert page["multiscale"]["regions"][0]["alternative"] == (
        "বিকল্প উচ্চ রেজোলিউশনের পাঠ্য।"
    )
    report = validate_book(book_root)
    assert "pending_high_resolution_alternatives" in {
        error["code"] for error in report["errors"]
    }
