from bangla_ocr.engines.surya_policy import (
    automatic_surya_requests,
    decide_surya_backend,
    decide_surya_runtime_fallback,
)
from bangla_ocr.engines.surya_runtime import (
    SURYA_RUNTIME_DEFINITIONS,
    SuryaRuntimeSelection,
)


def _runtime_status(backend, *, usable, reason=None):
    definition = SURYA_RUNTIME_DEFINITIONS[backend]
    devices = []
    if usable and definition.accelerated:
        devices = [
            {
                "identifier": "CUDA0" if backend == "cuda" else "Vulkan0",
                "description": (
                    "NVIDIA GeForce RTX 4050 (6140 MiB)"
                    if backend == "cuda"
                    else "AMD Radeon RX 6600 (8192 MiB, 7000 MiB free)"
                ),
            }
        ]
    return {
        "backend": backend,
        "display_name": definition.display_name,
        "installed": usable,
        "usable": usable,
        "status": "ready" if usable else "missing",
        "reason": reason or ("ready" if usable else "not installed"),
        "devices": devices,
    }


def _hardware_report(*, vendors, cpu=True, cuda=False, vulkan=False):
    return {
        "display_adapters": {
            "status": "ready",
            "adapters": [
                {"name": f"{vendor} adapter", "vendor": vendor}
                for vendor in vendors
            ],
        },
        "runtimes": {
            "cpu": _runtime_status("cpu", usable=cpu),
            "cuda": _runtime_status("cuda", usable=cuda),
            "vulkan": _runtime_status("vulkan", usable=vulkan),
        },
    }


def test_nvidia_automatic_policy_prefers_cuda(tmp_path):
    decision = decide_surya_backend(
        tmp_path,
        hardware_report=_hardware_report(
            vendors=["nvidia"], cuda=True, vulkan=True
        ),
    )

    assert decision.selected_backend == "cuda"
    assert decision.attempted_backends == ("cuda", "vulkan", "cpu")
    assert decision.cpu_fallback is False


def test_amd_automatic_policy_prefers_vulkan(tmp_path):
    decision = decide_surya_backend(
        tmp_path,
        hardware_report=_hardware_report(vendors=["amd"], vulkan=True),
    )

    assert decision.selected_backend == "vulkan"
    assert decision.attempted_backends == ("vulkan", "cpu")


def test_intel_automatic_policy_prefers_vulkan(tmp_path):
    decision = decide_surya_backend(
        tmp_path,
        hardware_report=_hardware_report(vendors=["intel"], vulkan=True),
    )

    assert decision.selected_backend == "vulkan"


def test_automatic_policy_uses_cpu_when_gpu_runtimes_fail(tmp_path):
    decision = decide_surya_backend(
        tmp_path,
        hardware_report=_hardware_report(vendors=["nvidia"], cpu=True),
    )

    assert decision.selected_backend == "cpu"
    assert decision.cpu_fallback is True
    assert "No GPU runtime passed" in decision.reason


def test_explicit_backend_does_not_fallback(tmp_path):
    report = _hardware_report(vendors=["nvidia"], cpu=True, cuda=False)
    report["runtimes"]["cuda"]["reason"] = "CUDA driver rejected the device"

    decision = decide_surya_backend(
        tmp_path,
        requested="cuda",
        hardware_report=report,
    )

    assert decision.usable is False
    assert decision.selected is None
    assert decision.attempted_backends == ("cuda",)
    assert decision.reason == "CUDA driver rejected the device"


def test_failed_windows_inventory_uses_backend_results(tmp_path):
    report = _hardware_report(vendors=[], vulkan=True)
    report["display_adapters"] = {
        "status": "failed",
        "reason": "CIM unavailable",
        "adapters": [],
    }

    decision = decide_surya_backend(
        tmp_path,
        hardware_report=report,
    )

    assert decision.selected_backend == "vulkan"
    assert decision.attempted_backends == ("cuda", "vulkan", "cpu")


def test_vulkan_automatic_device_prefers_discrete_gpu(tmp_path):
    report = _hardware_report(vendors=["intel", "nvidia"], vulkan=True)
    report["runtimes"]["vulkan"]["devices"] = [
        {
            "identifier": "Vulkan0",
            "description": "Intel UHD Graphics (3947 MiB, 3552 MiB free)",
        },
        {
            "identifier": "Vulkan1",
            "description": "Microsoft Direct3D12 (NVIDIA RTX 4050) (5921 MiB)",
        },
    ]
    report["runtimes"]["cuda"]["usable"] = False

    decision = decide_surya_backend(tmp_path, hardware_report=report)

    assert decision.selected_backend == "vulkan"
    assert decision.selected_device == "Vulkan1"
    assert decision.selected_request == "vulkan/Vulkan1"


