from __future__ import annotations

import copy
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import secrets
import threading
import time
import traceback
import webbrowser
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from . import __version__
from .assemble import finalize_book
from .config import APPLICATION_HOME, PIPELINE_ROOT, load_config
from .engines import EngineRegistry
from .engines.registry import RequiredEngineUnavailableError
from .engines.surya_engine import SuryaEngine
from .engines.surya_hardware import probe_surya_hardware
from .engines.surya_policy import (
    decide_surya_runtime_fallback,
    decide_surya_backend,
    parse_surya_request,
    ranked_runtime_devices,
)
from .openrouter_client import (
    CropRequest,
    OpenRouterClient,
    OpenRouterError,
    crop_page_region,
    load_openrouter_settings,
    proposal_request_id,
)
from .pdf import PDFSource
from .processor import RequiredEngineFailedError, process_book
from .storage import (
    cleanup_disposable_files,
    directory_size,
    estimate_workspace_bytes,
    format_bytes,
    resource_snapshot,
    rotate_file,
    runtime_paths,
    storage_preflight,
    update_resource_extrema,
)
from .utils import (
    nfc,
    parse_page_spec,
    read_json,
    sha256_file,
    slugify,
    write_json,
    write_text,
)
from .validation import text_anomalies, validate_book
from .workflow import normalize_page_state, set_human_status, workflow_summary


ProcessFunction = Callable[..., Path]
PAGE_ROLES = [
    "body_text",
    "section_heading",
    "story",
    "chapter_heading",
    "table",
    "caption",
    "references",
    "other_content",
    "front_matter",
    "advertisement_or_uploader_page",
    "blank_or_visual",
    "non_story_or_uncertain",
    "unreadable",
]
PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]")
PROGRESS_METRICS_RE = re.compile(
    r"\s+\| metrics page=([0-9.]+) average=([0-9.]+) eta=([0-9.]+)$"
)
JOB_HEARTBEAT_SECONDS = 10
JOB_STALL_SECONDS = 15 * 60


class _SessionCredential:
    def __init__(self, profiles: "SessionAIProfiles", profile_id: str) -> None:
        self._profiles = profiles
        self._profile_id = profile_id

    def get(self) -> str | None:
        return self._profiles.api_key(self._profile_id)


