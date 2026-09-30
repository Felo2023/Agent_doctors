# Multi-Agent Clinical Research System for OpenCode

> **Historical specification — superseded.** See [docs/architecture.md](docs/architecture.md), [model assignments](docs/model-assignments.md), and [validation protocol](docs/validation.md). This file is retained for reference; its case-specific scope, model policy, and implementation assumptions are not authoritative.

## 1. Purpose

This project defines a multi-agent **medical research and diagnostic hypothesis-generation system** implemented with OpenCode.

The system is intended for **research, experimentation, and software architecture evaluation**. It is not a clinical decision-support system and must not be treated as a substitute for qualified medical professionals.

The central idea is:

> Every "doctor" is an independent OpenCode TUI agent with its own role, system instructions, unique skills, and HTTP endpoint. A Lead Doctor orchestrates the agents, while a communication hub routes structured messages and maintains the canonical case state.

The architecture should allow:
- Opening and talking directly to any specialist agent.
- The Lead Doctor to programmatically consult specialists.
- Specialists to communicate through controlled HTTP messages.
- Multiple specialists to debate conflicting hypotheses.
- A dedicated Cancer Department / Tumor Board.
- A Diagnostic Review Board for contradiction, rare-disease, and safety analysis.
- Persistent case state with versioning and an audit trail.
- Each agent to have unique domain-specific skills rather than a generic "doctor" prompt.

---

# 2. High-Level Architecture

```text
                         ┌──────────────────────┐
                         │      USER / TUI      │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │     LEAD DOCTOR      │
                         │   Orchestrator/TUI   │
                         │      :3001           │
                         └──────────┬───────────┘
                                    │
                     ┌──────────────┼──────────────┐
                     │              │              │
                     ▼              ▼              ▼
             ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
             │ CASE / HTTP │ │   MESSAGE   │ │   CASE      │
             │  COORDINATOR│ │    ROUTER   │ │    STORE    │
             └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          │                         │                         │
          ▼                         ▼                         ▼
 ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
 │ CORE MEDICINE    │      │ SPECIALTIES      │      │ DIAGNOSTICS      │
 │                  │      │                  │      │                  │
 │ Internist :3002  │      │ Neurology :3010  │      │ Radiology :3040  │
 │ Pediatrics :3003  │      │ Cardiology :3011 │      │ Pathology :3041  │
 │ Emergency :3004  │      │ Nephrology :3012 │      │ Lab Med :3042     │
 │ Family Med :3005 │      │ Urology :3013    │      │ Genetics :3043    │
 │ Geriatrics :3006  │      │ Endocrine :3014  │      │ Molecular :3044   │
 └──────────────────┘      │ Rheumatology ... │      └──────────────────┘
                           └──────────────────┘

                           ┌───────────────────────────┐
                           │       CANCER DEPARTMENT   │
                           │                           │
                           │ Oncology Coordinator      │
                           │ Medical Oncology           │
                           │ Surgical Oncology          │
                           │ Radiation Oncology         │
                           │ Hematologic Oncology      │
                           │ Neuro-Oncology             │
                           │ Pediatric Oncology        │
                           │ GI Oncology               │
                           │ Breast Oncology           │
                           │ Thoracic Oncology         │
                           │ Urologic Oncology         │
                           │ Gynecologic Oncology      │
                           │ Sarcoma Oncology           │
                           │ Molecular Oncology         │
                           │ Cancer Genetics            │
                           └─────────────┬─────────────┘
                                         │
                                         ▼
                                  ┌───────────────┐
                                  │  TUMOR BOARD  │
                                  └───────────────┘

                           ┌───────────────────────────┐
                           │   DIAGNOSTIC REVIEW BOARD │
                           │                           │
                           │ Differential Auditor      │
                           │ Contradiction Hunter      │
                           │ Rare Disease Specialist   │
                           │ Cognitive Bias Auditor    │
                           │ Safety Auditor             │
                           └───────────────────────────┘
```

---

# 3. Core Design Principles

## 3.1 Independent agents

Each doctor is an independent OpenCode agent/TUI instance.

Each agent has:

- Unique identity.
- Unique system prompt.
- Unique skills.
- Unique responsibilities.
- Dedicated HTTP endpoint.
- Independent conversation/session.
- Structured input/output contract.
- Ability to be queried directly by the user.
- Ability to receive programmatic consultation requests.

Do **not** reduce the system to one giant prompt containing all specialties.

---

## 3.2 Centralized communication

Agents should not create an uncontrolled mesh where every agent can arbitrarily call every other agent.

Preferred architecture:

```text
Agent → HTTP Message Hub → Agent
```

rather than:

```text
Agent A ─────► Agent B
   │              │
   ├────► Agent C │
   │              └────► Agent D
   └──────────────► Agent E
```

The Message Hub controls:

