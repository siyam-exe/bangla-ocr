from __future__ import annotations

import ctypes
import datetime as dt
import os
import shutil
from pathlib import Path
from typing import Any


MIB = 1024 * 1024
GIB = 1024 * MIB


def runtime_paths(config: dict[str, Any]) -> dict[str, Path]:
    storage = config.get("storage", {})
    root = Path(storage.get("runtime_root", "runtime")).resolve()
    return {
        "root": root,
        "temp": root / "temp",
        "surya": root / "surya",
        "logs": root / "logs",
    }


def configure_runtime_environment(config: dict[str, Any]) -> dict[str, Path]:
    paths = runtime_paths(config)
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    temp = str(paths["temp"])
    os.environ["TEMP"] = temp
    os.environ["TMP"] = temp
    os.environ["TMPDIR"] = temp
    os.environ["SURYA_RUNTIME_DIR"] = str(paths["surya"])
    storage = config.get("storage", {})
    os.environ["SURYA_LOG_MAX_MIB"] = str(
        storage.get("surya_log_max_mib", 8)
    )
    os.environ["SURYA_LOG_BACKUPS"] = str(
        storage.get("surya_log_backups", 3)
    )
    return paths


def format_bytes(value: int | float) -> str:
    amount = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"


def _disk_snapshot(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total": format_bytes(usage.total),
        "used": format_bytes(usage.used),
        "free": format_bytes(usage.free),
    }


