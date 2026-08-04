# Changelog

## Unreleased

## 1.2.0 — 2026-08-03

- Added a bounded, configurable high-resolution Surya crop pass for small,
  low-confidence, visibly weak, or mechanically suspicious text regions.
- Preserved the full-page Surya draft and made every differing crop reading an
  explicit human choice with retained crop/model evidence and an audit trail.
- Blocked verified export while high-resolution alternatives remain undecided.
- Added a review-screen button that copies the complete current page text,
  including unsaved edits.
- Fixed crop coordinate mapping across deskew and conservative-crop transforms.
- Bounded Surya crop decoding to prevent small regions from triggering a
  full-page token loop.
- Added a reproducible 20-page real-book benchmark with visually checked
  references, source-page provenance, CER/WER, and recorded test hardware.
- Added pinned, checksummed CPU/CUDA llama.cpp installation and explicit
  runtime selection.
- Added local-network exposure safeguards, failed-upload cleanup, security and
  privacy documentation, third-party notices, release hygiene checks, weekly
  dependency auditing, and Dependabot.
- Upgraded to security-fixed Pillow 12.3 with a narrowly checked Surya 0.22.1
  compatibility override.

## 1.1.0 — 2026-08-02

- Added categorized OCR failure explanations, preserved-progress information,
  technical diagnostics, and explicit recovery using any available OCR engine.
- Added verified and unverified Markdown downloads alongside plain text, with
  the same human-verification and document-audit gates.
- Made repeated Markdown/text downloads safe on Windows by serving closed file
  snapshots.
- Added idempotent install and update workflows, compatibility checks,
  automated self-tests, OCR health checks, optional safe Git pulling, and
  double-click launchers.

## 1.0.0 — 2026-08-01

- Rebranded the application and Python package as Bangla OCR.
- Rebuilt the import, job, document, review, and settings interfaces.
- Kept Surya OCR, resumable jobs, local document checks, human verification,
  preview export, and verified export as the main workflow.
- Removed the unreliable whole-document AI reviewer.
- Retained optional page/crop OpenRouter proposals with explicit human
  accept/reject decisions and audit history.
- Normalized missing author metadata to `Unknown author`.
- Made runtime/output paths portable and excluded local models, tools, output,
  secrets, and generated caches from Git.
- Archived obsolete experiments and reviewer data without removing source
  scans, OCR text, revisions, audits, or exports.
