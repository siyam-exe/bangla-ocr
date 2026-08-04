# Accuracy benchmark

This directory contains a reproducible 20-page benchmark made from real scans
selected across **Volume 002-1**. It is not synthetic and contains no generated
book text. The selection deliberately includes a publisher page, a chapter
opening with an illustration, dense dialogue and prose, degraded/noisy pages,
page continuations, and a sparse final page.

The sequential page-to-source-page mapping and source PDF hash are recorded in
[`fixture/source-pages.json`](fixture/source-pages.json). Publication and
rights details are in [`fixture/NOTICE.md`](fixture/NOTICE.md).

Process the committed image-only PDF with the application or CLI, then score
the resulting workspace:

```powershell
.\bangla-ocr.ps1 process benchmarks\fixture\bangla-preservation-benchmark.pdf `
  --title "Volume 002-1 real-scan benchmark" `
  --author "Rokib Hasan" `
  --engines "surya,embedded" `
  --output-root benchmark-output

.\.venv\Scripts\python.exe benchmarks\score_workspace.py `
  benchmark-output\volume-002-1-real-scan-benchmark-* `
  --output benchmarks\results\local-surya.json
```

The scorer reports character error rate (CER), word error rate (WER), exact
page matches, paragraph counts, runtime, and multiscale crop activity. Absolute
local paths are omitted unless `--include-workspace-path` is requested.

Reference transcriptions were prepared from the real Surya drafts and visually
checked line by line against the scans. They preserve printed spelling,
punctuation, paragraph boundaries, and page fragments. They are reviewable
reference files, not independent double-key human transcriptions; corrections
and disagreements should be reported with the source-page evidence.

Published results and limitations are in [RESULTS.md](RESULTS.md). OCR output
remains a draft even when benchmark scores are high.
