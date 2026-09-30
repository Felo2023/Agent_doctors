"""Local durable mail bus for sequential, synthetic OpenCode case demonstrations.

This is deliberately NOT the clinical case store or a general-purpose secure service.
The synthetic launcher passes admin credentials through process environment,
not command-line arguments, and binds one reviewed case version per process.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from runtime.models import resolve_model



CASE = "SYNTH-001"
SYNTHETIC_CASE = re.compile(r"SYNTH-[0-9]{3,6}\Z")
ROLES = ("doctor-a", "doctor-b")
MAX_BODY = 8192


def open_db(path):
    db = sqlite3.connect(path, timeout=15, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS doctors (
            role TEXT PRIMARY KEY, url TEXT NOT NULL, session_id TEXT NOT NULL,
            model TEXT NOT NULL, token_hash TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS mail (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL,
            sender TEXT NOT NULL, recipient TEXT NOT NULL, body TEXT NOT NULL,
            dedupe_key TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
            attempt INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
            dispatched REAL, completed REAL, error TEXT,
            UNIQUE(sender, dedupe_key)
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mail_id INTEGER, event TEXT NOT NULL,
            at REAL NOT NULL, detail TEXT
        );
        CREATE TABLE IF NOT EXISTS phase (
            case_id TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    db.commit()
    return db


class Bus:
    def __init__(self, path, token, poll=0.4, deadline=180, server_passwords=None,
                 case_id=CASE, case_root=None):
        if not isinstance(case_id, str) or not SYNTHETIC_CASE.fullmatch(case_id):
            raise ValueError("only synthetic case IDs are accepted")
        self.db = open_db(path)
        self.lock = threading.RLock()
        self.case_id = case_id
        owner = self.db.execute("SELECT value FROM meta WHERE key='case_id'").fetchone()
        if owner and owner["value"] != case_id:
            self.db.close()
            raise ValueError("mail database belongs to another case")
        if not owner:
            existing = self.db.execute("SELECT DISTINCT case_id FROM mail").fetchall()
            if any(row["case_id"] != case_id for row in existing):
                self.db.close()
                raise ValueError("mail database contains another case")
            self.db.execute("INSERT INTO meta VALUES('case_id',?)", (case_id,))
        self.db.execute("INSERT OR IGNORE INTO phase(case_id,value) VALUES(?,'setup')", (case_id,))
        self.db.commit()
        self.case_store = None
        self.case_version = None
        self.case_hash = None
        if case_root is not None:
            from case_review.store import ReviewStore
            self.case_store = ReviewStore(case_root)
            if self.case_store.case_id != case_id:
                self.case_store.close()
                self.db.close()
                raise ValueError("reviewed case and mail case IDs differ")
            self.case_store.verify_snapshot()
            latest = self.case_store.version()
            self.case_version, self.case_hash = latest["version"], latest["hash"]
            binding = self.db.execute("SELECT value FROM meta WHERE key='case_binding'").fetchone()
            intended = f"{self.case_version}:{self.case_hash}"
            if binding and binding["value"] != intended:
                self.case_store.close()
                self.db.close()
                raise ValueError("mail database is bound to a different immutable case version")
            if not binding:
                self.db.execute("INSERT INTO meta VALUES('case_binding',?)", (intended,))
                self.db.commit()
        self.token = token
        self.server_passwords = server_passwords or {}
        self.poll = poll
        self.deadline = deadline
        self.running = False
        # In-flight dispatches remain in-flight and are reconciled against OpenCode
        # history by tick(). Never automatically resend an ambiguous HTTP request.

    def event(self, mail_id, name, detail=None):
        self.db.execute("INSERT INTO events(mail_id,event,at,detail) VALUES(?,?,?,?)", (mail_id, name, time.time(), detail))

    def register(self, role, url, session_id, model, role_token):
        if role not in ROLES or not isinstance(session_id, str) or not session_id.startswith("ses_"):
            raise ValueError("invalid role or session")
        parsed = urlparse(url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.path not in ("", "/"):
            raise ValueError("doctor must have a loopback HTTP URL")
        if not self.server_passwords.get(role):
            raise ValueError("server password not configured on bus")
        if not isinstance(role_token, str) or len(role_token) < 20:
            raise ValueError("role credential required")
        if model not in (resolve_model("opus"), resolve_model("sol")):
            raise ValueError("model not in synthetic prototype roster")
        with self.lock:
            previous = self.db.execute("SELECT session_id,token_hash,model FROM doctors WHERE role=?", (role,)).fetchone()
            token_hash = hashlib.sha256(role_token.encode()).hexdigest()
            if previous and (previous["session_id"] != session_id or previous["model"] != model
                             or not hmac.compare_digest(previous["token_hash"], token_hash)):
                raise ValueError("cannot rebind or rotate a role without a new run")
            self.db.execute("INSERT INTO doctors(role,url,session_id,model,token_hash) VALUES(?,?,?,?,?) ON CONFLICT(role) DO UPDATE SET url=excluded.url", (role, url, session_id, model, token_hash))
            self.db.commit()

    def authenticate_role(self, authorization):
        if not authorization.startswith("Bearer "):
            return None
        token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with self.lock:
            return next((row["role"] for row in self.db.execute("SELECT role,token_hash FROM doctors")
                         if hmac.compare_digest(row["token_hash"], token_hash)), None)

    def send(self, sender_role, sender_session, recipient, body, dedupe_key,
             case_id=None, case_version=None):
        if recipient not in ROLES or not isinstance(body, str) or not body.strip() or len(body) > 2000:
            raise ValueError("invalid recipient or body")
        if not isinstance(dedupe_key, str) or not (1 <= len(dedupe_key) <= 128):
            raise ValueError("dedupe_key is required")
        with self.lock:
            if self.case_store is not None:
                self.read_case()
            if case_id is not None and case_id != self.case_id:
                raise ValueError("mail case ID does not match this run")
            if self.case_version is not None and case_version != self.case_version:
                raise ValueError("mail case version does not match this run")
            if self.phase() != "discussion":
                raise ValueError("mail disabled outside discussion phase")
            row = self.db.execute("SELECT session_id FROM doctors WHERE role=?", (sender_role,)).fetchone()
            if row is None or row["session_id"] != sender_session or sender_role == recipient:
                raise ValueError("sender credential/session mismatch or self-mail")
            if not self.db.execute("SELECT 1 FROM doctors WHERE role=?", (recipient,)).fetchone():
                raise ValueError("recipient not registered")
            sender = sender_role
            old = self.db.execute("SELECT * FROM mail WHERE sender=? AND dedupe_key=?", (sender, dedupe_key)).fetchone()
            if old:
                if old["recipient"] != recipient or old["body"] != body:
                    raise ValueError("dedupe key reused with different content")
                return {"mail_id": old["id"], "state": old["state"], "duplicate": True}
            cur = self.db.execute("INSERT INTO mail(case_id,sender,recipient,body,dedupe_key,created) VALUES(?,?,?,?,?,?)", (self.case_id, sender, recipient, body.strip(), dedupe_key, time.time()))
            self.event(cur.lastrowid, "queued")
            self.db.commit()
            return {"mail_id": cur.lastrowid, "state": "queued", "duplicate": False}

    def phase(self):
        return self.db.execute("SELECT value FROM phase WHERE case_id=?", (self.case_id,)).fetchone()["value"]

    def set_phase(self, value):
        if value not in ("setup", "discussion", "closed"):
            raise ValueError("invalid phase")
        with self.lock:
            if value == "discussion" and self.case_store is not None:
                self.read_case()
            old = self.phase()
            if (old, value) not in (("setup", "discussion"), ("discussion", "closed")):
                raise ValueError("phase transition not allowed")
            if value == "discussion" and self.db.execute("SELECT COUNT(*) FROM doctors").fetchone()[0] != 2:
                raise ValueError("both doctors must be registered")
            if value == "closed" and self.db.execute("SELECT COUNT(*) FROM mail WHERE state IN ('queued','dispatched','processing')").fetchone()[0]:
                raise ValueError("cannot close with pending mail")
            self.db.execute("UPDATE phase SET value=? WHERE case_id=?", (value, self.case_id))
            self.event(None, "phase", value)
            self.db.commit()

    def set_paused(self, role, paused):
        if role not in ROLES or not isinstance(paused, bool):
            raise ValueError("invalid pause request")
        with self.lock:
            row = self.db.execute("SELECT 1 FROM doctors WHERE role=?", (role,)).fetchone()
            if row is None:
                raise ValueError("unknown doctor")
            # A pause stops future dispatches. An already dispatched turn cannot
            # safely be preempted; caller must wait for it before human typing.
            self.db.execute("UPDATE doctors SET paused=? WHERE role=?", (int(paused), role))
            self.event(None, "paused" if paused else "resumed", role)
            self.db.commit()
            in_flight = self.db.execute("SELECT 1 FROM mail WHERE recipient=? AND state IN ('dispatched','processing')", (role,)).fetchone()
            return {"paused": paused, "safe_for_human_turn": bool(paused and not in_flight)}

    def resolve(self, mid, action):
        if action not in ("cancel", "retry") or not isinstance(mid, int):
            raise ValueError("explicit cancel or retry required")
        with self.lock:
            row = self.db.execute("SELECT state FROM mail WHERE id=?", (mid,)).fetchone()
            if not row or row["state"] != "uncertain":
                raise ValueError("only uncertain mail can be resolved")
            # Retry is a *manual* decision; the operator must first inspect the
            # recipient session for a delayed/partial turn to avoid duplication.
            new_state = "queued" if action == "retry" else "cancelled"
            self.db.execute("UPDATE mail SET state=?, dispatched=NULL, completed=NULL, error=NULL WHERE id=?", (new_state, mid))
            self.event(mid, "operator_" + action)
            self.db.commit()
            return {"mail_id": mid, "state": new_state}

    def request(self, doctor, path, method="GET", body=None):
        authorization = base64.b64encode(("opencode:" + self.server_passwords[doctor["role"]]).encode()).decode()
        headers = {"Authorization": "Basic " + authorization, "Content-Type": "application/json"}
        req = urllib.request.Request(doctor["url"] + path, headers=headers, method=method, data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=8) as response:
            content = response.read()
            return response.status, json.loads(content) if content else None

    def update(self, mid, state, error=None):
        with self.lock:
            self.db.execute("UPDATE mail SET state=?, error=?, completed=? WHERE id=?", (state, error, time.time() if state in ("completed", "failed", "uncertain") else None, mid))
            self.event(mid, state, error)
            self.db.commit()

    def tick(self):
        if self.case_store is not None:
            try:
                self.read_case()
            except (ValueError, OSError):
                return  # Fail closed on any snapshot/version change.
        with self.lock:
            doctors = [dict(d) for d in self.db.execute("SELECT * FROM doctors")]
        for doctor in doctors:
            role, sid = doctor["role"], doctor["session_id"]
            with self.lock:
                pending = self.db.execute("SELECT * FROM mail WHERE recipient=? AND state IN ('dispatched','processing') ORDER BY id LIMIT 1", (role,)).fetchone()
                queued = self.db.execute("SELECT * FROM mail WHERE recipient=? AND state='queued' ORDER BY id LIMIT 1", (role,)).fetchone()
                uncertain = self.db.execute("SELECT 1 FROM mail WHERE recipient=? AND state='uncertain' LIMIT 1", (role,)).fetchone()
            if not pending and not queued:
                continue
            try:
                _, health = self.request(doctor, "/global/health")
                if not health.get("healthy"):
                    continue
                _, status = self.request(doctor, "/session/status")
                busy = sid in status and status[sid].get("type") != "idle"
                if pending:
                    mid = pending["id"]
                    _, messages = self.request(doctor, f"/session/{sid}/message")
                    marker = f"MAIL-ID: {mid}"
                    user_index = next((i for i, m in enumerate(messages) if m.get("info", {}).get("role") == "user" and any(marker in p.get("text", "") for p in m.get("parts", []) if isinstance(p, dict))), None)
                    if user_index is not None:
                        self.update(mid, "processing") if pending["state"] == "dispatched" else None
                        if not busy:
                            parent_id = messages[user_index].get("info", {}).get("id")
                            for m in messages[user_index + 1:]:
                                info = m.get("info", {})
                                if (info.get("role") == "assistant" and info.get("parentID") == parent_id
                                        and info.get("time", {}).get("completed") and info.get("finish") != "tool-calls"):
                                    if info.get("error"):
                                        self.update(mid, "failed", "model response error")
                                    else:
                                        self.update(mid, "completed")
                                    break
                    if time.time() - pending["dispatched"] > self.deadline:
                        self.update(mid, "uncertain", "deadline exceeded; inspect recipient session before retry")
                elif queued and not busy and not doctor["paused"] and not uncertain:
                    mid = queued["id"]
                    prompt = (f"SYNTHETIC CASE {self.case_id}, version {self.case_version or 0}, "
                              f"hash {self.case_hash or 'none'}. MAIL-ID: {mid}\n"
                              f"From {queued['sender']}: {queued['body']}\n"
                              "Respond to this letter. Use prototype_mail only if a reply is requested.")
                    provider, model = doctor["model"].split("/", 1)
                    payload = {"agent": role, "model": {"providerID": provider, "modelID": model}, "parts": [{"type": "text", "text": prompt}]}
                    # Mark before HTTP; after a crash the state is uncertain, never automatically resent.
                    with self.lock:
                        self.db.execute("UPDATE mail SET state='dispatched', attempt=attempt+1, dispatched=? WHERE id=? AND state='queued'", (time.time(), mid))
                        self.event(mid, "dispatched")
                        self.db.commit()
                    try:
                        code, _ = self.request(doctor, f"/session/{sid}/prompt_async", "POST", payload)
                        if code != 204:
                            self.update(mid, "uncertain", "unexpected async HTTP status")
                    except Exception as exc:
                        self.update(mid, "uncertain", f"dispatch transport error: {type(exc).__name__}")
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
                continue  # Health failure is retried; do not infer agreement or resend.

    def worker(self):
        while self.running:
            try:
                self.tick()
            except Exception as exc:
                # Keep the worker alive; never leak prompt contents to stdout.
                print("mail worker error:", type(exc).__name__, flush=True)
            time.sleep(self.poll)

    def list_mail(self):
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT id,case_id,sender,recipient,body,state,attempt,error FROM mail ORDER BY id")]

    def read_case(self):
        if self.case_store is None:
            raise ValueError("no reviewed synthetic case is bound to this bus")
        with self.lock:
            self.case_store.verify_snapshot()
            version = self.case_store.version(self.case_version)
            if version["hash"] != self.case_hash or self.case_store.version()["hash"] != self.case_hash:
                raise ValueError("bound case version changed; stop and start a new run")
            return version

    def binding_status(self):
        if self.case_store is None:
            return {"case_current": None, "reason": "no reviewed case bound"}
        try:
            self.read_case()
        except (ValueError, OSError) as exc:
            return {"case_current": False, "reason": str(exc)}
        return {"case_current": True, "reason": None}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # No bodies, passwords or patient content in HTTP logs.

    def reply(self, code, value):
        data = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_request(self):
        bus = self.server.bus
        authorization = self.headers.get("Authorization", "")
        admin = hmac.compare_digest(authorization, "Bearer " + bus.token)
        role = None if admin else bus.authenticate_role(authorization)
        if not admin and role is None:
            return self.reply(401, {"error": "unauthorized"})
        try:
            if self.command == "POST":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    return self.reply(413, {"error": "invalid body size"})
                data = json.loads(self.rfile.read(length))
                if self.path == "/register":
                    if not admin:
                        return self.reply(403, {"error": "admin required"})
                    bus.register(data["role"], data["url"], data["session_id"], data["model"], data["role_token"])
                    return self.reply(200, {"registered": True})
                if self.path == "/mail":
                    if admin:
                        return self.reply(403, {"error": "role credential required"})
                    if data.get("case_id") != bus.case_id:
                        raise ValueError("unknown case")
                    return self.reply(200, bus.send(role, data["sender_session"], data["recipient"],
                                                    data["body"], data["dedupe_key"],
                                                    case_id=data["case_id"], case_version=data.get("case_version")))
                if self.path == "/phase":
                    if not admin:
                        return self.reply(403, {"error": "admin required"})
                    bus.set_phase(data["phase"])
                    return self.reply(200, {"phase": bus.phase()})
                if self.path == "/pause":
                    if not admin:
                        return self.reply(403, {"error": "admin required"})
                    return self.reply(200, bus.set_paused(data["role"], data["paused"]))
                if self.path == "/resolve":
                    if not admin:
                        return self.reply(403, {"error": "admin required"})
                    return self.reply(200, bus.resolve(data["mail_id"], data["action"]))
            if self.command == "GET" and self.path == "/mail":
                if not admin:
                    return self.reply(403, {"error": "admin required"})
                return self.reply(200, {"messages": bus.list_mail()})
            if self.command == "GET" and self.path == "/case":
                return self.reply(200, bus.read_case())
            if self.command == "GET" and self.path == "/health":
                if not admin:
                    return self.reply(403, {"error": "admin required"})
                return self.reply(200, {"healthy": bus.running, "case_id": bus.case_id,
                                        "case_version": bus.case_version, "case_hash": bus.case_hash,
                                        "phase": bus.phase(), **bus.binding_status()})
            self.reply(404, {"error": "unknown endpoint"})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.reply(400, {"error": str(exc)})

    do_GET = handle_request
    do_POST = handle_request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--case-id", default=CASE)
    parser.add_argument("--case-root", help="marked synthetic fixture with committed reviewed case; no real paths")
    parser.add_argument("--token", default=os.getenv("PROTOTYPE_BUS_TOKEN"))
    parser.add_argument("--port", type=int, default=4400)
    parser.add_argument("--poll", type=float, default=0.4)
    args = parser.parse_args()
    if not args.token or len(args.token) < 20:
        parser.error("a bus token of at least 20 characters is required")
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    bus = Bus(args.db, args.token, args.poll, server_passwords={
        "doctor-a": os.getenv("PROTOTYPE_SERVER_PASSWORD_A"),
        "doctor-b": os.getenv("PROTOTYPE_SERVER_PASSWORD_B"),
    }, case_id=args.case_id, case_root=args.case_root)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.bus = bus
    bus.running = True
    thread = threading.Thread(target=bus.worker, daemon=True)
    thread.start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        bus.running = False
        server.server_close()
        thread.join(timeout=5)
        if bus.case_store is not None:
            bus.case_store.close()
        bus.db.close()


if __name__ == "__main__":
    main()