- Routing.
- Authentication/authorization if required.
- Case IDs.
- Message IDs.
- Correlation IDs.
- Agent discovery.
- Retries.
- Timeouts.
- Logging.
- Case-state versioning.
- Audit trail.

---

# 4. Agent Registry

Create a machine-readable registry such as:

```text
config/
  agents.yaml
```

Example:

```yaml
agents:
  lead-doctor:
    role: clinical_orchestrator
    port: 3001
    department: leadership

  internist:
    role: internal_medicine
    port: 3002
    department: core_medicine

  pediatrician:
    role: pediatrics
    port: 3003
    department: core_medicine

  emergency-medicine:
    role: emergency_medicine
    port: 3004
    department: core_medicine

  neurologist:
    role: neurology
    port: 3010
    department: medical_specialties

  cardiologist:
    role: cardiology
    port: 3011
    department: medical_specialties

  nephrologist:
    role: nephrology
    port: 3012
    department: medical_specialties

  urologist:
    role: urology
    port: 3013
    department: medical_specialties

  endocrinologist:
    role: endocrinology
    port: 3014
    department: medical_specialties

  rheumatologist:
    role: rheumatology
    port: 3015
    department: medical_specialties

  hematologist:
    role: hematology
    port: 3016
    department: medical_specialties

  pulmonologist:
    role: pulmonology
    port: 3017
    department: medical_specialties

  gastroenterologist:
    role: gastroenterology
    port: 3018
    department: medical_specialties

  infectious-disease:
    role: infectious_disease
    port: 3019
    department: medical_specialties

  dermatologist:
    role: dermatology
    port: 3020
    department: medical_specialties

  ophthalmologist:
    role: ophthalmology
    port: 3021
    department: medical_specialties

  ent:
    role: otolaryngology
    port: 3022
    department: medical_specialties

  obgyn:
    role: obstetrics_gynecology
    port: 3023
    department: medical_specialties

  psychiatrist:
    role: psychiatry
    port: 3024
    department: medical_specialties

  medical-oncologist:
    role: medical_oncology
    port: 3030
    department: cancer

  surgical-oncologist:
    role: surgical_oncology
    port: 3031
    department: cancer

  radiation-oncologist:
    role: radiation_oncology
    port: 3032
    department: cancer

  hematologic-oncologist:
    role: hematologic_oncology
    port: 3033
    department: cancer

  neuro-oncologist:
    role: neuro_oncology
    port: 3034
    department: cancer

  pediatric-oncologist:
    role: pediatric_oncology
    port: 3035
    department: cancer

  gynecologic-oncologist:
    role: gynecologic_oncology
    port: 3036
    department: cancer

  gi-oncologist:
    role: gi_oncology
    port: 3037
    department: cancer

  breast-oncologist:
    role: breast_oncology
    port: 3038
    department: cancer

  thoracic-oncologist:
    role: thoracic_oncology
    port: 3039
    department: cancer

  urologic-oncologist:
    role: urologic_oncology
    port: 3045
    department: cancer

  sarcoma-oncologist:
    role: sarcoma_oncology
    port: 3046
    department: cancer

  molecular-oncologist:
    role: molecular_oncology
    port: 3047
    department: cancer

  cancer-geneticist:
    role: cancer_genetics
    port: 3048
    department: cancer

  radiologist:
    role: radiology
    port: 3050
    department: diagnostics

  pathologist:
    role: pathology
    port: 3051
    department: diagnostics

  laboratory-medicine:
    role: laboratory_medicine
    port: 3052
    department: diagnostics

  clinical-geneticist:
    role: clinical_genetics
    port: 3053
    department: diagnostics

  molecular-diagnostics:
    role: molecular_diagnostics
    port: 3054
    department: diagnostics

  clinical-pharmacologist:
    role: clinical_pharmacology
    port: 3060
    department: medication

  evidence-researcher:
    role: medical_evidence
    port: 3070
    department: evidence

  clinical-trial-researcher:
    role: clinical_trials
    port: 3071
    department: evidence

  differential-auditor:
    role: differential_diagnosis_audit
    port: 3080
    department: diagnostic_review

  contradiction-hunter:
    role: contradiction_analysis
    port: 3081
    department: diagnostic_review

  rare-disease-specialist:
    role: rare_disease
    port: 3082
    department: diagnostic_review

  cognitive-bias-auditor:
    role: cognitive_bias_audit
    port: 3083
    department: diagnostic_review

  safety-auditor:
    role: safety_audit
    port: 3084
    department: diagnostic_review

  tumor-board:
    role: oncology_multidisciplinary_board
    port: 3090
    department: cancer
```

The exact ports are implementation choices. Keep them configurable rather than hard-coded throughout the project.

---

# 5. Complete Agent Hierarchy

