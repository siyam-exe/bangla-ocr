import os
import sys
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
from bangla_ocr.engines.surya_policy import SuryaBackendDecision
from bangla_ocr.engines.surya_runtime import (
    SURYA_RUNTIME_DEFINITIONS,
    SuryaRuntimeSelection,
    definition_for_binary,
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
    monkeypatch.delenv("BANGLA_OCR_MANAGED_LLAMA_CPP", raising=False)
    monkeypatch.delenv("BANGLA_OCR_SURYA_RUNTIME", raising=False)
    engine = SuryaEngine({}, tmp_path)
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_engine.importlib.util.find_spec",
        lambda name: object(),
    )
    selection = SuryaRuntimeSelection(
        definition=SURYA_RUNTIME_DEFINITIONS["cuda"],
        binary=binary,
        source="bundled",
    )
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_engine.decide_surya_backend",
        lambda *args, **kwargs: SuryaBackendDecision(
            requested="auto",
            selected=selection,
            automatic=True,
            cpu_fallback=False,
            reason="Automatic selection chose the CUDA runtime",
            attempted_backends=("cuda",),
            runtime_statuses={},
        ),
    )

    available, reason = engine.available()

    assert available is True
    assert "CUDA runtime" in reason
    configured = engine._configure_runtime()
    assert configured is not None
    assert configured.backend == "cuda"
    assert engine.working_root.as_posix() in configured.binary.as_posix()


def test_bundled_cpu_runtime_sets_zero_gpu_layers(monkeypatch, tmp_path):
    binary = tmp_path / "tools" / "llama.cpp-cpu" / "llama-server.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"test")
    for name in (
        "LLAMA_CPP_BINARY",
        "LLAMA_CPP_NGL",
        "LLAMA_CPP_EXTRA_ARGS",
        "SURYA_INFERENCE_URL",
        "BANGLA_OCR_MANAGED_LLAMA_CPP",
        "BANGLA_OCR_SURYA_RUNTIME",
    ):
        monkeypatch.delenv(name, raising=False)
    engine = SuryaEngine({}, tmp_path)
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_engine.importlib.util.find_spec",
        lambda name: object(),
    )
    selection = SuryaRuntimeSelection(
        definition=SURYA_RUNTIME_DEFINITIONS["cpu"],
        binary=binary,
        source="bundled",
    )
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_engine.decide_surya_backend",
        lambda *args, **kwargs: SuryaBackendDecision(
            requested="auto",
            selected=selection,
            automatic=True,
            cpu_fallback=True,
            reason="No GPU runtime passed. Surya will use CPU.",
            attempted_backends=("cpu",),
            runtime_statuses={},
        ),
    )

    available, reason = engine.available()

    assert available is True
    assert "use CPU" in reason
    configured = engine._configure_runtime()
    assert configured is not None
    assert configured.binary == binary
    assert configured.backend == "cpu"
    engine._activate_runtime()
    assert os.environ["LLAMA_CPP_NGL"] == "0"


def test_runtime_catalog_defines_cpu_cuda_and_vulkan():
    assert set(SURYA_RUNTIME_DEFINITIONS) == {"cpu", "cuda", "vulkan"}
    assert SURYA_RUNTIME_DEFINITIONS["cpu"].accelerated is False
    assert SURYA_RUNTIME_DEFINITIONS["cpu"].gpu_layers == 0
    assert SURYA_RUNTIME_DEFINITIONS["cuda"].accelerated is True
    assert SURYA_RUNTIME_DEFINITIONS["vulkan"].accelerated is True
    assert SURYA_RUNTIME_DEFINITIONS["vulkan"].gpu_layers == 99


def test_vulkan_runtime_definition_matches_its_bundled_path(tmp_path):
    vulkan = SURYA_RUNTIME_DEFINITIONS["vulkan"].binary_path(tmp_path)
    vulkan.parent.mkdir(parents=True)
    vulkan.write_bytes(b"test")

    assert definition_for_binary(vulkan).key == "vulkan"


def test_runtime_change_resets_the_previous_managed_server(monkeypatch, tmp_path):
    cpu = SURYA_RUNTIME_DEFINITIONS["cpu"].binary_path(tmp_path)
    cpu.parent.mkdir(parents=True)
    cpu.write_bytes(b"cpu")
    selection = SuryaRuntimeSelection(
        definition=SURYA_RUNTIME_DEFINITIONS["cpu"],
        binary=cpu,
        source="bundled",
    )
    monkeypatch.setenv("BANGLA_OCR_MANAGED_LLAMA_CPP", "1")
    monkeypatch.setenv("BANGLA_OCR_SURYA_RUNTIME", "cuda")
    monkeypatch.setenv("LLAMA_CPP_BINARY", "old-cuda-server.exe")
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_engine.importlib.util.find_spec",
        lambda name: object(),
    )
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_engine.decide_surya_backend",
        lambda *args, **kwargs: SuryaBackendDecision(
            requested="cpu",
            selected=selection,
            automatic=False,
            cpu_fallback=False,
            reason="The requested CPU runtime is ready",
            attempted_backends=("cpu",),
            runtime_statuses={},
        ),
    )
    engine = SuryaEngine({"surya_runtime_backend": "cpu"}, tmp_path)
    recovery_calls = []
    monkeypatch.setattr(
        engine,
        "recover_after_failure",
        lambda: recovery_calls.append("reset") or {"recovered": True},
    )

    available, _ = engine.available()

    assert available is True
    assert recovery_calls == []
    engine._activate_runtime()
    assert recovery_calls == ["reset"]
    assert os.environ["BANGLA_OCR_SURYA_RUNTIME"] == "cpu"
    assert os.environ["LLAMA_CPP_NGL"] == "0"


