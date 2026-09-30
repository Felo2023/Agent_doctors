# Two-Doctor Synthetic Transport Prototype

This code tests the transport architecture only. In the baseline, `doctor-a` and `doctor-b` exchange a question about `7 + 5` in `SYNTH-001`. In a second synthetic mode, they both read one **pinned reviewed version** of `SYNTH-002` and exchange a cited source block ID/date. They are **not** medical agents. The prototype never reads real clinical PDFs, private runbooks, or private case profiles.

## Run

Requires: Python 3 with `requirements.txt` installed, `opencode` 1.18.33 or compatible, and existing Anthropic and OpenAI credentials. Model IDs come from `config/models.yaml`; the current participants use `anthropic/claude-opus-5-5` and `openai/gpt-6.1-sol`.

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/synthetic_roundtrip.py --demo-shared-auth --timeout 240
# Also exercise a real bus restart during delivery:
.venv/bin/python scripts/synthetic_roundtrip.py --demo-shared-auth --restart-bus --timeout 240
```

To exercise the **case-scoped integration** in the Python environment with `requirements-intake.txt` installed:

```bash
.venv/bin/python -m scripts.synthetic_reviewed_case_demo
# Copy the printed Synthetic fixture path into --case-root:
.venv/bin/python scripts/synthetic_roundtrip.py --demo-shared-auth \
  --case-root '/path/from/demo' --restart-bus --timeout 300
```

This second run creates a fictional reviewed version from PDF, Word and Excel blocks. The bus validates its source hashes, binds a single `SYNTH-*` case ID + version/hash for the run, and offers a role-token-gated `GET /case` endpoint. Both distinct model sessions call the `prototype_case_read` tool; the recipient sends back a source block ID/date it read itself. A changed source or newly committed case version blocks further mail/reads; start a new bus run for a new version. One bus database belongs to **one case**. All runtimes remain synthetic-only.

This command explicitly authorizes a **short-lived synthetic-only credential symlink** from each isolated OpenCode profile to the user's existing OpenCode auth file. It is not for clinical use. The launcher creates a private (`0700`) system-temp workspace and copies only the two synthetic agent definitions and the synthetic mail tool. It does **not** copy patient records. Credential symlinks are removed when the run ends; private synthetic transcripts, isolated databases, and the mail audit remain in the printed temporary directory. OpenAI OAuth refresh and concurrent shared auth-file writes have not been validated; production requires an approved per-process credential provisioner.

Each participant runs as its own OpenCode **TUI process** with a unique port, isolated `XDG_DATA_HOME`, saved session, and agent. The launcher keeps the pseudo-terminal output drained (necessary for a headless automated run); it does not display the interactive TUIs in this terminal. The output directory contains `sessions.json`, `doctor-a-messages.json`, `doctor-b-messages.json`, and `mail.sqlite` for inspection. The bus admin credential is not stored in those files. With `--hold` in an interactive terminal, the supervisor accepts `inspect doctor-a` or `inspect doctor-b`: it pauses new bus deliveries, waits for in-flight mail and session activity to end, attaches a visible TUI to the doctor session, and resumes delivery when you exit the attached TUI. `status` shows mail states; `quit` stops all processes. The attach flow follows documented OpenCode CLI behavior but has not yet had an interactive human acceptance test. `tmux` is not installed on this machine.

## Implemented subset

| Part | Behavior |
|---|---|
| Mail bus | Localhost HTTP service in `bus/prototype.py`, distinct admin and role credentials, JSON inputs, SQLite WAL queue and event log |
| Registry | Two allowed roles bound to loopback URL, OpenCode session ID, fixed model, and a hash of that role's token; refuses session/model/token rebind |
| Case boundary | One synthetic case per DB and bus process; case ID and reviewed version/hash pinned for the run; wrong case/version or source change fails closed |
| Sender | Derived from the authenticated role token, then checked against the registered OpenCode session ID passed by custom tool context; no free-form `from` field |
| Phase | `setup → discussion → closed`; mail only accepted in discussion, closure refused with outstanding mail |
| Delivery | One in-flight letter per recipient; only prompt idle sessions; poll health/status/history; wait for final assistant response (not a `tool-calls` step) |
| Recovery | Durable queued/in-flight mail survives restart. Bus polls saved recipient session messages: a completed final assistant turn marks mail complete without resending. If dispatch cannot be resolved before its deadline, mail becomes `uncertain` and blocks later letters to that recipient until operator `cancel` or `retry` after transcript inspection |
| Human pause | Admin `/pause` stops new dispatches to one doctor; `safe_for_human_turn` is true only after in-flight mail clears. The `--hold` supervisor also checks OpenCode session status and attaches a TUI |
| Dedupe | Repeating sender + dedupe key with the same content returns the same mail ID; conflicting content is rejected |
| Test | Unit and HTTP authorization tests plus two real processes/models, two completed letters and a successful mid-run bus restart |

## Limits before clinical use

- Synthetic `SYNTH-*` cases and two participants only. A reviewed **subset** with source provenance is available, not a complete clinical case record or a diagnosis-ready snapshot. There is no patient consent control, clinical report generation, safety alerts, or external evidence retrieval.
- The bus distinguishes admin and per-role tokens and rejects cross-role session IDs, but a token holder can still claim its own session ID: it is not cryptographic proof that a tool call came from OpenCode. Production must bind tool calls through a trusted gateway and enforce case-scoped authentication beyond this synthetic proof.
- A `204` dispatch can still be ambiguous if OpenCode accepted a request but never persisted the expected turn; after the deadline the bus holds it as `uncertain`. `/resolve` permits an **explicit** operator cancel or retry after inspecting the recipient's transcript; there is no automatic re-send.
- `/pause` provides a scheduling handshake, but cannot detect typing started before the pause or prevent separate clients from posting directly to the OpenCode HTTP server. Use the supervisor's `inspect` command instead of independently attaching to the hidden test TUI. Concurrent human typing during bus delivery and the `--hold` attach flow still require manual acceptance tests.
- The bus and OpenCode servers use loopback-only HTTP, not TLS; all roles in this synthetic prototype run under the same OS user and share an OpenCode auth-file symlink. These controls are not sufficient for sensitive clinical data.
- Status polling is used instead of resilient SSE reconnection. No general delivery receipt beyond final assistant turn; no multi-doctor timeout escalation or robust process supervision.
- The OpenCode agent prompts and permissions are deliberately synthetic. Do not reuse them as clinical specialists or grant access to the repository's real records.
- Separate role workspaces are prepared in temp directories; no case-scoped authorization or filesystem sandbox has yet been verified against hostile tools.

## Verified result

On 2026-09-28, with the installed OpenCode and configured user credentials, the two independent TUI processes exchanged a question and a reply and the bus marked both final turns complete. The response from doctor-b said `7 + 5 = 12`. A later run restarted the bus **with a letter in flight**; both letters still completed. A case-scoped `SYNTH-002` run with a bus restart also completed: each model read the same reviewed version and doctor-b returned a matching source block ID and date. A pseudo-terminal run reached the `--hold` supervisor, accepted `status` and `quit`, and exited cleanly. No patient data was accessed.
