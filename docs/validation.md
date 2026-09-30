# Sequential Validation Protocol

## 1. Goal and limits

Evaluate whether structured multi-agent discussion improves diagnostic research and report quality compared with strong single-model baselines. Run **one case at a time**, and one experimental arm at a time, using the same engine that will process real cases.

A 15–20-case pilot is useful for debugging and feasibility. It does not establish clinical reliability, subgroup safety, or a personal probability of diagnosis. Published case reports overrepresent unusual presentations and cannot represent routine patient prevalence by themselves.

## 2. Data and answer isolation

- Use appropriately licensed/de-identified adult solved cases initially, including common conditions, mimics, multiple conditions, and uncertain or incomplete presentations where possible.
- Define the diagnostic time point: only history, examination, and tests available at that point are supplied. Remove answer-bearing titles, filenames, metadata, abstracts, conclusion sections, and postdiagnosis treatments.
- Keep reference diagnoses, adjudication notes, outcome records, and scoring materials in a separate scorer-controlled location outside any agent workspace.
- Patient workspace restrictions must be enforced outside prompts. Verify with explicit forbidden-path and cross-case access checks.
- During diagnosis, use a frozen permitted reference corpus or controlled search that prevents retrieval of the source case and answer-bearing duplicates. Audit searches. General web access cannot be assumed leakage-free.
- Publication after a claimed training cutoff and model self-report of recognition do not prove absence of contamination. Record suspected contamination and report sensitivity analyses excluding such cases.
- Translate selected cases into Spanish source documents to test the pipeline. Include text PDFs, scanned pages, Word tables/embedded images, and spreadsheets when licensed source material permits it. Preserve the original facts and chronology; do not fabricate a multi-year history. Label deliberately synthetic modifications separately.

## 3. Sequential execution

For each case, run these arms separately with fresh sessions, databases, mailboxes, and source snapshots:

1. Full team using the configured role/model registry.
2. Single Opus baseline.
3. Single Astra baseline.
4. Single Sol baseline.
5. Optional ablation: independent opinions synthesized without discussion.

Give all arms the same clinical information and equivalent access to permitted evidence and source-reading tools. Document differences in compute, calls, and context; optionally add a compute-matched baseline to distinguish extra sampling from architecture effects.

Prespecify repetitions and run order. Score frozen diagnostic outputs before allowing postdiagnostic answer access. Evaluate confirmation, treatment, and prognosis both conditional on each arm's hypotheses and, separately, with the reference diagnosis supplied after diagnostic scoring. This separates diagnosis failure from treatment-research failure.

Stop and export every arm before the next starts. Release ports only after confirmed process shutdown. No memory, prior report, model feedback, or answer material carries into another arm or case.

## 4. Metrics and adjudication

| Area | Assessment |
|---|---|
| Diagnosis | Top-1/top-3/top-10 inclusion using prespecified diagnostic equivalence and specificity rules; coverage at fixed list length |
| Critical omissions | Recall and false-positive burden for adjudicated must-not-miss conditions and appropriate urgency |
| Extraction | Value/unit/date/negation/source accuracy; unresolved discrepancies; item-level precision/recall |
| Tests | Guideline-consistent confirmation criteria, necessary next steps, unnecessary/invasive-test burden, and handling diseases without a definitive single test |
| Treatment | Current guideline concordance, applicability, prerequisites, contraindications, and monitoring; historical case treatment is not automatically the current standard |
| Prognosis | Correct outcome definition, horizon, population, uncertainty, source support, and justified refusal to personalize insufficient data |
| Evidence | Citation identity AND claim-level passage/table support; access limits and outdated guidance |
| Language | Clinical equivalence, negation, units, conditionality, drug names, and outcome measures |
| Operations | Isolation, delivery completeness, restart recovery, missing-consult visibility, and source traceability |

If probability calibration is evaluated, define valid labels and hypothesis sets. Multiclass Brier scoring uses the full mutually exclusive distribution; multiple coexisting diseases require prespecified binary/multilabel outcomes. Scoring only the assigned probability of the correct diagnosis is insufficient. Qualitative rankings remain permissible where numeric estimates are unjustified.

A single person's recovery or death cannot validate or invalidate a population survival percentage. Audit prognosis figures against their underlying study rather than treating a patient's outcome as a percentage-range test.

Use clinician-adjudicated labels and blinded review of material clinical claims when available. Automated model judging can triage outputs but is not independent clinical validation. Explicitly report when qualified human review has not occurred.

## 5. Development, held-out evaluation, and release decision

1. Use development cases to fix implementation and refine role assignments.
2. Freeze prompts, skills, models/settings, routing, retrieval rules, and scoring rubric.
3. Evaluate on a separate held-out case set without adapting the system to those answers.
4. Report paired results, uncertainty intervals, critical errors, all failed runs, and performance by case type. Predeclare acceptance criteria with the clinical reviewer before held-out execution.
5. If the team is not demonstrably useful compared with the best single-model baseline, investigate/simplify rather than claiming success. Material changes require a new version and fresh held-out evaluation.

Real-case diagnostic execution requires a recorded evaluation decision, disclosed limitations, and resolution of critical transport/data-isolation defects. Passing the pilot does not authorize autonomous clinical use or establish diagnostic accuracy for an individual patient.

### Evaluation decision artifact

The future launcher must require a reviewer-issued decision artifact before a real case enters P2–P9. It contains `decision_id`, `protocol_version`, `configuration_hash`, `resolved_model_manifest_hash`, `evaluation_run_ids`, `acceptance_criteria_ref`, `results_ref`, `critical_defects_resolved`, `limitations`, `decision` (`hold` or `proceed_for_clinician_review`), `reviewer_identity`, `clinical_reviewer_status`, and `issued_at`. Keep it in operator-controlled storage; clinical agents cannot issue or edit it.

