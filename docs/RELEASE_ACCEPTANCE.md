# Release acceptance

Bangla OCR is ready for release only when the exact files intended for GitHub
pass every applicable check below. A source test or local development run does
not replace an installed-package test.

## Supported claims

- Windows 10 and Windows 11 on x64 computers are supported.
- CPU processing is the compatibility path.
- NVIDIA CUDA processing is supported on physically tested hardware.
- AMD and Intel Vulkan processing remains experimental until it completes a
  real-page test on suitable hardware.
- The installer does not require a separate Python installation.
- The original scan remains the authority. OCR output still requires human
  review.

## Source gate

- The complete pytest suite passes with no unexpected failures or passes.
- Known defects may use strict expected-failure tests only while a repair is in
  progress. Release artifacts must not be published with those tests remaining.
- The dependency audit reports no known vulnerabilities.
- The repository scan finds no credentials, private document data, generated
  runtime state, development-machine paths, or unlicensed bundled binaries.
- README, installation instructions, third-party notices, and release notes
  match the actual package behaviour.

## Interactive installer gate

- A fresh installation shows the destination-location page.
- An upgrade also shows the current destination and permits changing it.
- The runtime page explains the detected hardware, recommended acceleration,
  CPU fallback, download size, and experimental status where applicable.
- The confirmation page shows the selected destination and runtimes.
- A custom local D-drive destination is honoured.
- Silent installation still honours `/DIR` and `/RUNTIME`.
- Installer tests remove their temporary application directories, shortcuts,
  and uninstall-registry entries.
- Upgrade preserves documents and downloaded models.
- Uninstall removes application files and registration while preserving user
  documents and models as documented.

## Runtime-storage gate

- Temporary files, Surya state, locks, sentinels, and server logs stay under
  the configured application workspace.
- Surya does not require or traverse `~\.cache\datalab\surya`.
- A stale or untrusted junction at that old path cannot prevent OCR.
- The application reports the exact failing runtime path and Windows error when
  a workspace cannot be opened.
- A user-selected installation directory is checked for local write access
  before OCR begins.

## Real OCR gate

- The installed release processes at least three real scanned Bengali pages
  with automatic NVIDIA CUDA selection on the available test computer.
- One real page completes through explicit CPU processing.
- Process inspection confirms that the selected bundled llama.cpp runtime and
  device are used.
- No automatic switch to EasyOCR occurs.
- The same tests use the packaged private Python runtime, not the development
  environment.

## Recovery and output gate

- Terminating a multi-page job after a completed page leaves that page intact.
- Restarting the installed application identifies the interruption and resumes
  from unfinished pages.
- Preserved page hashes and timestamps remain unchanged after resume.
- Job progress, completed-page count, and recovery wording agree for the pages
  selected by the current job.
- Reviewed Markdown and plain-text exports complete successfully.

## Launcher and package gate

- The launcher shows startup progress and opens the correct installation.
- It does not attach silently to a different Bangla OCR installation using the
  same port.
- Port conflicts, unavailable models, interrupted downloads, low storage, and
  missing runtimes produce actionable errors.
- Installer and portable packages contain the expected application version,
  runtime manifests, licenses, and icon assets.
- SHA-256 values match the generated checksum manifest.

## Public-download gate

- The final installer is downloaded from its GitHub Release after publication.
- The downloaded checksum matches both the manifest and GitHub asset digest.
- The downloaded installer is installed into a new custom directory.
- At least one real Surya OCR page completes from that public installation.
- Hardware selection, runtime path, review data, and export are inspected from
  the public installation.
- The verification installation and temporary files are removed afterward.

The release is incomplete if any required gate is skipped, unavailable without
an explicit compatibility limitation, or tested against files other than the
published artifacts.
