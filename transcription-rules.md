# Bangla OCR Faithful Transcription Rules

Policy version: 1.0.0
Status: Authoritative
Applies to: All OCR, AI verification, human review, and text exports in this project

## 1. Purpose

The project creates faithful digital transcriptions of Bengali documents from scanned PDFs.

The goal is to reproduce the printed literary text, not to edit, modernize, summarize, translate, improve, or reinterpret it.

The original page image is always the final authority.

## 2. Canonical output

The canonical reader text is UTF-8 Markdown:

```text
book.md
```

A plain-text `book.txt` may be generated from the Markdown file. Markdown is canonical because plain text cannot reliably represent headings, emphasis, scene breaks, or other meaningful formatting.

The reader-facing book contains only:

1. Book or story title
2. Author name
3. Story titles when the source contains more than one story
4. Chapter or section headings printed in the source
5. The story text

## 3. Source-of-truth rule

1. The original, unaltered page image is the source of truth.
2. OCR output is only a draft.
3. AI output is only a visually grounded correction proposal.
4. Context may help interpret visible characters, but context must never override the page image.
5. A plausible word must not be inserted when its printed characters cannot be established from the image.
6. When the source is unreadable, the text must remain explicitly unresolved.

## 4. Content to include

Include:

- Printed story titles
- Printed author names
- Chapter numbers and chapter titles
- Section headings
- Story paragraphs
- Dialogue
- Letters, diary entries, notes, signs, messages, poems, lists, and other text that forms part of the story
- Scene-break symbols when they communicate a structural break
- Meaningful emphasis such as italic or bold text
- Footnotes only when they are part of the literary work

## 5. Content to exclude

Exclude:

- Front and back covers
- Publisher names and addresses
- Printer information
- Prices, ISBNs, edition notices, and copyright pages
- Tables of contents, unless later requested
- Advertisements
- Uploader or scanner names
- Download-site text and watermarks
- Running headers and running footers
- Printed page numbers
- Scan borders, shadows, fingers, camera artifacts, and blank pages
- Repeated pages caused by scanning or PDF assembly

Text that overlaps a watermark or advertisement must still be transcribed if it belongs to the story. The watermark or advertisement itself must not enter the transcription.

## 6. Exact-text preservation

The transcription must preserve:

- Original Bengali spelling
- Original grammar
- Original word choices
- Names and invented terms
- Original punctuation
- Bengali danda `।`
- Commas, semicolons, colons, question marks, exclamation marks, dashes, ellipses, and brackets as printed
- Opening and closing quotation marks as printed
- Repetition, fragments, unusual expressions, and apparent authorial mistakes
- Deliberate capitalization in Latin-script text
- Bengali and Latin digits as printed

The transcription must not:

- Correct the author's grammar
- Modernize spelling
- Replace an uncommon word with a common one
- Smooth an awkward sentence
- Rewrite dialogue
- Translate English words into Bengali or Bengali words into English
- Censor or soften text
- Add punctuation because it seems stylistically preferable
- Convert `...` to `…`, or `…` to `...`, unless the source clearly supports the change

## 7. Paragraphs and reading order

1. Preserve every paragraph boundary.
2. Join a paragraph that continues across a page boundary.
3. Do not create a new paragraph merely because a new page begins.
4. Do not merge paragraphs separated by indentation, vertical space, a scene break, or another clear structural signal.
5. Paragraph reconstruction must use page geometry, indentation, spacing, and reading order before linguistic guesswork.
6. Multi-column pages must be read in their visual reading order.
7. A dropped, duplicated, or reordered line is a blocking error.

Printed line wrapping inside an ordinary prose paragraph must not become a Markdown line break. The paragraph should reflow naturally.

Preserve intentional line breaks in:

- Poetry
- Songs
- Lists
- Addresses
- Letters or notes whose layout carries meaning
- Signs or messages presented as separate lines

## 8. Dialogue and quotations

1. Preserve the exact dialogue punctuation visible in the source.
2. Preserve quotation marks, dialogue dashes, paragraph breaks, and speaker changes.
3. Do not replace one quotation style with another.
4. A quotation continuing into another paragraph must follow the printed structure.
5. Do not infer a missing opening or closing quote unless its printed form is visually recoverable from another source copy.

## 9. Headings, emphasis, and scene breaks

Use Markdown to preserve meaningful structure:

```markdown
# Book or story title

**লেখক: [উৎসে মুদ্রিত নাম]**

## Chapter heading

Ordinary paragraph text.

*Italic text*

**Bold text**

***
```

Rules:

