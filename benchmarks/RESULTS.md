# Release benchmark results

Test date: 2026-08-03
Bangla OCR: 1.2.0 release candidate
Surya: 0.22.1
llama.cpp: b10107 (`c0bc8591e`)

## Public accuracy fixture

The committed fixture contains five image-only PDF pages and 2,595 normalized
reference characters. Its Bengali text is original CC0 project material. Page
conditions are deterministic:

| PDF page | Condition | CER | WER | Crop activity |
|---:|---|---:|---:|---|
| 1 | clean | 0.621% | 3.615% | none |
| 2 | low contrast + blur | 0.383% | 2.273% | 3 rereads; 1 pending disagreement |
| 3 | 1.25° skew + speckle | 0.536% | 3.125% | none |
| 4 | JPEG compression + uneven light | 0.377% | 2.326% | none |
| 5 | resize + blur | 0.000% | 0.000% | none |
| **Total** | **five mixed pages** | **0.385%** | **2.273%** | **3 rereads; 1 pending** |

Aggregate automatic character accuracy was **99.615%**. One page was an exact
match. The full run took 68.427 seconds, including 14.946 seconds attributed to
the crop pass and its first-use overhead.

The remaining automatic errors were mainly straight-versus-curly quotation
marks, plus two Bengali word readings. On page 2 the high-resolution crop
produced the exact reference quote marks, but that value is not counted as an
automatic correction: the application correctly left it pending for a human.

Raw machine-readable output:
[`results/windows-rtx4050-surya-0.22.1.json`](results/windows-rtx4050-surya-0.22.1.json)

## Real-book crop validation (private source)

To validate geometry and behavior on an actual difficult book, 20 PDF pages
distributed across Volume 002-1 were processed privately. No source scan or
book text is committed.

| Check | Result |
|---|---:|
| Selected pages completed | 20 / 20 |
| Primary engine | Surya on all 20 |
| Silent fallbacks | 0 |
| Failed OCR attempts / page errors | 0 / 0 |
| Preprocessing | 13 deskew, 3 conservative crop, 4 unchanged |
| High-resolution regions | 52 |
| Crop readings agreeing | 20 |
| Crop readings held for review | 32 |
| Empty/unreadable/failed crop reads | 0 / 0 / 0 |
| Bounds and saved-dimension checks | 52 / 52 passed |
| Mean full OCR time | 22.858 s/page |
| Mean crop-pass time | 2.379 s/page |
| Mean total throughput | 23.200 s/page |
| Wall time | 464.0 s (7m 44s) |
| Workspace size | 11.2 MiB |

Six representative crop images were visually inspected across unchanged,
deskewed, and conservatively cropped pages. All landed on the intended printed
region. Some alternatives were better, some worse, and some merely exposed an
ambiguity—evidence that human choice is required.

This private pilot is an operational crop-system validation, **not an accuracy
benchmark**. There is no complete human ground truth for those pages, so no CER
claim is made from them.

## Hardware and compatibility

| Component | Value |
|---|---|
| OS | Windows 11 Pro 10.0.26200, build 26200 |
| CPU | Intel Core i5-13420H, 8 cores / 12 threads |
| RAM | 7.7 GiB usable |
| GPU | NVIDIA RTX 4050 Laptop GPU, 6,141 MiB |
| Driver | 560.94 |

The CUDA pilot used roughly 1.75 GiB VRAM during observation and reached about
85% GPU utilization. On public fixture page 5:

| Mode | Time | Text result |
|---|---:|---|
| RTX 4050 CUDA | 5.452 s | exact match |
| CPU only (`-ngl 0`) | 41.673 s | exact match |

The CPU run was started after terminating the prior GPU inference server; its
runtime used zero GPU layers. The 7.6× difference applies only to this page and
system.

## Methodology and limitations

- CER and WER use NFC-normalized text and Levenshtein edit distance.
- Paragraph counts are checked separately; all five automatic outputs retained
  the four reference blocks.
- The scorer uses `final.txt` when present, otherwise `draft.txt`.
- No dictionary, AI correction, or crop alternative is automatically applied.
- The fixture is small and synthetic. It is reproducible, but it does not cover
  every Bengali font, historical spelling, layout, photograph, handwriting, or
  damaged page.
- Runtime includes model startup/cache effects present in the recorded run and
  should not be treated as a hardware benchmark standard.

## Reproduce

```powershell
.\.venv\Scripts\python.exe benchmarks\generate_public_fixture.py
.\bangla-ocr.ps1 process benchmarks\fixture\bangla-preservation-benchmark.pdf `
  --title "Bangla preservation benchmark" `
  --author "Bangla OCR contributors" `
  --engines "surya,embedded" `
  --output-root benchmark-output
.\.venv\Scripts\python.exe benchmarks\score_workspace.py `
  benchmark-output\bangla-preservation-benchmark-* `
  --output benchmarks\results\local.json
```
