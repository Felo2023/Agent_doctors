# Case Runbook Template

Copy this template into `runbooks/private/<case-id>.md` for a real patient. Store the corresponding profile under `cases/private/<case-id>/case.yaml`. Never add patient facts to this template.

## Identity and intent

- Case ID and kind:
- Profile path:
- Clinical question and intended use:
- Age at intake, reference date, and source:
- Other demographics, sources, and unknowns:
- Requested source/report languages:
- Source roots (private paths only):

## Data and operational choices

- Providers and model configuration version:
- Provider data handling/retention review status:
- Patient authorization/data-sharing status:
- Local storage, retention, and backup choices:
- Workspace and transcript locations:

## Prerequisites and progress

| Item | Status | Evidence / date |
|---|---|---|
| Clinical question collected | Pending | |
| History and medication list collected | Pending | |
| Independent extraction reconciled | Pending | |
| Patient/family record review | Pending | |
| Model IDs/settings verified | Pending | |
| Sequential validation reviewed | Pending | |
| Real-case diagnostic execution authorized after evaluation | Pending | |
| Diagnostic synthesis and independent review | Pending | |
| Confirmation/treatment/prognosis dossiers | Pending | |
| Requested report languages and equivalence review | Pending | |
| Clinician feedback | Pending | |

## Run history

Record run ID, case snapshot, source hashes, model IDs/settings, prompt/config version, dates, errors, and report paths. Link independent reassessments rather than overwriting prior positions.

## Follow-up

Record new findings, source provenance, dates, clinician feedback, unresolved questions, and which prior conclusions need reassessment. Do not treat a model's suggestion as a confirmed clinical fact.
