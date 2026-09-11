from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "packaging" / "windows" / "build-hardware-probe.ps1"
INSTALLER_SCRIPT = ROOT / "packaging" / "windows" / "BanglaOCR.iss"


@pytest.fixture(scope="module")
def hardware_probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.name != "nt":
        pytest.skip("The Windows installer probe only runs on Windows")
    output = tmp_path_factory.mktemp("hardware-probe") / "hardware-probe.exe"
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BUILD_SCRIPT),
            "-OutputPath",
            str(output),
        ],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return output


def classify(hardware_probe: Path, *adapter_names: str) -> dict[str, object]:
    command = [str(hardware_probe), "--classify"]
    for name in adapter_names:
        command.extend(["--adapter", name])
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_nvidia_is_preferred_on_a_hybrid_laptop(hardware_probe: Path):
    report = classify(
        hardware_probe,
        "Intel(R) UHD Graphics",
        "NVIDIA GeForce RTX 4050 Laptop GPU",
    )

    assert report["recommendation"] == "cuda"
    assert report["default_runtimes"] == ["cpu", "cuda"]
    assert report["detected_adapters"] == [
        "Intel(R) UHD Graphics",
        "NVIDIA GeForce RTX 4050 Laptop GPU",
    ]


@pytest.mark.parametrize(
    "adapter_name",
    [
        "AMD Radeon RX 7800 XT",
        "Intel(R) Arc(TM) A770 Graphics",
        "Intel(R) UHD Graphics 770",
    ],
)
def test_amd_and_intel_graphics_select_vulkan(
    hardware_probe: Path,
    adapter_name: str,
):
    report = classify(hardware_probe, adapter_name)

    assert report["recommendation"] == "vulkan"
    assert report["default_runtimes"] == ["cpu", "vulkan"]


@pytest.mark.parametrize(
    "adapter_names",
    [
        (),
        ("Microsoft Basic Display Adapter",),
        ("Remote Display Adapter",),
    ],
)
def test_missing_or_software_graphics_select_cpu(
    hardware_probe: Path,
    adapter_names: tuple[str, ...],
):
    report = classify(hardware_probe, *adapter_names)

    assert report["recommendation"] == "cpu"
    assert report["default_runtimes"] == ["cpu"]


def test_installer_report_is_simple_and_human_readable(hardware_probe: Path):
    result = subprocess.run(
        [
            str(hardware_probe),
            "--installer",
            "--classify",
            "--adapter",
            "NVIDIA GeForce RTX 4050 Laptop GPU",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "recommendation=cuda",
        "adapters=NVIDIA GeForce RTX 4050 Laptop GPU",
        "reason=NVIDIA graphics detected. CUDA acceleration is recommended and CPU remains available as a fallback.",
        "detection_succeeded=1",
    ]


def find_inno_compiler() -> Path | None:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6/ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6/ISCC.exe",
    ]
    return next((path for path in candidates if path.is_file()), None)


def test_compiled_installer_honors_runtime_override(
    hardware_probe: Path,
    tmp_path: Path,
):
    compiler = find_inno_compiler()
    if compiler is None:
        pytest.skip("Inno Setup 6 is not installed")

    stage = tmp_path / "stage"
    output = tmp_path / "dist"
    install_root = tmp_path / "installed"
    for runtime in ("cpu", "cuda", "vulkan"):
        folder = stage / "tools" / f"llama.cpp-{runtime}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{runtime}.txt").write_text(runtime, encoding="ascii")
    (stage / "Bangla OCR.exe").write_bytes(b"test launcher")
    (stage / "README.md").write_text("test package", encoding="ascii")
    output.mkdir()

    script_text = INSTALLER_SCRIPT.read_text(encoding="utf-8-sig")
    test_app_guid = str(uuid.uuid4()).upper()
    test_app_id = "{{" + test_app_guid + "}"
    script_text, replacements = re.subn(
        r"^AppId=.*$",
        "AppId=" + test_app_id,
        script_text,
        count=1,
        flags=re.MULTILINE,
    )
    assert replacements == 1
    isolated_script = tmp_path / "BanglaOCR-test.iss"
    isolated_script.write_text(script_text, encoding="utf-8")

    subprocess.run(
        [
            str(compiler),
            "/Qp",
            f"/DStageDir={stage}",
            f"/DOutputDir={output}",
            "/DAppVersion=0.0.0",
            "/DRuntimeName=universal",
            f"/DHardwareProbePath={hardware_probe}",
            f"/DAppIconPath={ROOT / 'packaging/windows/assets/app-icon.ico'}",
            "/DCpuRuntimeSize=about 44 MB",
            "/DCudaRuntimeSize=about 1.1 GB",
            "/DVulkanRuntimeSize=about 93 MB",
            "/DUninstallableValue=no",
            str(isolated_script),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    setup = output / "Bangla-OCR-0.0.0-windows-x64-universal-setup.exe"

    detected = json.loads(
        subprocess.run(
            [str(hardware_probe)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    expectations = {
        "auto": set(detected["default_runtimes"]),
        "cpu": {"cpu"},
        "cuda": {"cpu", "cuda"},
        "vulkan": {"cpu", "vulkan"},
        "all": {"cpu", "cuda", "vulkan"},
    }
    try:
        for override, expected in expectations.items():
            subprocess.run(
                [
                    str(setup),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/NOICONS",
                    f"/DIR={install_root}",
                    f"/RUNTIME={override}",
                ],
                check=True,
                timeout=60,
            )
            installed = {
                runtime
                for runtime in ("cpu", "cuda", "vulkan")
                if (
                    install_root
                    / "tools"
                    / f"llama.cpp-{runtime}"
                    / f"{runtime}.txt"
                ).is_file()
            }
            assert installed == expected
            registration = subprocess.run(
                [
                    "reg.exe",
                    "query",
                    "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
                    f"{{{test_app_guid}}}_is1",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert registration.returncode != 0
    finally:
        uninstallers = list(install_root.glob("unins*.exe"))
        if uninstallers:
            subprocess.run(
                [
                    str(uninstallers[0]),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                ],
                check=False,
                timeout=60,
            )
        shutil.rmtree(install_root, ignore_errors=True)
