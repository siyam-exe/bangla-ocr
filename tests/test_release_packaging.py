from pathlib import Path
import zipfile

from PIL import Image

from scripts.archive_package_licenses import archive_package_licenses


ROOT = Path(__file__).resolve().parents[1]


def test_release_builder_defaults_to_the_universal_package():
    builder = (ROOT / "scripts" / "build_windows_release.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert '[ValidateSet("Universal", "Cpu", "Cuda", "Vulkan")]' in builder
    assert '[string]$Runtime = "Universal"' in builder
    assert '"Universal" { "universal" }' in builder
    assert (
        '"Universal" { @("llama.cpp-cpu", "llama.cpp-cuda", '
        '"llama.cpp-vulkan") }'
    ) in builder
    assert "bundled_runtimes" in builder
    assert '"Bangla-OCR-$AppVersion-windows-x64-$RuntimeLabel-setup.exe"' in builder
    assert '"/DRuntimeName=$RuntimeLabel"' in builder


def test_windows_icon_contains_the_required_display_sizes():
    icon_path = ROOT / "packaging" / "windows" / "assets" / "app-icon.ico"

    assert icon_path.is_file()
    with Image.open(icon_path) as icon:
        sizes = set(icon.info.get("sizes", []))

    assert {(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)} <= sizes


def test_release_build_passes_the_icon_to_both_windows_compilers():
    builder = (ROOT / "scripts" / "build_windows_release.ps1").read_text(
        encoding="utf-8-sig"
    )
    launcher_builder = (
        ROOT / "packaging" / "windows" / "build-launcher.ps1"
    ).read_text(encoding="utf-8-sig")

    assert "-IconPath $AppIconPath" in builder
    assert '"/DAppIconPath=$AppIconPath"' in builder
    assert '"/win32icon:$ResolvedIcon"' in launcher_builder


def test_installer_always_offers_the_destination_and_uses_the_app_icon():
    installer = (ROOT / "packaging" / "windows" / "BanglaOCR.iss").read_text(
        encoding="utf-8-sig"
    )

    assert "DisableDirPage=no" in installer
    assert "UsePreviousAppDir=yes" in installer
    assert "SetupIconFile={#AppIconPath}" in installer


def test_native_launcher_is_bound_to_its_executable_directory():
    launcher = (
        ROOT / "packaging" / "windows" / "launcher" / "BanglaOcrLauncher.cs"
    ).read_text(encoding="utf-8-sig")

    assert 'GetEnvironmentVariable("BANGLA_OCR_HOME")' not in launcher
    assert "AppDomain.CurrentDomain.BaseDirectory" in launcher
    assert 'value.TryGetValue("application_root"' in launcher
    assert "ExtractAssociatedIcon" in launcher
    assert 'SetFailure("The local address is already in use", probe.Detail);' in launcher


def test_installer_replaces_an_existing_inference_runtime():
    installer = (ROOT / "packaging" / "windows" / "BanglaOCR.iss").read_text(
        encoding="utf-8-sig"
    )

    assert '[InstallDelete]' in installer
    assert 'Name: "{app}\\tools\\llama.cpp-cpu"' in installer
    assert 'Name: "{app}\\tools\\llama.cpp-cuda"' in installer
    assert 'Name: "{app}\\tools\\llama.cpp-vulkan"' in installer


def test_runtime_installer_pins_and_verifies_the_vulkan_archive():
    installer = (ROOT / "install-runtime.ps1").read_text(encoding="utf-8-sig")

    assert '[ValidateSet("Auto", "All", "Cuda", "Vulkan", "Cpu")]' in installer
    assert '$Release = "b10107"' in installer
    assert '$Commit = "c0bc8591e8815c63cb01dd3f051a8b0df02501c9"' in installer
    assert 'Name = "llama-b10107-bin-win-vulkan-x64.zip"' in installer
    assert (
        'Sha256 = '
        '"c5b3a5ee8319b1eccbb748a54390aa806bbf7d1aceeea452e4c57921d113e53e"'
    ) in installer
    assert 'Get-FileHash -LiteralPath $Archive -Algorithm SHA256' in installer
    assert 'LICENSE.llama.cpp.txt' in installer


def test_llama_runtime_license_is_packaged_from_a_tracked_file():
    license_path = ROOT / "packaging" / "licenses" / "llama.cpp-LICENSE.txt"
    text = license_path.read_text(encoding="utf-8")

    assert text.startswith("MIT License\n")
    assert "Copyright (c) 2023-2026 The ggml authors" in text


def test_packaged_doctor_evidence_stays_outside_the_application():
    builder = (ROOT / "scripts" / "build_windows_release.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert '$PackageDoctorPath = Join-Path $StageParent "package-doctor.json"' in builder
    assert 'Join-Path $StageRoot "workspace\\logs\\package-doctor.json"' not in builder
    assert '@("documents", "models", "workspace")' in builder
    assert 'The release contains mutable application data' in builder


def test_resumed_release_preserves_the_prepared_license_archive():
    builder = (ROOT / "scripts" / "build_windows_release.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert 'if (-not $Resume -or -not (Test-Path -LiteralPath $LicenseArchive))' in builder
    assert 'Reusing the prepared third-party license archive.' in builder


def test_package_licenses_are_archived_without_long_install_paths(tmp_path):
    site_packages = tmp_path / "site-packages"
    first = site_packages / "torch-2.13.0.dist-info" / "licenses" / "third_party"
    second = site_packages / "numpy-2.5.1.dist-info" / "licenses"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "LICENSE.txt").write_text("torch license", encoding="utf-8")
    (second / "LICENSE.txt").write_text("numpy license", encoding="utf-8")
    destination = tmp_path / "runtime" / "third-party-licenses.zip"

    count = archive_package_licenses(site_packages, destination)

    assert count == 2
    assert not (site_packages / "torch-2.13.0.dist-info" / "licenses").exists()
    with zipfile.ZipFile(destination) as archive:
        assert archive.read(
            "torch-2.13.0.dist-info/licenses/third_party/LICENSE.txt"
        ) == b"torch license"
        assert archive.read(
            "numpy-2.5.1.dist-info/licenses/LICENSE.txt"
        ) == b"numpy license"
