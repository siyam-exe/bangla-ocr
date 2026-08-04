# Contributing

Contributions are welcome when they preserve the project's central rule: the
scan is the authority and software must not silently rewrite it.

## Before opening a change

1. Use a clean branch.
2. Do not add copyrighted books, private OCR workspaces, API keys, model
   weights, or runtime binaries.
3. Use the public CC0 fixture for screenshots and accuracy tests.
4. Add tests for behavior changes, especially state transitions, export gates,
   coordinate transforms, and recovery paths.
5. Explain any fidelity trade-off in the pull request.

## Development setup

```powershell
.\setup.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m build
```

Install the full OCR stack only when testing Surya:

```powershell
.\setup.ps1 -WithSurya -Runtime Auto
```

## Benchmark

```powershell
.\bangla-ocr.ps1 process benchmarks\fixture\bangla-preservation-benchmark.pdf `
  --title "Volume 002-1 real-scan benchmark" `
  --author "Rokib Hasan" `
  --engines "surya,embedded" `
  --output-root benchmark-output
.\.venv\Scripts\python.exe benchmarks\score_workspace.py `
  benchmark-output\volume-002-1-real-scan-benchmark-*
```

Do not update published numbers without retaining the raw JSON result and
recording hardware, package, model, and runtime versions.

## Design invariants

- no automatic dictionary or language-model correction;
- no silent OCR-engine fallback;
- no verified state without explicit human review;
- source render and decision evidence remain recoverable;
- failed/restarted jobs preserve completed pages;
- previews are visibly unverified;
- network document egress requires a user action;
- tests and the dependency audit must pass before release.

## Style

Prefer small, typed Python functions, explicit JSON state, bounded filesystem
operations, and plain server-rendered HTML. Avoid adding a frontend framework or
database unless a demonstrated requirement justifies the operational cost.
