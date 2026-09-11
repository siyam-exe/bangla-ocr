import json
import subprocess

from bangla_ocr.engines.surya_hardware import (
    _parse_runtime_output,
    detect_windows_display_adapters,
    probe_surya_runtime,
)
from bangla_ocr.engines.surya_runtime import SURYA_RUNTIME_DEFINITIONS


def _runtime_binary(tmp_path, backend):
    binary = SURYA_RUNTIME_DEFINITIONS[backend].binary_path(tmp_path)
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"runtime")
    return binary


def test_runtime_output_parser_reads_version_and_devices():
    version, devices = _parse_runtime_output(
        "version: 10107 (c0bc8591e)\n"
        "Available devices:\n"
        "  Vulkan0: AMD Radeon RX 6600 (8192 MiB, 7000 MiB free)\n"
    )

    assert version == "10107 (c0bc8591e)"
    assert devices[0].identifier == "Vulkan0"
    assert devices[0].description.startswith("AMD Radeon RX 6600")


def test_cpu_runtime_is_usable_without_accelerator_devices(monkeypatch, tmp_path):
    _runtime_binary(tmp_path, "cpu")
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_hardware.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "version: 10107\nAvailable devices:\n", ""
        ),
    )

    probe = probe_surya_runtime(
        SURYA_RUNTIME_DEFINITIONS["cpu"], tmp_path
    )

    assert probe.usable is True
    assert probe.status == "ready"
    assert probe.devices == ()


def test_runtime_probe_does_not_inherit_an_active_gpu_device(monkeypatch, tmp_path):
    _runtime_binary(tmp_path, "cpu")
    monkeypatch.setenv("LLAMA_ARG_DEVICE", "CUDA0")
    monkeypatch.setenv("LLAMA_ARG_SPLIT_MODE", "none")

    def run_probe(*args, **kwargs):
        environment = kwargs.get("env")
        if environment is None or any(
            name in environment
            for name in ("LLAMA_ARG_DEVICE", "LLAMA_ARG_SPLIT_MODE")
        ):
            return subprocess.CompletedProcess(
                args[0], 1, "", "invalid device: CUDA0"
            )
        return subprocess.CompletedProcess(
            args[0], 0, "version: 10107\nAvailable devices:\n", ""
        )

    monkeypatch.setattr(
        "bangla_ocr.engines.surya_hardware.subprocess.run",
        run_probe,
    )

    probe = probe_surya_runtime(
        SURYA_RUNTIME_DEFINITIONS["cpu"], tmp_path
    )

    assert probe.usable is True
    assert probe.status == "ready"


def test_gpu_runtime_requires_a_backend_reported_device(monkeypatch, tmp_path):
    _runtime_binary(tmp_path, "vulkan")
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_hardware.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "version: 10107\nAvailable devices:\n", ""
        ),
    )

    probe = probe_surya_runtime(
        SURYA_RUNTIME_DEFINITIONS["vulkan"], tmp_path
    )

    assert probe.usable is False
    assert probe.status == "no_device"
    assert "did not report a usable GPU" in probe.reason


def test_gpu_runtime_accepts_a_backend_reported_device(monkeypatch, tmp_path):
    _runtime_binary(tmp_path, "cuda")
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_hardware.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            "version: 10107\nAvailable devices:\n"
            "  CUDA0: NVIDIA GeForce RTX 4050 Laptop GPU (6140 MiB)\n",
            "",
        ),
    )

    probe = probe_surya_runtime(
        SURYA_RUNTIME_DEFINITIONS["cuda"], tmp_path
    )

    assert probe.usable is True
    assert probe.status == "ready"
    assert probe.devices[0].identifier == "CUDA0"


def test_missing_runtime_has_a_specific_status(tmp_path):
    probe = probe_surya_runtime(
        SURYA_RUNTIME_DEFINITIONS["vulkan"], tmp_path
    )

    assert probe.installed is False
    assert probe.usable is False
    assert probe.status == "missing"


def test_runtime_timeout_is_bounded_and_reported(monkeypatch, tmp_path):
    binary = _runtime_binary(tmp_path, "cuda")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired([str(binary), "--list-devices"], 3)

    monkeypatch.setattr(
        "bangla_ocr.engines.surya_hardware.subprocess.run", timeout
    )

    probe = probe_surya_runtime(
        SURYA_RUNTIME_DEFINITIONS["cuda"],
        tmp_path,
        timeout_seconds=3,
    )

    assert probe.usable is False
    assert probe.status == "timeout"
    assert "3 seconds" in probe.reason


def test_nonzero_runtime_probe_is_rejected(monkeypatch, tmp_path):
    _runtime_binary(tmp_path, "vulkan")
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_hardware.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, "version: 10107\n", "driver initialization failed"
        ),
    )

    probe = probe_surya_runtime(
        SURYA_RUNTIME_DEFINITIONS["vulkan"], tmp_path
    )

    assert probe.usable is False
    assert probe.status == "rejected"
    assert "driver initialization failed" in probe.diagnostic_output


def test_windows_adapter_inventory_is_structured(monkeypatch):
    payload = json.dumps(
        [
            {
                "Name": "AMD Radeon RX 6600",
                "DriverVersion": "32.0.1",
                "AdapterRAM": 8589934592,
                "PNPDeviceID": "PCI\\VEN_1002",
                "Status": "OK",
            },
            {
                "Name": "Intel(R) UHD Graphics",
                "DriverVersion": "31.0.2",
                "AdapterRAM": 1073741824,
                "PNPDeviceID": "PCI\\VEN_8086",
                "Status": "OK",
            },
        ]
    )
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_hardware.shutil.which",
        lambda name: "powershell.exe",
    )
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_hardware.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, payload, ""
        ),
    )

    inventory = detect_windows_display_adapters(platform_name="nt")

    assert inventory.status == "ready"
    assert [adapter.vendor for adapter in inventory.adapters] == ["amd", "intel"]
    assert (
        inventory.adapters[0].windows_reported_adapter_ram_bytes
        == 8589934592
    )


def test_adapter_inventory_reports_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_hardware.shutil.which",
        lambda name: "powershell.exe",
    )
    monkeypatch.setattr(
        "bangla_ocr.engines.surya_hardware.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "not-json", ""
        ),
    )

    inventory = detect_windows_display_adapters(platform_name="nt")

    assert inventory.status == "failed"
    assert "invalid adapter information" in inventory.reason
