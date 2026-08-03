from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from rapidfuzz.distance import Levenshtein


ROOT = Path(__file__).resolve().parent
GROUND_TRUTH = ROOT / "fixture" / "ground-truth"


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n").strip()


def tokens(text: str) -> list[str]:
    return re.findall(r"\S+", normalize(text))


def rate(reference: list[Any] | str, prediction: list[Any] | str) -> tuple[int, float]:
    edits = Levenshtein.distance(reference, prediction)
    return edits, edits / max(1, len(reference))


def score(workspace: Path, *, include_workspace_path: bool = False) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    total_reference = ""
    total_prediction = ""
    total_reference_tokens: list[str] = []
    total_prediction_tokens: list[str] = []
    for page_root in sorted((workspace / "pages").glob("[0-9][0-9][0-9][0-9]")):
        page_number = int(page_root.name)
        reference_path = GROUND_TRUTH / f"page-{page_number:04d}.txt"
        if not reference_path.exists():
            raise FileNotFoundError(
                f"No public ground truth exists for PDF page {page_number}."
            )
        prediction_path = page_root / "final.txt"
        if not prediction_path.exists():
            prediction_path = page_root / "draft.txt"
        reference = normalize(reference_path.read_text(encoding="utf-8"))
        prediction = normalize(prediction_path.read_text(encoding="utf-8"))
        char_edits, cer = rate(reference, prediction)
        word_edits, wer = rate(tokens(reference), tokens(prediction))
        state = json.loads((page_root / "page.json").read_text(encoding="utf-8"))
        pages.append(
            {
                "page": page_number,
                "reference_characters": len(reference),
                "prediction_characters": len(prediction),
                "character_edits": char_edits,
                "cer": round(cer, 6),
                "word_edits": word_edits,
                "wer": round(wer, 6),
                "exact_match": reference == prediction,
                "reference_paragraphs": len(reference.split("\n\n")),
                "prediction_paragraphs": len(prediction.split("\n\n")),
                "processing_seconds": state.get("timings", {}).get(
                    "throughput_page_seconds"
                ),
                "crop_seconds": state.get("timings", {}).get(
                    "multiscale_ocr_seconds", 0
                ),
                "crop_regions": len(
                    state.get("multiscale", {}).get("regions", [])
                ),
                "crop_disagreements": int(
                    state.get("multiscale", {}).get("pending_count", 0)
                ),
            }
        )
        total_reference += reference + "\n"
        total_prediction += prediction + "\n"
        total_reference_tokens.extend(tokens(reference))
        total_prediction_tokens.extend(tokens(prediction))
    char_edits, cer = rate(total_reference, total_prediction)
    word_edits, wer = rate(total_reference_tokens, total_prediction_tokens)
    return {
        "schema_version": 2,
        "created_utc": dt.datetime.now(dt.UTC).isoformat(),
        "workspace": (
            str(workspace.resolve()) if include_workspace_path else workspace.name
        ),
        "pages": pages,
        "summary": {
            "page_count": len(pages),
            "reference_characters": len(total_reference),
            "prediction_characters": len(total_prediction),
            "character_edits": char_edits,
            "cer": round(cer, 6),
            "character_accuracy": round(1 - cer, 6),
            "word_edits": word_edits,
            "wer": round(wer, 6),
            "word_accuracy": round(1 - wer, 6),
            "exact_pages": sum(page["exact_match"] for page in pages),
            "processing_seconds": round(
                sum(float(page["processing_seconds"] or 0) for page in pages), 3
            ),
            "crop_seconds": round(
                sum(float(page["crop_seconds"] or 0) for page in pages), 3
            ),
            "crop_regions": sum(page["crop_regions"] for page in pages),
            "crop_disagreements": sum(
                page["crop_disagreements"] for page in pages
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-workspace-path", action="store_true")
    args = parser.parse_args()
    result = score(
        args.workspace,
        include_workspace_path=args.include_workspace_path,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered)


if __name__ == "__main__":
    main()
