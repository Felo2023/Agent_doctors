# Source-Linked Evidence Library

This development stage captures **public publication evidence**, not patient records. Citation identity, retrieved text and claim support are three separate states. Nothing captured here becomes a diagnosis, treatment recommendation, or personal prognosis automatically.

## Capture an exact reference

```bash
python3 -m evidence.library --root '/private/operator/evidence-run' --pmid 35006617
```

Only a numeric PMID is accepted. The operator selects references; there is no patient-text search or arbitrary URL fetch. The tool queries Europe PMC's core metadata and verifies the returned PMID, records title/DOI/authors/date, preserves the indexed abstract, and attempts licensed full text where available. The full-text PMID and PMCID must match the requested record. Images and table layout are not interpreted; table text is explicitly flattened and needs original-layout inspection.

If full text is unavailable, inaccessible, mismatched, or has no recognized reuse license, the snapshot **discloses the limitation**. Abstract-only retrieval is not complete guideline or study review. `isRetracted` is recorded where the index provides it; an absent retraction field remains unknown. A guideline title produces an explicit **currency/version unchecked** warning; retrieval does not prove it is the latest recommendation.

The operator directory contains:

```text
PMID-<id>/
  metadata-original.json        exact retrieval response
  article-original.xml         only when readable full text and reuse license are recognized
  snapshot.json                identified source, passages, limitations and retrieval time
  manifest.json                frozen snapshot/metadata/full-text hashes
claim-events.jsonl             proposed claims and separate review events
```

Refreshing evidence requires a new library/run directory, not overwriting a captured reference. Reads and reviews check source hashes; these detect accidental drift, not hostile edits to both content and hashes by an OS user.

## Claims require substantive review

The Python APIs `propose_claim(...)` and `review_claim(...)` are currently operator-facing. A proposal must specify:

- Source ID and an existing passage ID.
- An **exact quote** within that passage.
- The claim, named proposer, and explanation of applicability/limitations.

A different reviewer then records `supports`, `contradicts`, `insufficient`, or `unclear`, with a substantive rationale. A retracted source cannot be approved as supporting evidence. The ledger preserves both events; no accepted quote silently writes into a clinical report.

Valid source IDs and exact quotations do **not** prove that the source supports an inference. Population, outcome, time horizon, treatment context, method quality, contraindications, guideline currency and patient comparability still need expert assessment. Reviewer identifiers are recorded, not authenticated as professional credentials by this prototype.

## Evaluation leakage boundary

Exclude the source case and known duplicates before building a frozen reference corpus:

```bash
python3 -m evidence.library --root '/private/operator/frozen-evidence' --pmid 35006617 \
  --exclude PMC12587510 --exclude 41194199
```

Exclusions are checked against requested PMID and resolved PMID/PMCID/DOI before storing a source. That does not detect all answer leakage or memorized cases. The synthetic comparison remains **closed-book** for every arm: this library is not yet connected to panel prompts. Enabling it later requires an equivalently permitted source corpus across baselines/team and an audit of retrieval leakage. Existing private and published-case execution gates stay in place.

## Tested status and next integration

A live public-reference capture returned one indexed abstract, without full-text access; the warning correctly stated that it was not complete guideline review. Unit tests cover full-text identity, exact passage quoting, exclusions, missing full text, independent support review, retractions and changed snapshots.

Next integration is a restricted Evidence Researcher role that reads an approved frozen corpus and proposes source-linked claims for review. Free-text search, automated guideline updates, complete clinical hypotheses, treatment/prognosis synthesis and clinically evaluated claim verification are not implemented by this module.
