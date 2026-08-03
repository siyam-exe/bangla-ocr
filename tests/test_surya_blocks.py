import os
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from bangla_ocr.engines.registry import (
    EngineRegistry,
    RequiredEngineUnavailableError,
)
from bangla_ocr.engines.surya_engine import SuryaEngine
from bangla_ocr.engines.surya_engine import (
    _html_to_text,
    strip_merged_running_footer,
    uploader_marker,
)
from bangla_ocr.utils import nfc


def test_html_to_text_preserves_paragraph_boundaries():
    expected = nfc("প্রথম অনুচ্ছেদ।\n\nদ্বিতীয় অনুচ্ছেদ।")
    assert _html_to_text(
        "<p>প্রথম অনুচ্ছেদ।</p><p>দ্বিতীয় অনুচ্ছেদ।</p>"
    ) == expected


def test_uploader_marker_detection_is_case_insensitive():
    assert uploader_marker("সাত Bangla Book's Direct Link") == "bangla book"


def test_normal_story_text_is_not_an_uploader_marker():
    assert uploader_marker("কিশোর দরজার দিকে এগিয়ে গেল।") is None


def test_bundled_surya_runtime_is_discovered(monkeypatch, tmp_path):
    binary = tmp_path / "tools" / "llama.cpp-cuda" / "llama-server.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"test")
    monkeypatch.delenv("LLAMA_CPP_BINARY", raising=False)
    monkeypatch.delenv("LLAMA_CPP_NGL", raising=False)
    monkeypatch.delenv("LLAMA_CPP_EXTRA_ARGS", raising=False)
    monkeypatch.delenv("SURYA_INFERENCE_URL", raising=False)
    engine = SuryaEngine({}, tmp_path)
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_engine.importlib.util.find_spec",
        lambda name: object(),
    )

    available, reason = engine.available()

    assert available is True
    assert "bundled CUDA" in reason
    assert engine.working_root.as_posix() in Path(
        engine._configure_bundled_runtime()
    ).as_posix()


def test_bundled_cpu_runtime_sets_zero_gpu_layers(monkeypatch, tmp_path):
    binary = tmp_path / "tools" / "llama.cpp-cpu" / "llama-server.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"test")
    for name in (
        "LLAMA_CPP_BINARY",
        "LLAMA_CPP_NGL",
        "LLAMA_CPP_EXTRA_ARGS",
        "SURYA_INFERENCE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    engine = SuryaEngine({}, tmp_path)
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_engine.importlib.util.find_spec",
        lambda name: object(),
    )

    available, reason = engine.available()

    assert available is True
    assert "bundled CPU" in reason
    assert engine._configure_bundled_runtime() == str(binary)
    assert os.environ["LLAMA_CPP_NGL"] == "0"


def test_required_primary_engine_cannot_silently_fallback(monkeypatch, tmp_path):
    registry = EngineRegistry(
        {
            "engine_order": ["surya", "easyocr", "embedded"],
            "preferred_primary_engine": "surya",
        },
        tmp_path,
    )
    monkeypatch.setattr(
        registry,
        "statuses",
        lambda: {
            "surya": {"available": False, "reason": "server missing"},
            "easyocr": {"available": True, "reason": "ready"},
            "tesseract": {"available": False, "reason": "missing"},
            "embedded": {"available": True, "reason": "ready"},
        },
    )

    with pytest.raises(RequiredEngineUnavailableError, match="fallback is disabled"):
        registry.required_primary()


def test_default_engine_plan_is_primary_plus_embedded_only(monkeypatch, tmp_path):
    registry = EngineRegistry(
        {
            "engine_order": ["surya", "easyocr", "tesseract", "embedded"],
            "preferred_primary_engine": "surya",
        },
        tmp_path,
    )
    for engine in registry._engines.values():
        monkeypatch.setattr(engine, "available", lambda: (True, "ready"))

    assert [engine.name for engine in registry.available()] == [
        "surya",
        "embedded",
    ]