class SessionAIProfiles:
    """Keep each visitor's model choice and API key isolated in server memory."""

    def __init__(self, defaults: dict[str, Any]) -> None:
        self._lock = threading.Lock()
        self._defaults = {
            "enabled": False,
            "model": str(defaults.get("model", "openrouter/free")),
            "daily_request_limit": int(defaults.get("daily_request_limit", 40)),
        }
        self._profiles: dict[str, dict[str, Any]] = {}
        self._daily_usage: dict[tuple[str, str], int] = {}

    def get(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            value = self._profiles.get(profile_id, self._defaults)
            return {
                "enabled": bool(value.get("enabled", False)),
                "model": str(value.get("model", self._defaults["model"])),
                "daily_request_limit": int(
                    value.get(
                        "daily_request_limit",
                        self._defaults["daily_request_limit"],
                    )
                ),
            }

    def update(
        self,
        profile_id: str,
        *,
        enabled: bool,
        model: str,
        daily_request_limit: int,
        api_key: str = "",
        remove_key: bool = False,
    ) -> dict[str, Any]:
        clean_model = model.strip() or str(self._defaults["model"])
        clean_limit = max(1, min(200, int(daily_request_limit)))
        with self._lock:
            profile = self._profiles.setdefault(
                profile_id, dict(self._defaults)
            )
            profile.update(
                {
                    "enabled": enabled,
                    "model": clean_model,
                    "daily_request_limit": clean_limit,
                }
            )
            if api_key.strip():
                profile["api_key"] = api_key.strip()
            if remove_key:
                profile.pop("api_key", None)
        return self.get(profile_id)

    def api_key(self, profile_id: str) -> str | None:
        with self._lock:
            value = self._profiles.get(profile_id, {}).get("api_key")
            return str(value) if value else None

    def credential(self, profile_id: str) -> _SessionCredential:
        return _SessionCredential(self, profile_id)

    def used_today(self, profile_id: str, date: str) -> int:
        with self._lock:
            return self._daily_usage.get((profile_id, date), 0)

    def record_usage(self, profile_id: str, date: str) -> None:
        with self._lock:
            key = (profile_id, date)
            self._daily_usage[key] = self._daily_usage.get(key, 0) + 1


def _duration_label(seconds: float) -> str:
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _job_progress(message: str) -> dict[str, Any]:
    match = PROGRESS_RE.match(message)
    if not match:
        return {}
    current, total = (int(value) for value in match.groups())
    percent = round((current / total) * 100) if total else 0
    value: dict[str, Any] = {
        "progress_current": current,
        "progress_total": total,
        "progress_percent": max(0, min(100, percent)),
    }
    metrics = PROGRESS_METRICS_RE.search(message)
    if metrics:
        page_seconds, average_seconds, eta_seconds = (
            float(item) for item in metrics.groups()
        )
        value.update(
            {
                "last_page_seconds": round(page_seconds, 3),
                "average_page_seconds": round(average_seconds, 3),
                "eta_seconds": round(eta_seconds, 1),
                "last_page_duration": _duration_label(page_seconds),
                "average_page_duration": _duration_label(average_seconds),
                "eta": _duration_label(eta_seconds),
                "progress_message": message[: metrics.start()].strip(),
            }
        )
    return value


def _failure_details(exc: Exception, engine: str) -> dict[str, str]:
    """Turn a technical worker exception into safe, actionable UI copy."""
    technical = str(exc).strip() or exc.__class__.__name__
    lowered = technical.casefold()
    label = engine.title() if engine else "OCR"
    runtime_path = os.environ.get("SURYA_RUNTIME_DIR", "")
    if any(term in lowered for term in ("no space", "disk full", "storage")):
        category = "storage"
        title = "Processing stopped because storage is unavailable"
        explanation = (
            "The workspace is preserved. Free space on the reported drive, "
            "then retry the same OCR engine."
        )
    elif "untrusted mount point" in lowered or "winerror 448" in lowered:
        category = "runtime_cache"
        title = f"{label} could not open its temporary workspace"
        explanation = (
            "Completed pages are safe. Check that the runtime folder shown "
            "below is a writable local folder, then retry the same engine."
        )
    elif any(term in lowered for term in ("cuda", "gpu", "llama", "server")):
        category = "engine_runtime"
        title = f"{label} stopped or lost its inference runtime"
        explanation = (
            "Completed pages are safe. Retry the same engine first; choose a "
            "different installed engine only if you accept mixed OCR output."
        )
    elif any(term in lowered for term in ("unavailable", "not installed", "missing")):
        category = "engine_unavailable"
        title = f"{label} is currently unavailable"
        explanation = (
            "Check the engine diagnostic below or run the health check, then "
            "retry. You may explicitly continue with another available engine."
        )
    elif any(term in lowered for term in ("pdf", "render", "page image")):
        category = "source_or_render"
        title = "The PDF page could not be prepared"
        explanation = (
            "The original PDF and completed pages remain preserved. Check that "
            "the source file is readable before retrying."
        )
    else:
        category = "ocr_failure"
        title = f"{label} could not finish the current page"
        explanation = (
            "Completed pages and failure evidence are preserved. Retrying "
            "continues from unfinished pages rather than starting over."
        )
    return {
        "category": category,
        "title": title,
        "explanation": explanation,
        "technical": technical,
        "runtime_path": runtime_path if category == "runtime_cache" else "",
        "engine": engine,
        "exception_type": exc.__class__.__name__,
        "occurred_utc": dt.datetime.now(dt.UTC).isoformat(),
    }


def _is_surya_runtime_failure(exc: Exception) -> bool:
    return isinstance(
        exc,
        (RequiredEngineFailedError, RequiredEngineUnavailableError),
    )


class JobRegistry:
    def __init__(
        self,
        storage_root: Path | None = None,
        *,
        stall_seconds: int = JOB_STALL_SECONDS,
    ) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._storage_root = storage_root
        self._stall_seconds = max(1, int(stall_seconds))
        if storage_root is not None:
            storage_root.mkdir(parents=True, exist_ok=True)
            for path in storage_root.glob("*.json"):
                try:
                    job = read_json(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                job_id = str(job.get("job_id", path.stem))
                if job.get("status") in {"queued", "running", "stalled"}:
                    completed = max(
                        0, int(job.get("completed_page_count") or 0)
                    )
                    workspace_id = str(job.get("workspace_book_id") or "")
                    if workspace_id and storage_root.parent.name == "_app":
                        book_root = storage_root.parent.parent / workspace_id
                        if (book_root / "audit" / "processing.json").is_file():
                            raw_indexes = job.get("page_indexes")
                            selected_pages = (
                                {
                                    value + 1
                                    for value in raw_indexes
                                    if isinstance(value, int) and value >= 0
                                }
                                if isinstance(raw_indexes, list)
                                else None
                            )
                            completed = _completed_page_count(
                                book_root,
                                selected_pages=selected_pages,
                            )
                    total = max(0, int(job.get("progress_total") or 0))
                    job.update(
                        {
                            "status": "interrupted",
                            "message": (
                                "The app stopped during this job. Import the same "
                                "PDF and title again to resume unfinished pages."
                            ),
                            "completed_page_count": completed,
                            "progress_current": completed,
                            "progress_percent": (
                                round(completed / total * 100) if total else 0
                            ),
                        }
                    )
                self._jobs[job_id] = job
                self._persist(job_id, job)

    def _persist(self, job_id: str, value: dict[str, Any]) -> None:
        if self._storage_root is not None:
            write_json(self._storage_root / f"{job_id}.json", value)

    def put(self, job_id: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            current = self._jobs.setdefault(
                job_id,
                {
                    "job_id": job_id,
                    "status": "queued",
                    "message": "Queued",
                    "created_utc": dt.datetime.now(dt.UTC).isoformat(),
                },
            )
            current.update(values)
            current["updated_utc"] = dt.datetime.now(dt.UTC).isoformat()
            self._persist(job_id, current)
            return dict(current)

    def _with_runtime_status(self, value: dict[str, Any]) -> dict[str, Any]:
        result = dict(value)
        if result.get("status") not in {"queued", "running"}:
            return result
        reference = result.get("last_progress_utc") or result.get("created_utc")
        try:
            last_progress = dt.datetime.fromisoformat(str(reference))
            if last_progress.tzinfo is None:
                last_progress = last_progress.replace(tzinfo=dt.UTC)
            elapsed = (dt.datetime.now(dt.UTC) - last_progress).total_seconds()
        except (TypeError, ValueError):
            return result
        if elapsed >= self._stall_seconds:
            result["status"] = "stalled"
            result["message"] = (
                f"No OCR stage completed for {round(elapsed / 60)} minutes. "
                "The job is preserved; check the engine before resuming."
            )
            result["stalled_seconds"] = round(elapsed)
        return result

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._jobs.get(job_id)
            return self._with_runtime_status(value) if value else None

    def values(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._with_runtime_status(value)
                for value in self._jobs.values()
            ]

    def running(self) -> bool:
        return any(
            value.get("status") in {"queued", "running", "stalled"}
            for value in self.values()
        )


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _completed_page_count(
    book_root: Path,
    *,
    selected_pages: set[int] | None = None,
) -> int:
    audit_path = book_root / "audit" / "processing.json"
    if not audit_path.is_file():
        return 0
    try:
        audit = read_json(audit_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0
    pages = audit.get("all_processed_pages", [])
    if not isinstance(pages, list):
        return 0
    completed = {
        page for page in pages if isinstance(page, int) and page > 0
    }
    if selected_pages is not None:
        completed &= selected_pages
    return len(completed)


def _safe_child(root: Path, value: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / value).resolve()
    if candidate.parent != resolved_root:
        abort(404)
    return candidate


def _mark_validation_stale(book_root: Path, page_number: int) -> None:
    report_path = book_root / "structural-validation.json"
    if not report_path.exists():
        return
    report = read_json(report_path)
    report["stale"] = True
    report["stale_after_page"] = page_number
    report["stale_utc"] = dt.datetime.now(dt.UTC).isoformat()
    write_json(report_path, report)


def _normalize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    value = dict(manifest)
    if str(value.get("author", "")).strip() in {"", "লেখক অজ্ঞাত"}:
        value["author"] = "Unknown author"
    return value


def _book_manifest(book_root: Path) -> dict[str, Any]:
    path = book_root / "book.json"
    if not path.exists():
        abort(404)
    return _normalize_manifest(read_json(path))


def _page_state_path(book_root: Path, page_number: int) -> Path:
    path = book_root / "pages" / f"{page_number:04d}" / "page.json"
    if not path.exists():
        abort(404)
    return path


def _load_pages(book_root: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for path in sorted((book_root / "pages").glob("*/page.json")):
        before = read_json(path)
        before_serialized = json.dumps(before, ensure_ascii=False, sort_keys=True)
        page = normalize_page_state(before)
        if json.dumps(page, ensure_ascii=False, sort_keys=True) != before_serialized:
            write_json(path, page)
        text_path = path.parent / "final.txt"
        if not text_path.exists():
            text_path = path.parent / "draft.txt"
        text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""
        page["anomalies"] = text_anomalies(text)
        page["proposal_count"] = sum(
            1
            for value in _read_jsonl(
                path.parent / "evidence" / "openrouter-proposals.jsonl"
            )
            if value.get("status") == "proposed"
        )
        pages.append(page)
    return pages


def _selected_lines(page: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = page.get("candidates", [])
    selected_index = page.get("decision", {}).get("selected_candidate")
    if not isinstance(selected_index, int) or selected_index >= len(candidates):
        selected_index = 0
    if not candidates:
        return []
    return list(candidates[selected_index].get("lines", []))


def _multiscale_regions(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        region
        for region in page.get("multiscale", {}).get("regions", [])
        if region.get("status") in {
            "pending",
            "accepted",
            "kept_full_page",
            "failed",
            "unreadable",
        }
    ]


def _current_text(page_root: Path) -> tuple[Path, str]:
    final_path = page_root / "final.txt"
    draft_path = page_root / "draft.txt"
    path = final_path if final_path.exists() else draft_path
    return path, path.read_text(encoding="utf-8") if path.exists() else ""


def _create_revision(page_root: Path, text: str, reason: str) -> Path:
    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    revision = page_root / "revisions" / f"{timestamp}-{slugify(reason)}.txt"
    write_text(revision, text)
    return revision


def _proposal_events(page_root: Path) -> list[dict[str, Any]]:
    return _read_jsonl(
        page_root / "evidence" / "openrouter-proposals.jsonl"
    )


def _proposal_view(page_root: Path) -> list[dict[str, Any]]:
    events = _proposal_events(page_root)
    decisions: dict[tuple[str, int], str] = {}
    for event in events:
        if event.get("event") in {"accepted", "rejected"}:
            decisions[
                (str(event.get("request_id")), int(event.get("change_index", -1)))
            ] = str(event["event"])
    proposals: list[dict[str, Any]] = []
    for event in events:
        if event.get("status") != "proposed":
            continue
        value = dict(event)
        changes = []
        for index, change in enumerate(event.get("changes", [])):
            rendered = dict(change)
            rendered["index"] = index
            rendered["review_status"] = decisions.get(
                (str(event.get("request_id")), index), "pending"
            )
            changes.append(rendered)
        value["changes"] = changes
        proposals.append(value)
    return proposals


def create_application(
    *,
    output_root: Path | None = None,
    source_root: Path | None = None,
    single_book_root: Path | None = None,
    process_function: ProcessFunction = process_book,
) -> Flask:
    config = load_config()
    output_root = (
        output_root or Path(config["output"]["default_root"])
    ).resolve()
    source_root = (
        source_root
        or (
            APPLICATION_HOME / "documents" / "imports"
            if APPLICATION_HOME is not None
            else PIPELINE_ROOT.parent / "sources" / "imports"
        )
    ).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    source_root.mkdir(parents=True, exist_ok=True)
    settings_path = PIPELINE_ROOT / "config" / "openrouter.json"
    runtime_settings_path = output_root / "_app" / "runtime-settings.json"
    try:
        saved_runtime_settings = read_json(runtime_settings_path)
    except (OSError, ValueError, json.JSONDecodeError):
        saved_runtime_settings = {}
    configured_runtime = str(
        saved_runtime_settings.get(
            "surya_backend",
            config["ocr"].get("surya_runtime_backend", "auto"),
        )
    ).strip()
    try:
        parse_surya_request(configured_runtime)
    except ValueError:
        configured_runtime = "auto"
    config["ocr"]["surya_runtime_backend"] = configured_runtime
    saved_validation = saved_runtime_settings.get("startup_validation", {})
    if not isinstance(saved_validation, dict):
        saved_validation = {}
    if saved_validation.get("status") == "running":
        saved_validation = {
            "status": "interrupted",
            "reason": "The app closed before the startup check finished.",
        }
    jobs = JobRegistry(output_root / "_app" / "jobs")
    ai_profiles = SessionAIProfiles(load_openrouter_settings(settings_path))
    ocr_registry = EngineRegistry(config["ocr"], PIPELINE_ROOT)
    hardware_cache: dict[str, Any] = {
        "report": None,
        "monotonic": 0.0,
    }
    runtime_settings_lock = threading.Lock()
    startup_validation: dict[str, Any] = saved_validation or {
        "status": "not_run"
    }

    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    app.config.update(
        JSON_AS_ASCII=False,
        MAX_CONTENT_LENGTH=1024 * 1024 * 1024,
    )

    def ai_profile_id() -> str:
        profile_id = session.get("ai_profile_id")
        if not profile_id:
            profile_id = secrets.token_urlsafe(24)
            session["ai_profile_id"] = profile_id
        return str(profile_id)

    def persist_runtime_settings() -> None:
        with runtime_settings_lock:
            value = {
                "surya_backend": str(
                    config["ocr"].get("surya_runtime_backend", "auto")
                ),
                "startup_validation": copy.deepcopy(startup_validation),
            }
            write_json(runtime_settings_path, value)

    def surya_hardware_report(*, refresh: bool = False) -> dict[str, Any]:
        if refresh:
            hardware_cache["report"] = None
            hardware_cache["monotonic"] = 0.0
        report = hardware_cache.get("report")
        age = time.monotonic() - float(hardware_cache.get("monotonic") or 0)
        if isinstance(report, dict) and age < 30:
            return report
        report = probe_surya_hardware(PIPELINE_ROOT)
        hardware_cache["report"] = report
        hardware_cache["monotonic"] = time.monotonic()
        return report

    def surya_settings_view(*, refresh: bool = False) -> dict[str, Any]:
        report = surya_hardware_report(refresh=refresh)
        requested = str(config["ocr"].get("surya_runtime_backend", "auto"))
        decision = decide_surya_backend(
            PIPELINE_ROOT,
            requested=requested,
            hardware_report=report,
        )
        automatic_decision = decide_surya_backend(
            PIPELINE_ROOT,
            requested="auto",
            hardware_report=report,
        )
        runtime_values = report.get("runtimes", {})
        runtimes = []
        for backend in ("cuda", "vulkan", "cpu"):
            value = dict(runtime_values.get(backend, {}))
            value.setdefault("backend", backend)
            value.setdefault("display_name", backend.upper())
            value.setdefault("installed", False)
            value.setdefault("usable", False)
            value.setdefault("status", "unknown")
            value.setdefault("reason", "No diagnostic result is available")
            runtimes.append(value)
        choices: list[dict[str, Any]] = [
            {
                "value": "auto",
                "backend": "auto",
                "device": None,
                "label": "Automatic",
                "detail": automatic_decision.reason,
                "usable": automatic_decision.usable,
                "experimental": False,
            }
        ]
        for runtime in runtimes:
            backend = str(runtime["backend"])
            if backend == "cpu":
                choices.append(
                    {
                        "value": "cpu",
                        "backend": "cpu",
                        "device": None,
                        "label": "CPU",
                        "detail": runtime["reason"],
                        "usable": bool(runtime["usable"]),
                        "experimental": False,
                    }
                )
                continue
            devices = ranked_runtime_devices(runtime)
            if not devices:
                choices.append(
                    {
                        "value": backend,
                        "backend": backend,
                        "device": None,
                        "label": runtime["display_name"],
                        "detail": runtime["reason"],
                        "usable": False,
                        "experimental": backend == "vulkan",
                    }
                )
                continue
            for device in devices:
                identifier = str(device["identifier"])
                choices.append(
                    {
                        "value": f"{backend}/{identifier}",
                        "backend": backend,
                        "device": identifier,
                        "label": f"{runtime['display_name']} · {identifier}",
                        "detail": str(device.get("description") or identifier),
                        "usable": bool(runtime["usable"]),
                        "experimental": backend == "vulkan",
                    }
                )
        inventory = copy.deepcopy(report.get("display_adapters", {}))
        for adapter in inventory.get("adapters", []):
            memory = adapter.get("windows_reported_adapter_ram_bytes")
            adapter["reported_memory"] = (
                format_bytes(int(memory)) if memory else "Not reported"
            )
        with runtime_settings_lock:
            validation = copy.deepcopy(startup_validation)
        tested_backend = validation.get("backend")
        tested_selection = validation.get("selection")
        tested_device = (
            tested_selection.get("device_identifier")
            if isinstance(tested_selection, dict)
            else validation.get("device")
        )
        validation["stale"] = bool(
            tested_backend
            and decision.selected_backend
            and (
                tested_backend != decision.selected_backend
                or tested_device != decision.selected_device
            )
        )
        selected_choice = requested
        if requested != "auto" and decision.selected_request:
            selected_choice = decision.selected_request
        return {
            "requested": requested,
            "selected_choice": selected_choice,
            "decision": decision.as_dict(),
            "automatic_decision": automatic_decision.as_dict(),
            "runtimes": runtimes,
            "choices": choices,
            "inventory": inventory,
            "generated_utc": report.get("generated_utc"),
            "validation": validation,
            "busy": jobs.running(),
        }

    def run_startup_validation(requested: str, selected_request: str) -> None:
        started_utc = dt.datetime.now(dt.UTC).isoformat()
        try:
            engine_config = copy.deepcopy(config["ocr"])
            engine_config["surya_runtime_backend"] = selected_request
            engine = SuryaEngine(engine_config, PIPELINE_ROOT)
            result = engine.validate_startup()
            result.update(
                {
                    "requested": requested,
                    "policy_backend": result.get("backend"),
                    "policy_request": selected_request,
                    "started_utc": started_utc,
                }
            )
            result.setdefault("backend", selected_request.partition("/")[0])
        except Exception as exc:
            result = {
                "status": "failed",
                "requested": requested,
                "backend": selected_request.partition("/")[0],
                "policy_request": selected_request,
                "started_utc": started_utc,
                "checked_utc": dt.datetime.now(dt.UTC).isoformat(),
                "reason": str(exc).strip() or exc.__class__.__name__,
                "exception_type": exc.__class__.__name__,
                "log_tail": SuryaEngine._bounded_log_tail(),
            }
        with runtime_settings_lock:
            startup_validation.clear()
            startup_validation.update(result)
        persist_runtime_settings()

    def resolve_book(book_id: str) -> Path:
        if single_book_root is not None:
            manifest = _book_manifest(single_book_root)
            if book_id in {
                single_book_root.name,
                str(manifest.get("book_id", "")),
            }:
                return single_book_root.resolve()
        root = _safe_child(output_root, book_id)
        _book_manifest(root)
        return root

    def discover_books() -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for manifest_path in sorted(
            output_root.glob("*/book.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ):
            manifest = _normalize_manifest(read_json(manifest_path))
            book_root = manifest_path.parent
            pages = _load_pages(book_root)
            values.append(
                {
                    "root": book_root,
                    "manifest": manifest,
                    "summary": workflow_summary(pages),
                    "validation": (
                        read_json(book_root / "structural-validation.json")
                        if (book_root / "structural-validation.json").exists()
                        else None
                    ),
                }
            )
        return values

    def ocr_readiness() -> dict[str, Any]:
        preferred = str(config["ocr"]["preferred_primary_engine"])
        statuses = ocr_registry.statuses()
        preferred_status = statuses.get(
            preferred,
            {"available": False, "reason": "Engine is not registered"},
        )
        labels = {
            "surya": "Surya",
            "easyocr": "EasyOCR",
            "tesseract": "Tesseract",
        }
        options: list[dict[str, Any]] = []
        engine_names = [
            name
            for name in config["ocr"]["engine_order"]
            if name != "embedded"
        ]
        engine_names.extend(
            name
            for name in statuses
            if name != "embedded" and name not in engine_names
        )
        for name in engine_names:
            if name == "embedded":
                continue
            status = statuses.get(
                name,
                {"available": False, "reason": "Engine is not registered"},
            )
            options.append(
                {
                    "name": name,
                    "label": labels.get(name, name),
                    "available": bool(status["available"]),
                    "reason": str(status["reason"]),
                    "preferred": name == preferred,
                }
            )
        return {
            "ready": bool(preferred_status["available"]),
            "engine": preferred,
            "label": labels.get(preferred, preferred),
            "reason": str(preferred_status["reason"]),
            "fallback_enabled": False,
            "statuses": statuses,
            "options": options,
        }

    def storage_status(
        estimated_workspace_bytes: int = 0,
    ) -> dict[str, Any]:
        status = storage_preflight(
            config,
            output_root,
            estimated_workspace_bytes=estimated_workspace_bytes,
        )
        paths = runtime_paths(config)
        status["runtime_root"] = str(paths["root"])
        status["temp_root"] = str(paths["temp"])
        status["runtime_usage_bytes"] = directory_size(paths["root"])
        status["runtime_usage"] = format_bytes(status["runtime_usage_bytes"])
        return status

    def run_processing_job(
        job_id: str,
        pdf_path: Path,
        title: str,
        author: str,
        page_indexes: list[int],
        ocr_engine: str,
        surya_backend_requested: str,
        resume_book_root: Path,
        processing_reason: str,
    ) -> None:
        total_pages = len(page_indexes)
        selected_pages = {page_index + 1 for page_index in page_indexes}
        completed_count = _completed_page_count(
            resume_book_root,
            selected_pages=selected_pages,
        )
        started_utc = dt.datetime.now(dt.UTC).isoformat()
        telemetry_path = resume_book_root / "audit" / "resources.jsonl"
        telemetry_interval = max(
            10,
            int(config.get("storage", {}).get("telemetry_interval_seconds", 30)),
        )
        extrema: dict[str, Any] = {}
        telemetry_lock = threading.Lock()
        runtime_state: dict[str, Any] = {
            "attempt": None,
            "attempts": None,
        }

        def record_resources(stage: str) -> None:
            nonlocal extrema
            try:
                snapshot = resource_snapshot(config, output_root)
                snapshot["stage"] = stage
                with telemetry_lock:
                    extrema = update_resource_extrema(extrema, snapshot)
                    _append_jsonl(telemetry_path, snapshot)
                    current_extrema = dict(extrema)
                jobs.put(
                    job_id,
                    resource_latest=snapshot,
                    resource_extrema=current_extrema,
                )
            except Exception as exc:
                jobs.put(job_id, resource_telemetry_error=str(exc))

        jobs.put(
            job_id,
            status="running",
            message=f"Preparing the document with {ocr_engine}",
            progress_current=completed_count,
            progress_total=total_pages,
            progress_percent=(
                round(completed_count / total_pages * 100)
                if total_pages
                else 0
            ),
            completed_page_count=completed_count,
            heartbeat_utc=started_utc,
            last_progress_utc=started_utc,
            surya_runtime_attempts=[],
            automatic_fallback_stop_reason=None,
        )
        record_resources("job_start")

        heartbeat_stop = threading.Event()

        def heartbeat() -> None:
            last_telemetry = time.monotonic()
            while not heartbeat_stop.wait(JOB_HEARTBEAT_SECONDS):
                jobs.put(
                    job_id,
                    heartbeat_utc=dt.datetime.now(dt.UTC).isoformat(),
                )
                if time.monotonic() - last_telemetry >= telemetry_interval:
                    record_resources("running")
                    last_telemetry = time.monotonic()

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()

        def progress(message: str) -> None:
            progress_utc = dt.datetime.now(dt.UTC).isoformat()
            progress_values = _job_progress(message)
            display_message = str(
                progress_values.pop("progress_message", message)
            )
            jobs.put(
                job_id,
                status="running",
                message=display_message,
                heartbeat_utc=progress_utc,
                last_progress_utc=progress_utc,
                **progress_values,
            )
            attempt = runtime_state.get("attempt")
            attempts = runtime_state.get("attempts")
            if (
                isinstance(attempt, dict)
                and isinstance(attempts, list)
                and attempt.get("status") == "running"
                and "last_page_seconds" in progress_values
            ):
                completed_count = _completed_page_count(
                    resume_book_root,
                    selected_pages=selected_pages,
                )
                if completed_count > 0:
                    attempt.update(
                        {
                            "status": "verified",
                            "verified_utc": progress_utc,
                            "completed_page_count": completed_count,
                        }
                    )
                    jobs.put(
                        job_id,
                        surya_runtime_attempts=attempts,
                        completed_page_count=completed_count,
                    )
                    _append_jsonl(
                        resume_book_root
                        / "audit"
                        / "runtime-recovery.jsonl",
                        {
                            "timestamp_utc": progress_utc,
                            "action": "runtime_verified",
                            **attempt,
                        },
                    )

        try:
            runtime_report = (
                surya_hardware_report() if ocr_engine == "surya" else None
            )
            runtime_request = surya_backend_requested
            runtime_attempts: list[dict[str, Any]] = []
            attempted_requests: list[str] = []
            attempt_reason = processing_reason

            while True:
                job_config = copy.deepcopy(config)
                backend_decision = None
                runtime_attempt: dict[str, Any] | None = None
                if ocr_engine == "surya":
                    backend_decision = decide_surya_backend(
                        PIPELINE_ROOT,
                        requested=runtime_request,
                        hardware_report=runtime_report,
                    )
                    if not backend_decision.usable:
                        raise RequiredEngineUnavailableError(
                            backend_decision.reason
                        )
                    selected_backend = str(backend_decision.selected_backend)
                    selected_request = str(backend_decision.selected_request)
                    attempted_requests.append(selected_request)
                    job_config["ocr"][
                        "surya_runtime_backend"
                    ] = selected_request
                    runtime_attempt = {
                        "attempt": len(runtime_attempts) + 1,
                        "request": selected_request,
                        "backend": selected_backend,
                        "device": backend_decision.selected_device,
                        "status": "running",
                        "reason": backend_decision.reason,
                        "started_utc": dt.datetime.now(dt.UTC).isoformat(),
                    }
                    runtime_attempts.append(runtime_attempt)
                    runtime_state["attempt"] = runtime_attempt
                    runtime_state["attempts"] = runtime_attempts
                    _append_jsonl(
                        resume_book_root / "audit" / "runtime-recovery.jsonl",
                        {
                            "timestamp_utc": runtime_attempt["started_utc"],
                            "action": "runtime_attempt_started",
                            **runtime_attempt,
                        },
                    )
                    jobs.put(
                        job_id,
                        message=(
                            f"Verifying Surya on {selected_backend.upper()} "
                            "with the first unfinished page"
                        ),
                        surya_backend=selected_backend,
                        surya_device=backend_decision.selected_device,
                        surya_runtime_selected=selected_request,
                        surya_backend_requested=surya_backend_requested,
                        surya_backend_decision=backend_decision.as_dict(),
                        surya_runtime_attempts=runtime_attempts,
                        backend_notice=(
                            backend_decision.reason
                            if backend_decision.cpu_fallback
                            else None
                        ),
                    )
                try:
                    book_root = process_function(
                        source_pdf=pdf_path,
                        title=title,
                        author=author,
                        output_root=output_root,
                        page_indexes=page_indexes,
                        config=job_config,
                        requested_engines=[ocr_engine, "embedded"],
                        force=False,
                        progress=progress,
                        resume_book_root=resume_book_root,
                        processing_reason=attempt_reason,
                    )
                except Exception as exc:
                    completed_count = _completed_page_count(
                        resume_book_root,
                        selected_pages=selected_pages,
                    )
                    if runtime_attempt is not None:
                        runtime_attempt.update(
                            {
                                "status": "failed",
                                "failed_utc": dt.datetime.now(
                                    dt.UTC
                                ).isoformat(),
                                "completed_page_count": completed_count,
                                "error": str(exc).strip()
                                or exc.__class__.__name__,
                            }
                        )
                        jobs.put(
                            job_id,
                            surya_runtime_attempts=runtime_attempts,
                            completed_page_count=completed_count,
                        )
                        _append_jsonl(
                            resume_book_root
                            / "audit"
                            / "runtime-recovery.jsonl",
                            {
                                "timestamp_utc": runtime_attempt[
                                    "failed_utc"
                                ],
                                "action": "runtime_attempt_failed",
                                **runtime_attempt,
                            },
                        )
                    runtime_failure = (
                        ocr_engine == "surya"
                        and _is_surya_runtime_failure(exc)
                    )
                    fallback = decide_surya_runtime_fallback(
                        original_request=surya_backend_requested,
                        attempted_requests=attempted_requests,
                        hardware_report=runtime_report or {},
                        completed_page_count=completed_count,
                        runtime_failure=runtime_failure,
                    )
                    if not fallback.allowed or not fallback.next_request:
                        if ocr_engine == "surya" and runtime_failure:
                            jobs.put(
                                job_id,
                                automatic_fallback_stop_reason=fallback.reason,
                            )
                        raise
                    from_backend = str(
                        backend_decision.selected_backend
                        if backend_decision
                        else "unknown"
                    )
                    to_backend, _ = parse_surya_request(
                        fallback.next_request
                    )
                    recovery_event = {
                        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
                        "action": "automatic_runtime_fallback",
                        "from_request": attempted_requests[-1],
                        "to_request": fallback.next_request,
                        "completed_page_count": completed_count,
                        "reason": fallback.reason,
                    }
                    _append_jsonl(
                        resume_book_root / "audit" / "runtime-recovery.jsonl",
                        recovery_event,
                    )
                    current_job = jobs.get(job_id) or {}
                    recovery_history = list(
                        current_job.get("recovery_history", [])
                    )
                    recovery_history.append(recovery_event)
                    jobs.put(
                        job_id,
                        status="running",
                        message=(
                            f"{from_backend.upper()} failed before the first "
                            f"page completed. Trying Surya on "
                            f"{to_backend.upper()}."
                        ),
                        backend_notice=fallback.reason,
                        automatic_fallback_stop_reason=None,
                        recovery_history=recovery_history,
                        surya_runtime_attempts=runtime_attempts,
                    )
                    runtime_request = fallback.next_request
                    attempt_reason = (
                        "automatic_runtime_fallback_"
                        f"{from_backend}_to_{to_backend}"
                    )
                    continue
                if runtime_attempt is not None:
                    runtime_attempt.update(
                        {
                            "status": "complete",
                            "completed_utc": dt.datetime.now(
                                dt.UTC
                            ).isoformat(),
                            "completed_page_count": _completed_page_count(
                                resume_book_root,
                                selected_pages=selected_pages,
                            ),
                        }
                    )
                    jobs.put(
                        job_id,
                        surya_runtime_attempts=runtime_attempts,
                    )
                    _append_jsonl(
                        resume_book_root / "audit" / "runtime-recovery.jsonl",
                        {
                            "timestamp_utc": runtime_attempt["completed_utc"],
                            "action": "runtime_attempt_completed",
                            **runtime_attempt,
                        },
                    )
                break
            runtime_state["attempt"] = None
            runtime_state["attempts"] = None
            record_resources("job_complete")
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)
            record_resources("post_job_cleanup")
            jobs.put(
                job_id,
                status="complete",
                message="OCR complete. Review is ready.",
                book_id=book_root.name,
                progress_current=total_pages,
                progress_total=total_pages,
                progress_percent=100,
                completed_page_count=total_pages,
                eta_seconds=0,
                eta="0s",
                heartbeat_utc=dt.datetime.now(dt.UTC).isoformat(),
            )
            write_json(
                book_root / "audit" / "job.json",
                jobs.get(job_id) or {},
            )
        except Exception as exc:
            record_resources("job_failed")
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)
            record_resources("post_job_cleanup")
            failure = _failure_details(exc, ocr_engine)
            completed_count = _completed_page_count(
                resume_book_root,
                selected_pages=selected_pages,
            )
            jobs.put(
                job_id,
                status="failed",
                message=failure["title"],
                failure=failure,
                completed_page_count=completed_count,
                progress_current=completed_count,
                progress_total=total_pages,
                progress_percent=(
                    round(completed_count / total_pages * 100)
                    if total_pages
                    else 0
                ),
                traceback=traceback.format_exc(),
                heartbeat_utc=dt.datetime.now(dt.UTC).isoformat(),
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)

    @app.get("/")
    def home() -> str:
        if single_book_root is not None:
            return redirect(
                url_for(
                    "book_dashboard",
                    book_id=_book_manifest(single_book_root)["book_id"],
                )
            )
        return render_template(
            "home.html",
            books=discover_books(),
            jobs=sorted(
                jobs.values(),
                key=lambda value: str(value.get("updated_utc", "")),
                reverse=True,
            )[:8],
            ocr=ocr_readiness(),
            surya=surya_settings_view(),
            storage=storage_status(),
            storage_cleanup_ready=not jobs.running(),
        )

    @app.get("/api/health")
    def application_health() -> Any:
        response = jsonify(
            {
                "application": "bangla-ocr",
                "application_root": str(PIPELINE_ROOT),
                "active_job": jobs.running(),
                "status": "ready",
                "version": __version__,
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/import")
    def import_book() -> Any:
        if jobs.running():
            flash("Another OCR job is already running.", "warning")
            return redirect(url_for("home"))
        with runtime_settings_lock:
            validation_running = startup_validation.get("status") == "running"
        if validation_running:
            flash(
                "Surya startup validation is running. Wait for it to finish "
                "before starting OCR.",
                "warning",
            )
            return redirect(url_for("home"))
        initial_storage = storage_status()
        if not initial_storage["ready"]:
            for error in initial_storage["errors"]:
                flash(error, "error")
            return redirect(url_for("home"))
        for warning in initial_storage["warnings"]:
            flash(warning, "warning")
        readiness = ocr_readiness()
        selected_engine = request.form.get(
            "ocr_engine",
            readiness["engine"],
        ).strip().lower()
        selected = next(
            (
                option
                for option in readiness["options"]
                if option["name"] == selected_engine
            ),
            None,
        )
        if selected is None:
            flash(
                f"OCR was not started. Unknown OCR model: {selected_engine}.",
                "error",
            )
            return redirect(url_for("home"))
        if not selected["available"]:
            flash(
                f"OCR was not started. {selected['label']} is unavailable: "
                f"{selected['reason']} Choose another model.",
                "error",
            )
            return redirect(url_for("home"))
        requested_backend = str(
            request.form.get(
                "surya_runtime",
                config["ocr"].get("surya_runtime_backend", "auto"),
            )
        ).strip()
        if selected_engine == "surya":
            try:
                runtime_decision = decide_surya_backend(
                    PIPELINE_ROOT,
                    requested=requested_backend,
                    hardware_report=surya_hardware_report(),
                )
            except ValueError as exc:
                flash(f"OCR was not started. {exc}", "error")
                return redirect(url_for("home"))
            if not runtime_decision.usable:
                flash(
                    f"OCR was not started. {runtime_decision.reason}",
                    "error",
                )
                return redirect(url_for("home"))
        upload = request.files.get("pdf")
        if not upload or not upload.filename:
            flash("Choose a PDF file.", "error")
            return redirect(url_for("home"))
        original_filename = Path(upload.filename).name
        title = nfc(
            request.form.get("title", "").strip()
            or Path(original_filename).stem.replace("_", " ").strip()
            or "Untitled document"
        )
        author = nfc(request.form.get("author", "").strip() or "Unknown author")
        pages_spec = (
            "all"
            if request.form.get("page_mode", "all") == "all"
            else request.form.get("pages", "").strip()
        ) or "all"
        filename = secure_filename(original_filename) or (
            f"document-{hashlib.sha256(original_filename.encode()).hexdigest()[:8]}.pdf"
        )
        if not filename.lower().endswith(".pdf"):
            flash("Only PDF files are accepted.", "error")
            return redirect(url_for("home"))
        temporary = source_root / f".upload-{hashlib.sha256(filename.encode()).hexdigest()}.pdf"
        upload.save(temporary)
        digest = sha256_file(temporary)
        destination = source_root / f"{Path(filename).stem}-{digest[:12]}.pdf"
        destination_created = not destination.exists()
        if destination.exists():
            temporary.unlink()
        else:
            temporary.replace(destination)
        try:
            with PDFSource(destination) as pdf:
                page_indexes = parse_page_spec(pages_spec, pdf.page_count)
        except Exception as exc:
            if destination_created:
                destination.unlink(missing_ok=True)
            flash(f"Cannot open the PDF: {exc}", "error")
            return redirect(url_for("home"))
        estimated_bytes = estimate_workspace_bytes(destination, len(page_indexes))
        job_storage = storage_status(estimated_bytes)
        if not job_storage["ready"]:
            if destination_created:
                destination.unlink(missing_ok=True)
            for error in job_storage["errors"]:
                flash(error, "error")
            return redirect(url_for("home"))
        job_id = hashlib.sha256(
            f"{digest}\0{title}\0{selected_engine}".encode("utf-8")
        ).hexdigest()[:16]
        workspace_book_id = f"{slugify(title)}-{digest[:10]}-{selected_engine}"
        workspace_book_root = output_root / workspace_book_id
        jobs.put(
            job_id,
            status="queued",
            message="Queued",
            title=title,
            author=author,
            filename=destination.name,
            pages=pages_spec,
            selected_page_count=len(page_indexes),
            ocr_engine=selected_engine,
            source_pdf=str(destination),
            page_indexes=page_indexes,
            workspace_book_id=workspace_book_id,
            recovery_history=[],
            surya_backend_requested=(
                requested_backend if selected_engine == "surya" else None
            ),
            surya_backend=None,
            surya_device=None,
            surya_runtime_selected=None,
            surya_runtime_attempts=[],
            automatic_fallback_stop_reason=None,
            estimated_workspace_bytes=estimated_bytes,
            estimated_workspace=format_bytes(estimated_bytes),
            storage_preflight=job_storage["snapshot"],
        )
        thread = threading.Thread(
            target=run_processing_job,
            args=(
                job_id,
                destination,
                title,
                author,
                page_indexes,
                selected_engine,
                requested_backend,
                workspace_book_root,
                "initial_import",
            ),
            daemon=True,
        )
        thread.start()
        return redirect(url_for("job_view", job_id=job_id))

    @app.get("/jobs/<job_id>")
    def job_view(job_id: str) -> str:
        job = jobs.get(job_id)
        if not job:
            abort(404)
        if job.get("status") == "complete" and job.get("book_id"):
            return redirect(
                url_for("book_dashboard", book_id=job["book_id"])
            )
        readiness = ocr_readiness()
        runtime_options: dict[str, dict[str, Any]] = {}
        if (
            job.get("status") in {"failed", "interrupted", "preserved"}
            and job.get("ocr_engine") == "surya"
        ):
            runtime_report = surya_hardware_report()
            runtime_options = {
                str(name): dict(value)
                for name, value in runtime_report.get("runtimes", {}).items()
                if isinstance(value, dict)
            }
        return render_template(
            "job.html",
            job=job,
            recovery_options={
                option["name"]: option for option in readiness["options"]
            },
            runtime_options=runtime_options,
        )

    @app.post("/jobs/<job_id>/recover")
    def recover_job(job_id: str) -> Any:
        job = jobs.get(job_id)
        if not job:
            abort(404)
        if job.get("status") not in {"failed", "interrupted", "preserved"}:
            flash("This job is not in a recoverable state.", "warning")
            return redirect(url_for("job_view", job_id=job_id))

        action = request.form.get("action", "")
        if action == "preserve":
            history = list(job.get("recovery_history", []))
            history.append(
                {
                    "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
                    "action": "preserve",
                    "from_engine": job.get("ocr_engine"),
                    "from_surya_backend": job.get("surya_backend"),
                }
            )
            jobs.put(
                job_id,
                status="preserved",
                message=(
                    "Processing remains stopped. Completed pages and all "
                    "failure evidence have been preserved."
                ),
                recovery_history=history,
            )
            return redirect(url_for("job_view", job_id=job_id))

        if jobs.running():
            flash("Another OCR job is already running.", "warning")
            return redirect(url_for("job_view", job_id=job_id))

        current_engine = str(job.get("ocr_engine") or "surya")
        current_backend = str(
            job.get("surya_runtime_selected")
            or job.get("surya_backend_requested")
            or job.get("surya_backend")
            or "auto"
        )
        target_backend = current_backend
        if action == "retry_selected":
            target_engine = current_engine
            processing_reason = f"retry_{target_engine}"
        elif action == "switch_surya_backend":
            if current_engine != "surya":
                abort(400)
            target_engine = "surya"
            target_backend = str(
                request.form.get("target_backend") or ""
            ).strip().casefold()
            if not target_backend or target_backend == current_backend:
                abort(400)
            if request.form.get("confirm_backend_switch") != "yes":
                abort(400)
            backend_decision = decide_surya_backend(
                PIPELINE_ROOT,
                requested=target_backend,
                hardware_report=surya_hardware_report(),
            )
            if not backend_decision.usable:
                flash(
                    f"Surya cannot resume with {target_backend}: "
                    f"{backend_decision.reason}",
                    "error",
                )
                return redirect(url_for("job_view", job_id=job_id))
            processing_reason = (
                f"explicit_backend_switch_{current_backend}_to_{target_backend}"
            )
        elif action in {"switch_engine", "switch_easyocr"}:
            target_engine = (
                "easyocr"
                if action == "switch_easyocr"
                else str(request.form.get("target_engine") or "").strip()
            )
            if (
                not target_engine
                or target_engine == "embedded"
                or target_engine == current_engine
            ):
                abort(400)
            if request.form.get("confirm_engine_switch") != "yes":
                abort(400)
            processing_reason = f"explicit_switch_{current_engine}_to_{target_engine}"
            if target_engine == "surya":
                target_backend = "auto"
        else:
            abort(400)

        readiness = ocr_readiness()
        target = next(
            (
                option
                for option in readiness["options"]
                if option["name"] == target_engine
            ),
            None,
        )
        if target is None or not target["available"]:
            reason = target["reason"] if target else "Engine is not registered"
            flash(
                f"OCR cannot resume with {target_engine}: {reason}",
                "error",
            )
            return redirect(url_for("job_view", job_id=job_id))

        source_pdf = Path(
            str(job.get("source_pdf") or source_root / str(job.get("filename", "")))
        ).resolve()
        if source_pdf.parent != source_root or not source_pdf.is_file():
            flash("The preserved source PDF is unavailable.", "error")
            return redirect(url_for("job_view", job_id=job_id))
        raw_page_indexes = job.get("page_indexes")
        if isinstance(raw_page_indexes, list) and all(
            isinstance(value, int) for value in raw_page_indexes
        ):
            page_indexes = list(raw_page_indexes)
        else:
            with PDFSource(source_pdf) as pdf:
                page_indexes = parse_page_spec(
                    str(job.get("pages") or "all"), pdf.page_count
                )

        workspace_book_id = str(job.get("workspace_book_id") or "")
        if not workspace_book_id:
            digest = sha256_file(source_pdf)
            workspace_book_id = (
                f"{slugify(str(job.get('title') or source_pdf.stem))}-"
                f"{digest[:10]}-{current_engine}"
            )
        workspace_book_root = _safe_child(output_root, workspace_book_id)
        history = list(job.get("recovery_history", []))
        recovery_event = {
            "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
            "action": action,
            "from_engine": current_engine,
            "to_engine": target_engine,
            "from_surya_backend": (
                current_backend if current_engine == "surya" else None
            ),
            "to_surya_backend": (
                target_backend if target_engine == "surya" else None
            ),
            "workspace_book_id": workspace_book_id,
        }
        history.append(recovery_event)
        if workspace_book_root.exists():
            _append_jsonl(
                workspace_book_root / "audit" / "job-recovery.jsonl",
                recovery_event,
            )
        jobs.put(
            job_id,
            status="queued",
            message=f"Queued to resume with {target_engine}",
            ocr_engine=target_engine,
            surya_backend_requested=(
                target_backend if target_engine == "surya" else None
            ),
            surya_backend=(
                target_backend if target_engine == "surya" else None
            ),
            recovery_history=history,
            failure=None,
            traceback=None,
            backend_notice=None,
            surya_backend_decision=None,
            surya_runtime_selected=(
                target_backend if target_engine == "surya" else None
            ),
        )
        thread = threading.Thread(
            target=run_processing_job,
            args=(
                job_id,
                source_pdf,
                str(job.get("title") or source_pdf.stem),
                str(job.get("author") or "Unknown author"),
                page_indexes,
                target_engine,
                target_backend,
                workspace_book_root,
                processing_reason,
            ),
            daemon=True,
        )
        thread.start()
        return redirect(url_for("job_view", job_id=job_id))

    @app.get("/api/jobs/<job_id>")
    def job_api(job_id: str) -> Any:
        job = jobs.get(job_id)
        if not job:
            abort(404)
        if job.get("status") == "complete" and job.get("book_id"):
            job["redirect_url"] = url_for(
                "book_dashboard", book_id=job["book_id"]
            )
        return jsonify(job)

    @app.post("/storage/cleanup")
    def cleanup_storage() -> Any:
        if jobs.running():
            flash("Storage cleanup is disabled while OCR is running.", "warning")
            return redirect(url_for("home"))
        minimum_age = int(
            config.get("storage", {}).get("cleanup_minimum_age_hours", 24)
        )
        result = cleanup_disposable_files(
            config,
            source_root,
            older_than_hours=minimum_age,
        )
        paths = runtime_paths(config)
        log_rotated = rotate_file(
            paths["surya"] / "llamacpp_server.log",
            max_bytes=int(
                float(config["storage"].get("surya_log_max_mib", 8))
                * 1024
                * 1024
            ),
            backups=int(config["storage"].get("surya_log_backups", 3)),
        )
        flash(
            (
                f"Storage cleanup removed {result['deleted_files']} disposable "
                f"file(s), recovering {result['deleted']}."
                + (" The Surya log was rotated." if log_rotated else "")
            ),
            "success",
        )
        return redirect(url_for("home"))

    @app.route("/settings/openrouter", methods=["GET", "POST"])
    @app.route("/settings", methods=["GET", "POST"])
    def openrouter_settings() -> Any:
        profile_id = ai_profile_id()
        settings = ai_profiles.get(profile_id)
        if request.method == "POST":
            try:
                settings = ai_profiles.update(
                    profile_id,
                    enabled=request.form.get("enabled") == "on",
                    model=request.form.get("model", ""),
                    daily_request_limit=int(
                        request.form.get("daily_request_limit", "40")
                    ),
                    api_key=request.form.get("api_key", ""),
                    remove_key=request.form.get("remove_key") == "on",
                )
                flash(
                    "Your OpenRouter settings are active for this browser session.",
                    "success",
                )
            except Exception as exc:
                flash(str(exc), "error")
            return redirect(url_for("openrouter_settings"))
        return render_template(
            "settings.html",
            settings=settings,
            api_key_configured=bool(ai_profiles.api_key(profile_id)),
            surya=surya_settings_view(),
        )

    @app.post("/settings/surya")
    def save_surya_settings() -> Any:
        requested = request.form.get(
            "surya_runtime",
            request.form.get("surya_backend", "auto"),
        ).strip()
        try:
            parse_surya_request(requested)
        except ValueError:
            flash("Unknown Surya runtime selection.", "error")
            return redirect(url_for("openrouter_settings"))
        with runtime_settings_lock:
            validation_running = startup_validation.get("status") == "running"
        if jobs.running() or validation_running:
            flash(
                "Runtime settings cannot change while OCR or startup "
                "validation is running.",
                "warning",
            )
            return redirect(url_for("openrouter_settings"))
        report = surya_hardware_report(refresh=True)
        decision = decide_surya_backend(
            PIPELINE_ROOT,
            requested=requested,
            hardware_report=report,
        )
        if not decision.usable:
            flash(f"Runtime selection was not saved: {decision.reason}", "error")
            return redirect(url_for("openrouter_settings"))
        previous = str(config["ocr"].get("surya_runtime_backend", "auto"))
        config["ocr"]["surya_runtime_backend"] = requested
        if previous != requested:
            with runtime_settings_lock:
                startup_validation.clear()
                startup_validation.update({"status": "not_run"})
        persist_runtime_settings()
        flash(
            f"Surya runtime preference saved. {decision.reason}.",
            "success",
        )
        return redirect(url_for("openrouter_settings"))

    @app.post("/settings/surya/refresh")
    def refresh_surya_diagnostics() -> Any:
        with runtime_settings_lock:
            validation_running = startup_validation.get("status") == "running"
        if jobs.running() or validation_running:
            flash(
                "Hardware diagnostics cannot refresh while OCR or startup "
                "validation is running.",
                "warning",
            )
        else:
            surya_settings_view(refresh=True)
            ocr_registry.refresh("surya")
            flash("Surya hardware diagnostics refreshed.", "success")
        return redirect(url_for("openrouter_settings"))

    @app.post("/settings/surya/validate")
    def validate_surya_startup() -> Any:
        with runtime_settings_lock:
            validation_running = startup_validation.get("status") == "running"
        if jobs.running() or validation_running:
            flash(
                "A document or startup validation is already running.",
                "warning",
            )
            return redirect(url_for("openrouter_settings"))
        requested = str(config["ocr"].get("surya_runtime_backend", "auto"))
        report = surya_hardware_report(refresh=True)
        decision = decide_surya_backend(
            PIPELINE_ROOT,
            requested=requested,
            hardware_report=report,
        )
        if not decision.usable or not decision.selected_request:
            flash(f"Startup validation could not begin: {decision.reason}", "error")
            return redirect(url_for("openrouter_settings"))
        with runtime_settings_lock:
            startup_validation.clear()
            startup_validation.update(
                {
                    "status": "running",
                    "requested": requested,
                    "backend": decision.selected_backend,
                    "device": decision.selected_device,
                    "started_utc": dt.datetime.now(dt.UTC).isoformat(),
                    "reason": "Loading the Surya model and inference server.",
                }
            )
        persist_runtime_settings()
        thread = threading.Thread(
            target=run_startup_validation,
            args=(requested, decision.selected_request),
            daemon=True,
        )
        thread.start()
        flash(
            "Surya startup validation started. This can take several minutes "
            "the first time.",
            "success",
        )
        return redirect(url_for("openrouter_settings"))

    @app.get("/api/settings/surya-validation")
    def surya_validation_status() -> Any:
        with runtime_settings_lock:
            value = copy.deepcopy(startup_validation)
        response = jsonify(value)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/books/<book_id>")
    def book_dashboard(book_id: str) -> str:
        book_root = resolve_book(book_id)
        pages = _load_pages(book_root)
        summary = workflow_summary(pages)
        report_path = book_root / "structural-validation.json"
        validation = read_json(report_path) if report_path.exists() else None
        validation_pages = {
            int(value["page_number"]): value
            for value in (
                [] if (validation or {}).get("stale") else (validation or {}).get("pages", [])
            )
        }
        for page in pages:
            audit_page = validation_pages.get(int(page["page_number"]), {})
            page["structural_findings"] = [
                {"severity": severity, **finding}
                for severity, findings in (
                    ("error", audit_page.get("errors", [])),
                    ("warning", audit_page.get("warnings", [])),
                )
                for finding in findings
                if finding.get("category") in {"structure", "integrity"}
            ]
        next_workflow_page = next(
            (
                page["page_number"]
                for page in pages
                if page["workflow"]["overall"]
                not in {"human_verified", "excluded_verified"}
            ),
            None,
        )
        next_structural_page = next(
            (
                page["page_number"]
                for page in pages
                if page.get("structural_findings")
            ),
            None,
        )
        return render_template(
            "book.html",
            book=_book_manifest(book_root),
            pages=pages,
            summary=summary,
            validation=validation,
            next_review_page=next_workflow_page or next_structural_page,
            verified_export_ready=bool(
                summary["total"]
                and summary["pending_human_review"] == 0
                and summary["unresolved"] == 0
                and summary["failed"] == 0
                and (
                    validation is None
                    or (
                        validation.get("complete", False)
                        and not validation.get("stale", False)
                    )
                )
            ),
        )

    @app.post("/books/<book_id>/validate")
    def validate_view(book_id: str) -> Any:
        book_root = resolve_book(book_id)
        report = validate_book(book_root)
        flash(
            (
                "Structural validation passed."
                if report["complete"]
                else f"Validation found {len(report['errors'])} blocking issue(s)."
            ),
            "success" if report["complete"] else "warning",
        )
        return redirect(url_for("book_dashboard", book_id=book_id))

    @app.post("/books/<book_id>/finalize")
    def finalize_view(book_id: str) -> Any:
        book_root = resolve_book(book_id)
        export_format = str(request.form.get("format") or "text").lower()
        if export_format not in {"text", "markdown"}:
            abort(400)
        report = finalize_book(book_root, allow_draft=False)
        if not report["complete"]:
            flash(
                "Verified export is locked. Review every page and clear the "
                "structural audit first. Use “Download current preview” for "
                "the unverified OCR text.",
                "warning",
            )
            return redirect(url_for("book_dashboard", book_id=book_id))
        suffix = "md" if export_format == "markdown" else "txt"
        mimetype = (
            "text/markdown; charset=utf-8"
            if export_format == "markdown"
            else "text/plain; charset=utf-8"
        )
        return send_file(
            BytesIO((book_root / f"book.{suffix}").read_bytes()),
            as_attachment=True,
            download_name=f"{book_id}-verified.{suffix}",
            mimetype=mimetype,
        )

    @app.post("/books/<book_id>/preview")
    def preview_view(book_id: str) -> Any:
        book_root = resolve_book(book_id)
        export_format = str(request.form.get("format") or "text").lower()
        if export_format not in {"text", "markdown"}:
            abort(400)
        finalize_book(book_root, allow_draft=True)
        suffix = "md" if export_format == "markdown" else "txt"
        preview_path = book_root / f"book.preview.{suffix}"
        if not preview_path.exists():
            abort(500)
        mimetype = (
            "text/markdown; charset=utf-8"
            if export_format == "markdown"
            else "text/plain; charset=utf-8"
        )
        return send_file(
            BytesIO(preview_path.read_bytes()),
            as_attachment=True,
            download_name=f"{book_id}-UNVERIFIED-preview.{suffix}",
            mimetype=mimetype,
        )

    @app.route(
        "/books/<book_id>/pages/<int:page_number>",
        methods=["GET", "POST"],
    )
    def page_review(book_id: str, page_number: int) -> Any:
        book_root = resolve_book(book_id)
        state_path = _page_state_path(book_root, page_number)
        page_root = state_path.parent
        page = normalize_page_state(read_json(state_path))
        text_path, old_text = _current_text(page_root)
        if request.method == "POST":
            new_text = nfc(request.form.get("text", "").strip())
            reviewer = nfc(request.form.get("reviewer", "").strip())
            human_status = request.form.get("human_status", "in_review")
            if human_status == "human_verified" and not reviewer:
                flash("A reviewer name is required for human verification.", "error")
                return redirect(
                    url_for(
                        "page_review",
                        book_id=book_id,
                        page_number=page_number,
                    )
                )
            _create_revision(page_root, old_text, "manual-review")
            write_text(page_root / "final.txt", new_text)
            manual = page.setdefault("manual", {})
            manual.update(
                {
                    "include": request.form.get("include") == "on",
                    "page_role": request.form.get(
                        "page_role", "non_story_or_uncertain"
                    ),
                    "heading": nfc(request.form.get("heading", "").strip()),
                    "break_before": request.form.get("break_before") == "on",
                    "join_without_space": (
                        request.form.get("join_without_space") == "on"
                    ),
                    "preserve_trailing_hyphen": (
                        request.form.get("preserve_trailing_hyphen") == "on"
                    ),
                }
            )
            reviewed_utc = dt.datetime.now(dt.UTC).isoformat()
            set_human_status(
                page,
                human_status,
                reviewer=reviewer,
                reviewed_utc=reviewed_utc,
            )
            write_json(state_path, page)
            _mark_validation_stale(book_root, page_number)
            diff = "\n".join(
                difflib.unified_diff(
                    old_text.splitlines(),
                    new_text.splitlines(),
                    fromfile=text_path.name,
                    tofile="final.txt",
                    lineterm="",
                )
            )
            _append_jsonl(
                book_root / "audit" / "corrections.jsonl",
                {
                    "timestamp_utc": reviewed_utc,
                    "page_number": page_number,
                    "reviewer": reviewer,
                    "human_status": human_status,
                    "include": manual["include"],
                    "page_role": manual["page_role"],
                    "diff": diff,
                },
            )
            flash("Page saved.", "success")
            direction = request.form.get("save_direction", "stay")
            if direction == "next":
                numbers = [item["page_number"] for item in _load_pages(book_root)]
                position = numbers.index(page_number)
                if position + 1 < len(numbers):
                    return redirect(
                        url_for(
                            "page_review",
                            book_id=book_id,
                            page_number=numbers[position + 1],
                        )
                    )
            return redirect(
                url_for(
                    "page_review", book_id=book_id, page_number=page_number
                )
            )
        pages = _load_pages(book_root)
        numbers = [int(item["page_number"]) for item in pages]
        position = numbers.index(page_number)
        profile_id = ai_profile_id()
        report_path = book_root / "structural-validation.json"
        validation = read_json(report_path) if report_path.exists() else {}
        audit_page = next(
            (
                value
                for value in validation.get("pages", [])
                if int(value.get("page_number", -1)) == page_number
            ),
            {},
        )
        structural_findings = [
            {"severity": severity, **finding}
            for severity, findings in (
                ("error", audit_page.get("errors", [])),
                ("warning", audit_page.get("warnings", [])),
            )
            for finding in findings
            if finding.get("category") in {"structure", "integrity"}
        ] if not validation.get("stale") else []
        return render_template(
            "review.html",
            book=_book_manifest(book_root),
            page=page,
            text=old_text,
            lines=_selected_lines(page),
            multiscale_regions=_multiscale_regions(page),
            proposals=_proposal_view(page_root),
            roles=PAGE_ROLES,
            anomalies=text_anomalies(old_text),
            structural_findings=structural_findings,
            previous_number=numbers[position - 1] if position else None,
            next_number=(
                numbers[position + 1] if position + 1 < len(numbers) else None
            ),
            openrouter=ai_profiles.get(profile_id),
            api_key_configured=bool(ai_profiles.api_key(profile_id)),
        )

    @app.get("/books/<book_id>/pages/<int:page_number>/image/<kind>")
    def page_image(book_id: str, page_number: int, kind: str) -> Any:
        book_root = resolve_book(book_id)
        page = read_json(_page_state_path(book_root, page_number))
        if kind == "source":
            filename = page["source_image"]
        elif kind == "selected":
            filename = page["selected_image"]
        else:
            abort(404)
        path = book_root / "pages" / f"{page_number:04d}" / filename
        if not path.exists():
            abort(404)
        return send_file(path)

    @app.get(
        "/books/<book_id>/pages/<int:page_number>/multiscale/<region_id>/image"
    )
    def multiscale_image(
        book_id: str, page_number: int, region_id: str
    ) -> Any:
        book_root = resolve_book(book_id)
        page_root = _page_state_path(book_root, page_number).parent
        page = read_json(page_root / "page.json")
        region = next(
            (
                value
                for value in page.get("multiscale", {}).get("regions", [])
                if str(value.get("region_id")) == region_id
            ),
            None,
        )
        if region is None or not region.get("crop_image"):
            abort(404)
        evidence_root = (page_root / "evidence").resolve()
        path = (page_root / str(region["crop_image"])).resolve()
        if path.parent != evidence_root or not path.exists():
            abort(404)
        return send_file(path)

    @app.post(
        "/books/<book_id>/pages/<int:page_number>/multiscale/<region_id>"
    )
    def review_multiscale_region(
        book_id: str, page_number: int, region_id: str
    ) -> Any:
        book_root = resolve_book(book_id)
        state_path = _page_state_path(book_root, page_number)
        page_root = state_path.parent
        page = normalize_page_state(read_json(state_path))
        region = next(
            (
                value
                for value in page.get("multiscale", {}).get("regions", [])
                if str(value.get("region_id")) == region_id
            ),
            None,
        )
        if region is None:
            abort(404)
        if region.get("status") != "pending":
            flash("That crop comparison has already been decided.", "warning")
            return redirect(
                url_for("page_review", book_id=book_id, page_number=page_number)
            )
        action = str(request.form.get("action", ""))
        if action not in {"use_crop", "keep_full_page"}:
            abort(400)

        _, text = _current_text(page_root)
        original = str(region.get("original", ""))
        alternative = str(region.get("alternative", ""))
        timestamp = dt.datetime.now(dt.UTC).isoformat()
        if action == "use_crop":
            if not alternative or text.count(original) != 1:
                flash(
                    "The page text changed or the original block is not unique; "
                    "the crop reading was not applied.",
                    "error",
                )
                return redirect(
                    url_for(
                        "page_review", book_id=book_id, page_number=page_number
                    )
                )
            _create_revision(page_root, text, "before-high-resolution-crop")
            write_text(
                page_root / "final.txt",
                text.replace(original, alternative, 1),
            )
            region["status"] = "accepted"
            _mark_validation_stale(book_root, page_number)
        else:
            region["status"] = "kept_full_page"

        region["decided_utc"] = timestamp
        reviewer = str(page["workflow"]["human"].get("reviewer", ""))
        set_human_status(
            page,
            "in_review",
            reviewer=reviewer,
            reviewed_utc=timestamp,
        )
        page["multiscale"]["pending_count"] = sum(
            value.get("status") == "pending"
            for value in page["multiscale"].get("regions", [])
        )
        write_json(state_path, page)
        _append_jsonl(
            book_root / "audit" / "multiscale-decisions.jsonl",
            {
                "timestamp_utc": timestamp,
                "page_number": page_number,
                "region_id": region_id,
                "action": action,
                "original": original,
                "alternative": alternative,
                "source_bbox": region.get("source_bbox"),
            },
        )
        flash(
            "High-resolution reading applied; verify the complete page."
            if action == "use_crop"
            else "Full-page Surya reading kept for this region.",
            "success",
        )
        return redirect(
            url_for("page_review", book_id=book_id, page_number=page_number)
        )

    @app.post("/books/<book_id>/pages/<int:page_number>/ai")
    def generate_ai_proposal(book_id: str, page_number: int) -> Any:
        book_root = resolve_book(book_id)
        profile_id = ai_profile_id()
        settings = ai_profiles.get(profile_id)
        if not settings["enabled"]:
            flash("Enable OpenRouter in Settings first.", "warning")
            return redirect(
                url_for("page_review", book_id=book_id, page_number=page_number)
            )
        state_path = _page_state_path(book_root, page_number)
        page_root = state_path.parent
        page = normalize_page_state(read_json(state_path))
        lines = _selected_lines(page)
        line_index_value = request.form.get("line_index", "")
        line_index = int(line_index_value) if line_index_value.isdigit() else None
        selected_line = (
            lines[line_index]
            if line_index is not None and line_index < len(lines)
            else None
        )
        _, full_text = _current_text(page_root)
        selected_text = (
            str(selected_line.get("text", "")).strip()
            if selected_line
            else full_text
        )
        bbox = list(selected_line.get("bbox", [])) if selected_line else None
        source_path = page_root / page["source_image"]
        crop_name = hashlib.sha256(
            f"{page_number}\0{line_index}\0{selected_text}".encode("utf-8")
        ).hexdigest()[:20]
        crop_path = crop_page_region(
            source_path,
            page_root / "evidence" / "crops" / f"{crop_name}.webp",
            bbox,
        )
        proposal_path = page_root / "evidence" / "openrouter-proposals.jsonl"
        request_id = proposal_request_id(
            settings["model"], selected_text, crop_path.read_bytes()
        )
        if any(
            value.get("request_id") == request_id
            for value in _read_jsonl(proposal_path)
        ):
            flash(
                "This exact crop and OCR text already have a saved proposal.",
                "success",
            )
            return redirect(
                url_for("page_review", book_id=book_id, page_number=page_number)
            )
        today = dt.datetime.now(dt.UTC).date().isoformat()
        used_today = ai_profiles.used_today(profile_id, today)
        if used_today >= settings["daily_request_limit"]:
            flash("The configured daily OpenRouter request limit is reached.", "warning")
            return redirect(
                url_for("page_review", book_id=book_id, page_number=page_number)
            )
        try:
            result = OpenRouterClient(
                model=settings["model"],
                credential_store=ai_profiles.credential(profile_id),
            ).propose(
                CropRequest(
                    image_path=crop_path,
                    text=selected_text,
                    page_number=page_number,
                    line_index=line_index,
                    bbox=bbox,
                )
            )
            existing = {
                value.get("request_id") for value in _read_jsonl(proposal_path)
            }
            if result["request_id"] not in existing:
                _append_jsonl(proposal_path, result)
            usage_path = output_root / "_app" / "openrouter-usage.jsonl"
            _append_jsonl(
                usage_path,
                {
                    "date": today,
                    "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
                    "book_id": book_id,
                    "page_number": page_number,
                    "model": result["model_used"],
                    "request_id": result["request_id"],
                },
            )
            ai_profiles.record_usage(profile_id, today)
            page["workflow"]["ai"].update(
                {
                    "status": result["status"],
                    "proposal_count": sum(
                        len(value.get("changes", []))
                        for value in _proposal_view(page_root)
                    ),
                    "last_requested_utc": result["created_utc"],
                }
            )
            page["workflow"]["overall"] = page["workflow"].get(
                "overall", "needs_review"
            )
            write_json(state_path, page)
            flash(
                (
                    f"AI proposed {len(result['changes'])} change(s)."
                    if result["changes"]
                    else "AI found no image-grounded change."
                ),
                "success",
            )
        except OpenRouterError as exc:
            page["workflow"]["ai"]["status"] = "failed"
            page["workflow"]["ai"]["last_error"] = str(exc)
            write_json(state_path, page)
            flash(str(exc), "error")
        return redirect(
            url_for("page_review", book_id=book_id, page_number=page_number)
        )

    @app.post(
        "/books/<book_id>/pages/<int:page_number>/ai/<request_id>/<int:change_index>"
    )
    def review_ai_change(
        book_id: str,
        page_number: int,
        request_id: str,
        change_index: int,
    ) -> Any:
        book_root = resolve_book(book_id)
        state_path = _page_state_path(book_root, page_number)
        page_root = state_path.parent
        proposals = [
            value
            for value in _proposal_events(page_root)
            if value.get("request_id") == request_id
            and value.get("status") == "proposed"
        ]
        if not proposals or change_index >= len(proposals[-1].get("changes", [])):
            abort(404)
        action = request.form.get("action")
        if action not in {"accepted", "rejected"}:
            abort(400)
        if any(
            value.get("event") in {"accepted", "rejected"}
            and value.get("request_id") == request_id
            and int(value.get("change_index", -1)) == change_index
            for value in _proposal_events(page_root)
        ):
            flash("That proposal has already been decided.", "warning")
            return redirect(
                url_for("page_review", book_id=book_id, page_number=page_number)
            )
        change = proposals[-1]["changes"][change_index]
        _, text = _current_text(page_root)
        if action == "accepted":
            original = str(change["original"])
            if text.count(original) != 1:
                flash(
                    "The page changed since this proposal; it was not applied.",
                    "error",
                )
                return redirect(
                    url_for(
                        "page_review",
                        book_id=book_id,
                        page_number=page_number,
                    )
                )
            _create_revision(page_root, text, "before-ai-change")
            write_text(
                page_root / "final.txt",
                text.replace(original, str(change["replacement"]), 1),
            )
            page = normalize_page_state(read_json(state_path))
            set_human_status(
                page,
                "in_review",
                reviewer=str(page["workflow"]["human"].get("reviewer", "")),
                reviewed_utc=dt.datetime.now(dt.UTC).isoformat(),
            )
            page["workflow"]["ai"]["accepted_count"] = int(
                page["workflow"]["ai"].get("accepted_count", 0)
            ) + 1
            write_json(state_path, page)
            _mark_validation_stale(book_root, page_number)
        _append_jsonl(
            page_root / "evidence" / "openrouter-proposals.jsonl",
            {
                "event": action,
                "request_id": request_id,
                "change_index": change_index,
                "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
                "change": change,
            },
        )
        _append_jsonl(
            book_root / "audit" / "ai-corrections.jsonl",
            {
                "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
                "page_number": page_number,
                "request_id": request_id,
                "change_index": change_index,
                "action": action,
                "change": change,
            },
        )
        flash(f"AI proposal {action}.", "success")
        return redirect(
            url_for("page_review", book_id=book_id, page_number=page_number)
        )

    return app


def run_application(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    from waitress import serve

    app = create_application()
    if open_browser:
        webbrowser.open(f"http://{host}:{port}/")
    serve(
        app,
        host=host,
        port=port,
        threads=4,
        channel_timeout=3600,
        max_request_body_size=1024 * 1024 * 1024,
    )
