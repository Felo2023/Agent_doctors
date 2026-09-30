# Clinical Research Team

A general, case-isolated multi-agent clinical research system designed for OpenCode. Each specialist has a separate process, TUI, and persistent case session. An HTTP Mail Bus coordinates independent assessments, specialist discussion, and Lead synthesis.

**Status: synthetic, case-scoped intake → independent abstraction → review → agent-mail integration, plus a bus-mediated fictional evaluation pilot.** Two independent model processes can propose source-linked observations from fictional documents; reviewers see disagreements, account for extracted blocks, and adjudicate what enters a case version. Separate OpenCode processes can read a pinned fictional version and exchange source references. A sequential runner executes a **durable HTTP-bus panel and three actual single-model baselines** on a fictional solved case; scores are only an operational check. No real-case intake, complete medical abstraction, full clinical specialist/research/review team, or clinical evaluation has been achieved. The YAML describes the intended full system; it is not native OpenCode configuration.

## Authoritative documents

- [Architecture and implementation sequence](docs/architecture.md)
- [Model assignments](docs/model-assignments.md)
- [Sequential validation protocol](docs/validation.md)
- [Generic case runbook template](runbooks/case-template.md)

The original root-level v1 specification is a historical reference. The superseded case-specific v2 specification is retained locally and gitignored because it contains patient-specific context. The general documents above are authoritative.

## Patient questionnaire

[Download the unfilled clinical history questionnaire](clinical_history_questionnaire.docx). This Spanish-first, bilingual Word template can be completed by any patient. Save completed copies in a private location, not at the repository root. Regenerate the template with `python -m scripts.create_patient_history_form` after installing `requirements-intake.txt`.

## Configuration

- `config/models.yaml`: requested models and exact IDs verified with synthetic live prompts on this machine; unavailable models must never be substituted silently.
- `config/agents.yaml`: role-to-model assignments and activation rules.
- `cases/private/`: gitignored real-case profiles.
- `runbooks/private/`: gitignored real-case runbooks.
- `case-store/`: gitignored runtime state, records, transcripts, and reports.

Executable prototypes resolve IDs through `runtime/models.py`. Current Sol roles use **GPT-6.1 Sol**; Astra remains the verified GPT-6 Astra because no GPT-6.1 Astra ID was listed. Model versions and synthetic access checks are recorded in [model assignments](docs/model-assignments.md). Install the complete runtime/test dependencies with `python -m pip install -r requirements.txt`.

Validation runs one case at a time, with fresh sessions for every experimental arm. Real-case diagnostic analysis follows evaluation; record preparation may happen earlier.

[Phase 0 compatibility results](docs/compatibility-spike.md) record tested behaviors and remaining setup requirements.

[Synthetic two-doctor prototype](docs/synthetic-transport.md) documents how to run and inspect the transport test, its credential handling, and its current limits.

[Synthetic document intake](docs/synthetic-intake.md) documents local text/OCR conversion for PDFs and Word images, spreadsheet formula preservation, source Markdown, correction records, and required manual review. PowerPoint is planned later.

[Independent synthetic abstraction](docs/synthetic-abstraction.md) runs Opus and Astra in separate OpenCode sessions on the same fictional source blocks and surfaces their proposals and disagreements without automatically promoting any case facts.

[Synthetic evidence-review app](docs/synthetic-review.md) shows how to review source-linked observations in a local browser and create immutable case versions without treating extraction as a diagnosis.

[Sequential evaluation pilot](docs/validation.md#operational-scoring-scaffold) stages one case/arm at a time and can run the three requested models plus a pilot panel on **fictional** inputs. Answer keys stay outside agent workspaces. It is not a clinical accuracy study.

[Evaluation Mail Bus](docs/evaluation-mail-bus.md) enforces independent rankings, anonymous critique, bounded tool-sent colleague questions/replies, final positions and Lead synthesis over role-authenticated HTTP jobs. Positions, question threads and audit events are persisted.

[Published solved-case preparation](docs/published-case-preparation.md) archives source passages, builds a draft answer-free packet with a separately held reference, and records independent eligibility review. One preparation example is pending review; no published medical case has been evaluated.

[Source-linked evidence library](docs/evidence-library.md) verifies public reference identity, preserves retrieved passages and hashes, and separates exact quotations from independent claim-support review. It is not yet enabled in the closed-book evaluation panel.

## Outputs

Reports cover diagnostic hypotheses, confirmation pathways, evidence-based treatment options, and prognosis where supported by evidence. Languages are configured per case. The reports support discussion with treating clinicians; model agreement does not establish a diagnosis or a personal survival estimate.
