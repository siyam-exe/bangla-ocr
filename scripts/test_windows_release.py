from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIRECTORY = "Bangla OCR"
RUNTIME_BACKENDS = ("cpu", "cuda", "vulkan")
MUTABLE_DIRECTORIES = {"documents", "models", "workspace"}
TEXT_SUFFIXES = {".json", ".md", ".txt", ".ini"}
PRIVATE_PATTERNS = {
    "OpenRouter API key": re.compile(r"sk-or-v1-[A-Za-z0-9_-]{20,}"),
    "personal Windows path": re.compile(
        r"(?:[A-Za-z]:\\Users\\[^\\\s]+|D:\\Projects\\OCR)",
        re.IGNORECASE,
    ),
}


class ReleaseTestError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksum_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw_line in enumerate(
        path.read_text(encoding="ascii").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line:
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s{2}([^/\\]+)", line)
        if not match:
            raise ReleaseTestError(
                f"Invalid checksum line {number} in {path.name}"
            )
        name = match.group(2)
        if name.casefold() in {value.casefold() for value in values}:
            raise ReleaseTestError(f"Duplicate checksum entry: {name}")
        values[name] = match.group(1).lower()
    if not values:
        raise ReleaseTestError(f"No checksums found in {path}")
    return values


def validate_archive_members(
    members: Iterable[zipfile.ZipInfo],
    *,
    maximum_relative_path: int = 180,
) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for member in members:
        name = member.filename
        if "\\" in name:
            raise ReleaseTestError(f"Archive member uses a backslash: {name}")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ReleaseTestError(f"Unsafe archive member: {name}")
        if not path.parts or path.parts[0] != PACKAGE_DIRECTORY:
            raise ReleaseTestError(
                f"Archive member is outside {PACKAGE_DIRECTORY}: {name}"
            )
        folded = name.rstrip("/").casefold()
        if folded in seen:
            raise ReleaseTestError(f"Case-insensitive duplicate archive member: {name}")
        seen.add(folded)
        relative = PurePosixPath(*path.parts[1:]).as_posix()
        if len(relative) > maximum_relative_path:
            raise ReleaseTestError(
                f"Archive path exceeds {maximum_relative_path} characters: {relative}"
            )
        if (
            not member.is_dir()
            and len(path.parts) > 1
            and path.parts[1].casefold() in MUTABLE_DIRECTORIES
        ):
            raise ReleaseTestError(
                f"Release contains mutable application data: {name}"
            )
        names.append(name)
    if not names:
        raise ReleaseTestError("Portable archive is empty")
    return names


def verify_artifacts(
    archive_path: Path,
    installer_path: Path,
    checksum_path: Path,
) -> dict[str, dict[str, int | str]]:
    expected = parse_checksum_file(checksum_path)
    results: dict[str, dict[str, int | str]] = {}
    for path in (archive_path, installer_path):
        if not path.is_file():
            raise ReleaseTestError(f"Missing release artifact: {path}")
        expected_hash = expected.get(path.name)
        if not expected_hash:
            raise ReleaseTestError(f"Checksum file does not list {path.name}")
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise ReleaseTestError(
                f"SHA-256 mismatch for {path.name}: {actual_hash}"
            )
        results[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": actual_hash,
        }
    with installer_path.open("rb") as stream:
        signature = stream.read(2)
    if signature != b"MZ":
        raise ReleaseTestError("Installer is not a Windows executable")
    return results


def verify_package_layout(package_root: Path) -> dict[str, object]:
    required = (
        package_root / "Bangla OCR.exe",
        package_root / "runtime" / "python" / "python.exe",
        package_root / "runtime" / "third-party-licenses.zip",
        package_root / "config" / "default.json",
        package_root / "release-manifest.json",
        package_root / "README.md",
        package_root / "LICENSE",
        package_root / "THIRD_PARTY_NOTICES.md",
    )
    missing = [str(path.relative_to(package_root)) for path in required if not path.is_file()]
    if missing:
        raise ReleaseTestError(f"Package is missing required files: {', '.join(missing)}")
    manifest = json.loads(
        (package_root / "release-manifest.json").read_text(encoding="utf-8-sig")
    )
    if manifest.get("application") != "Bangla OCR":
        raise ReleaseTestError("Release manifest has the wrong application name")
    if manifest.get("runtime") != "universal":
        raise ReleaseTestError("Release manifest is not universal")
    if set(manifest.get("bundled_runtimes", [])) != set(RUNTIME_BACKENDS):
        raise ReleaseTestError("Release manifest does not list all three runtimes")
    runtime_results: dict[str, object] = {}
    for backend in RUNTIME_BACKENDS:
        runtime_root = package_root / "tools" / f"llama.cpp-{backend}"
        server = runtime_root / "llama-server.exe"
        license_path = runtime_root / "LICENSE.llama.cpp.txt"
        manifest_path = runtime_root / "runtime-version.json"
        if not server.is_file() or not license_path.is_file() or not manifest_path.is_file():
            raise ReleaseTestError(f"The {backend} runtime is incomplete")
        runtime_manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if runtime_manifest.get("backend") != backend:
            raise ReleaseTestError(f"The {backend} runtime manifest is mislabeled")
        if runtime_manifest.get("release") != "b10107":
            raise ReleaseTestError(f"The {backend} runtime version is not pinned")
        assets = runtime_manifest.get("assets")
        if not isinstance(assets, list) or not assets:
            raise ReleaseTestError(f"The {backend} runtime has no source assets")
        for asset in assets:
            if not re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256", ""))):
                raise ReleaseTestError(f"The {backend} runtime has an invalid digest")
        runtime_results[backend] = {
            "release": runtime_manifest["release"],
            "commit": runtime_manifest.get("commit"),
        }
    return {"manifest": manifest, "runtimes": runtime_results}


def scan_private_content(package_root: Path) -> None:
    for path in package_root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(package_root)
        if relative.parts[:2] == ("runtime", "python"):
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in PRIVATE_PATTERNS.items():
            if pattern.search(text):
                raise ReleaseTestError(f"{label} found in packaged {relative}")


def packaged_environment(package_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["BANGLA_OCR_HOME"] = str(package_root)
    environment["PYTHONNOUSERSITE"] = "1"
    for name in (
        "LLAMA_CPP_BINARY",
        "LLAMA_CPP_NGL",
        "LLAMA_CPP_EXTRA_ARGS",
        "SURYA_INFERENCE_URL",
        "BANGLA_OCR_SURYA_RUNTIME",
        "BANGLA_OCR_MANAGED_LLAMA_CPP",
    ):
        environment.pop(name, None)
    return environment


def run_packaged_doctor(package_root: Path) -> dict[str, object]:
    python = package_root / "runtime" / "python" / "python.exe"
    result = subprocess.run(
        [str(python), "-m", "bangla_ocr", "doctor"],
        cwd=package_root,
        env=packaged_environment(package_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    if result.returncode != 0:
        raise ReleaseTestError(f"Packaged doctor failed: {result.stderr.strip()}")
    try:
        report = json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise ReleaseTestError(f"Packaged doctor returned invalid JSON: {exc}") from exc
    if Path(report.get("pipeline_root", "")).resolve() != package_root.resolve():
        raise ReleaseTestError("Packaged doctor used the wrong application root")
    engines = report.get("engines", {})
    if not engines.get("surya", {}).get("available"):
        raise ReleaseTestError("Packaged Surya is unavailable")
    if not engines.get("easyocr", {}).get("available"):
        raise ReleaseTestError("Packaged EasyOCR is unavailable")
    runtimes = report.get("surya_hardware", {}).get("runtimes", {})
    if not runtimes.get("cpu", {}).get("usable"):
        raise ReleaseTestError("Packaged CPU runtime is not usable")
    for backend in RUNTIME_BACKENDS:
        if not runtimes.get(backend, {}).get("installed"):
            raise ReleaseTestError(f"Packaged {backend} runtime was not detected")
    return report


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def run_http_smoke(package_root: Path, log_root: Path) -> dict[str, object]:
    port = _available_port()
    address = f"http://127.0.0.1:{port}"
    python = package_root / "runtime" / "python" / "python.exe"
    stdout_path = log_root / "server-stdout.log"
    stderr_path = log_root / "server-stderr.log"
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    started = time.monotonic()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        process = subprocess.Popen(
            [
                str(python),
                "-m",
                "bangla_ocr",
                "app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-browser",
            ],
            cwd=package_root,
            env=packaged_environment(package_root),
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
        try:
            payload: dict[str, object] | None = None
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ReleaseTestError(
                        "Packaged server exited before becoming healthy"
                    )
                try:
                    with urllib.request.urlopen(
                        f"{address}/api/health", timeout=2
                    ) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    if payload.get("application") == "bangla-ocr" and payload.get(
                        "status"
                    ) == "ready":
                        break
                except (OSError, urllib.error.URLError, json.JSONDecodeError):
                    time.sleep(0.25)
            if payload is None or payload.get("status") != "ready":
                raise ReleaseTestError("Packaged server did not become healthy")
            if Path(str(payload.get("application_root", ""))).resolve() != (
                package_root.resolve()
            ):
                raise ReleaseTestError(
                    "Packaged server reported the wrong application root"
                )
            with urllib.request.urlopen(address, timeout=5) as response:
                homepage = response.read().decode("utf-8", errors="replace")
            if "Bangla OCR" not in homepage:
                raise ReleaseTestError("Packaged dashboard did not render")
            return {
                "health": payload,
                "startup_seconds": round(time.monotonic() - started, 3),
                "port": port,
            }
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def artifact_paths(dist_root: Path, version: str) -> tuple[Path, Path, Path]:
    stem = f"Bangla-OCR-{version}-windows-x64-universal"
    return (
        dist_root / f"{stem}-portable.zip",
        dist_root / f"{stem}-setup.exe",
        dist_root / f"{stem}-SHA256SUMS.txt",
    )


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def run_release_test(
    dist_root: Path,
    *,
    keep_extracted: bool = False,
    skip_http: bool = False,
) -> dict[str, object]:
    archive_path, installer_path, checksum_path = artifact_paths(
        dist_root.resolve(), project_version()
    )
    artifacts = verify_artifacts(archive_path, installer_path, checksum_path)
    smoke_parent = ROOT / "build" / "windows" / "release-smoke"
    smoke_parent.mkdir(parents=True, exist_ok=True)
    smoke_root = Path(tempfile.mkdtemp(prefix="universal-", dir=smoke_parent))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            validate_archive_members(members)
            archive.extractall(smoke_root)
        package_root = smoke_root / PACKAGE_DIRECTORY
        layout = verify_package_layout(package_root)
        scan_private_content(package_root)
        doctor = run_packaged_doctor(package_root)
        http = None if skip_http else run_http_smoke(package_root, smoke_root)
        return {
            "status": "passed",
            "artifacts": artifacts,
            "package": {
                "version": layout["manifest"]["version"],
                "runtimes": list(layout["runtimes"]),
                "doctor_selection": doctor["engines"]["surya"]["reason"],
            },
            "http": http,
            "extracted_root": str(smoke_root) if keep_extracted else None,
        }
    finally:
        if not keep_extracted:
            shutil.rmtree(smoke_root, ignore_errors=False)
            try:
                smoke_parent.rmdir()
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test a built universal Bangla OCR Windows release"
    )
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--keep-extracted", action="store_true")
    parser.add_argument("--skip-http", action="store_true")
    args = parser.parse_args()
    try:
        result = run_release_test(
            args.dist,
            keep_extracted=args.keep_extracted,
            skip_http=args.skip_http,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc).strip() or exc.__class__.__name__,
                    "type": exc.__class__.__name__,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
