from __future__ import annotations

import datetime as dt
import json
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from . import __version__
from .engines import EngineRegistry
from .models import OCRCandidate
from .multiscale import (
    best_crop_reading,
    compare_readings,
    map_and_pad_bbox,
    map_bbox_to_original,
    select_regions,
)
from .pdf import PDFSource
from .preprocess import preprocess_page
from .scoring import decide_page, score_candidates
from .utils import nfc, read_json, sha256_file, slugify, write_json, write_text
from .workflow import normalize_page_state


ProgressCallback = Callable[[str], None]


class RequiredEngineFailedError(RuntimeError):
    """The explicitly required OCR engine failed and was not replaced."""


@dataclass
class EngineRunResult:
    candidate: OCRCandidate | None
    attempts: list[dict[str, Any]]
    errors: list[dict[str, Any]]


@dataclass
class PreparedPage:
    page_index: int
    page_number: int
    page_root: Path
    source_image_path: Path
    selected_image_path: Path
    preprocessing: Any
    timings: dict[str, float]


class PagePreparation:
    """One bounded background page preparation task."""

    def __init__(
        self,
        source_pdf: Path,
        page_index: int,
        page_root: Path,
        config: dict[str, Any],
    ) -> None:
        self._event = threading.Event()
        self._result: PreparedPage | None = None
        self._error: BaseException | None = None
        self._discard_requested = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run,
            args=(source_pdf, page_index, page_root, config),
            daemon=True,
        )
        self._thread.start()

    def _run(
        self,
        source_pdf: Path,
        page_index: int,
        page_root: Path,
        config: dict[str, Any],
    ) -> None:
        try:
            result = _prepare_page(
                source_pdf, page_index, page_root, config
            )
            with self._lock:
                if self._discard_requested:
                    _close_prepared_page(result)
                else:
                    self._result = result
        except BaseException as exc:
            self._error = exc
        finally:
            self._event.set()

    def result(self) -> tuple[PreparedPage, float]:
        wait_started = time.perf_counter()
        self._event.wait()
        wait_seconds = time.perf_counter() - wait_started
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise RuntimeError("Page preparation ended without a result")
        return self._result, wait_seconds

    def discard(self) -> None:
        with self._lock:
            self._discard_requested = True
            if self._result is not None:
                _close_prepared_page(self._result)
                self._result = None


def _close_prepared_page(prepared: PreparedPage) -> None:
    seen_images: set[int] = set()
    for image in (
        prepared.preprocessing.original,
        prepared.preprocessing.selected,
    ):
        if id(image) not in seen_images:
            image.close()
            seen_images.add(id(image))


def _prepare_page(
    source_pdf: Path,
    page_index: int,
    page_root: Path,
    config: dict[str, Any],
) -> PreparedPage:
    page_root.mkdir(parents=True, exist_ok=True)
    (page_root / "evidence").mkdir(parents=True, exist_ok=True)
    total_started = time.perf_counter()
    with PDFSource(source_pdf) as pdf:
        render_started = time.perf_counter()
        original = pdf.render_page(page_index, int(config["render"]["dpi"]))
        render_seconds = time.perf_counter() - render_started

        encoding_started = time.perf_counter()
        source_image_path = _save_review_image(
            original, page_root / "source", config["render"]
        )
        image_encoding_seconds = time.perf_counter() - encoding_started

        preprocessing_started = time.perf_counter()
        preprocessing = preprocess_page(original, config["preprocessing"])
        preprocessing_seconds = time.perf_counter() - preprocessing_started
        selected_image_path = source_image_path
        if (
            preprocessing.selected_name != "original"
            and config["preprocessing"].get("save_selected_variant", True)
        ):
            encoding_started = time.perf_counter()
            selected_image_path = _save_review_image(
                preprocessing.selected,
                page_root / "selected",
                config["render"],
            )
            image_encoding_seconds += time.perf_counter() - encoding_started

    return PreparedPage(
        page_index=page_index,
        page_number=page_index + 1,
        page_root=page_root,
        source_image_path=source_image_path,
        selected_image_path=selected_image_path,
        preprocessing=preprocessing,
        timings={
            "render_seconds": round(render_seconds, 6),
            "preprocessing_seconds": round(preprocessing_seconds, 6),
            "image_encoding_seconds": round(image_encoding_seconds, 6),
            "preparation_total_seconds": round(
                time.perf_counter() - total_started, 6
            ),
        },
    )


