from __future__ import annotations

import csv
import datetime as dt
import importlib
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from PIL import Image

from ..models import OCRCandidate, OCRLine
from ..storage import MIB, rotate_file
from ..utils import nfc
from .base import OCREngine
from .surya_policy import SuryaBackendDecision, decide_surya_backend
from .surya_runtime import (
    SuryaRuntimeSelection,
    configured_runtime,
)


UPLOADER_MARKERS = (
    "bangla book",
    "direct link",
    "facebook.com",
    "www.",
    "amarboi",
    "boilovers",
    "আমারবই",
    "বইলাভার",
)
RUNNING_FOOTER_SUFFIXES: tuple[str, ...] = ()
UPSTREAM_SURYA_LOG_PATH = "~/.cache/datalab/surya/llamacpp_server.log"


def uploader_marker(text: str) -> str | None:
    """Return the matched uploader/watermark marker, if this is such a block."""
    lower = text.casefold()
    return next((marker for marker in UPLOADER_MARKERS if marker in lower), None)


def strip_merged_running_footer(
    text: str,
    bbox: list[float],
    page_height: int,
) -> tuple[str, str | None]:
    """Remove a known running footer merged into a bottom story block."""
    if not text or not bbox or bbox[3] < page_height * 0.9:
        return text, None
    for suffix in RUNNING_FOOTER_SUFFIXES:
        if text.endswith(suffix) and text != suffix:
            return (
                text[: -len(suffix)].rstrip(),
                f"removed merged running footer suffix: {suffix}",
            )
    return text, None


