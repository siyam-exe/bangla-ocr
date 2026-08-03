from __future__ import annotations

import argparse
import importlib.metadata
import re
import subprocess
import sys


SURYA_PILLOW_CONFLICT = re.compile(
    r"^surya-ocr 0\.22\.1 has requirement pillow<11,>=10\.2\.0, "
    r"but you have pillow 12\.(?:[3-9]|[1-9][0-9])(?:\.[0-9]+)?\.$",
    re.IGNORECASE,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-surya-pillow-override", action="store_true")
    args = parser.parse_args()
    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    unexpected: list[str] = []
    accepted: list[str] = []
    for line in lines:
        if args.allow_surya_pillow_override and SURYA_PILLOW_CONFLICT.fullmatch(line):
            accepted.append(line)
        else:
            unexpected.append(line)
    if unexpected:
        print("\n".join(unexpected), file=sys.stderr)
        return 1
    if accepted:
        surya = importlib.metadata.version("surya-ocr")
        pillow = importlib.metadata.version("pillow")
        print(
            "Accepted tested compatibility override: "
            f"surya-ocr {surya} with Pillow {pillow}. See SECURITY.md."
        )
    else:
        print("No broken requirements found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