def test_new_process_resets_a_managed_server_before_selecting_runtime(
    monkeypatch, tmp_path
):
    cpu = SURYA_RUNTIME_DEFINITIONS["cpu"].binary_path(tmp_path)
    cpu.parent.mkdir(parents=True)
    cpu.write_bytes(b"cpu")
    selection = SuryaRuntimeSelection(
        definition=SURYA_RUNTIME_DEFINITIONS["cpu"],
        binary=cpu,
        source="bundled",
    )
    for name in (
        "BANGLA_OCR_SURYA_RUNTIME",
        "BANGLA_OCR_SURYA_DEVICE",
        "LLAMA_CPP_BINARY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_engine.importlib.util.find_spec",
        lambda name: object(),
    )
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_engine.decide_surya_backend",
        lambda *args, **kwargs: SuryaBackendDecision(
            requested="cpu",
            selected=selection,
            automatic=False,
            cpu_fallback=False,
            reason="The requested CPU runtime is ready",
            attempted_backends=("cpu",),
            runtime_statuses={},
        ),
    )
    engine = SuryaEngine({"surya_runtime_backend": "cpu"}, tmp_path)
    monkeypatch.setattr(engine, "_read_sentinel", lambda: {"pid": 1234})
    monkeypatch.setattr(engine, "_process_alive", lambda pid: pid == 1234)
    recovery_calls = []
    monkeypatch.setattr(
        engine,
        "recover_after_failure",
        lambda: recovery_calls.append("reset") or {"recovered": True},
    )

    engine._activate_runtime()

    assert recovery_calls == ["reset"]
    assert os.environ["BANGLA_OCR_SURYA_RUNTIME"] == "cpu"


def test_startup_validation_loads_model_server(monkeypatch, tmp_path):
    calls = []

    class FakeManager:
        def start(self):
            calls.append("start")

        def stop(self):
            calls.append("stop")

    class FakePredictor:
        def __init__(self, manager):
            calls.append(("predictor", manager))

    selection = SuryaRuntimeSelection(
        definition=SURYA_RUNTIME_DEFINITIONS["cpu"],
        binary=Path("C:/test/llama-server.exe"),
        source="bundled",
    )
    monkeypatch.setitem(
        sys.modules,
        "surya.inference",
        SimpleNamespace(SuryaInferenceManager=FakeManager),
    )
    monkeypatch.setitem(
        sys.modules,
        "surya.recognition",
        SimpleNamespace(RecognitionPredictor=FakePredictor),
    )
    engine = SuryaEngine({"surya_runtime_backend": "cpu"}, tmp_path)
    monkeypatch.setattr(engine, "available", lambda: (True, "ready"))
    monkeypatch.setattr(engine, "_activate_runtime", lambda: selection)
    monkeypatch.setattr(engine, "_rotate_server_log", lambda: False)
    monkeypatch.setattr(
        engine,
        "_configure_upstream_runtime_cache",
        lambda: tmp_path / "cache",
    )
    monkeypatch.setattr(engine, "_server_health", lambda sentinel=None: True)

    result = engine.validate_startup()

    assert calls[0] == "start"
    assert calls[1][0] == "predictor"
    assert calls[2] == "stop"
    assert result["status"] == "ready"
    assert result["backend"] == "cpu"
    assert result["server_healthy"] is True


def test_runtime_activation_updates_loaded_surya_settings(monkeypatch):
    settings = SimpleNamespace()
    monkeypatch.setitem(
        sys.modules,
        "surya.settings",
        SimpleNamespace(settings=settings),
    )
    monkeypatch.setenv("SURYA_INFERENCE_BACKEND", "llamacpp")
    monkeypatch.setenv("SURYA_INFERENCE_KEEP_ALIVE", "1")
    monkeypatch.setenv("SURYA_INFERENCE_PARALLEL", "1")
    monkeypatch.setenv("SURYA_INFERENCE_CTX_SIZE", "16384")
    monkeypatch.setenv("LLAMA_CPP_BINARY", "C:/test/cpu/llama-server.exe")
    monkeypatch.setenv("LLAMA_CPP_NGL", "0")
    monkeypatch.setenv("LLAMA_CPP_EXTRA_ARGS", "--cache-type-k q4_0")

    SuryaEngine._sync_upstream_settings()

    assert settings.LLAMA_CPP_BINARY == "C:/test/cpu/llama-server.exe"
    assert settings.LLAMA_CPP_NGL == 0
    assert settings.LLAMA_CPP_EXTRA_ARGS == "--cache-type-k q4_0"
    assert settings.SURYA_INFERENCE_PARALLEL == 1


def test_surya_uses_the_configured_runtime_cache(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "workspace" / "surya"
    upstream_spawn = SimpleNamespace(_cache_dir=lambda: Path("unused"))
    upstream_llamacpp = SimpleNamespace(Path=Path)
    monkeypatch.setenv("SURYA_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_engine.importlib.import_module",
        lambda name: (
            upstream_spawn
            if name == "surya.inference.backends.spawn"
            else upstream_llamacpp
        ),
    )

    engine = SuryaEngine({}, tmp_path)
    configured = engine._configure_upstream_runtime_cache()

    assert configured == runtime_dir
    assert upstream_spawn._cache_dir() == runtime_dir
    assert upstream_llamacpp.Path(
        "~/.cache/datalab/surya/llamacpp_server.log"
    ) == runtime_dir / "llamacpp_server.log"
    assert runtime_dir.is_dir()


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
