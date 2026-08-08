# Bangla OCR

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776ab)
![Platform](https://img.shields.io/badge/platform-Windows-0078d4)
![License](https://img.shields.io/badge/code-Apache--2.0-d22128)

Bangla OCR is a Bengali OCR application for converting scanned Bangla PDFs
and book pages into editable text. It uses Surya OCR, keeps the original scan
beside the result, and gives you a page-by-page review screen before export.

![Bangla OCR reviewing a real scanned book page](docs/images/reviewer-benchmark.png)

## Why I made this

I grew up reading *Tin Goyenda*, and Rokib Hasan's books were a big part of my
childhood. Many of the PDFs available today are rough scans, photographed
pages, or copies that are difficult to read. I wanted a practical way to
preserve the text without changing the original writing, dialogue, spelling,
or paragraph structure.

While working on *Tin Goyenda*, I realised the same tool could help preserve
many other Bengali books and documents. That is why I decided to make Bangla
OCR public instead of keeping it tied to one series.

## What it does

- Converts scanned Bengali PDFs into editable text.
- Uses Surya as the main OCR engine.
- Shows the scan and OCR result side by side.
- Rereads small or suspicious regions at higher resolution.
- Keeps crop disagreements for the user to decide.
- Saves progress so interrupted books can be resumed.
- Exports reviewed books as Markdown or plain text.
- Offers EasyOCR as a manual fallback when Surya cannot continue.
- Keeps document processing on your computer by default.

An optional OpenRouter tool can suggest a correction for one review page. It
never changes the document automatically.

## Accuracy on a real scanned book

The included benchmark uses 20 real pages from a scanned Bengali book. Human
corrections and crop suggestions were not counted as automatic OCR accuracy.

| Result | Surya OCR |
|---|---:|
| Character accuracy | **98.548%** |
| Character error rate | **1.452%** |
| Word error rate | **5.732%** |
| Average time on RTX 4050 | **23.2 seconds per page** |

These numbers describe one book and one computer, not every Bengali scan. See
[the benchmark results](benchmarks/RESULTS.md) for the selected pages,
hardware, method, and limitations.

## Install on Windows

You need:

- Windows 10 or 11
- Python 3.12 with the `py` launcher
- An NVIDIA GPU for the best speed, or CPU mode for slower processing
- Around 4 to 6 GiB of free space for the CUDA environment and model files

The easiest method is to download the repository, then double-click:

1. `Install Bangla OCR.cmd`
2. `Start Bangla OCR.cmd`

You can also install it from PowerShell:

```powershell
.\setup.ps1 -WithSurya -Runtime Auto
.\bangla-ocr.ps1 doctor
.\bangla-ocr.ps1 app
```

For CPU-only installation:

```powershell
.\setup.ps1 -WithSurya -Runtime Cpu
```

The application opens at <http://127.0.0.1:8765>. Read
[INSTALL.md](INSTALL.md) for updates, storage settings, and troubleshooting.

## How a book is processed

```text
Import PDF
  -> render and inspect each page
  -> apply conservative page-specific preprocessing
  -> run full-page Surya OCR
  -> reread selected regions at higher resolution
  -> review the scan and text side by side
  -> check the complete document
  -> export Markdown or plain text
```

The software does not silently apply dictionary corrections, AI rewrites, or
crop alternatives. The user makes the final decision when the readings differ.

## Things to know

- Bangla OCR is a public beta. Review the text against the scan before treating
  it as complete.
- The local web interface has no login system. Do not expose it directly to the
  public internet.
- Surya model weights use their own license. See
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- Only process documents you have permission to copy or transcribe.

## Project documents

- [Installation and troubleshooting](INSTALL.md)
- [Pipeline design](PIPELINE.md)
- [Transcription rules](transcription-rules.md)
- [Security notes](SECURITY.md)
- [Benchmark method and results](benchmarks/RESULTS.md)
- [Contributing](CONTRIBUTING.md)

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m build
```

## License

The Bangla OCR source code is licensed under Apache-2.0. Libraries, binaries,
and model weights keep their original licenses.