def _windows_memory_snapshot() -> dict[str, Any]:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.length = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {}

    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("page_fault_count", ctypes.c_ulong),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
            ("private_usage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(ProcessMemoryCountersEx)
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    process = ctypes.windll.kernel32.GetCurrentProcess()
    process_ok = ctypes.windll.psapi.GetProcessMemoryInfo(
        process, ctypes.byref(counters), counters.cb
    )
    physical_used = status.total_physical - status.available_physical
    commit_used = status.total_page_file - status.available_page_file
    result = {
        "physical_total_bytes": status.total_physical,
        "physical_available_bytes": status.available_physical,
        "physical_used_bytes": physical_used,
        "commit_limit_bytes": status.total_page_file,
        "commit_available_bytes": status.available_page_file,
        "commit_used_bytes": commit_used,
        "memory_load_percent": status.memory_load,
        "physical_available": format_bytes(status.available_physical),
        "commit_used": format_bytes(commit_used),
        "commit_limit": format_bytes(status.total_page_file),
    }
    if process_ok:
        result.update(
            {
                "process_working_set_bytes": counters.working_set_size,
                "process_private_bytes": counters.private_usage,
                "process_working_set": format_bytes(counters.working_set_size),
                "process_private": format_bytes(counters.private_usage),
            }
        )
    return result


def resource_snapshot(
    config: dict[str, Any], output_root: Path
) -> dict[str, Any]:
    storage = config.get("storage", {})
    system_root = Path(storage.get("system_drive", "C:\\"))
    runtime_root = runtime_paths(config)["root"]
    value = {
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
        "disks": {
            "system": _disk_snapshot(system_root),
            "runtime": _disk_snapshot(runtime_root),
            "output": _disk_snapshot(output_root.resolve()),
        },
        "memory": _windows_memory_snapshot() if os.name == "nt" else {},
    }
    return value


def update_resource_extrema(
    extrema: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    result = dict(extrema)
    disks = snapshot.get("disks", {})
    memory = snapshot.get("memory", {})
    minimum_fields = {
        "minimum_system_free_bytes": disks.get("system", {}).get("free_bytes"),
        "minimum_runtime_free_bytes": disks.get("runtime", {}).get("free_bytes"),
        "minimum_output_free_bytes": disks.get("output", {}).get("free_bytes"),
        "minimum_physical_available_bytes": memory.get(
            "physical_available_bytes"
        ),
    }
    maximum_fields = {
        "maximum_commit_used_bytes": memory.get("commit_used_bytes"),
        "maximum_process_working_set_bytes": memory.get(
            "process_working_set_bytes"
        ),
        "maximum_process_private_bytes": memory.get("process_private_bytes"),
    }
    for name, value in minimum_fields.items():
        if value is not None:
            result[name] = min(int(value), int(result.get(name, value)))
    for name, value in maximum_fields.items():
        if value is not None:
            result[name] = max(int(value), int(result.get(name, value)))
    result["updated_utc"] = snapshot.get("timestamp_utc")
    return result


def storage_preflight(
    config: dict[str, Any],
    output_root: Path,
    *,
    estimated_workspace_bytes: int = 0,
) -> dict[str, Any]:
    storage = config.get("storage", {})
    snapshot = resource_snapshot(config, output_root)
    system_free = snapshot["disks"]["system"]["free_bytes"]
    output_free = snapshot["disks"]["output"]["free_bytes"]
    warning_bytes = int(float(storage.get("system_warning_gib", 15)) * GIB)
    block_bytes = int(float(storage.get("system_block_gib", 12)) * GIB)
    output_reserve = int(float(storage.get("output_reserve_gib", 2)) * GIB)
    warnings: list[str] = []
    errors: list[str] = []
    if system_free < block_bytes:
        errors.append(
            f"C: has only {format_bytes(system_free)} free; at least "
            f"{format_bytes(block_bytes)} is required before OCR starts."
        )
    elif system_free < warning_bytes:
        warnings.append(
            f"C: is low on space ({format_bytes(system_free)} free)."
        )
    required_output = estimated_workspace_bytes + output_reserve
    if output_free < required_output:
        errors.append(
            f"The output drive has {format_bytes(output_free)} free, but this "
            f"job needs an estimated {format_bytes(estimated_workspace_bytes)} "
            f"plus a {format_bytes(output_reserve)} reserve."
        )
    return {
        "ready": not errors,
        "warnings": warnings,
        "errors": errors,
        "snapshot": snapshot,
        "estimated_workspace_bytes": estimated_workspace_bytes,
        "estimated_workspace": format_bytes(estimated_workspace_bytes),
        "system_warning_bytes": warning_bytes,
        "system_block_bytes": block_bytes,
    }


def estimate_workspace_bytes(source_pdf: Path, selected_page_count: int) -> int:
    source_bytes = source_pdf.stat().st_size if source_pdf.exists() else 0
    return source_bytes + max(1, selected_page_count) * 2 * MIB


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def rotate_file(path: Path, *, max_bytes: int, backups: int = 3) -> bool:
    if not path.exists() or path.stat().st_size <= max_bytes:
        return False
    for index in range(backups, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if index == backups:
            source.unlink(missing_ok=True)
        elif source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))
    return True


def cleanup_disposable_files(
    config: dict[str, Any],
    source_root: Path,
    *,
    older_than_hours: int = 24,
) -> dict[str, Any]:
    cutoff = dt.datetime.now().timestamp() - max(1, older_than_hours) * 3600
    paths = runtime_paths(config)
    temp_root = paths["temp"].resolve()
    source_root = source_root.resolve()
    deleted_files = 0
    deleted_bytes = 0

    def remove_file(path: Path, allowed_root: Path) -> None:
        nonlocal deleted_files, deleted_bytes
        resolved = path.resolve()
        if not resolved.is_relative_to(allowed_root):
            raise ValueError(f"Refusing cleanup outside {allowed_root}: {resolved}")
        try:
            size = resolved.stat().st_size
            resolved.unlink()
        except FileNotFoundError:
            return
        deleted_files += 1
        deleted_bytes += size

    for path in temp_root.rglob("*") if temp_root.exists() else []:
        if path.is_file() and path.stat().st_mtime < cutoff:
            remove_file(path, temp_root)
    for directory in sorted(
        (path for path in temp_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    for path in source_root.glob(".upload-*.pdf"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            remove_file(path, source_root)
    return {
        "deleted_files": deleted_files,
        "deleted_bytes": deleted_bytes,
        "deleted": format_bytes(deleted_bytes),
        "older_than_hours": older_than_hours,
    }
