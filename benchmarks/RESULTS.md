# Release benchmark results

Test date: 2026-08-04

Bangla OCR: 1.2.0 release candidate

Surya: 0.22.1

llama.cpp: b10107 (`c0bc8591e`)

## Real-book accuracy benchmark

The committed benchmark contains 20 image-only pages selected across Volume
002-1. Every image and every reference line comes from the supplied book scan;
no source prose was generated. The selection spans original PDF pages 3, 6,
17, 28, 50, 52, 70, 87, 101, 115, 130, 147, 149, 159, 168, 186, 207, 216,
233, and 241. The exact mapping and hashes are committed beside the fixture.

| Benchmark page | Original PDF page | Page type | CER | WER | Crop review |
|---:|---:|---|---:|---:|---:|
| 1 | 3 | publisher metadata / mixed Latin and Bengali | 4.552% | 5.319% | 3 / 0 pending |
| 2 | 6 | chapter opening, illustration, prose | 1.176% | 4.580% | 3 / 2 pending |
| 3 | 17 | dense dialogue | 1.810% | 6.000% | 3 / 2 pending |
| 4 | 28 | dense dialogue and prose | 0.308% | 1.577% | 3 / 0 pending |
| 5 | 50 | visibly degraded dense prose | 4.464% | 16.868% | 3 / 3 pending |
| 6 | 52 | noisy dense prose | 2.141% | 7.692% | 3 / 3 pending |
| 7 | 70 | dialogue and punctuation | 1.032% | 3.333% | 3 / 2 pending |
| 8 | 87 | dialogue-heavy prose | 0.826% | 4.015% | 3 / 1 pending |
| 9 | 101 | dense prose | 1.107% | 6.434% | 3 / 3 pending |
| 10 | 115 | dialogue and names | 0.813% | 4.546% | 3 / 1 pending |
| 11 | 130 | dialogue and damaged glyphs | 1.934% | 7.143% | 3 / 3 pending |
| 12 | 147 | dialogue and punctuation | 1.267% | 4.487% | 3 / 2 pending |
| 13 | 149 | dense dialogue | 2.378% | 6.135% | none selected |
| 14 | 159 | prose / proper names | 0.482% | 2.848% | 2 / 1 pending |
| 15 | 168 | continuation fragment / lower sharpness | 0.824% | 5.381% | 3 / 2 pending |
| 16 | 186 | dense prose / page continuation | 1.775% | 7.808% | 2 / 2 pending |
| 17 | 207 | dialogue and map terminology | 0.489% | 2.306% | 3 / 2 pending |
| 18 | 216 | dense prose | 0.804% | 4.237% | none selected |
| 19 | 233 | dense prose / repeated similar words | 0.798% | 3.261% | 3 / 3 pending |
| 20 | 241 | sparse final page and end marker | 2.207% | 14.451% | 3 / 0 pending |
| **Total** | **20 real pages** | **mixed book pages** | **1.452%** | **5.732%** | **52 / 32 pending** |

Aggregate automatic character accuracy was **98.548%** across **36,237
reference characters**. No page was an exact match before human review. This
is the honest automatic Surya result: crop alternatives were not silently
accepted, dictionary corrections were not applied, and no generative model
rewrote the text.

The full recorded run took 464.0 seconds (7 minutes 44 seconds), averaging 23.2
seconds per page. The high-resolution crop pass accounted for 47.588 seconds.
All 20 pages used Surya; there were no silent fallbacks or failed page OCR
attempts.

Raw machine-readable output:
[`results/windows-rtx4050-surya-0.22.1.json`](results/windows-rtx4050-surya-0.22.1.json)

## Hardware

| Component | Value |
|---|---|
| OS | Windows 11 Pro 10.0.26200, build 26200 |
| CPU | Intel Core i5-13420H, 8 cores / 12 threads |
| RAM | 7.7 GiB usable |
| GPU | NVIDIA RTX 4050 Laptop GPU, 6,141 MiB |
| Driver | 560.94 |

The CUDA run used roughly 1.75 GiB VRAM during observation and reached about
85% GPU utilization. Runtime includes model startup/cache effects present in
the recorded run and is not a universal hardware score.

## Methodology and limitations

- CER and WER use NFC-normalized text and Levenshtein edit distance.
- The scorer uses `final.txt` when present, otherwise `draft.txt`.
- Reference text was visually checked against the scans line by line, but was
  not independently transcribed twice by separate human operators.
- Printed spelling and unusual wording were retained; the reference was not
  modernized or stylistically rewritten.
- Page numbers and recurring book footers are excluded from reference prose;
  the semantic final-page end marker is retained.
- No dictionary, AI correction, or crop alternative is automatically applied.
- The 20 pages are varied but cannot represent every Bengali font, historical
  spelling, layout, photograph, handwriting, or type of scan damage.

## Reproduce

```powershell
.\bangla-ocr.ps1 process benchmarks\fixture\bangla-preservation-benchmark.pdf `
  --title "Volume 002-1 real-scan benchmark" `
  --author "Rokib Hasan" `
  --engines "surya,embedded" `
  --output-root benchmark-output
.\.venv\Scripts\python.exe benchmarks\score_workspace.py `
  benchmark-output\volume-002-1-real-scan-benchmark-* `
  --output benchmarks\results\local.json
```