```text
VIRTUAL CLINICAL RESEARCH SYSTEM
│
├── 1. CLINICAL LEADERSHIP
│   ├── Lead Doctor / Clinical Orchestrator
│   ├── Internist / Internal Medicine
│   └── Emergency Medicine / Critical Care
│
├── 2. CORE MEDICINE
│   ├── Pediatrician
│   ├── Family Medicine
│   └── Geriatrics
│
├── 3. MEDICAL SPECIALTIES
│   ├── Neurology
│   ├── Cardiology
│   ├── Pulmonology
│   ├── Gastroenterology
│   ├── Nephrology
│   ├── Urology
│   ├── Endocrinology
│   ├── Rheumatology
│   ├── Hematology
│   ├── Infectious Disease
│   ├── Dermatology
│   ├── Ophthalmology
│   ├── ENT
│   ├── OB/GYN
│   └── Psychiatry
│
├── 4. CANCER DEPARTMENT
│   ├── Oncology Coordinator
│   ├── Medical Oncology
│   ├── Surgical Oncology
│   ├── Radiation Oncology
│   ├── Hematologic Oncology
│   ├── Neuro-Oncology
│   ├── Pediatric Oncology
│   ├── Gynecologic Oncology
│   ├── GI Oncology
│   ├── Breast Oncology
│   ├── Thoracic Oncology
│   ├── Urologic Oncology
│   ├── Sarcoma Oncology
│   ├── Molecular Oncology
│   └── Cancer Genetics
│
├── 5. DIAGNOSTIC SCIENCES
│   ├── Radiology
│   ├── Pathology
│   ├── Laboratory Medicine
│   ├── Clinical Genetics
│   └── Molecular Diagnostics
│
├── 6. MEDICATION / PHARMACOLOGY
│   ├── Clinical Pharmacologist
│   └── Medication Review
│
├── 7. MEDICAL EVIDENCE
│   ├── Evidence Researcher
│   └── Clinical Trial Researcher
│
└── 8. DIAGNOSTIC REVIEW BOARD
    ├── Differential Diagnosis Auditor
    ├── Contradiction Hunter
    ├── Rare Disease Specialist
    ├── Cognitive Bias Auditor
    └── Safety Auditor
```

---

# 6. Skills

Each agent should have a shared base skill set plus specialty-specific skills.

Suggested structure:

```text
skills/
├── shared/
│   ├── case-comprehension
│   ├── clinical-reasoning
│   ├── differential-diagnosis
│   ├── evidence-evaluation
│   ├── uncertainty-management
│   ├── red-flag-detection
│   ├── structured-reporting
│   └── inter-agent-communication
│
├── internist/
│   ├── adult-medicine
│   ├── multisystem-differential
│   ├── complex-case-reasoning
│   ├── systemic-disease-recognition
│   ├── comorbidity-analysis
│   └── medication-review
│
├── neurologist/
│   ├── neuroanatomical-localization
│   ├── neurological-examination
│   ├── seizure-analysis
│   ├── headache-analysis
│   ├── movement-disorders
│   ├── neuromuscular-disease
│   └── neuroimmunology
│
├── cardiologist/
│   ├── ecg-analysis
│   ├── arrhythmia-analysis
│   ├── chest-pain-analysis
│   ├── syncope-analysis
│   ├── heart-failure
│   ├── cardiomyopathy
│   └── cardiovascular-risk
│
├── nephrologist/
│   ├── renal-function
│   ├── urinalysis
│   ├── electrolyte-analysis
│   ├── acid-base-analysis
│   ├── aki-analysis
│   ├── ckd-analysis
│   └── glomerular-disease
│
├── urologist/
│   ├── urinary-tract
│   ├── urolithiasis
│   ├── urinary-obstruction
│   ├── bladder-disease
│   ├── prostate
│   ├── male-reproductive-system
│   └── urologic-imaging
│
├── endocrinologist/
│   ├── thyroid
│   ├── diabetes
│   ├── adrenal
│   ├── pituitary
│   ├── parathyroid
│   ├── calcium-disorders
│   └── metabolic-disease
│
├── rheumatologist/
│   ├── autoimmune-disease
│   ├── inflammatory-disease
│   ├── connective-tissue-disease
│   ├── vasculitis
│   ├── arthritis
│   └── autoantibody-analysis
│
├── hematologist/
│   ├── cbc-analysis
│   ├── anemia
│   ├── leukocyte-disorders
│   ├── platelet-disorders
│   ├── coagulation
│   ├── thrombosis
│   └── bone-marrow-disorders
│
├── infectious-disease/
│   ├── infectious-pattern-recognition
│   ├── bacterial-disease
│   ├── viral-disease
│   ├── fungal-disease
│   ├── parasitic-disease
│   ├── fever-of-unknown-origin
│   └── antimicrobial-reasoning
│
├── pulmonologist/
│   ├── dyspnea-analysis
│   ├── respiratory-physiology
│   ├── asthma
│   ├── copd
│   ├── pneumonia
│   ├── interstitial-lung-disease
│   └── pulmonary-hypertension
│
├── dermatologist/
│   ├── rash-analysis
│   ├── lesion-analysis
│   ├── inflammatory-skin-disease
│   ├── skin-infection
│   ├── drug-eruption
│   └── skin-cancer
│
├── ophthalmologist/
│   ├── visual-symptoms
│   ├── retinal-disease
│   ├── optic-nerve
│   ├── glaucoma
│   ├── ocular-inflammation
│   └── systemic-ocular-manifestations
│
├── ent/
│   ├── ear-disease
│   ├── hearing
│   ├── vertigo
│   ├── sinus-disease
│   ├── throat
│   ├── voice
│   └── head-neck-disease
│
├── obgyn/
│   ├── gynecologic-history
│   ├── menstrual-disorders
│   ├── pregnancy
│   ├── pelvic-disease
│   ├── reproductive-endocrinology
│   └── gynecologic-malignancy
│
├── psychiatry/
│   ├── psychiatric-differential
│   ├── neuropsychiatric-differential
│   ├── medication-effects
│   ├── substance-related-disease
│   └── medical-vs-psychiatric-differential
│
├── oncology/
│   ├── cancer-differential
│   ├── tumor-biology
│   ├── staging
│   ├── metastatic-disease
│   ├── treatment-modalities
│   ├── biomarkers
│   └── tumor-board-reasoning
│
├── pathology/
│   ├── histology
│   ├── cytology
│   ├── biopsy-analysis
│   ├── tumor-classification
│   ├── immunohistochemistry
│   └── pathology-differential
│
├── radiology/
│   ├── xray
│   ├── ct
│   ├── mri
│   ├── ultrasound
│   ├── pet
│   ├── lesion-characterization
│   └── imaging-differential
│
├── clinical-genetics/
│   ├── phenotype-analysis
│   ├── pedigree-analysis
│   ├── inheritance-patterns
│   ├── syndrome-recognition
│   └── variant-interpretation
│
├── pharmacology/
│   ├── drug-interactions
│   ├── adverse-drug-reactions
│   ├── drug-toxicity
│   ├── pharmacokinetics
│   └── pharmacodynamics
│
└── diagnostic-review/
    ├── differential-audit
    ├── contradiction-detection
    ├── rare-disease-search
    ├── cognitive-bias-audit
    └── safety-audit
```

---

# 7. Lead Doctor Skills

The Lead Doctor is not simply another specialist.

It is the primary orchestration and synthesis agent.

Required skills:

```text
lead/
├── case-orchestration
├── case-decomposition
├── specialty-selection
├── consultation-management
├── differential-synthesis
├── hypothesis-management
├── evidence-integration
├── contradiction-resolution
├── uncertainty-management
├── information-gap-detection
├── agent-routing
├── debate-management
├── tumor-board-routing
├── diagnostic-review-routing
└── final-research-synthesis
```

The Lead should answer questions such as:

1. What information do we actually have?
2. What information is missing?
3. Which specialties are relevant?
4. Which agents should be consulted first?
5. Which hypotheses are supported?
6. Which hypotheses are contradicted?
7. What evidence would distinguish competing hypotheses?
8. Are two agents disagreeing?
9. Should a debate be initiated?
10. Should the Cancer Department be activated?
11. Should the Diagnostic Review Board challenge the current reasoning?
12. What uncertainty remains?

---

# 8. Common Agent Output Contract

Every doctor should return structured output.

Example:

```json
{
  "agent": "neurologist",
  "case_id": "CASE-001",
  "task_id": "TASK-014",
  "case_understanding": "Brief interpretation of the information provided.",
  "key_findings": [
    "Finding 1",
    "Finding 2"
  ],
  "differential_diagnoses": [
    {
      "diagnosis": "Hypothesis A",
      "supporting_evidence": [],
      "contradicting_evidence": [],
      "missing_information": []
    }
  ],
  "red_flags": [],
  "recommended_next_information": [],
  "confidence": "low",
  "questions_for_other_specialists": [],
  "questions_for_lead": [],
  "evidence": [],
  "limitations": []
}
```

Allowed confidence values:

```text
very_low
low
moderate
high
very_high
```

Confidence must describe confidence in the **research hypothesis**, not certainty about a patient's actual diagnosis.

---

# 9. Inter-Agent Communication Protocol

Use structured HTTP messages.

Example:

```json
{
  "message_id": "MSG-00042",
  "case_id": "CASE-001",
  "task_id": "TASK-014",
  "sender": "lead-doctor",
  "recipient": "neurologist",
  "message_type": "consultation_request",
  "priority": "normal",
  "timestamp": "2026-09-27T00:00:00Z",
  "case_version": 7,
  "content": {
    "question": "Evaluate the neurological differential.",
    "findings": [],
    "current_hypotheses": []
  }
}
```