class _TextHTMLParser(HTMLParser):
    block_tags = {
        "p",
        "div",
        "section",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "br",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    @staticmethod
    def _separator(tag: str) -> str:
        return "\n" if tag == "br" else "\n\n"

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.block_tags and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append(self._separator(tag))

    def handle_endtag(self, tag: str) -> None:
        if tag in self.block_tags:
            self.parts.append(self._separator(tag))

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return nfc(value.strip())


def _html_to_text(value: str) -> str:
    parser = _TextHTMLParser()
    parser.feed(value)
    return parser.text()


class SuryaEngine(OCREngine):
    name = "surya"
    expensive = True
    supports_preprocessed_variant = True
    supports_recovery = True
    _manager: Any = None
    _predictor: Any = None
    reader_labels = {"Text", "SectionHeader", "ListGroup", "Footnote", "Caption"}

    def __init__(self, config: dict[str, Any], working_root: Path):
        super().__init__(config, working_root)
        self._runtime_selection: SuryaRuntimeSelection | None = None
        self._runtime_decision: SuryaBackendDecision | None = None
        self._runtime_error: str | None = None
        self._runtime_decision_monotonic = 0.0

    @staticmethod
    def _sentinel_path() -> Path:
        root = os.environ.get("SURYA_RUNTIME_DIR")
        if root:
            return Path(root) / "llamacpp_server.json"
        return Path("~/.cache/datalab/surya/llamacpp_server.json").expanduser()

    @staticmethod
    def _log_path() -> Path:
        root = os.environ.get("SURYA_RUNTIME_DIR")
        if root:
            return Path(root) / "llamacpp_server.log"
        return Path("~/.cache/datalab/surya/llamacpp_server.log").expanduser()

    @classmethod
    def _rotate_server_log(cls) -> bool:
        max_bytes = int(float(os.environ.get("SURYA_LOG_MAX_MIB", "8")) * MIB)
        backups = int(os.environ.get("SURYA_LOG_BACKUPS", "3"))
        return rotate_file(cls._log_path(), max_bytes=max_bytes, backups=backups)

    @classmethod
    def _read_sentinel(cls) -> dict[str, Any] | None:
        path = cls._sentinel_path()
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _windows_process_name(pid: int) -> str | None:
        try:
            result = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    f"PID eq {pid}",
                    "/FO",
                    "CSV",
                    "/NH",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            rows = list(csv.reader(result.stdout.splitlines()))
            if not rows or not rows[0] or "No tasks" in rows[0][0]:
                return None
            return rows[0][0]
        except (OSError, subprocess.SubprocessError):
            return None

    @classmethod
    def _process_name(cls, pid: int) -> str | None:
        if pid <= 0:
            return None
        if os.name == "nt":
            return cls._windows_process_name(pid)
        try:
            return Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:
            try:
                os.kill(pid, 0)
            except (OSError, ProcessLookupError):
                return None
            return "unknown"

    @classmethod
    def _process_alive(cls, pid: int) -> bool:
        return cls._process_name(pid) is not None

    @classmethod
    def _server_health(cls, sentinel: dict[str, Any] | None = None) -> bool | None:
        if os.environ.get("SURYA_INFERENCE_URL"):
            base_url = os.environ["SURYA_INFERENCE_URL"].rstrip("/")
            if base_url.endswith("/v1"):
                base_url = base_url[:-3]
        else:
            value = sentinel or cls._read_sentinel()
            port = value.get("port") if value else None
            if not port:
                return None
            base_url = f"http://127.0.0.1:{int(port)}"
        try:
            with urllib.request.urlopen(
                f"{base_url}/health", timeout=1.5
            ) as response:
                return response.status == 200
        except (OSError, ValueError, urllib.error.URLError):
            return False

    @classmethod
    def _bounded_log_tail(cls, line_count: int = 30) -> list[str]:
        path = cls._log_path()
        if not path.exists():
            return []
        try:
            return path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()[-line_count:]
        except OSError:
            return []

    @staticmethod
    def _expected_server_name() -> str:
        configured = os.environ.get("LLAMA_CPP_BINARY", "llama-server.exe")
        return Path(configured).name

    @classmethod
    def _stop_managed_server(cls, pid: int) -> dict[str, Any]:
        process_name = cls._process_name(pid)
        expected_name = cls._expected_server_name()
        if process_name is None:
            return {"pid": pid, "action": "already_stopped"}
        if process_name.casefold() != expected_name.casefold():
            raise RuntimeError(
                f"Refusing to stop PID {pid}: expected {expected_name!r}, "
                f"found {process_name!r}."
            )
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
            if result.returncode != 0 and cls._process_alive(pid):
                raise RuntimeError(
                    f"Failed to stop managed Surya server PID {pid}: "
                    f"{(result.stderr or result.stdout).strip()}"
                )
        else:
            os.kill(pid, 15)
            for _ in range(20):
                if not cls._process_alive(pid):
                    break
                time.sleep(0.1)
            if cls._process_alive(pid):
                os.kill(pid, 9)
        return {
            "pid": pid,
            "process_name": process_name,
            "action": "stopped",
        }

    def retry_reason(self, candidate: OCRCandidate) -> str | None:
        if candidate.text.strip():
            return None
        health = self._server_health()
        if health is False:
            return "Surya returned empty text after its inference server stopped"
        raw = candidate.raw if isinstance(candidate.raw, list) else []
        if raw and any(bool(block.get("error")) for block in raw if isinstance(block, dict)):
            return "Surya returned only failed OCR blocks"
        return None

    def recovery_diagnostics(self) -> dict[str, Any]:
        sentinel = self._read_sentinel()
        pid = int(sentinel.get("pid") or 0) if sentinel else 0
        diagnostics = {
            "backend": "external" if os.environ.get("SURYA_INFERENCE_URL") else "llamacpp",
            "sentinel": sentinel,
            "server_process_name": self._process_name(pid) if pid else None,
            "server_healthy": self._server_health(sentinel),
            "log_tail": self._bounded_log_tail(),
        }
        if self._runtime_selection:
            diagnostics["runtime"] = self._runtime_selection.diagnostics()
        return diagnostics

    def recover_after_failure(self) -> dict[str, Any]:
        before = self.recovery_diagnostics()
        manager_error = None
        if self._manager is not None:
            try:
                self._manager.stop()
            except Exception as exc:  # best-effort client reset before restart
                manager_error = str(exc)
        self._manager = None
        self._predictor = None

        action: dict[str, Any] = {"action": "client_reset"}
        if not os.environ.get("SURYA_INFERENCE_URL"):
            sentinel = self._read_sentinel()
            if sentinel:
                pid = int(sentinel.get("pid") or 0)
                if pid and self._process_alive(pid):
                    action = self._stop_managed_server(pid)
                if not pid or not self._process_alive(pid):
                    try:
                        self._sentinel_path().unlink(missing_ok=True)
                    except OSError as exc:
                        raise RuntimeError(
                            f"Cannot remove the stale Surya sentinel: {exc}"
                        ) from exc
                    action["sentinel_removed"] = True
            time.sleep(0.2)
        return {
            "recovered": True,
            "manager_stop_error": manager_error,
            "server_action": action,
            "before": before,
            "after": self.recovery_diagnostics(),
        }

    def _configure_runtime(self) -> SuryaRuntimeSelection | None:
        model_root = self.working_root / "models"
        os.environ.setdefault("HF_HOME", str(model_root / "huggingface"))
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("MODEL_CACHE_DIR", str(model_root / "surya"))

        configured_binary = os.environ.get("LLAMA_CPP_BINARY")
        managed_binary = os.environ.get("BANGLA_OCR_MANAGED_LLAMA_CPP") == "1"
        if (
            configured_binary
            and not managed_binary
            and os.path.isfile(configured_binary)
        ):
            selection = configured_runtime(Path(configured_binary))
            self._runtime_selection = selection
            return selection

        requested_backend = str(
            self.config.get("surya_runtime_backend", "auto")
        ).strip().casefold() or "auto"
        if (
            self._runtime_decision
            and self._runtime_decision.requested == requested_backend
            and time.monotonic() - self._runtime_decision_monotonic < 30
        ):
            return self._runtime_decision.selected
        decision = decide_surya_backend(
            self.working_root,
            requested=requested_backend,
        )
        self._runtime_decision = decision
        self._runtime_decision_monotonic = time.monotonic()
        self._runtime_error = None if decision.usable else decision.reason
        selection = decision.selected
        if selection is not None:
            self._runtime_selection = selection
        return selection

    def refresh_runtime_policy(self) -> None:
        self._runtime_decision = None
        self._runtime_decision_monotonic = 0.0
        self._runtime_error = None

    def _activate_runtime(self) -> SuryaRuntimeSelection | None:
        selection = self._runtime_selection or self._configure_runtime()
        if selection is None or selection.source == "configured":
            return selection
        previous_backend = os.environ.get("BANGLA_OCR_SURYA_RUNTIME")
        previous_device = os.environ.get("BANGLA_OCR_SURYA_DEVICE")
        inherited_server = False
        if not previous_backend:
            sentinel = self._read_sentinel()
            pid = int(sentinel.get("pid") or 0) if sentinel else 0
            inherited_server = bool(pid and self._process_alive(pid))
        runtime_changed = bool(
            previous_backend
            and (
                previous_backend != selection.backend
                or previous_device != selection.device_identifier
            )
        )
        if inherited_server or runtime_changed:
            self.recover_after_failure()
        selection.configure_environment(os.environ, overwrite_runtime=True)
        self._sync_upstream_settings()
        self._runtime_selection = selection
        return selection

    @staticmethod
    def _sync_upstream_settings() -> None:
        settings_module = sys.modules.get("surya.settings")
        if settings_module is None:
            return
        settings = settings_module.settings
        settings.SURYA_INFERENCE_BACKEND = os.environ.get(
            "SURYA_INFERENCE_BACKEND", "llamacpp"
        )
        settings.SURYA_INFERENCE_KEEP_ALIVE = (
            os.environ.get("SURYA_INFERENCE_KEEP_ALIVE", "1") == "1"
        )
        settings.SURYA_INFERENCE_PARALLEL = int(
            os.environ.get("SURYA_INFERENCE_PARALLEL", "1")
        )
        settings.SURYA_INFERENCE_CTX_SIZE = int(
            os.environ.get("SURYA_INFERENCE_CTX_SIZE", "16384")
        )
        settings.LLAMA_CPP_BINARY = os.environ.get("LLAMA_CPP_BINARY")
        settings.LLAMA_CPP_NGL = int(os.environ.get("LLAMA_CPP_NGL", "0"))
        settings.LLAMA_CPP_EXTRA_ARGS = os.environ.get(
            "LLAMA_CPP_EXTRA_ARGS", ""
        )

    def runtime_diagnostics(self) -> dict[str, Any]:
        return {
            "selection": (
                self._runtime_selection.diagnostics()
                if self._runtime_selection
                else None
            ),
            "policy": (
                self._runtime_decision.as_dict()
                if self._runtime_decision
                else None
            ),
            "error": self._runtime_error,
        }

    def validate_startup(self) -> dict[str, Any]:
        available, reason = self.available()
        if not available:
            raise RuntimeError(reason)
        started = time.perf_counter()
        selection = self._activate_runtime()
        self._rotate_server_log()
        self._configure_upstream_runtime_cache()
        from surya.inference import SuryaInferenceManager
        from surya.recognition import RecognitionPredictor

        try:
            self._manager = SuryaInferenceManager()
            self._manager.start()
            self._predictor = RecognitionPredictor(self._manager)
            server_healthy = self._server_health()
            if server_healthy is not True:
                raise RuntimeError(
                    "Surya initialized but its inference server did not pass "
                    "the health check"
                )
        except Exception:
            try:
                self.recover_after_failure()
            except Exception:
                pass
            raise
        result = {
            "status": "ready",
            "checked_utc": dt.datetime.now(dt.UTC).isoformat(),
            "duration_seconds": round(time.perf_counter() - started, 3),
            "backend": selection.backend if selection else "external",
            "selection": selection.diagnostics() if selection else None,
            "server_healthy": True,
        }
        result["cleanup"] = self.recover_after_failure()
        return result

    def _configure_upstream_runtime_cache(self) -> Path:
        root = os.environ.get("SURYA_RUNTIME_DIR")
        runtime_dir = (
            Path(root)
            if root
            else self.working_root / "workspace" / "surya"
        )
        runtime_dir.mkdir(parents=True, exist_ok=True)
        spawn = importlib.import_module("surya.inference.backends.spawn")
        spawn._cache_dir = lambda: runtime_dir
        llamacpp = importlib.import_module("surya.inference.backends.llamacpp")
        original_path = getattr(
            llamacpp,
            "_bangla_ocr_original_path",
            llamacpp.Path,
        )
        llamacpp._bangla_ocr_original_path = original_path

        def runtime_path(*parts: Any) -> Path:
            if len(parts) == 1 and os.fspath(parts[0]) == UPSTREAM_SURYA_LOG_PATH:
                return runtime_dir / "llamacpp_server.log"
            return original_path(*parts)

        llamacpp.Path = runtime_path
        return runtime_dir

    def available(self) -> tuple[bool, str]:
        if importlib.util.find_spec("surya") is None:
            return False, "Python package surya-ocr is not installed"
        if os.environ.get("SURYA_INFERENCE_URL"):
            return True, "Surya will use the configured inference server"
        if runtime := self._configure_runtime():
            reason = (
                self._runtime_decision.reason
                if self._runtime_decision
                else runtime.availability_message()
            )
            return True, reason
        if self._runtime_error:
            return False, self._runtime_error
        if shutil.which("llama-server"):
            return True, "Surya will use the local llama.cpp CPU server"
        if shutil.which("docker"):
            return True, "Surya can use its Docker inference backend"
        return (
            False,
            "surya-ocr is installed but llama-server, Docker, or "
            "SURYA_INFERENCE_URL is required",
        )

    def _get_predictor(self) -> Any:
        if self._predictor is None:
            self._activate_runtime()
            self._rotate_server_log()
            self._configure_upstream_runtime_cache()
            from surya.inference import SuryaInferenceManager
            from surya.recognition import RecognitionPredictor

            self._manager = SuryaInferenceManager()
            self._predictor = RecognitionPredictor(self._manager)
        return self._predictor

    def recognize(
        self,
        image: Image.Image,
        variant: str,
        *,
        embedded_text: str = "",
    ) -> OCRCandidate:
        startup_started = time.perf_counter()
        predictor = self._get_predictor()
        model_startup_seconds = time.perf_counter() - startup_started
        inference_started = time.perf_counter()
        rgb_image = image.convert("RGB")
        recognition_mode = "full_page"
        if variant.startswith("multiscale-"):
            from surya.layout.schema import LayoutBox, LayoutResult

            # Block mode caps decoding for small crop rereads.
            estimated_tokens = max(
                50,
                min(1200, ((len(embedded_text) * 2 + 49) // 50) * 50),
            )
            layout = LayoutResult(
                bboxes=[
                    LayoutBox(
                        polygon=[0, 0, rgb_image.width, rgb_image.height],
                        label="Text",
                        raw_label="Text",
                        position=0,
                        count=estimated_tokens,
                        confidence=1.0,
                    )
                ],
                image_bbox=[0, 0, rgb_image.width, rgb_image.height],
            )
            prediction = predictor(
                [rgb_image], [layout], full_page=False
            )[0]
            recognition_mode = "bounded_block"
        else:
            prediction = predictor([rgb_image])[0]
        inference_seconds = time.perf_counter() - inference_started
        lines: list[OCRLine] = []
        blocks_raw: list[dict[str, Any]] = []
        text_blocks: list[str] = []
        excluded_uploader_blocks = 0
        for block in prediction.blocks:
            html = str(getattr(block, "html", "") or "")
            text = _html_to_text(html)
            bbox = [float(value) for value in getattr(block, "bbox", [0, 0, 0, 0])]
            reader_text, text_adjustment = strip_merged_running_footer(
                text, bbox, image.height
            )
            confidence = float(getattr(block, "confidence", 0.0) or 0.0)
            label = str(getattr(block, "label", "Text"))
            matched_uploader_marker = uploader_marker(text)
            exclusion_reason: str | None = None
            if label not in self.reader_labels:
                exclusion_reason = f"layout label {label} is not reader text"
            elif matched_uploader_marker:
                exclusion_reason = (
                    f"uploader/watermark marker: {matched_uploader_marker}"
                )
                excluded_uploader_blocks += 1
            included_in_reader_text = bool(reader_text) and exclusion_reason is None
            if reader_text and included_in_reader_text:
                text_blocks.append(reader_text)
                lines.append(
                    OCRLine(
                        text=reader_text,
                        bbox=bbox,
                        confidence=confidence,
                        label=label,
                    )
                )
            blocks_raw.append(
                {
                    "html": html,
                    "text": text,
                    "reader_text": reader_text,
                    "bbox": bbox,
                    "confidence": confidence,
                    "label": label,
                    "included_in_reader_text": included_in_reader_text,
                    "exclusion_reason": exclusion_reason,
                    "text_adjustment": text_adjustment,
                    "skipped": bool(getattr(block, "skipped", False)),
                    "error": bool(getattr(block, "error", False)),
                }
            )
        confidence = (
            statistics.mean(line.confidence or 0.0 for line in lines)
            if lines
            else 0.0
        )
        return OCRCandidate(
            engine=self.name,
            variant=variant,
            text=nfc("\n\n".join(text_blocks)),
            lines=lines,
            confidence=confidence,
            diagnostics={
                "excluded_uploader_blocks": excluded_uploader_blocks,
                "raw_block_count": len(blocks_raw),
                "recognition_mode": recognition_mode,
                "model_startup_seconds": round(model_startup_seconds, 6),
                "inference_seconds": round(inference_seconds, 6),
            },
            raw=blocks_raw,
        )
