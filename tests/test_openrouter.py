import json
from io import BytesIO
from urllib.error import HTTPError

from PIL import Image

from bangla_ocr.openrouter_client import (
    _extract_json,
    _openrouter_http_error,
    _validate_changes,
    crop_page_region,
    proposal_request_id,
    OpenRouterRateLimitError,
)


def test_openrouter_json_fence_is_parsed():
    value = _extract_json('```json\n{"changes":[],"uncertain":true}\n```')
    assert value["uncertain"] is True


def test_only_exact_unique_substring_change_is_allowed():
    changes = _validate_changes(
        "তাকে তুলে নিল।",
        {
            "changes": [
                {
                    "original": "তাকে",
                    "replacement": "তাঁকে",
                    "reason": "scan",
                    "confidence": 0.9,
                },
                {
                    "original": "নেই",
                    "replacement": "আছে",
                    "reason": "invented",
                    "confidence": 1,
                },
            ]
        },
    )
    assert len(changes) == 1
    assert changes[0]["original"] == "তাকে"


def test_crop_page_region_uses_requested_bbox(tmp_path):
    source = tmp_path / "page.png"
    destination = tmp_path / "crop.webp"
    Image.new("RGB", (1000, 800), "white").save(source)
    crop_page_region(source, destination, [200, 200, 600, 500], padding_ratio=0)
    with Image.open(destination) as crop:
        assert crop.size == (400, 300)


def test_proposal_request_id_is_stable_and_input_specific():
    first = proposal_request_id("openrouter/free", "এক", b"image")
    assert first == proposal_request_id("openrouter/free", "এক", b"image")
    assert first != proposal_request_id("openrouter/free", "দুই", b"image")


def test_http_429_exposes_provider_retry_delay_without_raw_error_dump():
    payload = {
        "error": {
            "metadata": {
                "provider_name": "Shared provider",
                "retry_after_seconds": 5,
            }
        }
    }
    error = HTTPError(
        "https://openrouter.ai",
        429,
        "rate limited",
        {},
        BytesIO(json.dumps(payload).encode("utf-8")),
    )

    rendered = _openrouter_http_error(error)

    assert isinstance(rendered, OpenRouterRateLimitError)
    assert rendered.retry_after_seconds == 5
    assert str(rendered) == "Shared provider temporarily rate-limited this model."