def test_cached_engine_statuses_avoid_repeated_availability_probes(
    monkeypatch, tmp_path
):
    registry = EngineRegistry(
        {
            "engine_order": ["surya", "easyocr", "embedded"],
            "preferred_primary_engine": "surya",
        },
        tmp_path,
    )
    calls = {name: 0 for name in registry._engines}
    for name, engine in registry._engines.items():
        monkeypatch.setattr(
            engine,
            "available",
            lambda name=name: (
                calls.__setitem__(name, calls[name] + 1) or True,
                "ready",
            ),
        )

    primary, statuses = registry.required_primary()
    engines = registry.available(statuses=statuses)

    assert primary == "surya"
    assert [engine.name for engine in engines] == ["surya", "embedded"]
    assert all(count == 1 for count in calls.values())


def test_explicit_engine_plan_is_preserved_without_duplicates(monkeypatch, tmp_path):
    registry = EngineRegistry(
        {
            "engine_order": ["surya", "easyocr", "embedded"],
            "preferred_primary_engine": "surya",
        },
        tmp_path,
    )
    for engine in registry._engines.values():
        monkeypatch.setattr(engine, "available", lambda: (True, "ready"))

    assert [
        engine.name
        for engine in registry.available(["easyocr", "embedded", "easyocr"])
    ] == ["easyocr", "embedded"]


def test_surya_recovery_refuses_to_stop_an_unrelated_process(
    monkeypatch, tmp_path
):
    engine = SuryaEngine({}, tmp_path)
    monkeypatch.setattr(
        SuryaEngine,
        "_process_name",
        classmethod(lambda cls, pid: "notepad.exe"),
    )

    with pytest.raises(RuntimeError, match="Refusing to stop"):
        engine._stop_managed_server(1234)


def test_collection_specific_running_footer_is_not_removed():
    text = "রবিন, প্রতি-তিন গোয়েন্দা"
    cleaned, reason = strip_merged_running_footer(
        text, [100, 1200, 1000, 1420], 1500
    )
    assert cleaned == text
    assert reason is None


def test_recognizer_excludes_watermark_and_footer_but_keeps_raw_evidence():
    blocks = [
        SimpleNamespace(
            html="<p>কিশোর দরজার দিকে এগিয়ে গেল।</p>",
            bbox=[10, 10, 300, 50],
            confidence=0.98,
            label="Text",
            skipped=False,
            error=False,
        ),
        SimpleNamespace(
            html="<p>Bangla Book's Direct Link</p>",
            bbox=[10, 60, 300, 90],
            confidence=0.99,
            label="SectionHeader",
            skipped=False,
            error=False,
        ),
        SimpleNamespace(
            html="<p>ভলিউম-২৪</p>",
            bbox=[200, 500, 300, 530],
            confidence=0.99,
            label="PageFooter",
            skipped=False,
            error=False,
        ),
    ]

    class FakePredictor:
        def __call__(self, images):
            return [SimpleNamespace(blocks=blocks)]

    engine = SuryaEngine({}, Path("."))
    engine._predictor = FakePredictor()
    result = engine.recognize(Image.new("RGB", (320, 540), "white"), "original")

    assert result.text == "কিশোর দরজার দিকে এগিয়ে গেল।"
    assert result.diagnostics["excluded_uploader_blocks"] == 1
    assert len(result.raw) == 3
    assert result.raw[1]["included_in_reader_text"] is False
    assert "uploader/watermark marker" in result.raw[1]["exclusion_reason"]
    assert result.raw[2]["included_in_reader_text"] is False


def test_multiscale_crop_uses_bounded_block_recognition(monkeypatch):
    pytest.importorskip("surya")
    blocks = [
        SimpleNamespace(
            html="<p>উচ্চ রেজোলিউশনের পাঠ্য।</p>",
            bbox=[0, 0, 400, 80],
            confidence=0.97,
            label="Text",
            skipped=False,
            error=False,
        )
    ]
    recorded = {}

    class FakePredictor:
        def __call__(self, images, layouts, *, full_page):
            recorded["layout"] = layouts[0]
            recorded["full_page"] = full_page
            return [SimpleNamespace(blocks=blocks)]

    engine = SuryaEngine({}, Path("."))
    engine._predictor = FakePredictor()
    result = engine.recognize(
        Image.new("RGB", (400, 80), "white"),
        "multiscale-r01-400dpi",
        embedded_text="মূল পাঠ্য।",
    )

    assert recorded["full_page"] is False
    assert recorded["layout"].bboxes[0].count == 50
    assert result.diagnostics["recognition_mode"] == "bounded_block"
    assert result.text == "উচ্চ রেজোলিউশনের পাঠ্য।"
