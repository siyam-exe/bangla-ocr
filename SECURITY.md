# Security and privacy

## Release posture

Bangla OCR is a local, single-user desktop web application. The default bind is
`127.0.0.1`. It has no accounts, authentication, TLS termination, tenant
isolation, or production CSRF design.

**Do not expose it directly to the public internet.** Binding to a non-loopback
address is refused unless `--allow-network` is supplied explicitly. Even with
that flag, use only a trusted private network and understand that anyone who can
reach the port may be able to read or change document data.

## Data flow

By default, these remain local:

- imported PDFs and page renders;
- OCR text and engine evidence;
- revision and review history;
- exports, logs, and structural reports;
- model weights and runtime binaries.

There is no application telemetry. The installer contacts Python package
indexes, Hugging Face, and the official llama.cpp GitHub release to download
dependencies. Surya model downloads may contact Hugging Face.

The optional OpenRouter page suggestion is the one intentional document-data
egress. It sends the selected page/crop and OCR text only after a user action.
The provider's privacy terms then apply. The API key is retained only in process
memory for that browser session, or read from `OPENROUTER_API_KEY`; it is not
written to a project config or OCR workspace.

## Untrusted PDFs

PDF and image parsing are attack surfaces. Process material from sources you
trust, keep Windows and GPU drivers updated, and run the application as a
normal user rather than an administrator. Uploads are capped at 1 GiB. Failed
PDF imports are removed when the application created the source-store copy.

Workspace path resolution rejects traversal outside configured output roots.
Uploaded names are reduced to a basename and sanitized before storage.

## Dependency audit

The release candidate was checked with `pip-audit 2.10.1`; the final environment
reported no known vulnerabilities on 2026-08-03. Free GitHub Actions reruns the
audit weekly and on demand. A clean result is a point-in-time signal, not a
guarantee.

### Surya and Pillow compatibility

Surya 0.22.1 declares `Pillow >=10.2,<11`. Pillow 10.4 now has known 2026
security advisories, while Surya has no newer published release. Bangla OCR uses
Pillow 12.3 and accepts only this exact metadata conflict.

The override passed:

- the complete 73-test project suite;
- a real Surya full-page recognition run;
- three high-resolution crop rereads;
- the public benchmark page with identical OCR output to the pre-upgrade run.

This is downstream compatibility testing, not an upstream guarantee. The
installer rejects every other dependency conflict. Reevaluate or remove the
override when Surya updates its declared Pillow support.

## Secrets

- Never commit `.env`, API keys, credential JSON, or local config.
- Use a restricted provider key and rotate any key pasted into a chat, issue,
  screenshot, terminal log, or commit.
- Repository secret scanning intentionally looks for OpenRouter-style keys.
- OCR workspaces may contain copyrighted or personal text; do not attach them
  to public issues.

## Reporting a vulnerability

Do not publish exploit details or secrets in a normal issue. Use GitHub's
private vulnerability-reporting feature when it is enabled for the repository.
Include the affected version, a minimal reproduction using non-sensitive test
data, impact, and suggested mitigation.

## Supported version

Security fixes are made on the latest release line. Older local snapshots may
not receive backports.