The launcher verifies that the decision allows proceeding, critical defects are resolved, and the current configuration/model hashes match. Missing or mismatched artifacts block diagnostic execution with an explicit reason. P0/P1 record preparation is permitted earlier. A new material configuration/model change requires renewed evaluation and a new decision. This is a planned enforcement contract, not a currently implemented approval mechanism.

## 6. Artifacts

Store non-identifying case manifests and rubrics under `validation/manifests/` and `validation/rubrics/` when implemented. Runtime outputs remain under ignored `case-store/`. Answer keys remain outside the agent runtime root. Each result records case/arm/run IDs, source/config hashes, model IDs, protocol deviations, scoring version, and reviewer status.

## Operational scoring scaffold

`validation/harness.py` is a **synthetic-only** operational check of case/arm order and exact-ID scoring. Its basic demo uses simulated submissions:

```bash
python3 -m unittest tests.test_validation_harness -v
python3 -m scripts.synthetic_validation_demo
```

`SequentialSuite` creates one minimal agent workspace containing only that arm's prepared input. It refuses answer, work and results directories nested inside each other or the repository; a submission matching the active case/arm is stored in an operator results directory, then that workspace is removed before the next arm. The scorer requires all four arms (`team`, `opus`, `astra`, `sol`) for every case and reads the answer keys **only from its separate answer directory**. It reports exact-ID top-1/3/10 counts plus critical-flag matches and omissions. Its report always states `clinical_validation: false`.

Operator steps are protected by a nonblocking per-suite file lock so two prepare/submit/score commands cannot update progress simultaneously. Every completed submission is fingerprinted; scoring rejects a changed output instead of silently using a later edit. These checks protect accidental concurrency and drift, not hostile tampering by an OS user who can rewrite both outputs and progress. Older prototype runs without submission fingerprints need a new run; they are not retroactively validated by the current scorer.

**The demo's team and model submissions are simulated fixed strings**, not LLM outputs; the scores say nothing about diagnostic accuracy. Directory separation is not an OS sandbox: before running published or real health cases, isolate each agent in a container/different user with no ability to traverse into answer or result directories, freeze input/output manifests, verify licensed/de-identified source material, define clinician-adjudicated labels and scoring rubrics, and implement actual diagnostic arms. No release decision can be based on this scaffold.

### Actual model-backed fictional pilot

Install the complete runner dependencies (`python -m pip install -r requirements.txt`); this includes YAML registry resolution alongside intake tooling.

`validation/model_runner.py` drives real OpenCode sessions in the order `team → opus → astra → sol`, each on a fresh fictional-case workspace. Versions resolve from `config/models.yaml`; the `sol` roles now use GPT-6.1 Sol. The team arm is routed through a [durable HTTP evaluation bus](evaluation-mail-bus.md): Opus, Astra and Sol first rank independently; they receive anonymous peer opinions for critique; each may send one targeted colleague question by `eval_question`. Replies arrive in a bounded answer round, then each peer submits a final position before the separate Opus Lead synthesizes and records dissent. The bus preserves original positions and threads; this remains a bounded-round panel, **not the full clinical specialist/research/review team**. All arms receive identical fictional input and no web/file access; only peers have the phase-gated question tool. Prompts, session IDs, transcript exports and model assignments stay in the operator-only audit directory. Do not infer clinical quality from panel mechanics or compare historical versions as though models were unchanged.

```bash
# Requires configured OpenCode Anthropic and OpenAI credentials; fictional data only.
.venv/bin/python -m scripts.synthetic_model_eval_demo --demo-shared-auth --timeout 300
```

On the one **invented BLUE/SQUARE marker case**, all four actual model arms returned the matching fictional code. The report deliberately says `clinical_validation: false` and `synthetic_model_backed_operational_test`; it is not a solved medical case. The panel's blind and critique rounds are visible in `operator-results/audit/`. The OpenCode provider credential symlink used by this demo is removed after each arm and is not acceptable for real health information. Separating directories on the same OS user account is also **not a security sandbox**.

After the Sol registry upgrade to GPT-6.1, the first fresh-profile attempt exposed a model-catalog mismatch and was not scored. After explicit verified-model registration was added, a new run completed all panel question/reply/final jobs and all baselines. Historical GPT-6 runs and the failed migration attempt remain distinct from GPT-6.1 results. Public literature retrieval is a [separate source-capture stage](evidence-library.md), not access given only to the team arm; this comparison remains closed-book for all arms.

### Published case discovery (not yet a benchmark)

`validation/curation.py` can retrieve metadata and license URLs for a specifically named PMCID from the Europe PMC REST API; it writes **only to scorer-owned storage outside this repository**. It never exports an answer-bearing article title or full text to a model input. For example:

```bash
python3 -m validation.curation --pmcid PMC12587510 --output '/private/scorer/candidate-metadata.json'
```

This is only a **candidate**. A qualified reviewer still needs to verify patient adulthood, license and de-identification, select a diagnostic cutoff *before* answer-revealing tests, prepare an answer-free case packet, adjudicate the reference diagnoses and acceptable aliases, assess model memorization risk, and freeze a held-out set. The current runner intentionally accepts only `SYNTH-*` case IDs, so published cases cannot accidentally be scored as validated outcomes. No clinical release decision should use synthetic scores or unreviewed article metadata.

The [published-case preparation workflow](published-case-preparation.md) now implements archived paragraph selection, separated packet/reference files, frozen hashes and an explicit independent clinical eligibility record. Its one prepared example is an **unreviewed draft**, not an accepted validation case. Exported `VAL-*` cases still cannot execute through the synthetic runner.
