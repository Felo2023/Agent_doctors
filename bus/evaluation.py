"""Durable, bounded-round HTTP Mail Bus for the fictional evaluation team.

No real/published records, credentials, or clinical answers are accepted here.
Jobs are claimed by a trusted runner; model processes receive only role-scoped
prompts. The bus constructs peer exchange and never reveals it in blind phase.
"""

import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler


PEERS = ("opinion-opus", "opinion-astra", "challenger")
ROLES = (*PEERS, "lead")
PHASES = ("blind", "critique", "questions", "answers", "final", "synthesis")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def validate_position(value, allowed, lead=False):
    keys = {"diagnoses", "critical_flags", "disagreements" if lead else "reason"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("position schema mismatch")
    codes = value["diagnoses"]
    flags = value["critical_flags"]
    if (not isinstance(codes, list) or len(codes) > 10
            or any(not isinstance(code, str) or code not in allowed for code in codes)
            or len(set(codes)) != len(codes)
            or not isinstance(flags, list) or len(flags) > 20
            or any(not isinstance(flag, str) or len(flag) > 100 for flag in flags)):
        raise ValueError("invalid codes or critical flags")
    if lead:
        if (not isinstance(value["disagreements"], list) or len(value["disagreements"]) > 20
                or any(not isinstance(item, str) or len(item) > 1500 for item in value["disagreements"])):
            raise ValueError("invalid lead disagreements")
    elif not isinstance(value["reason"], str) or not value["reason"].strip() or len(value["reason"]) > 2000:
        raise ValueError("source-based reason required")


class EvaluationBus:
    def __init__(self, db_path, case_id, case_input, admin_token):
        if not isinstance(case_id, str) or not re.fullmatch(r"SYNTH-\d{3,6}", case_id):
            raise ValueError("only fictional SYNTH-* cases allowed")
        if not isinstance(case_input, dict):
            raise ValueError("case input must be an object")
        allowed = case_input.get("allowed_diagnosis_ids")
        if (not isinstance(allowed, list) or not allowed or len(allowed) > 30
                or any(not isinstance(code, str) or not code.strip() for code in allowed)
                or len(set(allowed)) != len(allowed) or len(encoded(case_input)) > 30000):
            raise ValueError("bounded fictional candidate catalog required")
        if not isinstance(admin_token, str) or len(admin_token) < 24:
            raise ValueError("operator token of at least 24 characters required")
        self.case_id, self.admin_token = case_id, admin_token
        self.case_input = json.loads(encoded(case_input))  # no alias to mutable caller input
        self.allowed = tuple(allowed)
        self.case_hash = fingerprint(self.case_input)
        self.lock = threading.RLock()
        if Path(db_path).is_symlink():
            raise ValueError("bus database cannot be a symlink")
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS roles(role TEXT PRIMARY KEY,session_id TEXT UNIQUE NOT NULL,token_hash TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,phase TEXT NOT NULL,recipient TEXT NOT NULL,
                 prompt TEXT NOT NULL,status TEXT NOT NULL,turn_marker TEXT,result TEXT,
                 UNIQUE(phase,recipient));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,event TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS questions(
                id TEXT PRIMARY KEY, sender TEXT UNIQUE NOT NULL, recipient TEXT NOT NULL,
                body TEXT NOT NULL, dedupe_key TEXT NOT NULL, answer TEXT,
                UNIQUE(sender,dedupe_key));
        """)
        binding = self.db.execute("SELECT value FROM meta WHERE key='binding'").fetchone()
        current = encoded({"case_id": case_id, "case_hash": self.case_hash})
        if binding and binding["value"] != current:
            self.db.close()
            raise ValueError("bus belongs to another case or input snapshot")
        if not binding:
            self.db.executemany("INSERT INTO meta VALUES(?,?)", (("binding", current), ("phase", "setup")))
            self.db.commit()

    def close(self):
        self.db.close()

    def event(self, name, payload):
        self.db.execute("INSERT INTO events(event,payload) VALUES(?,?)", (name, encoded(payload)))

    def phase(self):
        return self.db.execute("SELECT value FROM meta WHERE key='phase'").fetchone()["value"]

    def register(self, role, session_id, role_token):
        if role not in ROLES or not isinstance(session_id, str) or not session_id.startswith("ses_"):
            raise ValueError("invalid role/session")
        if not isinstance(role_token, str) or len(role_token) < 24:
            raise ValueError("role token required")
        if hmac.compare_digest(role_token, self.admin_token):
            raise ValueError("operator credential cannot be used by a model role")
        token_hash = hashlib.sha256(role_token.encode()).hexdigest()
        with self.lock, self.db:
            previous = self.db.execute("SELECT * FROM roles WHERE role=?", (role,)).fetchone()
            if previous:
                if previous["session_id"] != session_id or not hmac.compare_digest(previous["token_hash"], token_hash):
                    raise ValueError("cannot rebind an evaluation role")
                return
            if self.phase() != "setup":
                raise ValueError("registration closed after setup")
            try:
                self.db.execute("INSERT INTO roles VALUES(?,?,?)", (role, session_id, token_hash))
            except sqlite3.IntegrityError as exc:
                raise ValueError("session/token already bound to another role") from exc
            self.event("registered", {"role": role, "session_id": session_id})

    def authenticate(self, authorization):
        if not authorization.startswith("Bearer "):
            return None
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with self.lock:
            return next((row["role"] for row in self.db.execute("SELECT role,token_hash FROM roles")
                         if hmac.compare_digest(digest, row["token_hash"])), None)

    def results(self, phase):
        return {row["recipient"]: json.loads(row["result"]) for row in self.db.execute(
            "SELECT recipient,result FROM jobs WHERE phase=? AND status='completed'", (phase,))}

    def transition(self, requested):
        with self.lock, self.db:
            old = self.phase()
            transitions = {"setup": "blind", "blind": "critique", "critique": "questions",
                           "questions": "answers", "answers": "final", "final": "synthesis", "synthesis": "closed"}
            if transitions.get(old) != requested:
                raise ValueError("invalid phase transition")
            if self.db.execute("SELECT COUNT(*) FROM roles").fetchone()[0] != len(ROLES):
                raise ValueError("all four roles must register before a round")
            if old != "setup" and self.db.execute("SELECT COUNT(*) FROM jobs WHERE phase=? AND status!='completed'", (old,)).fetchone()[0]:
                raise ValueError("round still has unfinished jobs")
            payloads = {}
            if requested == "blind":
                for role in PEERS:
                    payloads[role] = "First independent fictional ranking. Do not seek peer views.\n" + encoded(self.case_input)
            elif requested == "critique":
                first = self.results("blind")
                if set(first) != set(PEERS):
                    raise ValueError("missing independent positions")
                for role in PEERS:
                    others = [first[other] for other in PEERS if other != role]
                    payloads[role] = ("Critique anonymously provided peer opinions against the same fictional input. "
                                      "Revise only for a source-based reason; return diagnoses, critical_flags, reason.\n"
                                      + encoded({"case_input": self.case_input, "anonymous_other_opinions": others}))
            elif requested == "questions":
                for role in PEERS:
                    colleague = PEERS[(PEERS.index(role) + 1) % len(PEERS)]
                    payloads[role] = (
                        "Question round for this fictional test. Use eval_question to send exactly one concise, "
                        f"targeted question to {colleague}, asking it to check a source observation or competing code. "
                        "The bus permits at most one question, no self-mail and no questions to the Lead. "
                        "Do not wait for a reply. Then return your current position as diagnoses, critical_flags, reason.\n"
                        + encoded({"case_input": self.case_input, "current_position": self.results("critique")[role]}))
            elif requested == "answers":
                for role in PEERS:
                    assigned = self.question_threads(recipient=role)
                    payloads[role] = (
                        "Answer each assigned fictional peer question using the supplied case data. "
                        "Return exactly JSON with diagnoses, critical_flags, reason, and answers. "
                        "answers is a list of {question_id, answer}; include every assigned ID exactly once, "
                        "or an empty list if none. No new questions are permitted in this phase.\n"
                        + encoded({"case_input": self.case_input, "assigned_questions": assigned}))
            elif requested == "final":
                if self.db.execute("SELECT COUNT(*) FROM questions WHERE answer IS NULL").fetchone()[0]:
                    raise ValueError("peer replies are missing")
                for role in PEERS:
                    related = [thread for thread in self.question_threads()
                               if role in (thread["sender"], thread["recipient"])]
                    payloads[role] = (
                        "Final fictional position after targeted peer questions. Consider the replies, "
                        "but change a ranking only for a source-based reason. Return diagnoses, critical_flags, reason. "
                        "No new questions or tool calls.\n"
                        + encoded({"case_input": self.case_input, "current_position": self.results("answers")[role],
                                   "related_question_threads": related}))
            elif requested == "synthesis":
                first, final = self.results("blind"), self.results("final")
                if set(final) != set(PEERS):
                    raise ValueError("missing reviewed peer positions")
                payloads["lead"] = ("Synthesize independent rankings and critiques for this fictional case. "
                                    "Preserve disagreement, never count votes as evidence. Return diagnoses, critical_flags, disagreements.\n"
                                    + encoded({"case_input": self.case_input, "blind": list(first.values()),
                                               "critique": list(self.results("critique").values()),
                                               "question_threads": self.question_threads(),
                                               "final_positions": list(final.values())}))
            for role, prompt in payloads.items():
                self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,NULL,NULL)",
                                (f"{self.case_id}:{requested}:{role}", requested, role, prompt, "queued"))
            self.db.execute("UPDATE meta SET value=? WHERE key='phase'", (requested,))
            self.event("phase", {"from": old, "to": requested, "case_hash": self.case_hash})

    def job(self, role):
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE recipient=? AND phase=?", (role, self.phase())).fetchone()
            if row is None:
                return {"case_id": self.case_id, "case_hash": self.case_hash, "job": None}
            return {"case_id": self.case_id, "case_hash": self.case_hash,
                    "job": {key: row[key] for key in ("id", "phase", "recipient", "prompt", "status", "turn_marker")}}

    def started(self, role, session_id, job_id, marker):
        if not isinstance(marker, str) or not re.fullmatch(r"TURN-[a-f0-9]{24}", marker):
            raise ValueError("valid OpenCode turn marker required")
        with self.lock, self.db:
            self.bound_job(role, session_id, job_id)
            row = self.db.execute("SELECT status,turn_marker FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row["status"] == "running" and row["turn_marker"] == marker:
                return  # idempotent acknowledgment, not a new model call
            if row["status"] != "queued":
                raise ValueError("job already dispatched; reconcile its saved turn")
            self.db.execute("UPDATE jobs SET status='running',turn_marker=? WHERE id=?", (marker, job_id))
            self.event("started", {"job_id": job_id, "turn_marker": marker})

    def bound_job(self, role, session_id, job_id):
        identity = self.db.execute("SELECT session_id FROM roles WHERE role=?", (role,)).fetchone()
        row = self.db.execute("SELECT recipient,phase FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not identity or identity["session_id"] != session_id or not row or row["recipient"] != role or row["phase"] != self.phase():
            raise ValueError("job/case/phase/session does not match authenticated role")

    def question_threads(self, recipient=None):
        rows = self.db.execute("SELECT id,sender,recipient,body,answer FROM questions ORDER BY rowid")
        return [dict(row) for row in rows if recipient is None or row["recipient"] == recipient]

    def ask(self, role, session_id, recipient, body, dedupe_key):
        if role not in PEERS or recipient not in PEERS or role == recipient:
            raise ValueError("questions must target another peer, never self or Lead")
        if not isinstance(body, str) or not body.strip() or len(body) > 1200:
            raise ValueError("bounded nonempty question required")
        if not isinstance(dedupe_key, str) or not 1 <= len(dedupe_key) <= 128:
            raise ValueError("question idempotency key required")
        body = body.strip()
        with self.lock, self.db:
            if self.phase() != "questions":
                raise ValueError("peer questions are locked outside their question round")
            own_id = f"{self.case_id}:questions:{role}"
            self.bound_job(role, session_id, own_id)
            status = self.db.execute("SELECT status FROM jobs WHERE id=?", (own_id,)).fetchone()["status"]
            if status != "running":
                raise ValueError("ask only while your question-round job is running")
            old = self.db.execute("SELECT * FROM questions WHERE sender=?", (role,)).fetchone()
            if old:
                if old["dedupe_key"] == dedupe_key and old["recipient"] == recipient and old["body"] == body:
                    return {"question_id": old["id"], "state": "queued", "duplicate": True}
                raise ValueError("one-question budget exhausted")
            question_id = f"{self.case_id}:question:{role}"
            self.db.execute("INSERT INTO questions VALUES(?,?,?,?,?,NULL)",
                            (question_id, role, recipient, body, dedupe_key))
            self.event("peer_question", {"question_id": question_id, "sender": role, "recipient": recipient})
            return {"question_id": question_id, "state": "queued", "duplicate": False}

    def submit(self, role, session_id, job_id, position):
        with self.lock, self.db:
            self.bound_job(role, session_id, job_id)
            answer_phase = self.phase() == "answers"
            if answer_phase:
                if not isinstance(position, dict) or set(position) != {"diagnoses", "critical_flags", "reason", "answers"}:
                    raise ValueError("answer-round schema mismatch")
                validate_position({key: value for key, value in position.items() if key != "answers"}, self.allowed)
                replies = position["answers"]
                assigned = {thread["id"] for thread in self.question_threads(recipient=role)}
                if (not isinstance(replies, list) or len(replies) != len(assigned)
                        or any(not isinstance(reply, dict) or set(reply) != {"question_id", "answer"}
                               or not isinstance(reply["question_id"], str)
                               or not isinstance(reply["answer"], str) or not reply["answer"].strip()
                               or len(reply["answer"]) > 2000 for reply in replies)
                        or {reply["question_id"] for reply in replies} != assigned):
                    raise ValueError("reply to each assigned question ID exactly once")
            else:
                validate_position(position, self.allowed, lead=role == "lead")
            row = self.db.execute("SELECT status,result FROM jobs WHERE id=?", (job_id,)).fetchone()
            serialized = encoded(position)
            if row["status"] == "completed" and row["result"] == serialized:
                return
            if row["status"] != "running":
                raise ValueError("position cannot overwrite a completed or undispatched job")
            if answer_phase:
                for reply in replies:
                    self.db.execute("UPDATE questions SET answer=? WHERE id=? AND recipient=?",
                                    (reply["answer"].strip(), reply["question_id"], role))
            self.db.execute("UPDATE jobs SET status='completed',result=? WHERE id=?", (serialized, job_id))
            self.event("position_completed", {"job_id": job_id, "position_hash": fingerprint(position)})

    def report(self):
        with self.lock:
            return {"case_id": self.case_id, "case_hash": self.case_hash, "phase": self.phase(),
                    "blind": self.results("blind"), "critique": self.results("critique"),
                    "question_round": self.results("questions"), "answers": self.results("answers"),
                    "final_positions": self.results("final"), "question_threads": self.question_threads(),
                    "synthesis": self.results("synthesis"),
                    "jobs": [dict(row) for row in self.db.execute("SELECT id,phase,recipient,status,turn_marker FROM jobs ORDER BY rowid")],
                    "events": [dict(row) for row in self.db.execute("SELECT * FROM events ORDER BY id")],
                    "clinical_validation": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, body):
        content = encoded(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def handle_request(self):
        bus = self.server.bus
        authorization = self.headers.get("Authorization", "")
        admin = hmac.compare_digest(authorization, "Bearer " + bus.admin_token)
        role = None if admin else bus.authenticate(authorization)
        if not admin and role is None:
            return self.reply(401, {"error": "unauthorized"})
        try:
            if self.command == "GET":
                if self.path == "/report" and admin:
                    return self.reply(200, bus.report())
                if self.path == "/job" and role:
                    return self.reply(200, bus.job(role))
                return self.reply(403, {"error": "endpoint not available for this identity"})
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 12000:
                return self.reply(413, {"error": "invalid payload size"})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("JSON object required")
            if data.get("case_id") != bus.case_id or data.get("case_hash") != bus.case_hash:
                raise ValueError("case input binding mismatch")
            if self.path == "/register" and admin:
                bus.register(data["role"], data["session_id"], data["role_token"])
            elif self.path == "/phase" and admin:
                bus.transition(data["phase"])
            elif self.path == "/started" and role:
                bus.started(role, data["session_id"], data["job_id"], data["turn_marker"])
            elif self.path == "/position" and role:
                bus.submit(role, data["session_id"], data["job_id"], data["position"])
            elif self.path == "/question" and role:
                receipt = bus.ask(role, data["session_id"], data["recipient"], data["body"], data["dedupe_key"])
                return self.reply(200, receipt)
            else:
                return self.reply(403, {"error": "endpoint not available for this identity"})
            self.reply(200, {"accepted": True, "phase": bus.phase()})
        except (ValueError, KeyError, TypeError) as exc:
            self.reply(400, {"error": str(exc)})

    do_GET = handle_request
    do_POST = handle_request