def _save_review_image(
    image: Image.Image,
    path_without_suffix: Path,
    render_config: dict[str, Any],
) -> Path:
    image_format = str(render_config["review_image_format"]).lower()
    if image_format == "webp":
        path = path_without_suffix.with_suffix(".webp")
        image.save(
            path,
            format="WEBP",
            quality=int(render_config["review_image_quality"]),
            method=6,
        )
    else:
        path = path_without_suffix.with_suffix(".png")
        image.save(path, format="PNG", optimize=True)
    return path


def _close_images(*images: Image.Image) -> None:
    seen: set[int] = set()
    for image in images:
        if id(image) not in seen:
            image.close()
            seen.add(id(image))


def _run_multiscale_pass(
    *,
    pdf: PDFSource,
    page_index: int,
    page_number: int,
    page_root: Path,
    engine: Any,
    baseline: OCRCandidate | None,
    baseline_image: Image.Image | None,
    baseline_operations: list[dict[str, Any]],
    original_image_size: tuple[int, int],
    decision_status: str,
    config: dict[str, Any],
    progress: ProgressCallback,
    progress_prefix: str,
) -> tuple[dict[str, Any], float, float, float, list[dict[str, Any]]]:
    settings = config.get("multiscale", {})
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "not_run",
        "policy": "human_choice_only",
        "automatic_correction": False,
        "regions": [],
        "errors": [],
    }
    if (
        not settings.get("enabled", True)
        or engine is None
        or getattr(engine, "name", "") != "surya"
        or baseline is None
        or baseline.engine != "surya"
        or baseline_image is None
    ):
        result["reason"] = "A full-page Surya candidate was not available."
        return result, 0.0, 0.0, 0.0, []

    plans = select_regions(
        baseline,
        baseline_image,
        settings,
        page_needs_review=decision_status != "draft",
    )
    if not plans:
        result["status"] = "completed"
        result["reason"] = "No bounded region met the evidence-based risk rules."
        return result, 0.0, 0.0, 0.0, []

    high_dpi = int(settings.get("high_resolution_dpi", 400))
    padding_ratio = float(settings.get("padding_ratio", 0.12))
    minimum_disagreement = float(settings.get("minimum_disagreement", 0.015))
    evidence_dir = page_root / "evidence"
    ocr_seconds = 0.0
    startup_seconds = 0.0
    inference_seconds = 0.0
    all_attempts: list[dict[str, Any]] = []
    high_original: Image.Image | None = None
    try:
        high_original = pdf.render_page(page_index, high_dpi)
        result.update(
            {
                "status": "completed",
                "baseline_engine": baseline.engine,
                "baseline_variant": baseline.variant,
                "baseline_image_size": list(baseline_image.size),
                "high_resolution_dpi": high_dpi,
                "high_resolution_image_size": list(high_original.size),
                "high_resolution_preprocessing": {
                    "selected_name": "unchanged_source_render",
                    "operations": [],
                },
            }
        )
        for position, plan in enumerate(plans, start=1):
            original_bbox = map_bbox_to_original(
                plan.bbox,
                baseline_image.size,
                original_image_size,
                baseline_operations,
            )
            crop_bbox = map_and_pad_bbox(
                original_bbox,
                original_image_size,
                high_original.size,
                padding_ratio,
            )
            crop = high_original.crop(tuple(crop_bbox))
            crop_name = f"multiscale-{plan.region_id}-{high_dpi}dpi"
            crop_path = _save_review_image(
                crop,
                evidence_dir / crop_name,
                config["render"],
            )
            variant = f"multiscale-{plan.region_id}-{high_dpi}dpi"
            progress(
                f"{progress_prefix}: Surya high-resolution region "
                f"{position}/{len(plans)}"
            )
            started = time.perf_counter()
            engine_result = _run_engine(
                engine,
                crop,
                variant,
                plan.original_text,
                evidence_dir,
                progress=progress,
                progress_prefix=progress_prefix,
            )
            ocr_seconds += time.perf_counter() - started
            all_attempts.extend(engine_result.attempts)
            region: dict[str, Any] = {
                "region_id": plan.region_id,
                "line_index": plan.line_index,
                "source_bbox": plan.bbox,
                "original_image_bbox": original_bbox,
                "high_resolution_bbox": crop_bbox,
                "crop_image": f"evidence/{crop_path.name}",
                "original": plan.original_text,
                "reasons": plan.reasons,
                "risk_score": plan.risk_score,
                "metrics": plan.metrics,
                "status": "failed",
                "errors": engine_result.errors,
            }
            if engine_result.candidate is not None:
                candidate = engine_result.candidate
                alternative = best_crop_reading(candidate, plan.original_text)
                comparison = compare_readings(
                    plan.original_text,
                    alternative,
                    minimum_disagreement,
                )
                candidate_evidence = f"evidence/surya-{variant}.json"
                region.update(
                    {
                        "alternative": alternative,
                        "comparison": comparison,
                        "status": (
                            "unreadable"
                            if not alternative
                            else (
                                "pending"
                                if comparison["has_reviewable_alternative"]
                                else "agrees"
                            )
                        ),
                        "candidate": _candidate_without_raw(
                            candidate, candidate_evidence
                        ),
                    }
                )
                startup_seconds += float(
                    candidate.diagnostics.get("model_startup_seconds", 0.0)
                )
                inference_seconds += float(
                    candidate.diagnostics.get("inference_seconds", 0.0)
                )
            result["regions"].append(region)
            crop.close()
    except Exception as exc:
        result["status"] = "partial_failure"
        result["errors"].append(
            {
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        if high_original is not None:
            high_original.close()

    result["pending_count"] = sum(
        region.get("status") == "pending" for region in result["regions"]
    )
    result["failed_count"] = sum(
        region.get("status") == "failed" for region in result["regions"]
    )
    result["unreadable_count"] = sum(
        region.get("status") == "unreadable" for region in result["regions"]
    )
    return (
        result,
        ocr_seconds,
        startup_seconds,
        inference_seconds,
        all_attempts,
    )


def _candidate_without_raw(candidate: OCRCandidate, evidence_path: str) -> dict[str, Any]:
    value = candidate.to_dict()
    value["raw"] = None
    value["evidence_path"] = evidence_path.replace("\\", "/")
    return value


def _run_engine(
    engine: Any,
    image: Image.Image,
    variant: str,
    embedded_text: str,
    evidence_dir: Path,
    *,
    progress: ProgressCallback = print,
    progress_prefix: str = "",
) -> EngineRunResult:
    max_attempts = 2 if bool(getattr(engine, "supports_recovery", False)) else 1
    attempts: list[dict[str, Any]] = []
    terminal_errors: list[dict[str, Any]] = []

    for attempt in range(1, max_attempts + 1):
        started_utc = dt.datetime.now(dt.UTC).isoformat()
        failed_candidate: OCRCandidate | None = None
        try:
            candidate = engine.recognize(
                image,
                variant,
                embedded_text=embedded_text,
            )
            retry_reason = engine.retry_reason(candidate)
            if retry_reason:
                failed_candidate = candidate
                raise RuntimeError(retry_reason)
            evidence_name = f"{engine.name}-{candidate.variant}.json"
            write_json(
                evidence_dir / evidence_name,
                {
                    "engine": engine.name,
                    "variant": variant,
                    "attempt": attempt,
                    "raw": candidate.raw,
                },
            )
            candidate.raw = None
            attempts.append(
                {
                    "engine": engine.name,
                    "variant": variant,
                    "attempt": attempt,
                    "status": "succeeded",
                    "started_utc": started_utc,
                    "finished_utc": dt.datetime.now(dt.UTC).isoformat(),
                    "evidence_path": f"evidence/{evidence_name}",
                }
            )
            return EngineRunResult(candidate, attempts, [])
        except Exception as exc:
            traceback_value = traceback.format_exc()
            diagnostics = {}
            try:
                diagnostics = engine.recovery_diagnostics()
            except Exception as diagnostic_exc:
                diagnostics = {"diagnostic_error": str(diagnostic_exc)}
            evidence_name = (
                f"{engine.name}-{variant}-attempt-{attempt}-failure.json"
            )
            event: dict[str, Any] = {
                "engine": engine.name,
                "variant": variant,
                "attempt": attempt,
                "status": "failed",
                "started_utc": started_utc,
                "finished_utc": dt.datetime.now(dt.UTC).isoformat(),
                "error": str(exc),
                "traceback": traceback_value,
                "diagnostics": diagnostics,
                "returned_candidate": (
                    failed_candidate.to_dict() if failed_candidate else None
                ),
                "evidence_path": f"evidence/{evidence_name}",
            }
            recovery_error: str | None = None
            if attempt < max_attempts:
                if progress_prefix:
                    progress(
                        f"{progress_prefix}: {engine.name} failed; restarting "
                        f"the local engine and retrying this page once"
                    )
                try:
                    event["recovery"] = engine.recover_after_failure()
                except Exception as recovery_exc:
                    recovery_error = str(recovery_exc)
                    event["recovery"] = {
                        "recovered": False,
                        "error": recovery_error,
                        "traceback": traceback.format_exc(),
                    }
            write_json(evidence_dir / evidence_name, event)
            attempts.append(
                {
                    "engine": engine.name,
                    "variant": variant,
                    "attempt": attempt,
                    "status": "failed",
                    "started_utc": started_utc,
                    "finished_utc": event["finished_utc"],
                    "error": str(exc),
                    "recovery": event.get("recovery"),
                    "evidence_path": f"evidence/{evidence_name}",
                }
            )
            if recovery_error:
                terminal_errors.append(
                    {
                        "engine": engine.name,
                        "variant": variant,
                        "attempt": attempt,
                        "error": str(exc),
                        "recovery_error": recovery_error,
                        "evidence_path": f"evidence/{evidence_name}",
                    }
                )
                break
            if attempt == max_attempts:
                terminal_errors.append(
                    {
                        "engine": engine.name,
                        "variant": variant,
                        "attempt": attempt,
                        "error": str(exc),
                        "evidence_path": f"evidence/{evidence_name}",
                    }
                )
    return EngineRunResult(None, attempts, terminal_errors)


def _write_failed_page_state(
    *,
    page_state_path: Path,
    page_number: int,
    source_image_path: Path,
    selected_image_path: Path,
    preprocessing: Any,
    primary_name: str,
    attempts: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    timings: dict[str, float] | None = None,
) -> None:
    reason = f"Required OCR engine {primary_name!r} failed; no fallback was used"
    page_state = {
        "schema_version": 2,
        "page_number": page_number,
        "source_image": source_image_path.name,
        "selected_image": selected_image_path.name,
        "preprocessing": preprocessing.diagnostics(),
        "candidates": [],
        "decision": {
            "selected_candidate": None,
            "selected_engine": None,
            "selected_variant": None,
            "status": "needs_review",
            "include": False,
            "page_role": "unreadable",
            "reasons": [reason],
            "disagreement": 0.0,
            "text": "",
        },
        "manual": {
            "status": "unreviewed",
            "include": False,
            "page_role": "unreadable",
            "heading": "",
            "break_before": False,
            "join_without_space": False,
            "preserve_trailing_hyphen": False,
            "reviewer": "",
            "reviewed_utc": None,
        },
        "ocr_attempts": attempts,
        "timings": timings or {},
        "errors": errors,
        "processing_complete": False,
        "workflow": {
            "ocr": {"status": "failed"},
            "automated": {"status": "failed", "reasons": [reason]},
            "human": {
                "status": "unreviewed",
                "reviewer": "",
                "reviewed_utc": None,
            },
            "ai": {
                "status": "not_requested",
                "proposal_count": 0,
                "accepted_count": 0,
            },
        },
    }
    normalize_page_state(page_state)
    write_text(page_state_path.parent / "draft.txt", "")
    write_json(page_state_path, page_state)


def _completed_page_numbers(pages_root: Path) -> set[int]:
    completed: set[int] = set()
    if not pages_root.exists():
        return completed
    for state_path in pages_root.glob("*/page.json"):
        try:
            state = read_json(state_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if state.get("processing_complete"):
            try:
                completed.add(int(state["page_number"]))
            except (KeyError, TypeError, ValueError):
                continue
    return completed


def process_book(
    source_pdf: Path,
    title: str,
    author: str,
    output_root: Path,
    page_indexes: list[int],
    config: dict[str, Any],
    requested_engines: list[str] | None = None,
    force: bool = False,
    progress: ProgressCallback = print,
    resume_book_root: Path | None = None,
    processing_reason: str = "initial_import",
) -> Path:
    source_pdf = source_pdf.resolve()
    source_hash = sha256_file(source_pdf)
    registry = EngineRegistry(config["ocr"], Path(__file__).resolve().parents[2])
    primary_name, engine_statuses = registry.required_primary(requested_engines)
    engines = registry.available(requested_engines, engine_statuses)
    generated_book_id = f"{slugify(title)}-{source_hash[:10]}-{primary_name}"
    book_root = (
        resume_book_root.resolve()
        if resume_book_root is not None
        else output_root.resolve() / generated_book_id
    )
    pages_root = book_root / "pages"
    pages_root.mkdir(parents=True, exist_ok=True)
    primary_engine = next(
        (engine for engine in engines if engine.name == primary_name),
        None,
    )
    embedded_engines = [engine for engine in engines if engine.name == "embedded"]
    secondary_engines = [
        engine
        for engine in engines
        if engine is not primary_engine and engine.name != "embedded"
    ]

    with PDFSource(source_pdf) as pdf:
        manifest_path = book_root / "book.json"
        existing_manifest: dict[str, Any] = {}
        if manifest_path.exists():
            existing_manifest = read_json(manifest_path)
            if existing_manifest.get("source_sha256") != source_hash:
                raise ValueError(
                    "The existing book workspace belongs to a different PDF"
                )
        now = dt.datetime.now(dt.UTC).isoformat()
        book_id = str(existing_manifest.get("book_id") or book_root.name)
        requested_page_numbers = [index + 1 for index in page_indexes]
        completed_pages = _completed_page_numbers(pages_root)
        manifest = {
            "schema_version": 1,
            "pipeline_version": __version__,
            "book_id": book_id,
            "title": nfc(title),
            "author": nfc(author),
            "source_pdf": str(source_pdf),
            "source_sha256": source_hash,
            "source_page_count": pdf.page_count,
            "processed_pages": sorted(completed_pages),
            "requested_pages": requested_page_numbers,
            "created_utc": existing_manifest.get(
                "created_utc",
                existing_manifest.get("created_or_updated_utc", now),
            ),
            "updated_utc": now,
            "config": config,
            "engine_status": engine_statuses,
            "ocr_plan": {
                "required_primary_engine": primary_name,
                "selected_primary_engine": (
                    primary_engine.name if primary_engine is not None else None
                ),
                "requested_engines": [engine.name for engine in engines],
                "automatic_fallback_enabled": False,
            },
        }
        processing_path = book_root / "audit" / "processing.json"
        processing_audit: dict[str, Any] = {}
        if processing_path.exists():
            try:
                processing_audit = read_json(processing_path)
            except (OSError, ValueError, json.JSONDecodeError):
                processing_audit = {}
        processing_audit.update(
            {
                "schema_version": 1,
                "pipeline_version": manifest["pipeline_version"],
                "source_pdf": str(source_pdf),
                "source_sha256": source_hash,
                "source_page_count": pdf.page_count,
                "requested_pages": requested_page_numbers,
                "all_processed_pages": sorted(completed_pages),
                "updated_utc": now,
                "engine_status": manifest["engine_status"],
                "ocr_plan": manifest["ocr_plan"],
            }
        )
        processing_runs = processing_audit.setdefault("runs", [])
        processing_runs.append(
            {
                "started_utc": now,
                "reason": processing_reason,
                "required_primary_engine": primary_name,
                "requested_engines": [engine.name for engine in engines],
                "requested_pages": requested_page_numbers,
                "completed_pages_at_start": sorted(completed_pages),
                "automatic_fallback_enabled": False,
            }
        )

        def persist_progress() -> None:
            updated_utc = dt.datetime.now(dt.UTC).isoformat()
            manifest["processed_pages"] = sorted(completed_pages)
            manifest["updated_utc"] = updated_utc
            processing_audit["all_processed_pages"] = sorted(completed_pages)
            processing_audit["updated_utc"] = updated_utc
            write_json(manifest_path, manifest)
            write_json(processing_path, processing_audit)

        persist_progress()

        work_items: list[tuple[int, int]] = []
        for position, page_index in enumerate(page_indexes, start=1):
            page_number = page_index + 1
            page_root = pages_root / f"{page_number:04d}"
            page_state_path = page_root / "page.json"
            if page_state_path.exists() and not force:
                existing = json.loads(page_state_path.read_text(encoding="utf-8"))
                if existing.get("processing_complete"):
                    completed_pages.add(page_number)
                    persist_progress()
                    progress(
                        f"[{position}/{len(page_indexes)}] Page {page_number}: already complete"
                    )
                    continue
            work_items.append((position, page_index))

        processing_run = processing_runs[-1]
        prefetch_pages = int(
            config.get("performance", {}).get("prefetch_pages", 1)
        )
        if prefetch_pages != 1:
            raise ValueError(
                "This low-memory pipeline requires performance.prefetch_pages=1"
            )
        processing_run["prefetch_pages"] = prefetch_pages
        processing_run["timing"] = {
            "pages_completed": 0,
            "stage_totals_seconds": {},
            "recent_average_page_seconds": None,
            "estimated_remaining_seconds": None,
        }
        recent_page_seconds: list[float] = []
        eta_window_pages = max(
            1,
            int(config.get("performance", {}).get("eta_window_pages", 8)),
        )
        last_completion = time.perf_counter()
        prefetch: PagePreparation | None = None
        if work_items:
            first_position, first_index = work_items[0]
            progress(
                f"[{first_position}/{len(page_indexes)}] "
                f"Page {first_index + 1}: preparing"
            )
            prefetch = PagePreparation(
                source_pdf,
                first_index,
                pages_root / f"{first_index + 1:04d}",
                config,
            )

        for work_position, (position, page_index) in enumerate(work_items):
            if prefetch is None:
                raise RuntimeError("Page prefetch pipeline lost its current page")
            prepared, prefetch_wait_seconds = prefetch.result()
            if work_position + 1 < len(work_items):
                next_position, next_index = work_items[work_position + 1]
                prefetch = PagePreparation(
                    source_pdf,
                    next_index,
                    pages_root / f"{next_index + 1:04d}",
                    config,
                )
            else:
                prefetch = None

            page_number = prepared.page_number
            page_root = prepared.page_root
            page_state_path = page_root / "page.json"
            evidence_dir = page_root / "evidence"
            source_image_path = prepared.source_image_path
            selected_image_path = prepared.selected_image_path
            preprocessing = prepared.preprocessing
            page_timings = dict(prepared.timings)
            page_timings["prefetch_wait_seconds"] = round(
                prefetch_wait_seconds, 6
            )
            embedded_started = time.perf_counter()
            embedded_text = pdf.embedded_text(page_index)
            page_timings["embedded_text_seconds"] = round(
                time.perf_counter() - embedded_started, 6
            )
            ocr_seconds = 0.0
            model_startup_seconds = 0.0
            inference_seconds = 0.0
            selection_seconds = 0.0
            candidates: list[OCRCandidate] = []
            errors: list[dict[str, Any]] = []
            ocr_attempts: list[dict[str, Any]] = []
            variants = [("original", preprocessing.original)]
            if preprocessing.selected_name != "original":
                variants.append(
                    (preprocessing.selected_name, preprocessing.selected)
                )

            first_pass_engines = (
                ([primary_engine] if primary_engine is not None else [])
                + embedded_engines
            )
            for engine in first_pass_engines:
                engine_variants = (
                    variants
                    if engine.supports_preprocessed_variant
                    and config["ocr"]["run_original_and_selected_variant"]
                    else [variants[-1]]
                )
                for variant_name, variant_image in engine_variants:
                    progress(
                        f"[{position}/{len(page_indexes)}] Page {page_number}: "
                        f"{engine.name} ({variant_name})"
                    )
                    ocr_started = time.perf_counter()
                    result = _run_engine(
                        engine,
                        variant_image,
                        variant_name,
                        embedded_text,
                        evidence_dir,
                        progress=progress,
                        progress_prefix=(
                            f"[{position}/{len(page_indexes)}] Page {page_number}"
                        ),
                    )
                    ocr_seconds += time.perf_counter() - ocr_started
                    ocr_attempts.extend(result.attempts)
                    if result.candidate:
                        candidates.append(result.candidate)
                        model_startup_seconds += float(
                            result.candidate.diagnostics.get(
                                "model_startup_seconds", 0.0
                            )
                        )
                        inference_seconds += float(
                            result.candidate.diagnostics.get(
                                "inference_seconds", 0.0
                            )
                        )
                    errors.extend(result.errors)
                    if engine is primary_engine and result.candidate is None:
                        if not result.errors:
                            errors.append(
                                {
                                    "engine": engine.name,
                                    "variant": variant_name,
                                    "error": "Required OCR engine returned no candidate",
                                }
                            )
                        _write_failed_page_state(
                            page_state_path=page_state_path,
                            page_number=page_number,
                            source_image_path=source_image_path,
                            selected_image_path=selected_image_path,
                            preprocessing=preprocessing,
                            primary_name=primary_name,
                            attempts=ocr_attempts,
                            errors=errors,
                            timings={
                                **page_timings,
                                "ocr_seconds": round(ocr_seconds, 6),
                                "model_startup_seconds": round(
                                    model_startup_seconds, 6
                                ),
                                "inference_seconds": round(
                                    inference_seconds, 6
                                ),
                                "selection_seconds": round(
                                    selection_seconds, 6
                                ),
                            },
                        )
                        completed_pages.discard(page_number)
                        persist_progress()
                        _close_prepared_page(prepared)
                        if prefetch is not None:
                            prefetch.discard()
                        raise RequiredEngineFailedError(
                            f"Page {page_number}: required OCR engine "
                            f"'{primary_name}' failed after bounded recovery. "
                            "No fallback OCR engine was used. Resume the same "
                            "document after correcting the engine problem."
                        )

            selection_started = time.perf_counter()
            candidates, disagreement = score_candidates(
                candidates, config["selection"]
            )
            selection_seconds += time.perf_counter() - selection_started
            best_candidate = candidates[0] if candidates else None
            low_ocr_quality = (
                best_candidate is None
                or not best_candidate.text.strip()
                or best_candidate.score
                < float(config["selection"]["minimum_candidate_score"])
                or (
                    best_candidate.confidence is not None
                    and best_candidate.confidence
                    < float(config["selection"]["minimum_engine_confidence"])
                )
            )
            use_secondary = bool(secondary_engines) and (
                not config["ocr"].get(
                    "secondary_engines_on_uncertain_only", True
                )
                or low_ocr_quality
            )
            if use_secondary:
                for engine in secondary_engines:
                    engine_variants = (
                        variants
                        if engine.supports_preprocessed_variant
                        and config["ocr"]["run_original_and_selected_variant"]
                        else [variants[-1]]
                    )
                    for variant_name, variant_image in engine_variants:
                        progress(
                            f"[{position}/{len(page_indexes)}] Page {page_number}: "
                            f"{engine.name} verification ({variant_name})"
                        )
                        ocr_started = time.perf_counter()
                        result = _run_engine(
                            engine,
                            variant_image,
                            variant_name,
                            embedded_text,
                            evidence_dir,
                            progress=progress,
                            progress_prefix=(
                                f"[{position}/{len(page_indexes)}] "
                                f"Page {page_number}"
                            ),
                        )
                        ocr_seconds += time.perf_counter() - ocr_started
                        ocr_attempts.extend(result.attempts)
                        if result.candidate:
                            candidates.append(result.candidate)
                            model_startup_seconds += float(
                                result.candidate.diagnostics.get(
                                    "model_startup_seconds", 0.0
                                )
                            )
                            inference_seconds += float(
                                result.candidate.diagnostics.get(
                                    "inference_seconds", 0.0
                                )
                            )
                        errors.extend(result.errors)
                selection_started = time.perf_counter()
                candidates, disagreement = score_candidates(
                    candidates, config["selection"]
                )
                selection_seconds += time.perf_counter() - selection_started

            selection_started = time.perf_counter()
            decision = decide_page(
                candidates,
                disagreement,
                page_index,
                pdf.page_count,
                config["selection"],
                config["classification"],
                preprocessing.diagnostics()["metrics"],
            )
            selection_seconds += time.perf_counter() - selection_started
            surya_candidates = [
                candidate
                for candidate in candidates
                if candidate.engine == "surya" and candidate.lines
            ]
            surya_baseline = (
                max(surya_candidates, key=lambda candidate: candidate.score)
                if surya_candidates
                else None
            )
            variant_images = {name: image for name, image in variants}
            baseline_image = (
                variant_images.get(surya_baseline.variant)
                if surya_baseline is not None
                else None
            )
            if baseline_image is None and surya_baseline is not None:
                baseline_image = preprocessing.selected
            (
                multiscale,
                multiscale_ocr_seconds,
                multiscale_startup_seconds,
                multiscale_inference_seconds,
                multiscale_attempts,
            ) = _run_multiscale_pass(
                pdf=pdf,
                page_index=page_index,
                page_number=page_number,
                page_root=page_root,
                engine=primary_engine,
                baseline=surya_baseline,
                baseline_image=baseline_image,
                baseline_operations=(
                    preprocessing.operations
                    if baseline_image is preprocessing.selected
                    else []
                ),
                original_image_size=preprocessing.original.size,
                decision_status=decision.status,
                config=config,
                progress=progress,
                progress_prefix=(
                    f"[{position}/{len(page_indexes)}] Page {page_number}"
                ),
            )
            ocr_seconds += multiscale_ocr_seconds
            model_startup_seconds += multiscale_startup_seconds
            inference_seconds += multiscale_inference_seconds
            ocr_attempts.extend(multiscale_attempts)
            if int(multiscale.get("pending_count", 0)):
                decision.status = "needs_review"
                reason = (
                    f"{multiscale['pending_count']} high-resolution crop "
                    "reading(s) disagree with the full-page Surya result"
                )
                if reason not in decision.reasons:
                    decision.reasons.append(reason)
            if int(multiscale.get("unreadable_count", 0)):
                decision.status = "needs_review"
                reason = (
                    f"{multiscale['unreadable_count']} suspicious crop "
                    "region(s) could not be reread"
                )
                if reason not in decision.reasons:
                    decision.reasons.append(reason)
            page_save_started = time.perf_counter()
            write_text(page_root / "draft.txt", decision.text)
            candidate_values: list[dict[str, Any]] = []
            for candidate in candidates:
                evidence_name = f"{candidate.engine}-{candidate.variant}.json"
                candidate_values.append(
                    _candidate_without_raw(
                        candidate, f"evidence/{evidence_name}"
                    )
                )
            page_state = {
                "schema_version": 2,
                "page_number": page_number,
                "source_image": source_image_path.name,
                "selected_image": selected_image_path.name,
                "preprocessing": preprocessing.diagnostics(),
                "candidates": candidate_values,
                "decision": decision.to_dict(),
                "manual": {
                    "status": "unreviewed",
                    "include": decision.include,
                    "page_role": decision.page_role,
                    "heading": "",
                    "break_before": False,
                    "join_without_space": False,
                    "preserve_trailing_hyphen": False,
                    "reviewer": "",
                    "reviewed_utc": null_value(),
                },
                "ocr_attempts": ocr_attempts,
                "multiscale": multiscale,
                "timings": {
                    **page_timings,
                    "ocr_seconds": round(ocr_seconds, 6),
                    "model_startup_seconds": round(
                        model_startup_seconds, 6
                    ),
                    "inference_seconds": round(inference_seconds, 6),
                    "selection_seconds": round(selection_seconds, 6),
                    "multiscale_ocr_seconds": round(
                        multiscale_ocr_seconds, 6
                    ),
                    "page_save_seconds": 0.0,
                },
                "errors": errors,
                "processing_complete": True,
            }
            normalize_page_state(page_state)
            write_json(page_state_path, page_state)
            completed_pages.add(page_number)
            persist_progress()
            page_save_seconds = time.perf_counter() - page_save_started
            page_timings.update(
                {
                    "ocr_seconds": round(ocr_seconds, 6),
                    "model_startup_seconds": round(
                        model_startup_seconds, 6
                    ),
                    "inference_seconds": round(inference_seconds, 6),
                    "selection_seconds": round(selection_seconds, 6),
                    "multiscale_ocr_seconds": round(
                        multiscale_ocr_seconds, 6
                    ),
                    "page_save_seconds": round(page_save_seconds, 6),
                }
            )
            page_timings["page_total_stage_seconds"] = round(
                float(page_timings.get("preparation_total_seconds", 0.0))
                + float(page_timings.get("embedded_text_seconds", 0.0))
                + ocr_seconds
                + selection_seconds
                + page_save_seconds,
                6,
            )
            now_perf = time.perf_counter()
            throughput_page_seconds = now_perf - last_completion
            last_completion = now_perf
            recent_page_seconds.append(throughput_page_seconds)
            recent_page_seconds = recent_page_seconds[-eta_window_pages:]
            average_page_seconds = sum(recent_page_seconds) / len(
                recent_page_seconds
            )
            remaining_pages = len(work_items) - work_position - 1
            eta_seconds = average_page_seconds * remaining_pages
            page_timings["throughput_page_seconds"] = round(
                throughput_page_seconds, 6
            )
            page_state["timings"] = page_timings
            write_json(page_state_path, page_state)

            timing_summary = processing_run["timing"]
            timing_summary["pages_completed"] = work_position + 1
            timing_summary["recent_average_page_seconds"] = round(
                average_page_seconds, 3
            )
            timing_summary["estimated_remaining_seconds"] = round(
                eta_seconds, 1
            )
            stage_totals = timing_summary["stage_totals_seconds"]
            for name in (
                "render_seconds",
                "preprocessing_seconds",
                "image_encoding_seconds",
                "embedded_text_seconds",
                "ocr_seconds",
                "model_startup_seconds",
                "inference_seconds",
                "selection_seconds",
                "multiscale_ocr_seconds",
                "page_save_seconds",
                "prefetch_wait_seconds",
            ):
                stage_totals[name] = round(
                    float(stage_totals.get(name, 0.0))
                    + float(page_timings.get(name, 0.0)),
                    6,
                )
            persist_progress()
            _close_prepared_page(prepared)
            progress(
                f"[{position}/{len(page_indexes)}] Page {page_number}: "
                f"{decision.status}, {decision.selected_engine or 'no OCR'} "
                f"| metrics page={throughput_page_seconds:.3f} "
                f"average={average_page_seconds:.3f} eta={eta_seconds:.1f}"
            )
        processing_run["finished_utc"] = dt.datetime.now(dt.UTC).isoformat()
        processing_run["completed_pages_at_end"] = sorted(completed_pages)
        persist_progress()
    return book_root


def null_value() -> None:
    return None
