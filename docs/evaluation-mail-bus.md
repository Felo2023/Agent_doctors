# Bus-Mediated Evaluation Panel

`bus/evaluation.py` connects the fictional evaluation panel to a durable localhost HTTP Mail Bus. It implements the **communication/phase-control slice** of the intended team architecture; it is not the final clinical diagnostic team and does not accept real or published patient cases.

## Run

With the configured OpenCode models and the synthetic-only credential option:

```bash
.venv/bin/python -m scripts.synthetic_model_eval_demo --demo-shared-auth --timeout 300
```

One entirely fictional marker case runs sequentially through the `team`, `opus`, `astra`, and `sol` arms. Model versions resolve from `config/models.yaml` (currently Opus 5.5, Astra, GPT-6.1 Sol). All see the same input catalog; web/file tools are denied and only peer processes may use the phase-gated question tool. Each role has its own OpenCode process, TUI, data directory and saved session. The runner drains hidden TUI pseudo-terminals and exports transcripts. The `--demo-shared-auth` symlinks are fictional-only and removed when the arm stops; they remain unsuitable for clinical data.

## Phases enforced by the bus

| Phase | Who gets a job | What the bus supplies |
|---|---|---|
| Setup | Operator only | Bind the case ID/input hash and four distinct role/session tokens |
| Blind | Three peers | Identical case input; no peer positions and no Lead input |
| Critique | Three peers | Same case plus other peers' completed first opinions, without role/model labels |
| Questions | Three peers | Same case and own critique position; one colleague question per peer via `eval_question` |
| Answers | Three peers | Only questions addressed to that recipient; every assigned question ID must receive one reply |
| Final positions | Three peers | Same case and related question/reply threads, for a final source-based position |
| Synthesis | Lead only | Same case, preserved blind opinions, critiques, final positions and complete question threads |
| Closed | No new work | Frozen role positions, job states and event history |

The operator cannot advance while required jobs are unfinished. Authenticated roles can retrieve **only their own current job** and submit a result only for their bound session/job/phase. The Lead has no job in the blind round. A completed position can be acknowledged again with identical data, but cannot be replaced. The bus validates the allowed codes and response fields; this does not establish that model claims are correct.

The `eval_question` tool lets peers send targeted consultation letters directly. The server derives the sender from its token/session binding; the model supplies only the recipient and question. It permits **one question per sender**, rejects self-mail and questions to the Lead, and locks the tool outside the Questions round. Questions are asynchronous: no peer waits for an immediate reply. Answer-round jobs prohibit recursive follow-up, preventing unbounded chains. After replies, final-position jobs give both senders and recipients their related threads before the Lead receives everything.

The first critique round omits role/model labels. Later question exchanges are attributed, so they are not anonymous. This is a single bounded question round, not unrestricted chat; the specialty pool and additional diagnostic/research roles remain future work. Synthesis never turns vote counts into diagnostic probabilities.

## Transport and durability

The operator runner dispatches through authenticated `/register`, `/phase`, `/job`, `/started`, `/position` and `/report` endpoints; the model mail tool posts to `/question`. It records a turn marker **before** asking OpenCode to generate. The bus persists phase, prompt, recipient, turn marker, validated position, question/reply threads and events in SQLite WAL; raw role/admin tokens are not stored in the database.

Peer processes receive a private runtime binding file containing only their own role token, case ID/hash, bus URL and session ID. The trusted mail tool reads it on invocation and rejects a different session. The file is removed on process shutdown; model file-read tools remain denied. This is a local prototype identity binding, not an OS-level secret boundary.

On reopening the bus with the same case/input, existing jobs and their markers remain. A new case or changed input is rejected. A running job is not silently resent; a trusted runner must inspect that saved OpenCode turn and submit or explicitly resolve its state. **Automatic full-run resume after a runner crash is not yet implemented.** Unit tests verify persisted in-flight jobs and no duplicated job creation; that is narrower than production crash recovery.

The runner is trusted to call the HTTP bus with the correct role token and to relay genuine OpenCode output. Tokens do not prove that a request originated inside a model's tool call. This is not an OS sandbox or a cryptographically verified tool gateway.

## Audit and observed result

The printed temporary evaluation root contains:

```text
operator-results/audit/SYNTH-931/team/
  evaluation-mail.sqlite        durable queue, positions, events
  evaluation-mail-report.json   closed-phase operator export
  panel-notes.json              preserved blind/critique positions and dissent
  opinion-opus.json             OpenCode message/tool transcript
  opinion-astra.json
  challenger.json
  lead.json
```

Team submission provenance is `variant: http_mail_bus_bounded_panel`. The single-model submissions remain `single_model_closed_book`. The live run completed **16 team jobs**, including three actual tool-sent peer questions, three replies and final positions, then all three baselines. Every arm selected the matching fictional code. Original question wording and replies are preserved in `panel-notes.json` and `evaluation-mail-report.json`. The scoring result remains `clinical_validation: false`: matching an invented marker exercise does not measure diagnostic ability or establish benefit from deliberation.

## Remaining before published-case execution

- Specialty routing, clinically meaningful source-aware hypotheses, multi-round consultation strategy and review-board workflow.
- Free-form differentials, confirmation pathways, evidence-backed treatment/prognosis, and bilingual report contracts.
- Strong process/credential isolation with scorer data outside every agent's access.
- Clinician-reviewed published inputs and answers, controlled evidence retrieval, held-out evaluation, and comparable per-arm scoring.

The existing published-case draft stays pending clinical review; this panel does not unlock `VAL-*` or private cases.
