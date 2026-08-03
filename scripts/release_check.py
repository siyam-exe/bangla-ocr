from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
    "benchmark-output",
    "build",
    "dist",
    "models",
    "tmp",
    "tools",
    "__pycache__",
}
ALLOWED_PDF = Path("benchmarks/fixture/bangla-preservation-benchmark.pdf")
BENCHMARK_SHA256 = "c95e95a7190f767549ff0588db00637435b2f55b77014b12240dacbad25c538d"
TEXT_SUFFIXES = {
    ".cmd",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}
SECRET_PATTERNS = {
    "OpenRouter API key": re.compile(r"sk-or-v1-[A-Za-z0-9_-]{20,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "personal Windows path": re.compile(
        r"(?:[A-Za-z]:\\Users\\[^\\\s]+|D:\\Projects\\OCR)",
        re.IGNORECASE,
    ),
}


def included_files() -> list[Path]:
    values: list[Path] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if not path.is_file() or any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if any(part.endswith(".egg-info") for part in relative.parts):
            continue
        values.append(path)
    return sorted(values)


def main() -> int:
    errors: list[str] = []
    required = {
        Path("LICENSE"),
        Path("README.md"),
        Path("SECURITY.md"),
        Path("THIRD_PARTY_NOTICES.md"),
        Path("benchmarks/RESULTS.md"),
        ALLOWED_PDF,
    }
    present = {path.relative_to(ROOT) for path in included_files()}
    for missing in sorted(required - present):
        errors.append(f"missing release file: {missing}")

    for path in included_files():
        relative = path.relative_to(ROOT)
        if path.suffix.lower() == ".pdf" and relative != ALLOWED_PDF:
            errors.append(f"unexpected PDF: {relative}")
        if path.stat().st_size > 5 * 1024 * 1024:
            errors.append(f"unexpected file larger than 5 MiB: {relative}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{label} found in {relative}")

    benchmark = ROOT / ALLOWED_PDF
    if benchmark.exists():
        digest = hashlib.sha256(benchmark.read_bytes()).hexdigest()
        if digest != BENCHMARK_SHA256:
            errors.append(
                "public benchmark PDF hash changed; regenerate and review the fixture"
            )

    if errors:
        print("Release check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Release check passed ({len(present)} public files inspected).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
