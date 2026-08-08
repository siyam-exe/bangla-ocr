from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .assemble import finalize_book
from .application import run_application
from .config import load_config
from .engines import EngineRegistry
from .pdf import PDFSource
from .processor import process_book
from .review import run_review_server
from .utils import parse_page_spec


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _common_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=_path,
        help="Optional JSON configuration overriding config/default.json",
    )


def _doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = Path(__file__).resolve().parents[2]
    registry = EngineRegistry(config["ocr"], root)
    report: dict[str, Any] = {
        "python": {
            "version": sys.version,
            "executable": sys.executable,
        },
        "pipeline_root": str(root),
        "engines": registry.statuses(),
        "nvidia": None,
    }
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0:
            report["nvidia"] = result.stdout.strip()
    except OSError:
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _process(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    source_pdf = args.pdf.resolve()
    if not source_pdf.exists():
        raise FileNotFoundError(source_pdf)
    with PDFSource(source_pdf) as pdf:
        page_indexes = parse_page_spec(args.pages, pdf.page_count)
    output_root = (
        args.output_root.resolve()
        if args.output_root
        else Path(config["output"]["default_root"]).resolve()
    )
    engines = (
        [value.strip() for value in args.engines.split(",") if value.strip()]
        if args.engines
        else [config["ocr"]["preferred_primary_engine"], "embedded"]
    )
    book_root = process_book(
        source_pdf=source_pdf,
        title=args.title,
        author=args.author or config["output"]["author"],
        output_root=output_root,
        page_indexes=page_indexes,
        config=config,
        requested_engines=engines,
        force=args.force,
    )
    print(f"\nBook workspace: {book_root}")
    print(f"Review: bangla-ocr review \"{book_root}\"")
    return 0


def _review(args: argparse.Namespace) -> int:
    _validate_bind_host(args.host, args.allow_network)
    run_review_server(
        args.book.resolve(),
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )
    return 0


def _app(args: argparse.Namespace) -> int:
    _validate_bind_host(args.host, args.allow_network)
    run_application(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )
    return 0


def _validate_bind_host(host: str, allow_network: bool) -> None:
    if host.strip().lower() not in _LOOPBACK_HOSTS and not allow_network:
        raise ValueError(
            "Refusing to expose document data on the network. Use "
            "--allow-network only on a trusted network after reading SECURITY.md."
        )


def _finalize(args: argparse.Namespace) -> int:
    report = finalize_book(
        args.book.resolve(),
        allow_draft=args.allow_draft,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for name in report["exported_files"]:
        print(f"Output: {args.book.resolve() / name}")
    if report["complete"]:
        print("Verified book export is complete.")
        return 0
    print(
        "A draft was assembled, but it is not a verified-complete book. "
        "The report lists pages that still need review."
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bangla-ocr",
        description="Bengali PDF-to-text OCR and review pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="Check local OCR engines and hardware"
    )
    _common_config(doctor)
    doctor.set_defaults(handler=_doctor)

    process = subparsers.add_parser(
        "process", help="Render and OCR selected pages from one book"
    )
    process.add_argument("pdf", type=_path)
    process.add_argument("--title", required=True)
    process.add_argument("--author")
    process.add_argument(
        "--pages",
        default="all",
        help="One-based pages: all, 1-20, or 1,5-10,30",
    )
    process.add_argument(
        "--engines",
        help=(
            "Comma-separated explicit OCR plan, e.g. surya,embedded. "
            "Default: configured primary plus embedded-text evidence; no "
            "automatic OCR-model fallback."
        ),
    )
    process.add_argument("--output-root", type=_path)
    process.add_argument("--force", action="store_true")
    _common_config(process)
    process.set_defaults(handler=_process)

    review = subparsers.add_parser(
        "review", help="Open the side-by-side verification screen"
    )
    review.add_argument("book", type=_path)
    review.add_argument("--host", default="127.0.0.1")
    review.add_argument("--port", type=int, default=8765)
    review.add_argument("--no-browser", action="store_true")
    review.add_argument(
        "--allow-network",
        action="store_true",
        help="Explicitly allow a non-loopback bind; no authentication is provided",
    )
    review.set_defaults(handler=_review)

    application = subparsers.add_parser(
        "app", help="Open the one-book-at-a-time OCR application"
    )
    application.add_argument("--host", default="127.0.0.1")
    application.add_argument("--port", type=int, default=8765)
    application.add_argument("--no-browser", action="store_true")
    application.add_argument(
        "--allow-network",
        action="store_true",
        help="Explicitly allow a non-loopback bind; no authentication is provided",
    )
    application.set_defaults(handler=_app)

    finalize = subparsers.add_parser(
        "finalize", help="Assemble verified pages into book.md and book.txt"
    )
    finalize.add_argument("book", type=_path)
    finalize.add_argument(
        "--allow-draft",
        action="store_true",
        help="Include unverified draft pages; finalization report will flag them",
    )
    finalize.set_defaults(handler=_finalize)
    return parser


def main() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args()
    try:
        code = int(args.handler(args))
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        parser.exit(1, f"Error: {exc}\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
