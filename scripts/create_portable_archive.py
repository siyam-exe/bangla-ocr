from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def create_archive(source: Path, output: Path) -> None:
    source = source.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for path in sorted(source.rglob("*")):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix.lower() not in {".pyc", ".pyo", ".a", ".lib"}
            ):
                name = Path(source.name) / path.relative_to(source)
                archive.write(path, name.as_posix())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    create_archive(args.source, args.output)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
