# Phase 0 OpenCode Compatibility Spike

Date: 2026-09-28 UTC. Scope: synthetic-only local tests on macOS. No patient files were opened or interpreted. This is not a clinical evaluation.

**Later model migration:** on 2026-09-30 UTC, `openai/gpt-6.1-sol` was listed and successfully prompted, then replaced the active `sol` alias. The GPT-6 entries below describe historical tests; use `config/models.yaml` and [current role assignments](model-assignments.md) for new runs. No GPT-6.1 Astra ID was listed.

## Verified on the installed environment

| Question | Result | Method |
|---|---|---|
| Installed OpenCode | **1.18.33** | `opencode --version` |
| Exact requested model IDs | `anthropic/claude-opus-5-5`, `openai/gpt-6-astra`, `openai/gpt-6-sol` | Provider listings and one short synthetic text prompt for each; all replied successfully |
| Isolated process storage | **Yes** with distinct `XDG_DATA_HOME` per process | Two authenticated localhost `opencode serve` processes: distinct `opencode.db` paths; session created on A returned HTTP 404 on B |
| Local HTTP health and sessions | **Yes** | `/global/health`, `POST /session`, `GET /session/:id`, `GET /session/status` |
| Asynchronous prompt and event | **Yes** | `POST /session/:id/prompt_async` returned HTTP 204; message history contained synthetic reply; SSE `/event` emitted `session.idle` for that session |
| Open TUI and HTTP coexistence | **Yes** on a synthetic session | Launched TUI in a pseudo-terminal with `--agent plan --session <id> --port <port>`; HTTP-delivered turn appeared in that session and was rendered in the TUI |
| TUI session agent selection | **Yes, in the tested path** | `POST /tui/append-prompt` then `/tui/submit-prompt`; saved user and assistant messages recorded `agent: plan` and response appeared in the same session |
| Saved history across TUI restarts | **Yes, in the tested path** | Relaunched TUI against same session ID; prior synthetic messages were present |
| HTTP server password | **Used** | All local test calls sent Basic credentials set through `OPENCODE_SERVER_PASSWORD`; unauthenticated rejection was not separately tested |

**Test caveat:** the TUI was run in a pseudo-terminal. The pseudo-terminal output must be drained continuously in automated tests; otherwise its buffer fills and the TUI may stop processing, creating a false API failure. The initial probe hit this condition; repeating with a drained PTY succeeded. The test did not verify simultaneous human typing during an HTTP delivery, so the planned per-session scheduling lock remains necessary.

## Local prerequisites and unresolved questions

1. **Provider credentials under isolation:** `opencode providers list` showed 2 credentials in the normal data directory (OpenAI OAuth and Anthropic API), but 0 in an isolated `XDG_DATA_HOME`. Live model prompts succeeded only in a synthetic workspace using the existing normal credentials. Implement secure provider authentication for isolated processes and test long-running refresh/restart; do not commit/copy credential files into the project.
2. **No `tmux` executable** was found. Install it before implementing tmux windows, or provide a native-terminal launcher with equivalent independent TUIs.
3. **PDF extraction route untested.** `pdftotext` and `tesseract` were unavailable; Python lacked `pypdf`, `PyPDF2`, `pdfplumber`, and `fitz`. No record content was read. Install/test a text extraction and OCR pipeline with synthetic PDFs before record intake, and verify page-level traceability.
4. **Reasoning and modality options untested.** Prompt tests confirm text model access, not “maximum reasoning” settings, scanned PDF/image interpretation, or any evidence-source integration.
5. **Mail Bus and plugin not implemented.** A synthetic single recipient proved API mechanics, not durable multi-agent routing, role binding, crash recovery, or privacy enforcement.
6. **Configuration loading not implemented.** `config/*.yaml` are future launcher specifications, not native OpenCode config. No live doctor agents were created in this spike.
7. **All endpoints/flags must be revalidated** if the installed OpenCode version changes; the earlier docs named 1.18.32, while the executable measured here is 1.18.33.

## Next engineering milestone

In a synthetic-only project, implement a two-doctor transport prototype with isolated authenticated processes, one persistent TUI/session per doctor, a minimal durable queue, and explicit completion/timeout states. Inject no clinical records until isolation, restart, scheduling, and privacy tests pass. Build the OCR/text-source pipeline independently using synthetic documents.
