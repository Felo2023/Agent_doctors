"""Actual OpenCode model calls for a fictional sequential evaluation pilot.

Only SYNTH-* cases. The `team` arm uses a durable bounded-round HTTP evaluation
bus, NOT the full clinical specialist / research / safety team.
"""

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import threading
from http.server import ThreadingHTTPServer

from scripts.synthetic_roundtrip import call, start_tui, stop, unused_port, wait_health
from validation.harness import SequentialSuite, write_atomic
from bus.evaluation import EvaluationBus, Handler as EvaluationHandler, PHASES
from runtime.models import load_models, opencode_configuration


ROOT = Path(__file__).resolve().parents[1]
MODELS = load_models()


def parse_response(text, *, role, allowed):
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    data = json.loads(text)
    required = {"diagnoses", "critical_flags"} if role == "baseline" else (
        {"diagnoses", "critical_flags", "disagreements"} if role == "lead"
        else {"diagnoses", "critical_flags", "reason", "answers"} if role == "answers"
        else {"diagnoses", "critical_flags", "reason"})
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError("model reply does not match the required structured schema")
    codes = data["diagnoses"]
    alerts = data["critical_flags"]
    if (not isinstance(codes, list) or len(codes) > 10
            or any(not isinstance(code, str) or code not in allowed for code in codes)
            or len(codes) != len(set(codes))
            or not isinstance(alerts, list) or any(not isinstance(item, str) or len(item) > 100 for item in alerts)):
        raise ValueError("model returned an unknown/duplicate code or invalid flags")
    if role == "lead" and (not isinstance(data["disagreements"], list)
                           or any(not isinstance(item, str) for item in data["disagreements"])):
        raise ValueError("lead disagreements must be a list")
    if role in ("peer", "answers") and not isinstance(data["reason"], str):
        raise ValueError("peer must state a short reason")
    if role == "answers" and (not isinstance(data["answers"], list) or len(data["answers"]) > 3):
        raise ValueError("bounded answers list required")
    return data


def blind_peer_packet(case_input, peers):
    """Each peer sees other rankings without the names/models that produced them."""
    return {"case_input": case_input, "anonymous_other_opinions": [
        {"diagnoses": opinion["diagnoses"], "critical_flags": opinion["critical_flags"],
         "reason": opinion["reason"]}
        for opinion in peers
    ]}


