# Changelog

## 1.1.0 - 2026-09-11

First public release.

### OCR and review

- Surya OCR for scanned Bengali PDFs and book pages.
- Side-by-side review of each scan and its transcription.
- Human verification before a verified export is allowed.
- Markdown and plain-text exports.
- Resumable jobs that preserve completed pages after interruption.
- EasyOCR available only as a manual fallback.
- Optional OpenRouter suggestions for one page at a time. Suggestions never
  change the transcription automatically.

### Windows application

- One universal Windows installer with CPU, NVIDIA CUDA, and experimental
  Vulkan runtimes.
- Automatic hardware detection and a visible runtime recommendation during
  setup.
- Exact device selection and runtime diagnostics in Settings.
- First-page runtime verification before a long OCR job continues.
- Safe fallback to another Surya runtime only before any page has completed.
- Native launcher with startup progress and a button to open the local
  workspace.
- Portable Windows package for users who do not want to install the program.
- Setup lets users choose the installation folder on fresh installs and
  upgrades.
- The Bangla OCR icon is used by Setup, shortcuts, the launcher, and the
  uninstaller.
- Surya server logs and temporary state stay inside the selected application
  workspace.
- Runtime path failures provide clear recovery choices without silently
  changing engines.
- Interrupted jobs restore accurate progress from completed page records.

### Release notes

- CUDA and CPU were tested on an NVIDIA RTX 4050 laptop with 8 GB RAM.
- Vulkan support for AMD and Intel GPUs is experimental until it is tested on
  suitable physical hardware.
- The installer is not code signed, so Windows may display a SmartScreen
  warning.
- CPU OCR works without a supported GPU but can be much slower.