Response:

```json
{
  "message_id": "MSG-00043",
  "case_id": "CASE-001",
  "task_id": "TASK-014",
  "sender": "neurologist",
  "recipient": "lead-doctor",
  "message_type": "consultation_response",
  "priority": "normal",
  "timestamp": "2026-09-27T00:01:10Z",
  "case_version": 7,
  "content": {
    "key_findings": [],
    "hypotheses": [],
    "supporting_evidence": [],
    "contradicting_evidence": [],
    "missing_information": [],
    "red_flags": []
  },
  "confidence": "moderate"
}
```

---

# 10. Message Types

Minimum protocol:

```text
case_init
case_update
consultation_request
consultation_response
clarification_request
clarification_response
challenge
rebuttal
evidence_update
hypothesis_update
emergency_alert
tumor_board_request
tumor_board_response
diagnostic_review_request
diagnostic_review_response
synthesis_request
synthesis_response
```

---

# 11. Communication Patterns

## 11.1 Standard Consultation

```text
Lead
  │
  ├── consultation_request ──► Specialist
  │
  ◄── consultation_response ──┤
  │
  ▼
Update case model
```

## 11.2 Specialist Debate

```text
Lead
 │
 ├──► Specialist A
 │
 └──► Specialist B

Specialist A ──challenge──► Specialist B

Specialist B ──rebuttal──► Specialist A

Both ──responses──► Lead

Lead
 └──► Diagnostic Auditor
```

The goal is not to create endless agent conversation. The Lead should terminate a debate when:
- The disagreement is resolved.
- Required evidence is identified.
- Further discussion is unlikely to add information.
- The case must move to another specialty.

---

# 12. Cancer Department

Cancer should be treated as a dedicated multidisciplinary department.

```text
CANCER DEPARTMENT
│
├── Oncology Coordinator
│
├── Medical Oncology
├── Surgical Oncology
├── Radiation Oncology
├── Hematologic Oncology
│
├── Disease-Specific Oncology
│   ├── Neuro-Oncology
│   ├── Gynecologic Oncology
│   ├── GI Oncology
│   ├── Breast Oncology
│   ├── Thoracic Oncology
│   ├── Urologic Oncology
│   ├── Pediatric Oncology
│   └── Sarcoma Oncology
│
├── Molecular Oncology
└── Cancer Genetics
```

---

# 13. Tumor Board

The Tumor Board is a multidisciplinary agent/group.

Participants:

```text
Medical Oncologist
Surgical Oncologist
Radiation Oncologist
Pathologist
Radiologist
Molecular Oncologist
Cancer Geneticist
Disease-Specific Oncologist when appropriate
```

Flow:

```text
Lead
  │
  └──► Tumor Board
          │
          ├── Medical Oncology
          ├── Surgery
          ├── Radiation
          ├── Pathology
          ├── Radiology
          ├── Molecular
          └── Genetics
                  │
                  ▼
             Board synthesis
                  │
                  ▼
                 Lead
```

The Tumor Board should only be activated when cancer-related hypotheses, pathology, imaging, molecular findings, or treatment-related research questions justify it.

---

# 14. Diagnostic Review Board

The Diagnostic Review Board exists to challenge the current reasoning.

```text
DIAGNOSTIC REVIEW BOARD
│
├── Differential Diagnosis Auditor
├── Contradiction Hunter
├── Rare Disease Specialist
├── Cognitive Bias Auditor
└── Safety Auditor
```

Core questions:

```text
What are we missing?

What evidence contradicts the current hypothesis?

What alternative explanation fits the findings?

Are we anchoring on the first plausible explanation?

Are we assuming causation from correlation?

What information would falsify the current hypothesis?

Could two conditions explain the findings better than one?

Is there a time-critical concern that requires escalation?
```

---

# 15. Case State

The communication system must maintain one canonical case state.

Recommended:

```text
case-store/
├── CASE-001/
│   ├── case.json
│   ├── events.jsonl
│   ├── hypotheses.json
│   ├── consultations.json
│   ├── evidence.json
│   └── summaries/
```

Important principle:

> Agents should not independently maintain competing versions of the patient/case record.

The Case Coordinator owns the canonical version.

Each update increments:

```text
case_version
```

Example:

```text
Version 1
   ↓
Initial history

Version 2
   ↓
Laboratory information added

Version 3
   ↓
Radiology information added

Version 4
   ↓
Neurology consultation

Version 5
   ↓
Contradiction detected
```

---

# 16. Event Log

Every significant communication should be recorded.

Example:

```json
{
  "event_id": "EVT-00128",
  "case_id": "CASE-001",
  "case_version": 12,
  "event_type": "consultation_response",
  "agent": "nephrologist",
  "timestamp": "2026-09-27T00:20:00Z",
  "summary": "Renal differential updated.",
  "message_id": "MSG-00092"
}
```

This provides reproducibility and makes it possible to inspect how the system reached a research hypothesis.

---

# 17. Suggested Project Structure

```text
clinical-agent-system/
│
├── README.md
├── ARCHITECTURE.md
├── agents/
│   ├── lead-doctor/
│   │   ├── agent.md
│   │   └── skills/
│   │
│   ├── internist/
│   ├── pediatrician/
│   ├── emergency-medicine/
│   ├── neurologist/
│   ├── cardiologist/
│   ├── nephrologist/
│   ├── urologist/
│   ├── endocrinologist/
│   ├── rheumatologist/
│   ├── hematologist/
│   ├── pulmonologist/
│   ├── gastroenterologist/
│   ├── infectious-disease/
│   ├── dermatologist/
│   ├── ophthalmologist/
│   ├── ent/
│   ├── obgyn/
│   ├── psychiatrist/
│   │
│   ├── oncology/
│   │   ├── medical-oncologist/
│   │   ├── surgical-oncologist/
│   │   ├── radiation-oncologist/
│   │   ├── hematologic-oncologist/
│   │   ├── neuro-oncologist/
│   │   ├── pediatric-oncologist/
│   │   ├── gynecologic-oncologist/
│   │   ├── gi-oncologist/
│   │   ├── breast-oncologist/
│   │   ├── thoracic-oncologist/
│   │   ├── urologic-oncologist/
│   │   ├── sarcoma-oncologist/
│   │   ├── molecular-oncologist/
│   │   └── cancer-geneticist/
│   │
│   ├── radiologist/
│   ├── pathologist/
│   ├── laboratory-medicine/
│   ├── clinical-geneticist/
│   ├── molecular-diagnostics/
│   ├── clinical-pharmacologist/
│   ├── evidence-researcher/
│   ├── clinical-trial-researcher/
│   │
│   └── diagnostic-review/
│       ├── differential-auditor/
│       ├── contradiction-hunter/
│       ├── rare-disease-specialist/
│       ├── cognitive-bias-auditor/
│       └── safety-auditor/
│
├── skills/
│   ├── shared/
│   ├── internal-medicine/
│   ├── neurology/
│   ├── cardiology/
│   ├── nephrology/
│   ├── oncology/
│   ├── pathology/
│   ├── radiology/
│   ├── genetics/
│   ├── pharmacology/
│   └── diagnostic-review/
│
├── communication/
│   ├── protocol.md
│   ├── schemas/
│   │   ├── message.schema.json
│   │   ├── consultation.schema.json
│   │   └── case.schema.json
│   ├── router/
│   └── registry/
│
├── case-store/
│
├── config/
│   ├── agents.yaml
│   ├── routing.yaml
│   └── environment.example
│
├── scripts/
│   ├── start-all-agents
│   ├── stop-all-agents
│   ├── health-check
│   └── register-agents
│
└── tests/
    ├── protocol/
    ├── routing/
    ├── agents/
    └── integration/
```

---

# 18. Agent Definition Template

Every agent should have a definition similar to:

```markdown
# Neurologist Agent

## Identity

You are the Neurologist agent in a multi-agent medical research system.

## Mission

Analyze neurological aspects of the case and provide structured diagnostic hypotheses.

## Responsibilities

- Neurological localization
- Differential diagnosis
- Identification of neurological red flags
- Interpretation of neurological findings
- Identification of missing neurological information
- Challenge unsupported neurological assumptions

## Unique Skills

- neuroanatomical-localization
- neurological-examination
- seizure-analysis
- headache-analysis
- movement-disorders
- neuromuscular-disease
- neuroimmunology

## Communication

You communicate through the system's HTTP message protocol.

Do not silently modify the canonical case.

Return structured responses.

## Output

Return the common consultation schema.

## Research Behavior

- Separate observations from hypotheses.
- Explicitly state uncertainty.
- Distinguish supporting from contradicting evidence.
- State what evidence would change the conclusion.
- Do not manufacture patient information.
```

Each agent should have the same basic contract but a different domain identity and skill set.

---

# 19. Routing Logic

The Lead should dynamically select specialists rather than calling every agent for every case.

Example:

```text
Case arrives
    │
    ▼
Lead analyzes case
    │
    ├── neurological findings?
    │       └──► Neurologist
    │
    ├── renal findings?
    │       └──► Nephrologist
    │
    ├── abnormal imaging?
    │       └──► Radiologist
    │
    ├── biopsy/pathology?
    │       └──► Pathologist
    │
    ├── cancer hypothesis?
    │       └──► Cancer Department
    │
    └── unclear multisystem case?
            └──► Internist
```

