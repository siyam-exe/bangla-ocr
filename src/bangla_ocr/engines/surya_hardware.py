from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .surya_runtime import (
    SURYA_RUNTIME_DEFINITIONS,
    SuryaRuntimeDefinition,
)


_DEVICE_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_.-]*\d+):\s*(.+?)\s*$")
_VERSION_LINE = re.compile(r"^version:\s*(.+?)\s*$", re.IGNORECASE)
_MAX_DIAGNOSTIC_CHARS = 16_384


def _bounded_output(value: str) -> str:
    if len(value) <= _MAX_DIAGNOSTIC_CHARS:
        return value
    half = (_MAX_DIAGNOSTIC_CHARS - 25) // 2
    return f"{value[:half]}\n... output truncated ...\n{value[-half:]}"


def _command_output(result: subprocess.CompletedProcess[str]) -> str:
    values = [value.strip() for value in (result.stdout, result.stderr) if value]
    return _bounded_output("\n".join(values))


def _timeout_output(error: subprocess.TimeoutExpired) -> str:
    values: list[str] = []
    for value in (error.stdout, error.stderr):
        if isinstance(value, bytes):
            values.append(value.decode("utf-8", errors="replace"))
        elif value:
            values.append(str(value))
    return _bounded_output("\n".join(values).strip())


def _subprocess_options() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


@dataclass(frozen=True, slots=True)
class DisplayAdapter:
    name: str
    vendor: str
    driver_version: str | None
    windows_reported_adapter_ram_bytes: int | None
    status: str | None
    pnp_device_id: str | None

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "name": self.name,
            "vendor": self.vendor,
            "driver_version": self.driver_version,
            "windows_reported_adapter_ram_bytes": (
                self.windows_reported_adapter_ram_bytes
            ),
            "status": self.status,
            "pnp_device_id": self.pnp_device_id,
        }


@dataclass(frozen=True, slots=True)
class DisplayAdapterInventory:
    status: str
    reason: str
    adapters: tuple[DisplayAdapter, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "adapters": [adapter.as_dict() for adapter in self.adapters],
        }


@dataclass(frozen=True, slots=True)
class RuntimeDevice:
    identifier: str
    description: str

    def as_dict(self) -> dict[str, str]:
        return {
            "identifier": self.identifier,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class SuryaRuntimeProbe:
    backend: str
    display_name: str
    binary: Path
    installed: bool
    usable: bool
    status: str
    reason: str
    version: str | None
    manifest: dict[str, Any] | None
    devices: tuple[RuntimeDevice, ...]
    duration_ms: int
    return_code: int | None
    diagnostic_output: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "display_name": self.display_name,
            "binary": str(self.binary),
            "installed": self.installed,
            "usable": self.usable,
            "status": self.status,
            "reason": self.reason,
            "version": self.version,
            "manifest": self.manifest,
            "devices": [device.as_dict() for device in self.devices],
            "duration_ms": self.duration_ms,
            "return_code": self.return_code,
            "diagnostic_output": self.diagnostic_output,
        }


