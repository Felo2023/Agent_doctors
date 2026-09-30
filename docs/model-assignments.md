# Model Assignments

## Policy

Current models: **Anthropic Claude Opus 5.5**, **OpenAI Astra (GPT-6)**, and **OpenAI GPT-6.1 Sol**. The Sol upgrade was requested by the user and verified with a synthetic prompt. OpenCode currently lists no GPT-6.1 Astra variant; Astra is retained explicitly rather than guessing an ID or substituting another model.

`config/models.yaml` records aliases `opus`, `astra`, and `sol`. On 2026-09-28, the installed OpenCode 1.18.33 listed and successfully prompted `anthropic/claude-opus-5-5`, `openai/gpt-6-astra`, and `openai/gpt-6-sol` with synthetic text. This establishes local text-prompt access, **not** clinical performance, raw PDF capability, or compatibility with untested reasoning settings. See [Phase 0 findings](compatibility-spike.md). The future launcher must fail clearly if an ID is unresolved or unavailable. Never silently downgrade or substitute a model.

On 2026-09-30 UTC, `openai/gpt-6.1-sol` answered the synthetic access check successfully and replaced GPT-6 Sol for **every `sol` role**, including evidence, pharmacology, reconciliation, safety, and specialty roles. `runtime/models.py` reads exact IDs from the registry for executable prototypes, avoiding separate hard-coded versions. This model/configuration change requires fresh evaluation; old GPT-6 scores are historical, not GPT-6.1 results.

The first isolated-profile panel attempt failed because its OpenCode catalog did not include the new model. The registry now includes the new model's **observed OpenCode metadata** and each isolated process supplies that exact definition through supported native provider configuration; no model ID is substituted. A fresh fictional panel and all three baselines subsequently completed, including GPT-6.1 Sol's colleague-question tool call and structured replies. This verifies operational access, not clinical competence. Model availability is checked in the isolated process before generation to avoid silently waiting on an unresolvable ID.

Validate reasoning-effort options, supported inputs, context limits, and structured-output behavior per model. Do not assume a shared temperature or “maximum thinking” parameter works across providers. Record resolved IDs and supported settings in each run manifest.

The assignments below are experimental defaults for independent perspectives and cross-provider review. They are **not clinical performance claims**. Validation may support changing them, using a held-out set to avoid tuning to the answers.

## Core roles

| Role | Alias / requested model | Activation |
|---|---|---|
| Lead | opus / Opus 5.5 | Coordination and synthesis |
| Records Abstractor A | opus / Opus 5.5 | P1 |
| Records Abstractor B | astra / Astra | P1, independent of A |
| Records Reconciler | sol / GPT-6.1 Sol | P1, consult original pages |
| History Interviewer | opus / Opus 5.5 | P0 and clarification |
| Diagnostician: most likely | opus / Opus 5.5 | P3–P7 |
| Diagnostician: unifying | astra / Astra | P3–P7 |
| Diagnostician: multiple conditions | sol / GPT-6.1 Sol | P3–P7 |
| Internist | astra / Astra | P3–P8 as needed |
| Clinical Pharmacologist | sol / GPT-6.1 Sol | P3–P8 as needed |
| Test Strategist | astra / Astra | P3–P8 |
| Evidence Researcher | sol / GPT-6.1 Sol | Scoped retrieval throughout |
| Contradiction Hunter | astra / Astra | P4/P7/P8 review |
| Cognitive Bias Auditor | sol / GPT-6.1 Sol | P7 |
| Rare Disease Specialist | opus / Opus 5.5 | P3/P7 as relevant |
| Safety Auditor | sol / GPT-6.1 Sol | All phases |
| Treatment Specialist | opus / Opus 5.5 | P8 |
| Prognosis Analyst | astra / Astra | P8 |
| Treatment Safety Reviewer | sol / GPT-6.1 Sol | P8 |
| Report Translator | opus / Opus 5.5 | P9; Astra checks clinical equivalence |

## Specialist pool

| Alias | Roles |
|---|---|
| opus | Rheumatology, allergy/immunology, infectious disease, dermatology, OB/GYN, psychiatry, clinical genetics, diagnostic oncology, medical oncology, palliative care, pediatrics, geriatric medicine |
| astra | Neurology, cardiology, pulmonology, ophthalmology, ENT, sleep medicine, vascular medicine, radiology-report review, surgical oncology, general surgery, interventional radiology |
| sol | Nephrology, urology, endocrinology, hematology, gastroenterology, hepatology, toxicology, metabolic genetics, laboratory medicine, pathology-report review, hematologic oncology, cancer genetics, radiation oncology, transplant, rehabilitation |

The machine-readable source of assignments is [`../config/agents.yaml`](../config/agents.yaml). All assignments are case-independent. Selection of specialties is based on case findings and relevant patient context.

### Activation labels and reviewer instances

The launcher interprets `activation` labels as these eligible phases, not a requirement to run continuously:

| Label | Eligible phases |
|---|---|
| coordination | P0–P10 |
| intake | P1, including new-record intake during P10 |
| history | P0, plus explicitly requested clarification in later phases |
| diagnosis | P3–P7 |
| diagnosis_and_treatment | P3–P8 |
| diagnosis_and_confirmation | P3–P8 |
| scoped_retrieval | P0–P10, restricted by phase information boundaries |
| review | P4, P7, P8 |
| relevant_differential_review | P3–P7 when relevant |
| all_phases | P0–P10 |
| treatment, prognosis, treatment_review | P8 |
| report | P9 |

Pool roles lacking an activation label are scheduled only on an explicit Lead request: diagnostic specialists in P3–P8, treatment-pool specialists in P8. Initial eligibility is determined in P2. Follow-up requests invoke the corresponding phase again.

A cross-model review is a **new role instance**, not a model switch inside the original conversation. The launcher creates an ID such as `<role>--review-<n>`, with `base_role`, `review_of`, `review_model_alias`, `context_snapshot_id`, and an independently bound process/session. It inherits the base role's relevant clinical instructions but receives only the authorized review inputs. The registry retains the original and review identities separately.

`equivalence_review_model: astra` specifically creates a `report-translator--review-<n>` instance in P9, bound to Astra and instructed to check clinical equivalence rather than add clinical claims. These temporary reviewer instances are additional to the 20 core role definitions. Model overrides are permitted only for explicitly recorded reviewer instances using aliases from `config/models.yaml`; ordinary roles retain their configured bindings.

## Cross-model checks

- Preserve three independently generated generalist assessments before exchange.
- Lead synthesis receives Astra contradiction review and Sol safety/bias review.
- Important evidence and record disagreements are resolved against source material, not majority vote.
- For a pivotal specialist assertion, create a linked independent review on another model; prefer another provider when useful. Log the selected reviewer and supplied context.
- Bilingual output gets a separate Astra equivalence review; it does not introduce new clinical claims.
- Astra and Sol share a provider and may share failure modes. Different providers can also share training data and errors. Diversity does not establish correctness.

## Verification before enabling runs

1. Inspect installed OpenCode provider/model listings and exact IDs accessible to the account.
2. Bind each alias explicitly, record the verification date and test a synthetic prompt (original models on 2026-09-28; GPT-6.1 Sol on 2026-09-30 UTC, including a fresh isolated panel run). Recheck when credentials/catalog change.
3. Confirm supported settings and source-document handling with synthetic material.
4. Require all configured roles to resolve, or explicitly mark a role unavailable and block workflows that require it.
5. Freeze the model/configuration manifest before held-out validation; log every later change.
