import argparse
import shutil
import zipfile
from pathlib import Path


def archive_package_licenses(site_packages: Path, destination: Path) -> int:
    license_directories = sorted(site_packages.glob("*.dist-info/licenses"))
    license_directories = [path for path in license_directories if path.is_dir()]
    if not license_directories:
        return 0

    current_files = {}
    for directory in license_directories:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                current_files[path.relative_to(site_packages).as_posix()] = path

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()

    existing = {}
    if destination.exists():
        with zipfile.ZipFile(destination) as source:
            for name in source.namelist():
                if name not in current_files:
                    existing[name] = source.read(name)

    with zipfile.ZipFile(
        temporary,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as target:
        for name in sorted(existing):
            target.writestr(name, existing[name])
        for name in sorted(current_files):
            target.write(current_files[name], name)

    temporary.replace(destination)
    for directory in license_directories:
        shutil.rmtree(directory)
    return len(current_files)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    count = archive_package_licenses(arguments.site_packages, arguments.destination)
    print(f"Archived {count} package license files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
