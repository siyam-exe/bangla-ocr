from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from .surya_hardware import probe_surya_hardware
from .surya_runtime import (
    SURYA_RUNTIME_DEFINITIONS,
    SuryaRuntimeSelection,
)


VALID_BACKEND_REQUESTS = frozenset({"auto", *SURYA_RUNTIME_DEFINITIONS})
_MEMORY_PATTERN = re.compile(r"(\d+)\s*MiB(?:\s+free)?", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SuryaBackendDecision:
    requested: str
    selected: SuryaRuntimeSelection | None
    automatic: bool
    cpu_fallback: bool
    reason: str
    attempted_backends: tuple[str, ...]
    runtime_statuses: dict[str, dict[str, Any]]
    requested_backend: str = ""
    requested_device: str | None = None

    @property
    def usable(self) -> bool:
        return self.selected is not None

    @property
    def selected_backend(self) -> str | None:
        return self.selected.backend if self.selected else None

    @property
    def selected_device(self) -> str | None:
        return self.selected.device_identifier if self.selected else None

    @property
    def selected_request(self) -> str | None:
        return self.selected.request_key if self.selected else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "requested_backend": self.requested_backend,
            "requested_device": self.requested_device,
            "selected_backend": self.selected_backend,
            "selected_device": self.selected_device,
            "selected_request": self.selected_request,
            "automatic": self.automatic,
            "cpu_fallback": self.cpu_fallback,
            "reason": self.reason,
            "attempted_backends": list(self.attempted_backends),
            "runtime_statuses": self.runtime_statuses,
            "selection": (
                self.selected.diagnostics() if self.selected else None
            ),
        }


@dataclass(frozen=True, slots=True)
class SuryaRuntimeFallbackDecision:
    allowed: bool
    next_request: str | None
    reason: str


def _adapter_vendors(hardware_report: dict[str, Any]) -> set[str]:
    adapters = hardware_report.get("display_adapters", {}).get("adapters", [])
    return {
        str(adapter.get("vendor") or "unknown").casefold()
        for adapter in adapters
        if isinstance(adapter, dict)
    }


def parse_surya_request(requested: str) -> tuple[str, str | None]:
    value = requested.strip() or "auto"
    backend, separator, device = value.partition("/")
    backend = backend.casefold()
    device = device.strip() if separator else ""
    if backend not in VALID_BACKEND_REQUESTS:
        raise ValueError(f"Unknown Surya runtime backend: {backend}")
    if backend in {"auto", "cpu"} and device:
        raise ValueError(f"The {backend} runtime does not accept a GPU device")
    if separator and not device:
        raise ValueError("A runtime device identifier is required after '/'")
    return backend, device or None


def _device_score(device: dict[str, Any]) -> tuple[int, int, int, str]:
    description = str(device.get("description") or "")
    lowered = description.casefold()
    memory = [int(value) for value in _MEMORY_PATTERN.findall(description)]
    total_memory = memory[0] if memory else 0
    free_memory = memory[1] if len(memory) > 1 else total_memory
    if any(value in lowered for value in ("llvmpipe", "software", "basic display")):
        hardware_class = 0
    elif "nvidia" in lowered or "radeon" in lowered or "intel arc" in lowered:
        hardware_class = 3
    elif "intel" in lowered and any(
        value in lowered for value in ("uhd", "iris", "hd graphics")
    ):
        hardware_class = 1
    else:
        hardware_class = 2
    return hardware_class, free_memory, total_memory, description.casefold()


def ranked_runtime_devices(runtime_status: dict[str, Any]) -> list[dict[str, Any]]:
    devices = [
        dict(device)
        for device in runtime_status.get("devices", [])
        if isinstance(device, dict) and str(device.get("identifier") or "").strip()
    ]
    return sorted(devices, key=_device_score, reverse=True)


def _select_device(
    runtime_status: dict[str, Any], requested_device: str | None
) -> dict[str, Any] | None:
    devices = ranked_runtime_devices(runtime_status)
    if requested_device is None:
        return devices[0] if devices else None
    requested_key = requested_device.casefold()
    return next(
        (
            device
            for device in devices
            if str(device.get("identifier") or "").casefold() == requested_key
        ),
        None,
    )


def _automatic_order(hardware_report: dict[str, Any]) -> tuple[str, ...]:
    inventory = hardware_report.get("display_adapters", {})
    vendors = _adapter_vendors(hardware_report)
    if inventory.get("status") == "ready":
        if "nvidia" in vendors:
            return "cuda", "vulkan", "cpu"
        if vendors.intersection({"amd", "intel"}):
            return "vulkan", "cpu"
        return ("cpu",)
    return "cuda", "vulkan", "cpu"


def automatic_surya_requests(
    hardware_report: dict[str, Any],
) -> tuple[str, ...]:
    statuses = hardware_report.get("runtimes", {})
    requests: list[str] = []
    for backend in _automatic_order(hardware_report):
        status = statuses.get(backend, {})
        if not isinstance(status, dict) or not bool(status.get("usable")):
            continue
        definition = SURYA_RUNTIME_DEFINITIONS[backend]
        if not definition.accelerated:
            requests.append(backend)
            continue
        device = _select_device(status, None)
        if device is not None:
            requests.append(f"{backend}/{device['identifier']}")
    return tuple(requests)


