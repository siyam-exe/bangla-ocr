from __future__ import annotations

import importlib.util
import csv
import json
import os
import re
import shutil
import statistics
import subprocess
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
        return {
            "backend": "external" if os.environ.get("SURYA_INFERENCE_URL") else "llamacpp",
            "sentinel": sentinel,
            "server_process_name": self._process_name(pid) if pid else None,
            "server_healthy": self._server_health(sentinel),
            "log_tail": self._bounded_log_tail(),
        }

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

    def _configure_bundled_runtime(self) -> str | None:
        """Configure the project-local llama.cpp runtime when it is present."""
        model_root = self.working_root / "models"
        os.environ.setdefault("HF_HOME", str(model_root / "huggingface"))
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("MODEL_CACHE_DIR", str(model_root / "surya"))

        configured_binary = os.environ.get("LLAMA_CPP_BINARY")
        if configured_binary and os.path.isfile(configured_binary):
            return configured_binary

        bundled_cuda = (
            self.working_root
            / "tools"
            / "llama.cpp-cuda"
            / "llama-server.exe"
        )
        bundled_cpu = (
            self.working_root
            / "tools"
            / "llama.cpp-cpu"
            / "llama-server.exe"
        )
        bundled_binary = bundled_cuda if bundled_cuda.is_file() else bundled_cpu
        if not bundled_binary.is_file():
            return None

        os.environ["LLAMA_CPP_BINARY"] = str(bundled_binary)
        os.environ.setdefault("SURYA_INFERENCE_BACKEND", "llamacpp")
        os.environ.setdefault("SURYA_INFERENCE_PARALLEL", "1")
        os.environ.setdefault("SURYA_INFERENCE_CTX_SIZE", "16384")
        os.environ.setdefault(
            "LLAMA_CPP_NGL", "99" if bundled_binary == bundled_cuda else "0"
        )
        os.environ.setdefault(
            "LLAMA_CPP_EXTRA_ARGS",
            (
                "--flash-attn on --cache-type-k q4_0 --cache-type-v q4_0"
                if os.environ["LLAMA_CPP_NGL"] != "0"
                else "--cache-type-k q4_0 --cache-type-v q4_0"
            ),
        )
        return str(bundled_binary)

    def available(self) -> tuple[bool, str]:
        if importlib.util.find_spec("surya") is None:
            return False, "Python package surya-ocr is not installed"
        if os.environ.get("SURYA_INFERENCE_URL"):
            return True, "Surya will use the configured inference server"
        if runtime := self._configure_bundled_runtime():
            if os.environ.get("LLAMA_CPP_NGL") == "0" or "llama.cpp-cpu" in runtime:
                return True, "Surya will use the bundled CPU llama.cpp server"
            return True, "Surya will use the bundled CUDA llama.cpp server"
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
            self._rotate_server_log()
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

            # A high-resolution evidence crop is already a known text block.
            # Block mode gives it a bounded token budget instead of allowing a
            # tiny strip to enter the full-page model's 12k-token decode path.
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
