# Accuracy benchmarks

This directory contains a reproducible, redistributable benchmark for Bangla
OCR. The fixture text is original project material released under CC0-1.0. It
does not contain scans or text from Tin Goyenda or any other copyrighted book.

Generate the image-only PDF and reference renders:

```powershell
.\.venv\Scripts\python.exe benchmarks\generate_public_fixture.py
```

Process it with the application or CLI, then score the workspace:

```powershell
.\bangla-ocr.ps1 process benchmarks\fixture\bangla-preservation-benchmark.pdf `
  --title "Bangla preservation benchmark" --author "Bangla OCR contributors" `
  --engines "surya,embedded" --output-root benchmark-output

.\.venv\Scripts\python.exe benchmarks\score_workspace.py `
  benchmark-output\bangla-preservation-benchmark-* `
  --output benchmarks\results\local-surya.json
```

The scorer reports character error rate (CER), word error rate (WER), exact
page matches, paragraph counts, runtime, and multiscale crop activity. It
scores only the fixture pages present in the workspace, so a one-page
compatibility run is valid. Absolute local paths are omitted unless
`--include-workspace-path` is requested.

Published release results and limitations are in [RESULTS.md](RESULTS.md).
OCR output remains a draft even when benchmark scores are high.

The private 20-page Volume 002-1 validation is described in the published
results only as aggregate data. Its source images and transcription are never
placed in this repository.
