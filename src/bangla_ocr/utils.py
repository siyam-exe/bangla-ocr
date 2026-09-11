from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable


BENGALI_RE = re.compile(r"[\u0980-\u09FF]")
LATIN_RE = re.compile(r"[A-Za-z]")
DIGIT_RE = re.compile(r"[0-9\u09E6-\u09EF]")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_value).strip("-").lower()
    if slug:
        return slug
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value).replace("\r\n", "\n")


def text_counts(text: str) -> dict[str, int]:
    return {
        "bengali": len(BENGALI_RE.findall(text)),
        "latin": len(LATIN_RE.findall(text)),
        "digits": len(DIGIT_RE.findall(text)),
        "non_whitespace": sum(not char.isspace() for char in text),
        "replacement": text.count("\ufffd"),
    }


def parse_page_spec(spec: str | None, page_count: int) -> list[int]:
    """Return zero-based page indexes from a one-based user page specification."""
    if not spec or spec.strip().lower() in {"all", "*"}:
        return list(range(page_count))

    pages: set[int] = set()
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise ValueError(f"Invalid page range: {item}")
            values: Iterable[int] = range(start, end + 1)
        else:
            values = [int(item)]
        for value in values:
            if value < 1 or value > page_count:
                raise ValueError(
                    f"Page {value} is outside the PDF range 1-{page_count}"
                )
            pages.add(value - 1)
    return sorted(pages)


def merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged
