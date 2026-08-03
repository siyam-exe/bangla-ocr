from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import queue
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .utils import nfc, read_json


OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openrouter/free"


class OpenRouterError(RuntimeError):
    pass


class OpenRouterRateLimitError(OpenRouterError):
    def __init__(self, message: str, *, retry_after_seconds: float = 10) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1.0, float(retry_after_seconds))


class OpenRouterTimeoutError(OpenRouterError):
    pass


class OpenRouterResponseError(OpenRouterError):
    pass


def _openrouter_http_error(exc: urllib.error.HTTPError) -> OpenRouterError:
    detail = exc.read().decode("utf-8", errors="replace")[:800]
    if exc.code == 429:
        retry_after: float = 10
        provider = "OpenRouter's upstream provider"
        try:
            payload = json.loads(detail)
            metadata = payload.get("error", {}).get("metadata", {})
            retry_after = float(metadata.get("retry_after_seconds", 10) or 10)
            provider = str(metadata.get("provider_name", provider))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            header_value = exc.headers.get("Retry-After") if exc.headers else None
            try:
                retry_after = float(header_value or 10)
            except (TypeError, ValueError):
                retry_after = 10
        return OpenRouterRateLimitError(
            f"{provider} temporarily rate-limited this model.",
            retry_after_seconds=retry_after,
        )
    return OpenRouterError(f"OpenRouter returned HTTP {exc.code}: {detail}")


def _post_openrouter_json(
    payload: dict[str, Any], api_key: str, timeout_seconds: int
) -> dict[str, Any]:
    """POST with a true wall-clock deadline around urllib's idle timeout."""
    result: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def request_worker() -> None:
        http_request = urllib.request.Request(
            OPENROUTER_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1",
                "X-Title": "Bangla OCR",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                http_request, timeout=timeout_seconds
            ) as response:
                value = json.loads(response.read().decode("utf-8"))
            result.put(("value", value))
        except urllib.error.HTTPError as exc:
            result.put(("error", _openrouter_http_error(exc)))
        except urllib.error.URLError as exc:
            result.put(
                (
                    "error",
                    OpenRouterError(f"OpenRouter is unavailable: {exc.reason}"),
                )
            )
        except Exception as exc:
            result.put(("error", OpenRouterError(str(exc))))

    thread = threading.Thread(
        target=request_worker,
        name="openrouter-http-request",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=max(1, timeout_seconds))
    if thread.is_alive():
        raise OpenRouterTimeoutError(
            f"OpenRouter did not finish within {timeout_seconds} seconds."
        )
    kind, value = result.get_nowait()
    if kind == "error":
        raise value
    if not isinstance(value, dict):
        raise OpenRouterError("OpenRouter returned an unexpected response.")
    return value


class CredentialStore:
    """Read an optional API key from the current process environment."""

    def get(self) -> str | None:
        environment_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        return environment_key or None


@dataclass
class CropRequest:
    image_path: Path
    text: str
    page_number: int
    line_index: int | None
    bbox: list[float] | None


def proposal_request_id(
    model: str, text: str, image_bytes: bytes
) -> str:
    return hashlib.sha256(
        (
            model
            + "\0"
            + text
            + "\0"
            + hashlib.sha256(image_bytes).hexdigest()
        ).encode("utf-8")
    ).hexdigest()


def crop_page_region(
    source_path: Path,
    destination: Path,
    bbox: list[float] | None,
    *,
    padding_ratio: float = 0.025,
) -> Path:
    image = Image.open(source_path).convert("RGB")
    if bbox and len(bbox) == 4:
        left, top, right, bottom = [float(value) for value in bbox]
        pad_x = int(image.width * padding_ratio)
        pad_y = int(image.height * padding_ratio)
        crop_box = (
            max(0, int(left) - pad_x),
            max(0, int(top) - pad_y),
            min(image.width, int(right) + pad_x),
            min(image.height, int(bottom) + pad_y),
        )
        if crop_box[2] > crop_box[0] and crop_box[3] > crop_box[1]:
            image = image.crop(crop_box)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "WEBP", quality=94, method=6)
    return destination


def _extract_json(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise OpenRouterResponseError("The model did not return valid JSON.")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise OpenRouterResponseError(
                "The model did not return valid JSON."
            ) from exc
    if not isinstance(parsed, dict):
        raise OpenRouterResponseError("The model response must be a JSON object.")
    return parsed


def _validate_changes(text: str, value: dict[str, Any]) -> list[dict[str, Any]]:
    changes = value.get("changes", [])
    if not isinstance(changes, list):
        raise OpenRouterError("The response field 'changes' must be a list.")
    validated: list[dict[str, Any]] = []
    for raw in changes[:20]:
        if not isinstance(raw, dict):
            continue
        original = nfc(str(raw.get("original", "")).strip())
        replacement = nfc(str(raw.get("replacement", "")).strip())
        if not original or not replacement or original == replacement:
            continue
        occurrences = text.count(original)
        if occurrences != 1:
            continue
        if len(replacement) > max(120, len(original) * 4):
            continue
        try:
            confidence = float(raw.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        validated.append(
            {
                "original": original,
                "replacement": replacement,
                "reason": nfc(str(raw.get("reason", "")).strip()),
                "confidence": max(0.0, min(1.0, confidence)),
                "status": "proposed",
            }
        )
    return validated


class OpenRouterClient:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        credential_store: CredentialStore | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self.model = model
        self.credentials = credential_store or CredentialStore()
        self.timeout_seconds = timeout_seconds

    def propose(self, request: CropRequest) -> dict[str, Any]:
        api_key = self.credentials.get()
        if not api_key:
            raise OpenRouterError(
                "No OpenRouter API key is configured in secure storage."
            )
        image_bytes = request.image_path.read_bytes()
        image_url = (
            "data:image/webp;base64,"
            + base64.b64encode(image_bytes).decode("ascii")
        )
        prompt = (
            "You are verifying an exact Bengali document transcription against "
            "the supplied scan crop. Preserve the source spelling, wording, "
            "punctuation, quotation marks, paragraph structure, and style. "
            "Do not rewrite, modernize, summarize, or complete invisible text. "
            "Return JSON only with this schema: "
            '{"changes":[{"original":"exact substring from OCR",'
            '"replacement":"exact visible source text","reason":"short image-grounded '
            'reason","confidence":0.0}],"uncertain":false,"notes":""}. '
            "Only propose a change when the replacement is visibly supported "
            "by the image. Use an empty changes list when uncertain.\n\n"
            f"OCR text:\n{request.text}"
        )
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
        }
        response_value = _post_openrouter_json(
            payload, api_key, self.timeout_seconds
        )

        try:
            message = response_value["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError("OpenRouter returned an unexpected response.") from exc
        parsed = _extract_json(str(message))
        changes = _validate_changes(request.text, parsed)
        created = dt.datetime.now(dt.UTC).isoformat()
        request_hash = proposal_request_id(self.model, request.text, image_bytes)
        return {
            "schema_version": 1,
            "request_id": request_hash,
            "created_utc": created,
            "page_number": request.page_number,
            "line_index": request.line_index,
            "bbox": request.bbox,
            "model_requested": self.model,
            "model_used": response_value.get("model", self.model),
            "status": "proposed" if changes else "no_change",
            "changes": changes,
            "uncertain": bool(parsed.get("uncertain", False)),
            "notes": nfc(str(parsed.get("notes", "")).strip()),
            "usage": response_value.get("usage", {}),
        }

def load_openrouter_settings(settings_path: Path) -> dict[str, Any]:
    if not settings_path.exists():
        return {
            "enabled": False,
            "model": DEFAULT_MODEL,
            "daily_request_limit": 40,
        }
    value = read_json(settings_path)
    return {
        "enabled": bool(value.get("enabled", False)),
        "model": str(value.get("model", DEFAULT_MODEL)),
        "daily_request_limit": max(
            1, min(200, int(value.get("daily_request_limit", 40)))
        ),
    }