def decide_surya_runtime_fallback(
    *,
    original_request: str,
    attempted_requests: list[str] | tuple[str, ...],
    hardware_report: dict[str, Any],
    completed_page_count: int,
    runtime_failure: bool,
) -> SuryaRuntimeFallbackDecision:
    requested_backend, _ = parse_surya_request(original_request)
    if requested_backend != "auto":
        return SuryaRuntimeFallbackDecision(
            allowed=False,
            next_request=None,
            reason="An explicit Surya runtime never changes automatically.",
        )
    if completed_page_count > 0:
        return SuryaRuntimeFallbackDecision(
            allowed=False,
            next_request=None,
            reason=(
                "At least one page completed, so the runtime must not change "
                "without the user's approval."
            ),
        )
    if not runtime_failure:
        return SuryaRuntimeFallbackDecision(
            allowed=False,
            next_request=None,
            reason="The failure was not identified as a Surya runtime failure.",
        )
    attempted = {str(value).casefold() for value in attempted_requests}
    next_request = next(
        (
            request
            for request in automatic_surya_requests(hardware_report)
            if request.casefold() not in attempted
        ),
        None,
    )
    if next_request is None:
        return SuryaRuntimeFallbackDecision(
            allowed=False,
            next_request=None,
            reason="Every compatible installed Surya runtime has been tried.",
        )
    return SuryaRuntimeFallbackDecision(
        allowed=True,
        next_request=next_request,
        reason=(
            "The selected runtime failed before the first page completed. "
            f"Surya can retry that page with {next_request}."
        ),
    )


def decide_surya_backend(
    working_root: Path,
    *,
    requested: str = "auto",
    hardware_report: dict[str, Any] | None = None,
) -> SuryaBackendDecision:
    requested_backend, requested_device = parse_surya_request(requested)
    request = (
        f"{requested_backend}/{requested_device}"
        if requested_device
        else requested_backend
    )
    report = hardware_report or probe_surya_hardware(working_root)
    statuses = {
        str(key): dict(value)
        for key, value in report.get("runtimes", {}).items()
        if isinstance(value, dict)
    }
    order = (
        _automatic_order(report)
        if requested_backend == "auto"
        else (requested_backend,)
    )
    selected_backend = next(
        (
            backend
            for backend in order
            if bool(statuses.get(backend, {}).get("usable"))
        ),
        None,
    )
    if selected_backend is None:
        if requested_backend == "auto":
            reason = "No installed Surya runtime passed its device health check"
        else:
            detail = statuses.get(requested_backend, {}).get("reason")
            reason = str(detail or f"The {requested_backend} runtime is unavailable")
        return SuryaBackendDecision(
            requested=request,
            requested_backend=requested_backend,
            requested_device=requested_device,
            selected=None,
            automatic=requested_backend == "auto",
            cpu_fallback=False,
            reason=reason,
            attempted_backends=order,
            runtime_statuses=statuses,
        )
    definition = SURYA_RUNTIME_DEFINITIONS[selected_backend]
    selected_device = None
    if definition.accelerated:
        selected_device = _select_device(
            statuses.get(selected_backend, {}),
            requested_device if selected_backend == requested_backend else None,
        )
        if selected_device is None:
            reason = (
                f"The {definition.display_name} runtime did not report "
                f"device {requested_device}"
                if requested_device
                else f"The {definition.display_name} runtime did not report a usable GPU"
            )
            return SuryaBackendDecision(
                requested=request,
                requested_backend=requested_backend,
                requested_device=requested_device,
                selected=None,
                automatic=requested_backend == "auto",
                cpu_fallback=False,
                reason=reason,
                attempted_backends=order,
                runtime_statuses=statuses,
            )
    selection = SuryaRuntimeSelection(
        definition=definition,
        binary=definition.binary_path(working_root),
        source="bundled",
        device_identifier=(
            str(selected_device.get("identifier")) if selected_device else None
        ),
        device_description=(
            str(selected_device.get("description")) if selected_device else None
        ),
    )
    cpu_fallback = requested_backend == "auto" and selected_backend == "cpu"
    if cpu_fallback:
        reason = (
            "No GPU runtime passed its device health check. "
            "Surya will use CPU."
        )
    elif requested_backend == "auto":
        reason = (
            f"Automatic selection chose {definition.display_name}"
            + (
                f" on {selection.device_description}"
                if selection.device_description
                else ""
            )
        )
    else:
        reason = f"The requested {definition.display_name} runtime is ready"
        if selection.device_description:
            reason += f" on {selection.device_description}"
    return SuryaBackendDecision(
        requested=request,
        requested_backend=requested_backend,
        requested_device=requested_device,
        selected=selection,
        automatic=requested_backend == "auto",
        cpu_fallback=cpu_fallback,
        reason=reason,
        attempted_backends=order,
        runtime_statuses=statuses,
    )