1. Do not invent chapter numbers or titles.
2. Decorative type alone does not require special markup unless it signals a heading or emphasis.
3. Preserve printed scene breaks as `***`.
4. Preserve italic and bold text only when the source clearly uses them meaningfully.
5. Do not reproduce page positioning, ornamental borders, font family, font size, or line width in the canonical text.

## 10. Page boundaries and hyphenation

Page boundaries are recorded internally but are not printed in the reader-facing Markdown.

Internal page identifiers must allow every paragraph and correction to be traced back to its source page.

When a word is split at the end of a printed line or page:

1. Join it only when the source clearly shows typographic line wrapping.
2. Preserve a hyphen when it is linguistically part of the printed word or expression.
3. Flag the case for review when the distinction is unclear.

## 11. Bengali Unicode handling

1. Store all text as UTF-8.
2. Store the reader-facing transcription in Unicode Normalization Form C (NFC).
3. Unicode normalization may change encoding representation only; it must not change visible or linguistic content.
4. Preserve Bengali conjuncts, vowel signs, hasanta, nukta, and punctuation.
5. Remove accidental zero-width or control characters only when they are encoding artifacts and not required for the intended text.
6. Keep the raw OCR output unchanged before normalization for audit purposes.

## 12. OCR requirements

1. Use at least two independent Bengali OCR results whenever practical.
2. Retain line coordinates and OCR confidence.
3. Retain the raw output from every OCR engine.
4. Never silently discard a line because an OCR engine returned no text.
5. Compare detected text-line count with the visible text-line count.
6. Detect duplicate pages, missing pages, cropped final lines, and incorrect reading order before accepting a book.

## 13. AI correction policy

AI may:

- Compare OCR candidates against the original image crop
- Correct a substituted, omitted, duplicated, or merged character when supported by visible pixels
- Restore punctuation when the punctuation is visible
- Identify that OCR read lines in the wrong order
- Confirm paragraph continuation using image geometry
- Mark a passage as uncertain

AI may not:

- Rewrite or paraphrase
- Correct grammar or spelling in the printed work
- Complete a sentence from context when pixels are missing
- Invent punctuation
- Add a missing line from memory or an unrelated edition
- Remove repetitions that appear in the source
- Change paragraph boundaries without layout evidence
- Hide uncertainty

AI verification must receive:

1. Original image crop
2. Restored image crop, when available
3. OCR candidates
4. One neighbouring line before and after, when available
5. Page and line identifiers

Every AI change must be recorded with:

- Original OCR text
- Verified text
- Changed span
- Page and line
- Reason
- Confidence
- Review status

## 14. Unclear and damaged text

Use this reader-text marker when printed text cannot be recovered:

```text
[অস্পষ্ট]
```

Rules:

1. Do not put competing guesses inside the reader-facing marker.
2. Store possible readings in the internal review record.
3. If part of a word is readable, preserve the readable portion internally, but do not present a misleading completed word.
4. If a line or page is cropped or missing, create a blocking review item.
5. A book containing unresolved markers may be exported as a draft but must not be marked verified.

## 15. Verification states

### Green - verified

- OCR engines agree or differences have been visually resolved
- Text is supported by the page image
- Reading order and paragraph structure are confirmed
- No unresolved text remains in the unit

### Yellow - corrected

- OCR required an image-supported correction
- The correction is recorded in the audit log
- The passage remains eligible for sampling or second review

### Red - unresolved

- Printed characters are unclear
- Source pixels are damaged or missing
- Lines may be missing, duplicated, cropped, or reordered
- Paragraph structure cannot be established confidently
- Human review or a better scan is required

Red items must never be silently promoted to verified text.

## 16. Required internal records

For every completed book, retain:

```text
book.md
book.txt
audit/corrections.jsonl
audit/unresolved.jsonl
audit/processing.json
```

The audit records are not part of the reader-facing book, but they must make every automated correction traceable and reversible.

## 17. Completion requirements

A book is `verified` only when:

1. Every included source page has been processed.
2. No story line has been silently omitted.
3. Page and reading order have been checked.
4. Chapter and paragraph boundaries match the printed source.
5. Dialogue and punctuation match the printed source.
6. All AI changes are logged.
7. No red unresolved items remain.
8. Title and author are present.
9. The final Markdown passes UTF-8 and Unicode-normalization checks.
10. The final text can be traced back to its source pages.

## 18. Governing principle

When fluency conflicts with fidelity, choose fidelity.

When context conflicts with visible evidence, choose visible evidence.

When evidence is insufficient, preserve uncertainty instead of inventing text.
