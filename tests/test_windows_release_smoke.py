from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipInfo

import pytest

from scripts.test_windows_release import (
    ReleaseTestError,
    parse_checksum_file,
    validate_archive_members,
)


def test_checksum_manifest_requires_two_space_separator(tmp_path: Path):
    path = tmp_path / "SHA256SUMS.txt"
    digest = "a" * 64
    path.write_text(f"{digest}  package.zip\n", encoding="ascii")

    assert parse_checksum_file(path) == {"package.zip": digest}


def test_checksum_manifest_rejects_paths(tmp_path: Path):
    path = tmp_path / "SHA256SUMS.txt"
    path.write_text(f"{'a' * 64}  ../package.zip\n", encoding="ascii")

    with pytest.raises(ReleaseTestError, match="Invalid checksum line"):
        parse_checksum_file(path)


def test_archive_validation_accepts_clean_application_files():
    members = [
        ZipInfo("Bangla OCR/README.md"),
        ZipInfo("Bangla OCR/runtime/python/python.exe"),
        ZipInfo("Bangla OCR/tools/llama.cpp-cpu/llama-server.exe"),
    ]

    assert validate_archive_members(members) == [item.filename for item in members]


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "Another App/README.md",
        "Bangla OCR/../outside.txt",
    ],
)
def test_archive_validation_rejects_unsafe_members(name: str):
    with pytest.raises(ReleaseTestError):
        validate_archive_members([ZipInfo(name)])


def test_archive_validation_rejects_backslashes():
    member = SimpleNamespace(
        filename="Bangla OCR\\README.md",
        is_dir=lambda: False,
    )

    with pytest.raises(ReleaseTestError, match="backslash"):
        validate_archive_members([member])


@pytest.mark.parametrize(
    "name",
    [
        "Bangla OCR/documents/private.pdf",
        "Bangla OCR/models/downloaded-model.gguf",
        "Bangla OCR/workspace/logs/package-doctor.json",
    ],
)
def test_archive_validation_rejects_mutable_application_data(name: str):
    with pytest.raises(ReleaseTestError, match="mutable application data"):
        validate_archive_members([ZipInfo(name)])


def test_archive_validation_rejects_case_collisions():
    with pytest.raises(ReleaseTestError, match="duplicate"):
        validate_archive_members(
            [ZipInfo("Bangla OCR/README.md"), ZipInfo("Bangla OCR/readme.md")]
        )
