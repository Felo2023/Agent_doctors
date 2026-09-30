# General Clinical Research Team — Architecture

Status: implementation specification, not a running or clinically validated system.

This document supersedes the root-level v1 and v2 designs. Model policy is in [model-assignments.md](model-assignments.md); evaluation is in [validation.md](validation.md).

## 1. Purpose and fixed decisions

Build a reusable clinical research team that prepares a source-grounded briefing for a patient's clinicians. Patient facts, language preferences, and questions come from a private case profile, never from shared prompts or skills.

Decisions:

- **Topology B:** one OpenCode process and reviewable TUI per active doctor.
- One persistent session per doctor, case, and run; explicitly separate sessions for fresh independent reviews.
- A deterministic HTTP Mail Bus routes messages, schedules work, enforces phase rules, and stores canonical state.
- Anthropic and OpenAI model assignments are configurable by role. Exact API IDs must be verified.
- Validation runs **one case at a time**; experimental arms also run sequentially with fresh state.
- No case facts may be written into shared agents, skills, configuration, or public documentation.
- Diagnostic quality, source accuracy, and preserved uncertainty take priority over token minimization.

The team produces hypotheses, not established diagnoses. Treatment sections describe conditional options for clinician review. Prognosis distinguishes population evidence from individual prediction.

## 2. Components and topology

```text
Case launcher / process supervisor
  ├── Lead OpenCode process ───────────── Lead TUI
  ├── Specialist OpenCode process ─────── Specialist TUI
  ├── Reviewer OpenCode process ───────── Reviewer TUI
  └── ...
          ▲ HTTP prompts / session events
          ▼
Local HTTP Mail Bus
  ├── durable mail queue and per-session scheduler
  ├── role/session registry and phase controller
  ├── canonical case store and source index
  ├── positions, hypothesis dossiers, and reviews
  └── append-only audit events and transcript exports
```

The Lead selects consultations and synthesizes findings. The bus enforces transport and state rules. Specialists never write directly to the canonical record.

Each registry entry contains `case_id`, `run_id`, `doctor_id`, `agent`, `server_url`, `session_id`, `data_dir`, `model_alias`, resolved model ID, and status. The launcher allocates unique ports within the active run; no model prompt contains a port number.

Separate processes provide operational isolation, not independent medical expertise. OpenCode agent selection is not assumed to be an immutable identity boundary; the launcher and tool gateway must validate the bound role.