The Lead can start with a small set of agents and expand the consultation graph as new findings appear.

---

# 20. Evidence Research Agent

The Evidence Researcher is not a doctor.

Its job is evidence retrieval and verification.

Skills:

```text
literature-search
guideline-search
systematic-review-search
meta-analysis-search
clinical-trial-search
citation-verification
source-quality-assessment
evidence-hierarchy
```

Potential output:

```json
{
  "question": "Research question",
  "sources": [
    {
      "title": "Source",
      "source_type": "guideline",
      "date": "YYYY-MM-DD",
      "relevance": "high",
      "key_points": [],
      "limitations": []
    }
  ]
}
```

Every factual claim retrieved from external literature should preserve its source.

---

# 21. Safety and Uncertainty

This is a research system.

Agents should never present generated hypotheses as established diagnoses.

Use explicit distinctions:

```text
OBSERVATION
What is directly contained in the case.

INTERPRETATION
What the agent believes the observation may indicate.

HYPOTHESIS
A possible explanation.

EVIDENCE
Information supporting or contradicting the hypothesis.

UNCERTAINTY
What remains unknown.

NEXT INFORMATION
What additional evidence could discriminate between hypotheses.
```

Avoid:

```text
"This patient definitely has X."
```

Prefer:

```text
"X is a hypothesis worth investigating because A and B support it,
while C is contradictory or currently unknown."
```

---

# 22. Emergency / Safety Channel

Safety messages should bypass normal low-priority routing.

Example:

```json
{
  "message_type": "emergency_alert",
  "priority": "critical",
  "case_id": "CASE-001",
  "sender": "safety-auditor",
  "recipient": "lead-doctor",
  "content": {
    "issue": "Potential time-critical finding",
    "evidence": [],
    "uncertainty": "moderate"
  }
}
```

The Lead should receive critical messages before ordinary consultation traffic.

---

# 23. Direct TUI Usage

The architecture must preserve independent human access.

Example:

```text
Terminal 1
└── Lead Doctor TUI

Terminal 2
└── Neurologist TUI

Terminal 3
└── Nephrologist TUI

Terminal 4
└── Oncologist TUI

Terminal 5
└── Pathologist TUI
```

The user should be able to interact directly with any agent.

At the same time:

```text
Lead → HTTP → Neurologist
Lead → HTTP → Nephrologist
Lead → HTTP → Pathologist
```

can occur without requiring the user to manually switch terminals.

---

# 24. Agent Lifecycle

Suggested lifecycle:

```text
START
  │
  ▼
Load configuration
  │
  ▼
Load agent identity
  │
  ▼
Load unique skills
  │
  ▼
Start OpenCode HTTP interface
  │
  ▼
Register with Message Hub
  │
  ▼
HEALTHY
  │
  ├── receive consultation
  ├── respond
  ├── receive challenge
  ├── send evidence update
  └── heartbeat
  │
  ▼
SHUTDOWN
  │
  ▼
Unregister
```

---

# 25. Health Checks

Every agent should expose or participate in a health mechanism.

Example conceptual response:

```json
{
  "agent": "neurologist",
  "status": "healthy",
  "version": "1.0.0",
  "skills": [
    "neuroanatomical-localization",
    "seizure-analysis"
  ],
  "active_cases": 2
}
```

The Message Hub should know whether an agent is:

```text
healthy
busy
degraded
offline
```

If a specialist is offline, the Lead should know that the consultation was not completed rather than assuming silence means agreement.

---

# 26. Preventing Agent Hallucination

Every agent should follow:

```text
1. Never invent findings.
2. Never invent laboratory values.
3. Never invent imaging results.
4. Never invent pathology.
5. Never invent citations.
6. Separate known information from assumptions.
7. Identify missing information.
8. State uncertainty.
9. Cite external evidence when evidence retrieval is used.
10. Ask for clarification when required information is ambiguous.
```

---

# 27. Preventing Groupthink

The system should deliberately create disagreement when appropriate.

Useful mechanisms:

```text
Lead hypothesis
      │
      ├── Specialist supporting hypothesis
      │
      ├── Specialist challenging hypothesis
      │
      └── Diagnostic Auditor attempting to falsify hypothesis
```

The Contradiction Hunter should specifically search for:

```text
- Evidence incompatible with the current hypothesis
- Alternative explanations
- Temporal inconsistencies
- Anatomical inconsistencies
- Laboratory inconsistencies
- Imaging inconsistencies
- Medication-related explanations
- Multiple-condition explanations
```

---

# 28. Research Evaluation

The system should be evaluated as an AI-agent architecture.

Potential metrics:

