from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, MutableMapping


CPU_EXTRA_ARGS = "--cache-type-k q4_0 --cache-type-v q4_0"
GPU_EXTRA_ARGS = f"--flash-attn on {CPU_EXTRA_ARGS}"


@dataclass(frozen=True, slots=True)
class SuryaRuntimeDefinition:
    key: str
    display_name: str
    folder_name: str
    accelerated: bool
    gpu_layers: int
    extra_args: str

    def binary_path(self, working_root: Path) -> Path:
        return working_root / "tools" / self.folder_name / "llama-server.exe"


@dataclass(frozen=True, slots=True)
class SuryaRuntimeSelection:
    definition: SuryaRuntimeDefinition | None
    binary: Path
    source: str
    device_identifier: str | None = None
    device_description: str | None = None

    @property
    def backend(self) -> str:
        return self.definition.key if self.definition else "custom"

    @property
    def display_name(self) -> str:
        if self.definition:
            return self.definition.display_name
        return "configured llama.cpp"

    @property
    def accelerated(self) -> bool | None:
        if self.definition:
            return self.definition.accelerated
        return None

    @property
    def request_key(self) -> str:
        if self.device_identifier:
            return f"{self.backend}/{self.device_identifier}"
        return self.backend

    def configure_environment(
        self,
        environ: MutableMapping[str, str],
        *,
        overwrite_runtime: bool = False,
    ) -> None:
        environ["LLAMA_CPP_BINARY"] = str(self.binary)
        environ.setdefault("SURYA_INFERENCE_BACKEND", "llamacpp")
        environ.setdefault("SURYA_INFERENCE_KEEP_ALIVE", "1")
        environ.setdefault("SURYA_INFERENCE_PARALLEL", "1")
        environ.setdefault("SURYA_INFERENCE_CTX_SIZE", "16384")
        if self.definition:
            values = {
                "LLAMA_CPP_NGL": str(self.definition.gpu_layers),
                "LLAMA_CPP_EXTRA_ARGS": self.definition.extra_args,
            }
            for name, value in values.items():
                if overwrite_runtime:
                    environ[name] = value
                else:
                    environ.setdefault(name, value)
            environ["BANGLA_OCR_SURYA_RUNTIME"] = self.definition.key
            environ["BANGLA_OCR_MANAGED_LLAMA_CPP"] = "1"
            if self.device_identifier:
                environ["LLAMA_ARG_DEVICE"] = self.device_identifier
                environ["LLAMA_ARG_SPLIT_MODE"] = "none"
                environ["BANGLA_OCR_SURYA_DEVICE"] = self.device_identifier
            else:
                environ.pop("LLAMA_ARG_DEVICE", None)
                environ.pop("LLAMA_ARG_SPLIT_MODE", None)
                environ.pop("BANGLA_OCR_SURYA_DEVICE", None)

    def availability_message(self) -> str:
        if self.definition is None:
            return "Surya will use the configured llama.cpp server"
        return (
            f"Surya will use the {self.source} "
            f"{self.display_name} llama.cpp server"
        )

    def diagnostics(self) -> dict[str, str | bool | int | None]:
        return {
            "backend": self.backend,
            "display_name": self.display_name,
            "source": self.source,
            "binary": str(self.binary),
            "accelerated": self.accelerated,
            "gpu_layers": (
                self.definition.gpu_layers if self.definition else None
            ),
            "device_identifier": self.device_identifier,
            "device_description": self.device_description,
            "request_key": self.request_key,
        }


SURYA_RUNTIME_DEFINITIONS: Mapping[str, SuryaRuntimeDefinition] = MappingProxyType(
    {
        "cpu": SuryaRuntimeDefinition(
            key="cpu",
            display_name="CPU",
            folder_name="llama.cpp-cpu",
            accelerated=False,
            gpu_layers=0,
            extra_args=CPU_EXTRA_ARGS,
        ),
        "cuda": SuryaRuntimeDefinition(
            key="cuda",
            display_name="CUDA",
            folder_name="llama.cpp-cuda",
            accelerated=True,
            gpu_layers=99,
            extra_args=GPU_EXTRA_ARGS,
        ),
        "vulkan": SuryaRuntimeDefinition(
            key="vulkan",
            display_name="Vulkan",
            folder_name="llama.cpp-vulkan",
            accelerated=True,
            gpu_layers=99,
            extra_args=GPU_EXTRA_ARGS,
        ),
    }
)


def definition_for_binary(binary: Path) -> SuryaRuntimeDefinition | None:
    path_parts = {part.casefold() for part in binary.parts}
    for definition in SURYA_RUNTIME_DEFINITIONS.values():
        if definition.folder_name.casefold() in path_parts:
            return definition
    return None


def configured_runtime(binary: Path) -> SuryaRuntimeSelection:
    return SuryaRuntimeSelection(
        definition=definition_for_binary(binary),
        binary=binary,
        source="configured",
    )
