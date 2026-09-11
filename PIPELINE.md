# Processing architecture

Bangla OCR is a one-document-at-a-time Bengali OCR pipeline. The scan
is always the authority. OCR, heuristics, and optional AI can create proposals;
only a human can verify a page.

## Runtime flow

1. **Import:** a PDF is copied into the content-addressed source store. Invalid
   uploads and imports rejected before job creation are removed when the app
   created the copy.
2. **Render:** PDFium renders the selected page at 220 DPI. Embedded text is
   retained only as independent evidence.
3. **Inspect:** contrast, sharpness, foreground ratio, border ink, content
   bounds, and line skew are measured.
4. **Preprocess conservatively:** the page may remain unchanged, receive a
   bounded deskew, or receive a local contrast adjustment. Automatic blank
   border cropping is disabled by default. Every operation and metric is saved.
5. **Full-page OCR:** the explicitly selected engine runs. Surya is preferred.
   No model fallback occurs unless the user chooses it after a failure.
6. **Automated checks:** the pipeline evaluates coverage, Bengali ratio,
   punctuation balance, structural markers, likely uploader pages, and other
   risks. These checks flag; they do not rewrite.
7. **Human review:** the reviewer sees source and processed scans, editable
   text, signals, revision history, and optional page-level
   AI suggestions.
8. **Whole-document validation:** page order, inclusion, unresolved states,
   headings, quote/paragraph anomalies, blank output, duplicate content, and
   export readiness are checked together.
9. **Export:** preview files are marked unverified. Verified Markdown and
    plain text remain locked until all included pages are human verified and the
    latest structural audit passes.

## OCR failure recovery

Surya receives one controlled retry after a detected backend failure. Runtime
selection and OCR-engine selection are separate.

Before a Surya job starts, automatic runtime policy uses backend-owned device
checks in this order:

- NVIDIA: CUDA, Vulkan, then CPU;
- AMD or Intel: Vulkan, then CPU;
- failed Windows inventory: try CUDA, Vulkan, then CPU based on their own
  device reports.

Automatic mode verifies its choice by running the first real selected page. If
that runtime fails after its bounded retry and no page has completed, the job
records the failure and tries the next compatible installed Surya runtime. It
never changes from Surya to EasyOCR automatically.

An explicitly requested runtime never falls through to another one. Once any
page has completed, the runtime is locked for that run. A later failure stops
the job and preserves all completed pages. The interface offers explicit
actions:

- retry Surya;
- resume Surya from the failed page with another usable runtime;
- resume from the failed page with EasyOCR;
- stop and preserve the workspace.

The processing audit records the requested engine, every attempt, recovery
diagnostics, the Surya runtime decision, completed pages, timings, and whether
fallback was enabled. `audit/runtime-recovery.jsonl` records each runtime
attempt and automatic transition. Switching CPU, CUDA, or Vulkan resets a
previous managed llama.cpp server before starting the new runtime.

The Settings screen stores the preferred Surya runtime in the application data
folder. Automatic, CUDA, Vulkan, and CPU are available when their device checks
pass. Refresh reruns bounded adapter and runtime probes. The separate model
startup check launches the selected llama.cpp server and loads the real Surya
model. It releases the test server afterward. A device check is never
presented as proof that the model can start.

Changing the saved preference does not alter an active job. Startup validation
and OCR are mutually exclusive so two model-loading operations cannot compete
for the same memory or managed server.

The universal Windows package carries the pinned CPU, CUDA, and Vulkan
llama.cpp builds beside one shared Python and application layer. NVIDIA systems
prefer CUDA. AMD and Intel systems prefer Vulkan. CPU remains the final
pre-job fallback. The application may replace an automatically selected
runtime only while zero pages are complete. After that boundary, recovery
requires an explicit user action.

## Storage layout

```text
../sources/imports/<name>-<sha12>.pdf
../output/<book-id>/
  book.json
  audit/
    processing.json
    ai-corrections.jsonl
  pages/0001/
    source.webp
    selected.webp
    draft.txt
    final.txt                    # after manual edits
    page.json
    evidence/
      embedded-pdf.json
      surya-<variant>.json
      openrouter-proposals.jsonl # optional
  structural-validation.json
  book.preview.md
  book.preview.txt
  book.md                        # verified only
  book.txt                       # verified only
```

## Page state

Each page tracks separate states:

- `ocr`: pending, processing, complete, or failed;
- `automated`: checks passed or needs review;
- `human`: unreviewed, in review, unresolved, or human verified;
- `ai`: not requested, proposed, failed, accepted, or rejected;
- `overall`: the derived state used by queues and export gates.

Automated success never means human verification.

## Optional OpenRouter suggestion

The optional feature is deliberately narrow. After the user clicks on a page
or selected line, the app sends that text plus an image crop to the configured
vision model. The response must identify a literal source substring and a
visible replacement. It is saved as a proposal and requires manual acceptance.

It cannot scan the whole book, apply changes by itself, or mark a page
verified. API keys live in process memory for the browser session (or in the
`OPENROUTER_API_KEY` environment variable); they are not written into the
document workspace.

## Performance model

- One inference request runs at a time to avoid VRAM contention.
- The next PDF page may be prefetched while the current page is recognized.
- Full-page Surya uses high-accuracy recognition.
- Runtime/model caches and temporary files are redirected to project storage.
- Processing is resumable from completed pages.

On the release-validation laptop, the public 20-page real-scan benchmark
averaged 23.2 seconds per processed page. This is hardware- and scan-dependent.

## Fidelity rules

The complete transcription contract is in [transcription-rules.md](transcription-rules.md)
and its machine-readable companion `transcription-policy.json`. In short:

- preserve spelling, punctuation, quotation style, paragraphs, and headings;
- do not modernize language or silently repair the author;
- exclude covers, publisher lists, advertisements, and uploader material when
  the reviewer classifies them as non-content;
- use `[অস্পষ্ট]` only when the scan cannot support a reading;
- preserve uncertainty and human decisions in audit records.

## Commands

```powershell
.\bangla-ocr.ps1 app
.\bangla-ocr.ps1 doctor
.\bangla-ocr.ps1 process .\book.pdf --title "Book title" --author "Author"
.\bangla-ocr.ps1 review ..\output\book-workspace
.\bangla-ocr.ps1 finalize ..\output\book-workspace
.\.venv\Scripts\python.exe -m pytest -q
```