def _vendor_for_adapter(name: str) -> str:
    lowered = name.casefold()
    if "nvidia" in lowered:
        return "nvidia"
    if "amd" in lowered or "radeon" in lowered or "advanced micro devices" in lowered:
        return "amd"
    if "intel" in lowered:
        return "intel"
    if "microsoft basic display" in lowered:
        return "microsoft"
    return "unknown"


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def detect_windows_display_adapters(
    *,
    timeout_seconds: float = 8,
    platform_name: str | None = None,
) -> DisplayAdapterInventory:
    platform = os.name if platform_name is None else platform_name
    if platform != "nt":
        return DisplayAdapterInventory(
            status="not_applicable",
            reason=(
                "Windows display-adapter inventory is not available on this "
                "platform"
            ),
            adapters=(),
        )
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return DisplayAdapterInventory(
            status="unavailable",
            reason="Windows PowerShell was not found",
            adapters=(),
        )
    script = (
        "$ErrorActionPreference='Stop';"
        "$items=@(Get-CimInstance Win32_VideoController | "
        "Select-Object Name,DriverVersion,AdapterRAM,PNPDeviceID,Status);"
        "ConvertTo-Json -InputObject $items -Compress"
    )
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            **_subprocess_options(),
        )
    except subprocess.TimeoutExpired:
        return DisplayAdapterInventory(
            status="timeout",
            reason=f"Windows adapter detection exceeded {timeout_seconds:g} seconds",
            adapters=(),
        )
    except OSError as exc:
        return DisplayAdapterInventory(
            status="unavailable",
            reason=f"Windows adapter detection could not start: {exc}",
            adapters=(),
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        return DisplayAdapterInventory(
            status="failed",
            reason=f"Windows adapter detection failed: {detail}",
            adapters=(),
        )
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        return DisplayAdapterInventory(
            status="failed",
            reason=f"Windows returned invalid adapter information: {exc}",
            adapters=(),
        )
    values = payload if isinstance(payload, list) else [payload]
    adapters: list[DisplayAdapter] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        name = _optional_text(value.get("Name"))
        if not name:
            continue
        adapters.append(
            DisplayAdapter(
                name=name,
                vendor=_vendor_for_adapter(name),
                driver_version=_optional_text(value.get("DriverVersion")),
                windows_reported_adapter_ram_bytes=_optional_int(
                    value.get("AdapterRAM")
                ),
                status=_optional_text(value.get("Status")),
                pnp_device_id=_optional_text(value.get("PNPDeviceID")),
            )
        )
    if not adapters:
        return DisplayAdapterInventory(
            status="empty",
            reason="Windows did not report a display adapter",
            adapters=(),
        )
    return DisplayAdapterInventory(
        status="ready",
        reason=f"Windows reported {len(adapters)} display adapter(s)",
        adapters=tuple(adapters),
    )


def _parse_runtime_output(
    output: str,
) -> tuple[str | None, tuple[RuntimeDevice, ...]]:
    version: str | None = None
    devices: list[RuntimeDevice] = []
    reading_devices = False
    for line in output.splitlines():
        if version is None and (match := _VERSION_LINE.match(line.strip())):
            version = match.group(1)
        if line.strip().casefold() == "available devices:":
            reading_devices = True
            continue
        if not reading_devices:
            continue
        match = _DEVICE_LINE.match(line)
        if match:
            devices.append(
                RuntimeDevice(
                    identifier=match.group(1),
                    description=match.group(2),
                )
            )
    return version, tuple(devices)


def _runtime_manifest(binary: Path) -> dict[str, Any] | None:
    path = binary.parent / "runtime-version.json"
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > 65_536:
            return None
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _reported_runtime_version(
    parsed_version: str | None,
    manifest: dict[str, Any] | None,
) -> str | None:
    if parsed_version:
        return parsed_version
    if not manifest:
        return None
    release = _optional_text(manifest.get("release"))
    build = _optional_text(manifest.get("build"))
    if release and build:
        return f"{release} (build {build})"
    return release or build


def probe_surya_runtime(
    definition: SuryaRuntimeDefinition,
    working_root: Path,
    *,
    timeout_seconds: float = 10,
) -> SuryaRuntimeProbe:
    binary = definition.binary_path(working_root)
    manifest = _runtime_manifest(binary)
    if not binary.is_file():
        return SuryaRuntimeProbe(
            backend=definition.key,
            display_name=definition.display_name,
            binary=binary,
            installed=False,
            usable=False,
            status="missing",
            reason=f"The {definition.display_name} runtime is not installed",
            version=None,
            manifest=None,
            devices=(),
            duration_ms=0,
            return_code=None,
            diagnostic_output="",
        )
    started = time.perf_counter()
    probe_environment = os.environ.copy()
    probe_environment.pop("LLAMA_ARG_DEVICE", None)
    probe_environment.pop("LLAMA_ARG_SPLIT_MODE", None)
    try:
        result = subprocess.run(
            [str(binary), "--list-devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            env=probe_environment,
            **_subprocess_options(),
        )
    except subprocess.TimeoutExpired as exc:
        return SuryaRuntimeProbe(
            backend=definition.key,
            display_name=definition.display_name,
            binary=binary,
            installed=True,
            usable=False,
            status="timeout",
            reason=(
                f"The {definition.display_name} device probe exceeded "
                f"{timeout_seconds:g} seconds"
            ),
            version=None,
            manifest=manifest,
            devices=(),
            duration_ms=round((time.perf_counter() - started) * 1000),
            return_code=None,
            diagnostic_output=_timeout_output(exc),
        )
    except OSError as exc:
        return SuryaRuntimeProbe(
            backend=definition.key,
            display_name=definition.display_name,
            binary=binary,
            installed=True,
            usable=False,
            status="launch_failed",
            reason=f"The {definition.display_name} device probe could not start: {exc}",
            version=None,
            manifest=manifest,
            devices=(),
            duration_ms=round((time.perf_counter() - started) * 1000),
            return_code=None,
            diagnostic_output=str(exc),
        )
    output = _command_output(result)
    parsed_version, devices = _parse_runtime_output(output)
    version = _reported_runtime_version(parsed_version, manifest)
    duration_ms = round((time.perf_counter() - started) * 1000)
    if result.returncode != 0:
        return SuryaRuntimeProbe(
            backend=definition.key,
            display_name=definition.display_name,
            binary=binary,
            installed=True,
            usable=False,
            status="rejected",
            reason=(
                f"The {definition.display_name} device probe exited with code "
                f"{result.returncode}"
            ),
            version=version,
            manifest=manifest,
            devices=devices,
            duration_ms=duration_ms,
            return_code=result.returncode,
            diagnostic_output=output,
        )
    if definition.accelerated and not devices:
        return SuryaRuntimeProbe(
            backend=definition.key,
            display_name=definition.display_name,
            binary=binary,
            installed=True,
            usable=False,
            status="no_device",
            reason=f"The {definition.display_name} runtime did not report a usable GPU",
            version=version,
            manifest=manifest,
            devices=(),
            duration_ms=duration_ms,
            return_code=result.returncode,
            diagnostic_output=output,
        )
    device_count = len(devices)
    reason = (
        f"The {definition.display_name} runtime reported {device_count} "
        "usable device(s)"
        if definition.accelerated
        else "The CPU runtime started successfully"
    )
    return SuryaRuntimeProbe(
        backend=definition.key,
        display_name=definition.display_name,
        binary=binary,
        installed=True,
        usable=True,
        status="ready",
        reason=reason,
        version=version,
        manifest=manifest,
        devices=devices,
        duration_ms=duration_ms,
        return_code=result.returncode,
        diagnostic_output=output,
    )


def probe_surya_hardware(
    working_root: Path,
    *,
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    inventory = detect_windows_display_adapters(
        timeout_seconds=min(timeout_seconds, 8)
    )
    runtime_probes = {
        key: probe_surya_runtime(
            definition,
            working_root,
            timeout_seconds=timeout_seconds,
        ).as_dict()
        for key, definition in SURYA_RUNTIME_DEFINITIONS.items()
    }
    return {
        "generated_utc": dt.datetime.now(dt.UTC).isoformat(),
        "display_adapters": inventory.as_dict(),
        "runtimes": runtime_probes,
    }
