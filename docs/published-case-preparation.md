# Published Solved-Case Preparation

This workflow prepares **draft research inputs**, not diagnoses or clinical validation. It is separate from the synthetic inference harness. It does not access private patient records and never sends an article or reference answer to a model.

## What is implemented

`validation/published_packets.py` separates four artifacts:

1. **Scorer-owned archive:** full article XML, bibliographic metadata, license links, source hash, and an index of body paragraphs. Titles, abstracts, discussion, and later results may reveal the answer; they stay outside agent workspaces.
2. **Draft packet:** a defined question and neutral `E-001` evidence labels containing exact selected source excerpts. Paragraph IDs and the original publication identity stay in the scorer-owned selection record, not in the packet that will eventually be given to agents.
3. **Separate reference:** proposed accepted diagnoses/aliases, answer paragraph IDs, critical flags and an adjudication rationale. This is not independently verified just because the article states a result.
4. **Independent review record:** packet/reference hashes, named reviewer and reviewer role, cutoff/faithfulness/license/de-identification/leakage checks, contamination risk and decision. No artifact is labelled eligible by an AI draft alone.

A human-readable `review-worksheet-private.md` combines these items for review. It prominently says **SCORER / REVIEWER ONLY** because it contains the answer. Never copy it into an agent input directory.

## Commands

Use a scorer-controlled location outside this repository; the directories below are placeholders. Scripts reject repository-local answer-bearing archives. First archive a candidate with a detected reuse license:

```bash
python3 -m validation.published_packets archive \
  --owner-root '/private/scorer/curation' --case-id VAL-001 --pmcid PMC12587510
```

This fetches from Europe PMC and creates `VAL-001/selection-template.json`. It does not select evidence or generate a diagnosis prompt automatically. An operator must populate:

- The diagnostic question and what was known at that time.
- The **proposed** last included original paragraph and explicitly excluded answer-bearing paragraphs.
- Exact pre-cutoff quotes to include; no invented findings, typical disease patterns, or guessed test results.
- A separate proposed reference, source support and acceptable aliases.
- Known diagnoses that should remain as legitimate history rather than being deleted to make the problem artificially blind.
- Contamination risk (`possible`, `likely`, or `uncertain`); “unseen” is not inferred from publication date.

Prepare a versioned draft:

```bash
python3 -m validation.published_packets draft \
  --owner-root '/private/scorer/curation' --selection '/private/scorer/curation/VAL-001/selection.json'
```

The script checks every excerpt against an archived paragraph, rejects evidence from excluded/later paragraphs and publication identifiers in the packet, and records frozen packet/reference hashes. It flags exact matches to target-diagnosis labels for human inspection. **These mechanical checks do not establish temporal faithfulness or absence of answer leakage.** A synonym, treatment, gene name, or distinctive phrasing can still reveal the answer; the clinical reviewer must inspect the entire packet. Paragraph order is not a substitute for a true clinical timepoint.

The generated `review-template.json` starts with `decision: hold`, no reviewer identity and all checks false. After a substantive independent review, the reviewer can record `hold` or `eligible_for_research_evaluation`:

```bash
python3 -m validation.published_packets review \
  --owner-root '/private/scorer/curation' --attestation '/private/scorer/curation/VAL-001/review.json'
```

Eligibility requires a reviewer different from the preparer, the `qualified_clinician` role, matching frozen hashes, and all required checks attested. The software does not authenticate credentials; verifying the reviewer is an operator responsibility. Tests using fake reviewer strings do not count as a clinical review. Draft/review files are not overwritten; a changed packet needs a new preparation version/case ID.

Only an eligible record can be exported into **disjoint** input and answer directories, also separate from the archive:

```bash
python3 -m validation.published_packets export \
  --owner-root '/private/scorer/curation' --case-id VAL-001 \
  --inputs '/private/research/answer-free-inputs' --answers '/private/scorer/references'
```

Bibliographic attribution and adaptation notes remain with the scorer and must accompany any redistributed source derivatives or evaluation publication. Check the exact license and third-party content before reuse. `licensed_for_review` is a discovery flag, not a legal clearance for every figure or patient detail.

## Current prepared candidate

One source from the Europe PMC open-access collection has been archived and an **AI-assisted draft** prepared outside the repository. It proposes an additional-neuropathy question before later genetic results while retaining the already documented diagnoses as context. Clinical question, chronological cutoff, de-identification, reference equivalence, and critical-flag labels are **pending independent review**. It has not been run through a model or counted in any accuracy results.

The archived case is an exploratory preparation example, not a held-out benchmark: the development assistant has seen its answer. A scored evaluation should use a separately selected, frozen set with contamination risks reported.

## Execution remains disabled

Even an exported eligible `VAL-*` packet is not accepted by the current `SYNTH-*` model runner. The published-case engine still needs:

- A final Mail Bus team arm, comparable individual-model baselines, and a free-form differential/report contract (not a fictional multiple-choice code catalog).
- Process/OS isolation proving the agents cannot read scorer archives, answer files, previous runs or results. Separate directories with one OS user are insufficient.
- A frozen, clinician-reviewed development/held-out dataset and scoring rubric, including coexisting diagnoses, confirmation tests, safety, treatment and prognosis claims.
- Versioned evidence retrieval with no source-case answer leakage and proper handling of model memorization.

No automated eligibility attestation, prototype export or synthetic score authorizes a run on private patient records.