```text
Diagnostic hypothesis coverage
Alternative hypothesis coverage
Contradiction detection rate
Evidence citation accuracy
Citation relevance
Calibration of confidence
Specialty routing efficiency
Number of unnecessary consultations
Token usage
Latency
HTTP message volume
Debate termination efficiency
Case-state consistency
Reproducibility
```

For benchmark testing, use de-identified or synthetic cases whenever possible.

---

# 29. Recommended Initial Implementation

Do not implement all agents simultaneously.

Phase 1:

```text
Lead Doctor
Internist
Neurologist
Cardiologist
Nephrologist
Radiologist
Pathologist
Evidence Researcher
Differential Auditor
Message Hub
Case Store
```

Phase 2:

```text
Urologist
Endocrinologist
Rheumatologist
Hematologist
Pulmonologist
Infectious Disease
Gastroenterologist
Clinical Pharmacologist
```

Phase 3:

```text
Cancer Department
Tumor Board
Molecular Oncology
Cancer Genetics
Clinical Genetics
Rare Disease Specialist
```

Phase 4:

```text
Additional disease-specific specialists
Advanced debate routing
Automated evaluation
Benchmark suite
Observability
Performance optimization
```

This creates a useful working system before expanding to dozens of independent agents.

---

# 30. Important Architectural Rule

The system should distinguish between:

```text
AGENTS
    = reasoning specialists

MESSAGE HUB
    = communication and routing

CASE STORE
    = canonical state

LEAD
    = orchestration and synthesis

EVIDENCE AGENT
    = external research

AUDIT AGENTS
    = challenge and error detection
```

Do not make the Lead responsible for everything.

The architecture should remain modular so that the Lead can be replaced or upgraded without rebuilding the specialist agents.

---

# 31. Desired End State

The final system should behave conceptually like:

```text
USER
 │
 ▼
LEAD DOCTOR
 │
 ├── understands case
 │
 ├── identifies missing information
 │
 ├── selects specialists
 │
 ├── sends HTTP consultations
 │
 ├── receives structured responses
 │
 ├── updates case model
 │
 ├── detects disagreement
 │
 ├── starts specialist debate when useful
 │
 ├── activates Cancer/Tumor Board when appropriate
 │
 ├── activates Diagnostic Review Board
 │
 ├── asks Evidence Researcher to verify important claims
 │
 └── produces final research synthesis
             │
             ▼
      STRUCTURED CASE REPORT
```

The final report should contain:

```text
1. Case summary
2. Established observations
3. Major hypotheses
4. Evidence supporting each hypothesis
5. Evidence contradicting each hypothesis
6. Missing information
7. Specialist opinions
8. Areas of disagreement
9. Evidence quality
10. Safety/red-flag findings
11. Suggested research questions / next information
12. Uncertainty and limitations
13. Full audit trail
```

---

# 32. Instructions to the OpenCode Implementation Agent

When implementing this architecture:

1. First inspect the current OpenCode version and its official HTTP/API capabilities.
2. Do not assume undocumented OpenCode endpoints.
3. Determine exactly how an OpenCode TUI agent can be started, addressed, and queried programmatically.
4. Determine whether each agent should use a separate OpenCode process, session, server, or another supported mechanism.
5. Build the smallest working prototype first.
6. Implement the Message Hub before creating dozens of agents.
7. Implement the common message schema.
8. Implement the agent registry.
9. Implement the canonical case store.
10. Implement Lead → Specialist consultation.
11. Implement Specialist → Lead response.
12. Implement one specialist-to-specialist challenge/rebuttal flow.
13. Add health checks and timeouts.
14. Add structured logging.
15. Only then scale to the full specialist tree.
16. Keep every agent's skills modular and independently editable.
17. Keep all case information versioned.
18. Never silently merge conflicting case versions.
19. Preserve source citations for external evidence.
20. Keep the system explicitly positioned as a research / hypothesis-generation environment.

---

# 33. First Prototype Target

The first end-to-end demonstration should be:

```text
User
 │
 ▼
Lead Doctor TUI
 │
 ├── HTTP consultation → Internist
 │
 ├── HTTP consultation → Neurologist
 │
 └── HTTP consultation → Radiologist
 │
 ▼
Message Hub
 │
 ▼
Responses collected
 │
 ▼
Case Store updated
 │
 ▼
Lead synthesizes
 │
 ▼
Differential Auditor challenges synthesis
 │
 ▼
Lead produces final research report
```

Once this works reliably, expand the architecture.

---

# 34. Final Principle

The goal is not to make one LLM pretend to be many doctors.

The goal is to create a **real multi-agent software architecture** in which:

```text
Different agents
        +
Different skills
        +
Different perspectives
        +
Structured communication
        +
Shared versioned case state
        +
Evidence retrieval
        +
Adversarial review
        =
A research-oriented multi-agent clinical reasoning laboratory
```

The architecture should remain observable, reproducible, modular, testable, and easy to extend.