class ModelProcess:
    def __init__(self, role, model, active, credential):
        self.role = role
        self.model = model
        self.closed = False
        self.root = Path(active["workspace"]) / role
        work = self.root / "workspace"
        self.work = work
        agents = work / ".opencode/agents"
        agents.mkdir(parents=True)
        definition = {"baseline": "eval-baseline.md", "lead": "eval-lead.md",
                      "challenger": "eval-challenger.md"}.get(role, "eval-hypothesis.md")
        self.agent = definition.removesuffix(".md")
        shutil.copy2(ROOT / ".opencode/agents" / definition, agents / definition)
        if role not in ("baseline", "lead"):
            tools = work / ".opencode/tools"
            tools.mkdir()
            shutil.copy2(ROOT / ".opencode/tools/eval_question.ts", tools / "eval_question.ts")
        auth_dir = self.root / "data/opencode"
        auth_dir.mkdir(parents=True)
        self.auth_link = auth_dir / "auth.json"
        self.auth_link.symlink_to(credential)
        self.password = secrets.token_urlsafe(28)
        port = unused_port()
        self.url = f"http://127.0.0.1:{port}"
        self.env = {**os.environ, "XDG_DATA_HOME": str(self.root / "data"),
                    "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_CACHE_HOME": str(self.root / "cache"),
                    "OPENCODE_SERVER_PASSWORD": self.password,
                    "OPENCODE_CONFIG_CONTENT": json.dumps(opencode_configuration(model)),
                    "OPENCODE_DISABLE_AUTOUPDATE": "1"}
        try:
            with (self.root / "bootstrap.log").open("w") as log:
                process = subprocess.Popen(["opencode", "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                                           cwd=work, env=self.env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    wait_health(self.url, self.password, process)
                    _, providers = call(self.url, "/provider", password=self.password)
                    provider_id, model_id = model.split("/", 1)
                    provider = next((item for item in providers["all"] if item["id"] == provider_id), None)
                    if not provider or model_id not in provider.get("models", {}):
                        raise RuntimeError("isolated OpenCode profile cannot resolve the configured model: " + model)
                    _, session = call(self.url, "/session", password=self.password, method="POST",
                                      body={"title": active["case_id"] + " / " + active["arm"] + " / " + role})
                    self.session = session["id"]
                finally:
                    stop(process)
            self.process, self.master, self.stopped, self.reader = start_tui(self.agent, port, self.session, work, self.env)
            wait_health(self.url, self.password, self.process)
        except BaseException:
            self.close()
            raise

    def prompt(self, question, marker=None):
        provider, model_id = self.model.split("/", 1)
        marker = marker or "TURN-" + secrets.token_hex(12)
        status, _ = call(self.url, f"/session/{self.session}/prompt_async", password=self.password,
                         method="POST", body={"agent": self.agent, "model": {"providerID": provider, "modelID": model_id},
                                              "parts": [{"type": "text", "text": marker + "\n" + question}]})
        if status != 204:
            raise RuntimeError("model prompt was not accepted")
        return marker

    def wait(self, marker, timeout=240):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("OpenCode process exited before final response")
            _, messages = call(self.url, f"/session/{self.session}/message", password=self.password)
            user = next((m for m in reversed(messages) if m.get("info", {}).get("role") == "user"
                         and any(part.get("type") == "text" and part.get("text", "").startswith(marker)
                                 for part in m.get("parts", []) if isinstance(part, dict))), None)
            if user:
                final = next((m for m in reversed(messages) if m.get("info", {}).get("role") == "assistant"
                              and m["info"].get("parentID") == user["info"]["id"]
                              and m["info"].get("time", {}).get("completed")
                              and m["info"].get("finish") not in (None, "tool-calls")), None)
                if final:
                    if final["info"].get("error"):
                        raise RuntimeError("model finished with an error")
                    return "\n".join(part.get("text", "") for part in final.get("parts", [])
                                     if part.get("type") == "text")
            time.sleep(0.5)
        raise TimeoutError("model did not finish within the configured synthetic timeout")

    def export(self, target):
        _, messages = call(self.url, f"/session/{self.session}/message", password=self.password)
        write_atomic(target, messages)

    def configure_mail(self, url, binding, role_token):
        path = self.work / ".eval-mail-binding.json"
        write_atomic(path, {**binding, "url": url, "role_token": role_token, "session_id": self.session})
        path.chmod(0o600)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if hasattr(self, "process"):
            stop(self.process)
            self.stopped.set()
            self.reader.join(timeout=2)
            os.close(self.master)
        if self.auth_link.is_symlink():
            self.auth_link.unlink()
        (self.work / ".eval-mail-binding.json").unlink(missing_ok=True)


def run_bus_panel(sessions, case_id, data, audit_dir, timeout):
    """All phase/job/position routing goes through authenticated loopback HTTP."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    admin_token = secrets.token_urlsafe(32)
    tokens = {role: secrets.token_urlsafe(32) for role in sessions}
    bus = EvaluationBus(audit_dir / "evaluation-mail.sqlite", case_id, data, admin_token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), EvaluationHandler)
    server.bus = bus
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    binding = {"case_id": case_id, "case_hash": bus.case_hash}
    try:
        for role, session in sessions.items():
            call(url, "/register", token=admin_token, method="POST",
                 body={**binding, "role": role, "session_id": session.session, "role_token": tokens[role]})
            if role != "lead":
                session.configure_mail(url, binding, tokens[role])
        for phase in PHASES:
            call(url, "/phase", token=admin_token, method="POST", body={**binding, "phase": phase})
            recipients = ("lead",) if phase == "synthesis" else ("opinion-opus", "opinion-astra", "challenger")
            pending = {}
            for role in recipients:
                _, envelope = call(url, "/job", token=tokens[role])
                job = envelope["job"]
                marker = "TURN-" + secrets.token_hex(12)
                call(url, "/started", token=tokens[role], method="POST",
                     body={**binding, "session_id": sessions[role].session,
                           "job_id": job["id"], "turn_marker": marker})
                sessions[role].prompt(job["prompt"], marker=marker)
                pending[role] = (job, marker)
            for role, (job, marker) in pending.items():
                response = parse_response(sessions[role].wait(marker, timeout),
                                          role="lead" if role == "lead" else "answers" if phase == "answers" else "peer",
                                          allowed=data["allowed_diagnosis_ids"])
                call(url, "/position", token=tokens[role], method="POST",
                     body={**binding, "session_id": sessions[role].session,
                           "job_id": job["id"], "position": response})
        call(url, "/phase", token=admin_token, method="POST", body={**binding, "phase": "closed"})
        _, report = call(url, "/report", token=admin_token)
        write_atomic(audit_dir / "evaluation-mail-report.json", report)
        final = report["synthesis"]["lead"]
        return final, {"blind": report["blind"], "critique": report["critique"],
                       "question_threads": report["question_threads"], "final_positions": report["final_positions"],
                       "disagreements": final["disagreements"]}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        bus.close()


def run_active(suite, credential, timeout=240):
    progress = suite.ensure_progress()
    active = progress["active"]
    if not active:
        raise ValueError("prepare an arm first")
    raw = (Path(active["workspace"]) / "case-input.txt").read_text(encoding="utf-8")
    data = json.loads(raw)
    allowed = data.get("allowed_diagnosis_ids")
    if (not isinstance(allowed, list) or not allowed
            or any(not isinstance(code, str) for code in allowed)
            or len(set(allowed)) != len(allowed)):
        raise ValueError("model-backed synthetic run needs a candidate-code catalog")
    arm = active["arm"]
    roles = ({"baseline": MODELS[arm]} if arm != "team" else
             {"opinion-opus": MODELS["opus"], "opinion-astra": MODELS["astra"],
              "challenger": MODELS["sol"], "lead": MODELS["opus"]})
    if any((Path(active["workspace"]) / role).exists() for role in roles):
        raise ValueError("arm has already started sessions; inspect before rerunning")
    sessions = {}
    try:
        for role, model in roles.items():
            sessions[role] = ModelProcess(role, model, active, credential)
        case_text = json.dumps(data, ensure_ascii=False)
        audit_dir = suite.result_root / "audit" / active["case_id"] / arm
        if arm == "team":
            final, pilot_notes = run_bus_panel(sessions, active["case_id"], data, audit_dir, timeout)
        else:
            marker = sessions["baseline"].prompt("Single-model fictional case ranking. Return JSON only.\n" + case_text)
            final = parse_response(sessions["baseline"].wait(marker, timeout), role="baseline", allowed=allowed)
            pilot_notes = {}
        for role, session in sessions.items():
            session.export(audit_dir / (role + ".json"))
        if pilot_notes:
            write_atomic(audit_dir / "panel-notes.json", pilot_notes)
        submission_path = suite.result_root / "pending" / (active["case_id"] + "-" + arm + ".json")
        write_atomic(submission_path, {"case_id": active["case_id"], "arm": arm,
                                       "diagnoses": final["diagnoses"], "critical_flags": final["critical_flags"]})
        provenance = {"kind": "model_backed", "variant": "http_mail_bus_bounded_panel" if arm == "team" else "single_model_closed_book",
                      "models": roles, "sessions": {role: session.session for role, session in sessions.items()},
                      "audit_dir": str(audit_dir)}
        for session in reversed(list(sessions.values())):
            session.close()
        return suite.submit(submission_path, provenance=provenance)
    finally:
        for role, session in sessions.items():
            try:
                failed_export = suite.result_root / "audit" / active["case_id"] / arm / (role + ".json")
                if not failed_export.exists() and not session.closed:
                    session.export(failed_export)
            except Exception:
                pass  # Preserve any reachable transcripts without masking the original failure.
        for session in reversed(list(sessions.values())):
            session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--answers", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--demo-shared-auth", action="store_true")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    if not args.demo_shared_auth:
        parser.error("synthetic model run requires explicit --demo-shared-auth")
    credential = Path.home() / ".local/share/opencode/auth.json"
    if not credential.is_file():
        parser.error("OpenCode provider credentials not found")
    suite = SequentialSuite(args.manifest, args.answers, args.work, args.results)
    print("Completed model-backed arm:", run_active(suite, credential, args.timeout))


if __name__ == "__main__":
    main()
