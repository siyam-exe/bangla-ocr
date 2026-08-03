# Install, update, and remove Bangla OCR

## Supported setup

- Windows 10 or 11, x64
- Python 3.12 with the Windows `py` launcher
- PowerShell 5.1 or newer
- NVIDIA GPU recommended; CPU inference is supported
- 4–6 GiB free for a CUDA installation and first model download, plus output
  space (the exact footprint changes with package and model releases)

The release-validation machine used Windows 11, an Intel i5-13420H, 7.7 GiB
RAM, and an RTX 4050 Laptop GPU with 6 GiB VRAM.

## Clean install

Clone or download the repository to a drive with sufficient space, then run:

```powershell
.\setup.ps1 -WithSurya -Runtime Auto
```

`Auto` chooses the pinned CUDA 12.4 llama.cpp runtime when `nvidia-smi` is
available; otherwise it installs the CPU x64 runtime. Explicit choices are:

```powershell
.\setup.ps1 -WithSurya -Runtime Cuda
.\setup.ps1 -WithSurya -Runtime Cpu
.\setup.ps1 -WithSurya -Runtime None   # provide your own llama-server
```

The runtime installer downloads llama.cpp b10107 from `ggml-org/llama.cpp` and
verifies the published SHA-256 digest before replacing the ignored local
runtime folder. `Install Bangla OCR.cmd` performs the `Auto` installation.

The installer is idempotent. It repairs missing dependencies without deleting
source PDFs, OCR workspaces, models, review decisions, or exports. It runs the
dependency check and tests before reporting success.

A cold install downloads a large machine-learning stack and can take ten
minutes or longer depending on the connection and disk. In the clean CPU test,
the environment, pip cache, and runtime occupied about 1.49 GiB before any
Surya model weights were downloaded. The first OCR run downloads additional
model data; CUDA installations also use a much larger runtime.

## First health check

```powershell
.\bangla-ocr.ps1 doctor
```

Then start the interface:

```powershell
.\bangla-ocr.ps1 app
```

or double-click `Start Bangla OCR.cmd`.

If Surya is unavailable, the interface explains why and lets the user select an
installed engine. It never silently changes OCR engines.

## Data locations

All paths are relative to the repository unless overridden in local config:

```text
../sources/imports/       retained source PDFs
../output/                document workspaces and exports
../runtime/               temporary and inference runtime state
models/                   model caches (ignored by Git)
tools/                    llama.cpp binaries (ignored by Git)
.venv/                    Python environment (ignored by Git)
.cache/pip/               project-local pip cache (ignored by Git)
```

The launcher redirects temporary files and model caches away from the system
drive where supported. A small Surya compatibility junction may be created at
`%USERPROFILE%\.cache\datalab\surya`; its target is the project runtime folder.

## CPU and custom inference

CPU mode is functional but slow for book-scale work. To force CPU layers with a
compatible llama.cpp binary:

```powershell
$env:LLAMA_CPP_NGL = "0"
.\bangla-ocr.ps1 app
```

Advanced users may set `LLAMA_CPP_BINARY` or `SURYA_INFERENCE_URL`. Environment
values are respected by the launcher instead of being overwritten.

## Update

After replacing or pulling application source:

```powershell
.\update.ps1
```

For a clean Git checkout, `-Pull` performs a fast-forward-only pull and refuses
to overwrite local modifications:

```powershell
.\update.ps1 -Pull
```

The updater preserves data and detects whether Surya is installed. Use
`-SkipTests` only while troubleshooting.

## Pillow compatibility override

Surya 0.22.1 declares `Pillow <11`, while that Pillow line has known security
advisories. Bangla OCR installs Pillow 12.3 and accepts only this exact,
release-tested metadata mismatch. Every other `pip check` conflict remains a
hard installation failure. See [SECURITY.md](SECURITY.md).

## Remove the application

Back up `../output` and `../sources` first. The following folders are generated
and can be removed independently when Bangla OCR is not running:

```text
.venv/
.cache/
models/
tools/
../runtime/
```

Removing `models/` or `tools/` means they must be downloaded again. Removing
`../output/` deletes OCR workspaces, human review history, and exports; it is
not part of normal uninstall or update.
