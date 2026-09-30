# Synthetic Multi-Format Intake Prototype

This component creates source-linked JSON and Markdown from generated, fictional PDFs, DOCX, XLSX, CSV, and TSV. It is not authorized to read real patient records or populate the canonical case store. PowerPoint is deliberately deferred.

## Run

From the repository root, create a local Python virtual environment and install the declared extraction dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m scripts.synthetic_intake_demo
.venv/bin/python -m scripts.synthetic_multiformat_demo
```

The PDF demo generates **two fictional PDFs** in a private system-temporary directory: a text-layer report and a scanned image report. The multi-format demo generates fictional PDF, DOCX, XLSX and CSV files and proposes a sample correction. Both write `extraction.json`, a `SRC-<hash>.md` per original file, and `index.md`. The proposal is written to `corrections.jsonl` and displayed separately in `corrections.md`, with a link to its exact source block; it does not change the original Markdown. The snapshot is marked `unverified_synthetic_extraction`, and parsed lab candidates are marked `unverified_candidate`.

The module requires an explicit synthetic fixture root containing `.synthetic-fixture` with `SYNTHETIC_INTAKE_FIXTURE_V1`. It rejects requests with `synthetic_only=False`, input outside the marked root, and the known private-record/case-store directories. This is an **accidental-use guard**, not a security boundary against a caller able to label arbitrary files synthetic. No workflow should call it with real records until source authorization, isolation, and clinical review are implemented.

## What the snapshot records

- SHA-256 of each source PDF, a stable source ID, original filename, and pages numbered from one.
- Separate native PDF text lines and local OCR lines, with OCR confidence and bounding boxes where available. On macOS, `intake/vision_ocr.swift` uses Apple Vision; OCR images are rendered from the PDF into temporary files and deleted after processing. Documents are **not** uploaded for OCR.
- An estimated image-coverage fraction per page. A scanned page triggers OCR; a mixed image/text page retains both channels and is explicitly flagged for comparison.
- Conservative candidate matches for **Creatinina, Glucosa, and Hemoglobina only**: original Spanish source line, document/page/line/channel, decimal-comma value normalization alongside the original value, unit, optional reference range, and explicit ISO collection date if found on the page. A filename never supplies a date.
- Conflicting candidate values for the same analyte, explicit collection date, and unit are retained and marked `unresolved`. Undated values from different documents are not merged or called contradictory without more context.
- Warnings for OCR failure, low-confidence OCR, mixed channels, conflicting/invalid dates, and pages without an explicit collection date. Empty text extraction never implies a normal finding.
- DOCX: source blocks for body paragraphs/headings, table cells, header/footer paragraphs and locally OCR'd raster images. The original file and source hash remain the reference. Tracked changes, comments, notes, text boxes, and charts are flagged where detectable; some Word constructs may still be missed.
- XLSX: source blocks with sheet name and cell coordinate, hidden-sheet visibility, original cell value, formula **and separately saved cached result**. A formula without a cached result is shown as **not available**, never calculated or mistaken for zero. External links, comments, chart/pivot/drawing content and merged layouts are flagged where detectable. CSV/TSV cells retain row/column references and original strings.
- Derived Markdown preserves block IDs and source labels. It is not the source of truth; unusual layouts should always be checked against the originals. Cells with potentially unsafe Markdown characters are escaped in the readable projection; exact values remain in JSON.
- Correction proposals link to an existing block ID and original source hash. A second review event can accept or reject a proposal in `corrections.jsonl`; a separate `corrections.md` makes the proposal and status readable. **Neither changes the original Markdown or silently updates the case record.**

## Tests and limits

Tests cover native extraction, scanned-image Vision OCR, mixed image/text handling, contradictory values, undated reports, unavailable OCR, DOCX text/tables and image OCR, XLSX formulas/saved values/hidden sheets, CSV escaping, correction review, unsupported formats, and the synthetic-only guard. In the PDF demo, two candidate values produce one unresolved conflict. The combined suite also runs the synthetic transport tests.

The candidate parser deliberately covers only three synthetic PDF lab names and a narrow report layout; Word and spreadsheet data are preserved as **uninterpreted source blocks**, not interpreted as clinical findings. It does not understand arbitrary clinical tables, full Spanish dates, radiology, medications, context-specific reference ranges, or complex medical records. It does not calculate Excel formulas. OCR confidence is not clinical confidence. Date/value extraction needs independent verification against every original page, element or cell before it can become a case fact. Dual independent LLM abstraction, a correction-to-canonical-case approval flow, and family/clinician reconciliation remain future work.