def test_explicit_vulkan_device_is_preserved(tmp_path):
    report = _hardware_report(vendors=["intel", "nvidia"], vulkan=True)
    report["runtimes"]["vulkan"]["devices"] = [
        {"identifier": "Vulkan0", "description": "Intel UHD Graphics"},
        {"identifier": "Vulkan1", "description": "NVIDIA RTX 4050"},
    ]

    decision = decide_surya_backend(
        tmp_path,
        requested="vulkan/Vulkan0",
        hardware_report=report,
    )

    assert decision.selected_device == "Vulkan0"
    assert decision.selected_request == "vulkan/Vulkan0"


def test_unknown_explicit_device_is_rejected(tmp_path):
    report = _hardware_report(vendors=["amd"], vulkan=True)

    decision = decide_surya_backend(
        tmp_path,
        requested="vulkan/Vulkan9",
        hardware_report=report,
    )

    assert decision.usable is False
    assert "Vulkan9" in decision.reason


def test_backend_environment_overwrites_previous_runtime_settings(tmp_path):
    definition = SURYA_RUNTIME_DEFINITIONS["cpu"]
    selection = SuryaRuntimeSelection(
        definition=definition,
        binary=definition.binary_path(tmp_path),
        source="bundled",
    )
    environ = {
        "LLAMA_CPP_BINARY": "old-cuda-server.exe",
        "LLAMA_CPP_NGL": "99",
        "LLAMA_CPP_EXTRA_ARGS": "--flash-attn on",
        "BANGLA_OCR_SURYA_RUNTIME": "cuda",
        "BANGLA_OCR_MANAGED_LLAMA_CPP": "1",
    }

    selection.configure_environment(environ, overwrite_runtime=True)

    assert environ["LLAMA_CPP_BINARY"].endswith("llama-server.exe")
    assert environ["LLAMA_CPP_NGL"] == "0"
    assert "flash-attn" not in environ["LLAMA_CPP_EXTRA_ARGS"]
    assert environ["BANGLA_OCR_SURYA_RUNTIME"] == "cpu"


def test_gpu_environment_pins_the_selected_device(tmp_path):
    definition = SURYA_RUNTIME_DEFINITIONS["vulkan"]
    selection = SuryaRuntimeSelection(
        definition=definition,
        binary=definition.binary_path(tmp_path),
        source="bundled",
        device_identifier="Vulkan1",
        device_description="AMD Radeon RX 6600",
    )
    environ = {}

    selection.configure_environment(environ, overwrite_runtime=True)

    assert environ["LLAMA_ARG_DEVICE"] == "Vulkan1"
    assert environ["LLAMA_ARG_SPLIT_MODE"] == "none"
    assert environ["BANGLA_OCR_SURYA_DEVICE"] == "Vulkan1"


def test_automatic_runtime_candidates_keep_policy_order_and_exact_device():
    report = _hardware_report(
        vendors=["nvidia"], cpu=True, cuda=True, vulkan=True
    )

    requests = automatic_surya_requests(report)

    assert requests == ("cuda/CUDA0", "vulkan/Vulkan0", "cpu")


def test_first_page_runtime_failure_can_move_to_next_surya_runtime():
    report = _hardware_report(vendors=["nvidia"], cpu=True, cuda=True)

    fallback = decide_surya_runtime_fallback(
        original_request="auto",
        attempted_requests=["cuda/CUDA0"],
        hardware_report=report,
        completed_page_count=0,
        runtime_failure=True,
    )

    assert fallback.allowed is True
    assert fallback.next_request == "cpu"
    assert "first page" in fallback.reason


def test_automatic_runtime_does_not_change_after_a_completed_page():
    report = _hardware_report(vendors=["nvidia"], cpu=True, cuda=True)

    fallback = decide_surya_runtime_fallback(
        original_request="auto",
        attempted_requests=["cuda/CUDA0"],
        hardware_report=report,
        completed_page_count=1,
        runtime_failure=True,
    )

    assert fallback.allowed is False
    assert fallback.next_request is None
    assert "completed" in fallback.reason


def test_explicit_runtime_never_falls_back_automatically():
    report = _hardware_report(vendors=["nvidia"], cpu=True, cuda=True)

    fallback = decide_surya_runtime_fallback(
        original_request="cuda/CUDA0",
        attempted_requests=["cuda/CUDA0"],
        hardware_report=report,
        completed_page_count=0,
        runtime_failure=True,
    )

    assert fallback.allowed is False
    assert fallback.next_request is None
    assert "explicit" in fallback.reason
