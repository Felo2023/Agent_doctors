# Synthetic Evidence Review App

This localhost-only prototype connects synthetic source extraction to an explicitly reviewed, versioned case **subset**. It cannot diagnose and cannot ingest real clinical records. PDF, Word and spreadsheet text can be read via the source Markdown; the limited automatic lab-candidate parser recognizes three fictional Spanish labels in PDFs. A human may propose other attributed source statements against text/cell block IDs for independent review.

## Try it

Use the isolated Python environment from [synthetic intake](synthetic-intake.md). Generate a fictional fixture and copy the printed `Synthetic fixture` path:

```bash
.venv/bin/python -m scripts.synthetic_intake_demo
```

Start the server in a separate terminal, replacing the directory below with that fixture's printed path and setting your own local password (20+ characters):

```bash
REVIEW_APP_PASSWORD='choose-a-unique-local-password' \
  .venv/bin/python -m case_review.server --fixture-root '/path/from/demo' --port 8765
```

Open `http://127.0.0.1:8765/` in a browser. Username: `reviewer`; password: the one set above. The server binds **only** to `127.0.0.1` and must not be exposed through a tunnel or reverse proxy. Synthetic sources and the SQLite database remain in the private fixture directory. Stop with Ctrl-C.

## Review workflow

1. Open each **original file** from its candidate card. The source Markdown is helpful for navigation but the original is the evidence to check.
2. If the extraction misread a source line, propose a correction. A **different reviewer identifier** accepts or rejects it in the correction ledger. An accepted correction does **not** automatically modify a case fact.
3. Review every lab candidate as *verify*, *exclude*, or *defer*, with reviewer and explanation. To verify a corrected value, enter the accepted `COR-...` proposal ID. The system checks source hashes, block references, analyte and unit.
4. Optionally run [two independent synthetic abstractors](synthetic-abstraction.md) and open **Compare two independent AI abstractions** to inspect their proposals and disagreements. Neither agreement nor a correct citation automatically becomes a fact.
5. Expand a document in **Browse extracted blocks** to propose a symptom, medication, prior diagnosis, imaging/pathology report statement, family history or other supported category. Attribute it to the patient, clinician, or source text. Do not assume a diagnosis is confirmed because it was mentioned in a report. Provide an exact YYYY-MM-DD or an approximate `YYYY`/`YYYY-MM` date where supported, or choose unknown. A **different reviewer identifier** must verify, exclude, or defer the proposed statement before a commit.
6. Account for **each extracted block and extraction warning**. A verified observation links to its source block; other blocks need an explicit context/defer decision. Unreadable pages, missing OCR, unextracted chart content and missing spreadsheet formula results **cannot be marked complete**. An `omitted_by_both` AI result is not a negative finding. The coverage indicator audits *available extracted blocks*, not whether every clinically relevant record exists.
7. Create a reviewed case version. Unreviewed lab candidates, pending statement proposals, and two conflicting values verified for the same analyte/date/unit block committing. If two measurements on the same day are genuinely different, **defer them until specimen time and context are established**; do not exclude a real result to satisfy the prototype's limited conflict rule. Verified lab observations, attributed source statements, precision-qualified timeline entries, held/excluded reasons and the coverage status are persisted in a versioned SQLite store. Partial versions may be saved with `intake_coverage_complete: false`. Earlier versions remain readable via authenticated `/case/version/<n>` links and the latest via `/case`.

The app labels all committed versions `reviewed_subset` and sets `ready_for_diagnosis: false`. A separate synthetic case-scoped mail run can read one pinned version but **does not perform diagnosis**. The app does not claim that an identifier typed into a form establishes professional credentials, that a human proposal is automatically true, or that the limited parser caught every result.

## Technical behavior and limits

- `case_review/store.py`: optimistic revision checks, source and extraction SHA-256 checks, review events and linked version hashes in `review.sqlite` under the synthetic fixture root. This is an audit-friendly prototype, not tamper-proof storage against an OS user who can rewrite the database.
- `case_review/server.py`: localhost HTTP Basic authentication, form token and same-origin checks, no-cache responses, escaped source display and a responsive evidence-review screen. It serves only files in the marked synthetic fixture that match the original source hashes.
- The `--fixture-root` marker is an accidental-use guard, **not** a security sandbox. Do not point this at real records or assume the local HTTP service is suitable for private health data.
- Human correction review and candidate verification are **distinct** events. No model, OCR score, or accepted transcription can silently write into the canonical case.
- The app can supply a pinned reviewed synthetic version to the [case-scoped Mail Bus](synthetic-transport.md). Independent A/B proposals can be compared, and block-level coverage can be audited; determining whether all source records were supplied, clinically authenticated adjudication, and a solved clinical benchmark do not yet exist.

## Verification

Automated tests cover incomplete reviews, contradictory values, stale revisions, source tampering, separately accepted corrections, immutable versions, HTTP authentication, original-file links and end-to-end form submission. An optional browser check uses Playwright (not a runtime requirement):

```bash
.venv/bin/python -m pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python -m scripts.synthetic_review_browser_check
```

It creates fictional PDFs, exercises review and commit in headless Chromium, and saves desktop/mobile screenshots to a private temporary directory. No patient records are opened.