**Phase 0 result:** separate `XDG_DATA_HOME` values yielded distinct session databases and independent session lookup on OpenCode 1.18.33. Credential files also follow `XDG_DATA_HOME`; with separate data directories and no exported API keys, isolated doctor processes had zero configured provider credentials. Before actual role runs, provide authorized per-process credential access (for example, initialize each isolated profile through OpenCode's provider login or use provider-approved runtime environment credentials). Do not copy credentials into the repository or assume OpenAI OAuth can be represented by `OPENAI_API_KEY`. Confirm refresh/restart behavior without exposing secrets.

### Startup and restart

1. Validate the case profile, model availability, role credential access, and case workspace boundaries.
2. Start the bus and each required doctor backend using supported OpenCode mechanisms.
3. Create or recover the session, then open its TUI. Verify whether a bootstrap server plus attached TUI or direct TUI startup best preserves Topology B on the installed version.
4. Save session and process mappings before scheduling consultations.
5. On restart, reconcile persisted jobs with OpenCode session/message state before retrying delivery.

The implementation spike must verify per-process data-directory isolation, including whether `XDG_DATA_HOME` is honored on this platform. Do not claim independent storage before testing it. Give each process only the provider credentials and local service credentials it needs.

### Human access

Every active doctor has its own terminal window. Users may review history or converse directly. Before interactive use, pause bus delivery to that session and wait for in-flight work to finish. Resume after the human turn completes. This avoids treating a momentary idle event as an exclusive lock.

New patient facts from a conversation become source-labelled case-update proposals. A private conversation does not silently become shared case truth.

## 3. Repository and isolation boundaries

```text
docs/                         general specification
config/                       model aliases and role registry (no case facts)
.opencode/                    future general agents, skills, tools, plugins
bus/                          future deterministic service
scripts/                      future launcher, export, and evaluation commands
runbooks/case-template.md      public generic template
runbooks/private/             ignored real-case runbooks
cases/examples/               explicitly fictional examples, if added
cases/private/                ignored real-case settings
case-store/<case>/<run>/       ignored runtime artifacts
validation/manifests/          public non-identifying evaluation metadata
validation/rubrics/            scoring criteria
```

Correct answers and adjudication notes for validation live in a **separate scorer-owned location outside agent workspaces**, not in a file merely named “sealed.” Restrict filesystem and network access using enforceable process boundaries; prompts and a plugin alone are not a sandbox.

Real source directories may remain in place and be referenced by private case profiles. Do not symlink the whole repository into agent-accessible workspaces. Reuse ports only after all processes for the prior run have stopped.

## 4. Case profile

A profile contains:

- Unique ID, kind (`real`, `validation`, or `synthetic`), and schema version.
- Patient demographics with provenance, age reference date, and explicitly unknown fields.
- Clinical question and source roots, resolved relative to the profile file.
- Source languages and requested report languages; mixed-language records are supported.
- Runbook location, workflow options, and model configuration version.
- Validation prerequisite for real-case diagnostic runs.

Age, anatomy, reproductive context, and history guide relevant modules. They must not be used as rigid exclusions for relevant specialists. Available age-specific roles include pediatrics and geriatric medicine.

## 5. Clinical team

The 20 core roles are **phase-activated**, not simultaneously required in every round:

- Lead; abstractors A/B; reconciler; history interviewer.
- Three independent generalists: common explanations, unifying explanation, and multiple conditions.
- Internist; clinical pharmacologist; test strategist; evidence researcher.
- Contradiction hunter; cognitive-bias auditor; rare-disease specialist; safety auditor.
- Treatment specialist; prognosis analyst; treatment-safety reviewer; report translator.

The specialist pool and exact default assignments are defined in `config/agents.yaml`. Activate relevant specialties from the verified findings, and record selection reasons. Rare diagnoses should be assessed against prevalence and fit rather than rewarded for novelty.

Radiology and pathology initially interpret **reports**, not raw scans or slides. Raw-image review requires separately verified capabilities and modality-specific evaluation. Treatment-pool roles become active when relevant during treatment research. A tumor board is a conference of appropriate roles, not an extra vote.

Each role uses a few substantive procedural skills: task prerequisites, source checks, examination/report limitations, common pitfalls, and structured outputs. Skill names follow the installed OpenCode discovery format, with flat unique names such as `neuro-localization` and `shared-source-grounding`. Do not hard-code patient-specific disease suspicion in a skill.

## 6. Workflow

| Phase | Work and completion condition |
|---|---|
| P0 History | Capture the clinical question, symptom chronology, medications, exposures, relevant family/reproductive history, and current clinician assessments. Label patient/family reports separately. |
| P1 Records | Two independent extractions; source-based reconciliation; unresolved ambiguities listed; patient/family review of readable chronology and values. |
| P2 Routing | Select relevant specialists using a versioned case snapshot; log why. |
| P3 Independent assessment | Each diagnostician gets observations and clinically necessary history without AI-team conclusions; submits an immutable first position. |
| P4 Discussion | Specialists exchange targeted questions, challenges, and evidence through the bus. |
| P5 Final positions | Each assessment records changes, reasons, unresolved questions, and missing consultations. |
| P6 Synthesis | Lead ranks hypotheses by evidence and explains disagreement; no majority-vote probabilities. |
| P7 Diagnostic review | Auditors check omissions, contradictions, source support, and urgent concerns. Important findings reopen targeted discussion. |
| P8 Confirmation/treatment/prognosis | Relevant roles build conditional, cited hypothesis dossiers and complete safety review. |
| P9 Report | Produce requested languages, cross-model clinical-equivalence review, and human-readable source index. |
| P10 Follow-up | Incorporate new evidence as a new case version and rerun affected assessments. |

Safety alerts are available in every phase. Routine treatment research follows diagnostic review, but urgent escalation must never wait for consensus or the P8 phase.

### Independence and discussion

- In P3, block peer opinions but allow scoped literature requests to the Evidence Researcher and urgent alerts. Retrieval replies must not disclose other agents' hypotheses.
- Preserve real clinician diagnoses and treatments as attributed history; “blind” means blind to the AI team's conclusions, not removal of clinically essential context.
- Share pseudonym-labelled first positions in round one; later discussion may reveal specialty identity. This reduces attribution cues but does not guarantee anonymity.
- A changed opinion may cite a corrected calculation or interpretation, not only a new test result. Structured justification does not prove that the reasoning is sound; reviewers check substance.
- Preserve clinically relevant minority opinions without arbitrary probability cutoffs.
- For follow-up “fresh eyes,” start a new linked session. An established session cannot unsee prior conclusions.

Three discussion rounds and two diagnostic-review loops are initial defaults, not clinical stopping criteria. Finalization requires terminal states for all requested jobs, no undelivered required mail, explicit handling of unanswered questions, and documented remaining uncertainty. Queue silence alone is not completion. Timeouts and unavailable doctors must be visible in the report.

## 7. Mail protocol and delivery

Each message includes `message_id`, `idempotency_key`, `case_id`, `run_id`, `case_version`, `sender_role`, recipients, `thread_id`, optional `in_reply_to`, message type, priority, source references, and creation time.

Message types include question, answer, challenge, rebuttal, evidence request/result, position submission, review finding, case-update proposal, and urgent alert.

Delivery is asynchronous. `send_mail` returns a durable receipt, not a colleague response. A per-session worker serializes deliveries and monitors documented OpenCode status/events. HTTP acceptance is not proof of successful generation or processing.

Track `queued`, `dispatched`, `processing`, `completed`, `failed`, and `cancelled`. Use transactional persistence, bounded retries, deduplication, deadlines, and explicit acknowledgments. After crashes, reconcile before retrying; do not promise exactly-once LLM execution. Batch only messages with compatible snapshot and phase context.

Bus rules:

- Authenticate the calling process; derive sender identity from its role/session binding, not a free-form `from` field.
- Restrict recipients to the active case/run and enforce phase permissions.
- Let all clinical questions be audited by the Lead without prompting the Lead on every delivery.
- Allow bounded multi-step consultation chains; asynchronous replies avoid recursive synchronous waits. Detect repetitive/cyclic threads.
- Keep the diagnostic snapshot fixed for a round. New facts produce a new version and explicitly invalidate or refresh affected tasks.

Proposed bus API: `/mail`, `/positions`, `/reviews`, `/dossiers`, `/case`, `/case/proposals`, `/phase`, `/alerts`, `/health`. Every operation is case/run scoped and schema validated. These are **custom service endpoints**, not claims about native OpenCode routes.

Doctor tools: `send_mail`, `reply_mail`, `read_case`, `read_source`, `propose_case_update`, `submit_position`, `submit_review`, `submit_dossier_part`, `verify_citation`, and Lead-only phase/proposal controls. Citation verification checks metadata; content support requires a separate evidence assessment.

## 8. Canonical records and audit

Use a transactional store (e.g. SQLite for the bus) with a single controlled write path. Export JSON/JSONL snapshots and events for inspection; a collection of separately written JSON files must not be assumed atomic.

Every observation includes a stable ID, source document/page, Word body/table element, spreadsheet sheet/cell, or interview reference; original text; translated text if needed; event date versus report date; units/reference interval where applicable; extraction status; and provenance. Preserve original values alongside normalized ones. Explicitly distinguish absent, not assessed, unknown, and conflicting.

For each uploaded source, maintain the original file, a structured immutable extraction with exact source locators, and a **derived Markdown document** for people and agents to read. An index links all source Markdown files. Corrections are recorded as separate proposal/review events against source hash and block ID; they never rewrite original text or its Markdown projection. Accepted corrections can only enter canonical case state through a subsequent versioned review action. Word and spreadsheet adapters follow the same contract as PDF text/OCR. Presentation files are a later adapter; unknown file formats must fail visibly.

Symptoms, labs, imaging, pathology, medications, prior diagnoses, exposures, and family history are separate item types. Medication records include dose if known, route, frequency, start/stop dates, and source. Track trends without interpreting a missing measurement as normal.

Keep:

```text
case-store/<case>/<run>/
  registry.json, manifest.json, store.sqlite
  sources/                     source index and permitted copies/extractions
  markdown/                    derived per-source Markdown and index
  corrections.jsonl            separately reviewed source corrections
  case-versions/               immutable observation snapshots
  positions/                  initial/final positions and version links
  dossiers/                   confirmation/treatment/prognosis per hypothesis
  reviews/, reports/, transcripts/, exports/
```

The manifest records code/config version, model IDs/settings, prompt/skill versions, source hashes, retrieved evidence versions, and phase timings. Exports preserve observable messages, outputs, and tool events; they do not expose or promise private model reasoning. Stored history is not unlimited active context. After compaction, reread source-backed case state; an experimental hook cannot guarantee perfect memory.

## 9. Clinical output contracts

### Diagnostic position

Include scope, case version, observations with IDs, ranked hypotheses, evidence for/against, missing information, alternative explanations, proposed confirmation criteria, useful next tests, red flags, limitations, and justified revisions.

Numerical disease estimates are optional and must be labelled uncalibrated unless evaluated. Do not require percentages merely to make a report look precise. If using a mutually exclusive distribution, define its hypothesis set and normalize it with an “other” category. Coexisting disease probabilities are separate marginals and need not sum to one. Never convert vote counts into probabilities.

### Confirmation pathway

For each leading or clinically consequential hypothesis: accepted criteria, criteria met/unknown, reference standard if one exists, test purpose, test limitations, false-negative/positive considerations, prerequisites, risks, urgency, and suggested sequence for clinician discussion. Separate confirmation, discrimination, exclusion, staging, and monitoring. Some diseases have no single definitive test; a negative test does not universally rule out disease.

### Treatment dossier

Provide current guideline-based first-line and alternative options, goals (cure/remission/control/symptom relief), eligibility, expected benefits, adverse effects, contraindications, monitoring, and patient factors still needed. The pharmacology/safety review considers interactions, organ function, allergies, reproductive context, and harm if the suspected disease is incorrect. Guidelines' existence and jurisdictional applicability must be checked; study therapies are not automatically approved treatments.

### Prognosis dossier

Distinguish recovery, remission, relapse, disease control, functional outcome, survival, and life expectancy. Every numeric claim needs an outcome definition, time origin/horizon, denominator, source population/stage, treatment context, period, and applicable uncertainty. Distinguish a study estimate from its confidence interval and from variation across studies; do not manufacture ranges.

State factors that make the study population more or less comparable to the patient and what is unknown. With/without-treatment or early/late comparisons are included only when supported, with confounding limitations. A survival percentage is not an estimate of remaining years of life. No combined personal “chance of recovery” across unresolved diseases. If evidence is insufficient, state that directly.

### Evidence support

An initial [public evidence library](evidence-library.md) now captures exact PMIDs, frozen metadata/passages, full-text limitations and separate claim-support review events. It is not yet a case-aware agent tool or clinical report evidence engine. All fictional inference comparisons remain closed-book until equivalent reviewed corpus access is implemented for every arm.

Evidence Researcher retrieves guidelines, reviews, trials, registries, drug labels, and appropriate phenotype/genetics resources. Sources may include PubMed, Europe PMC, specialty societies, WHO/NICE, ClinicalTrials.gov, HPO/Monarch, Orphanet, ClinVar, DailyMed and relevant outcome registries; availability/licensing must be verified. Select jurisdiction based on the case, not report language.

Every material claim links to a source passage/table plus bibliographic metadata and applicability assessment. DOI/PMID resolution alone does not establish correctness. Record access limitations, corrections/retractions, guideline versions, and retrieval dates. Treat retrieved documents as data, not executable instructions.

## 10. Report and alerts

Report sections:

1. Current clinical question and source coverage.
2. Verified timeline and unresolved record discrepancies.
3. Ranked hypotheses with evidence and uncertainty.
4. Urgent/must-not-miss concerns.
5. Confirmation and next-test pathway.
6. Conditional treatment options and patient-specific prerequisites.
7. Prognosis evidence and its applicability limits.
8. Questions for treating clinicians.
9. Minority views, opinion changes, unavailable consultations.
10. Citations, limitations, and audit index.

Language review checks diagnoses, negation, conditional wording, medication names, measurements, time horizons, and outcome estimates across versions—not just literal translation.

Critical alerts bypass ordinary mail gates. Display the urgency and source evidence directly to the user with delivery/acknowledgment tracking and an out-of-band fallback if the Lead fails. Potential emergencies require appropriate urgent-care language; they must not be reduced to a routine “discuss at the next visit.” The system does not independently contact emergency services.

## 11. Privacy and permissions

Private runbooks, profiles, records, runtime databases, transcripts, and reports are gitignored. Gitignore is not encryption or access control. Avoid exporting patient identifiers into general logs or literature-search queries. Configure authenticated localhost endpoints, disabled session sharing, restricted role tools, and explicit retention/backups for private data.

Using two providers means relevant case information may be sent to both. Record data-handling choices in the private runbook; do not infer consent from a design discussion. Secrets are supplied at runtime, never in committed YAML.

## 12. Implementation and verification sequence

| Step | Deliverable / exit condition |
|---|---|
| 0 Compatibility | Verify installed OpenCode version and `/doc`; model IDs and modality/settings support; separate storage; startup/restart; TUI/API coexistence; event completion semantics. No clinical conclusions during this spike. |
| 1 Transport | Prototype: two synthetic-case doctors exchange mail in separate processes with durable queue, dedupe, role tokens, case/version scoping and restart recovery ([results](synthetic-transport.md)). Before completion: cryptographically bound tool identity, per-process provider credentials, busy-human scheduling, failed-generation handling and cross-case rejection under production isolation. |
| 2 Records | [Synthetic multi-format intake](synthetic-intake.md) converts PDF/DOCX/XLSX/CSV/TSV to source-linked Markdown + JSON. Two [independent model passes](synthetic-abstraction.md) propose, but do not promote, source statements; the [local review app](synthetic-review.md) displays disagreement, tracks **extracted-block coverage and unresolved limitations**, and records separate human decisions. It creates immutable versions of reviewed labs and attributed **source statement subsets** with a timeline. The bus can read a pinned version. This is not complete clinical abstraction or real-case ingestion. Before completion: diverse layout validation, robust batching, full source inventory/reconciliation, clinician/family review and real-case authorization gate. |
| 3 Team | Run P0–P9 on development cases, including evidence retrieval and independent review. |
| 4 Evaluation | A [bus-mediated fictional pilot](evaluation-mail-bus.md) tests sequential baselines, independent panel/critique, bounded tool-sent colleague questions/replies, final positions and Lead synthesis, role-scoped HTTP jobs, answer-directory separation and frozen audit exports. A [published-case preparation path](published-case-preparation.md) archives passages and creates separated draft packet/reference files with an independent eligibility review gate; one example is pending review and not executed. Next: complete the specialist/research/review team, source-aware clinical contracts, OS isolation, clinical adjudication, comparable differential/report scoring, paired metrics and held-out evaluation. |
| 5 Real case | Run the reviewed configuration, produce clinician briefing, and preserve follow-up history. |

The general documentation and YAML remain future design specifications. The three requested model IDs, basic local session/TUI APIs, a **synthetic-only case-scoped two-doctor bus**, synthetic multi-format Markdown extraction, independent A/B proposals, reviewed-subset case versions with block-coverage checks, and a model-backed **fictional** evaluation pilot have passed tests. Clinical agents, full clinical case-state management, real-record intake, reasoning settings, production credential isolation, and medical performance remain unverified. See [compatibility results](compatibility-spike.md), [transport results](synthetic-transport.md), [synthetic intake results](synthetic-intake.md), [abstraction results](synthetic-abstraction.md), [review app results](synthetic-review.md) and the [evaluation pilot](validation.md#operational-scoring-scaffold).
