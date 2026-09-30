"""Review synthetic extraction candidates before creating a case version.

Only marked SYNTH-* fixtures are supported. This is not a clinical case store.
"""

import hashlib
import json
import re
from pathlib import Path
import sqlite3
import threading
from datetime import date, datetime, timezone
from uuid import uuid4

from intake.extract import LAB, SYNTHETIC_CASE, check_fixture, checksum, parse_lines


FACT_TYPES = frozenset({"symptom", "history", "medication", "prior_diagnosis", "imaging_report",
                        "pathology_report", "family_history", "test_report", "exposure"})
ATTRIBUTIONS = frozenset({"patient_reported", "clinician_documented", "source_text"})
BLOCKING_WARNINGS = ("ocr failed", "no extracted text", "not extracted", "may be omitted",
                     "unresolved embedded image", "formula has no saved value")


def validate_event_date(event_date, date_precision):
    """Retain source precision; do not invent a day for a known year/month."""
    if date_precision == "unknown":
        if event_date not in (None, ""):
            raise ValueError("unknown event date must be null")
        return None
    if date_precision not in ("exact", "approximate") or not isinstance(event_date, str):
        raise ValueError("invalid date precision or date")
    if date_precision == "exact":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_date):
            raise ValueError("exact date requires YYYY-MM-DD")
        date.fromisoformat(event_date)
    elif re.fullmatch(r"\d{4}", event_date):
        date.fromisoformat(event_date + "-01-01")
    elif re.fullmatch(r"\d{4}-\d{2}", event_date):
        date.fromisoformat(event_date + "-01")
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_date):
        date.fromisoformat(event_date)
    else:
        raise ValueError("approximate date must be YYYY, YYYY-MM or YYYY-MM-DD")
    return event_date


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class ConflictError(ValueError):
    """Optimistic revision or evidence state changed before a decision."""


class ReviewStore:
    def __init__(self, root):
        self.root = check_fixture(root, root)
        file = check_fixture(self.root / "extraction.json", root)
        raw = file.read_bytes()
        self.snapshot_hash = hashlib.sha256(raw).hexdigest()
        self.snapshot = json.loads(raw)
        self.case_id = self.snapshot.get("case_id")
        if (not isinstance(self.case_id, str) or not SYNTHETIC_CASE.fullmatch(self.case_id)
                or self.snapshot.get("status") != "unverified_synthetic_extraction"):
            raise ValueError("review requires an unverified synthetic extraction")
        self.documents = {doc["source_id"]: doc for doc in self.snapshot["documents"]}
        if len(self.documents) != len(self.snapshot["documents"]):
            raise ValueError("duplicate source IDs")
        self.candidates = {}
        self.coverage_catalog = {}
        for doc in self.documents.values():
            for block in doc["blocks"]:
                if block["id"] in self.coverage_catalog:
                    raise ValueError("duplicate source block ID")
                locator = block["locator"]
                image_has_ocr = any(
                    other["type"] == "ocr_line" and all(other["locator"].get(key) == locator.get(key)
                                                        for key in ("paragraph", "run", "image"))
                    for other in doc["blocks"]
                ) if block["type"] == "image" else False
                self.coverage_catalog[block["id"]] = {"id": block["id"], "source_id": doc["source_id"],
                                                       "type": "block", "block_type": block["type"],
                                                       "text": block["text"][:250],
                                                       "blocking": (block["type"] == "image" and not image_has_ocr)
                                                                   or (block["type"] == "cell" and bool(block.get("formula"))
                                                                       and block.get("cached_value") is None)}
            for index, warning in enumerate(doc.get("warnings", []), 1):
                self._catalog_warning(doc, f"warning:{index}", warning)
            for page in doc.get("pages", []):
                for index, warning in enumerate(page.get("warnings", []), 1):
                    self._catalog_warning(doc, f"page:{page['page']}:warning:{index}", warning)
            for candidate in doc["candidates"]:
                if candidate["id"] in self.candidates or candidate["source"]["document_id"] != doc["source_id"]:
                    raise ValueError("candidate/source IDs are invalid")
                block = next((b for b in doc["blocks"] if b["id"] == candidate["source"]["block_id"]), None)
                if not block or block["text"] != candidate["source"]["exact_text"]:
                    raise ValueError("candidate is not backed by its original source block")
                self.candidates[candidate["id"]] = (candidate, doc)
        self.lock = threading.RLock()
        dbpath = self.root / "review.sqlite"
        if dbpath.is_symlink():
            raise ValueError("symlinked review database is not allowed")
        self.db = sqlite3.connect(dbpath, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions (
                candidate_id TEXT PRIMARY KEY, action TEXT NOT NULL, reviewer TEXT NOT NULL,
                note TEXT NOT NULL, correction_id TEXT, effective_value TEXT, updated TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS versions (
                number INTEGER PRIMARY KEY, created TEXT NOT NULL, content TEXT NOT NULL,
                hash TEXT NOT NULL, previous_hash TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL,
                at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, block_id TEXT NOT NULL,
                category TEXT NOT NULL, statement TEXT NOT NULL, event_date TEXT,
                date_precision TEXT NOT NULL, attribution TEXT NOT NULL,
                proposer TEXT NOT NULL, created TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT 'pending', reviewer TEXT, review_note TEXT, updated TEXT
            );
            CREATE TABLE IF NOT EXISTS coverage (
                item_id TEXT PRIMARY KEY, action TEXT NOT NULL,
                reviewer TEXT NOT NULL, note TEXT NOT NULL, updated TEXT NOT NULL
            );
        """)
        recorded = self.db.execute("SELECT value FROM meta WHERE key='snapshot_sha256'").fetchone()
        if recorded and recorded["value"] != self.snapshot_hash:
            raise ConflictError("source extraction changed since review began")
        if not recorded:
            self.db.execute("INSERT INTO meta VALUES('snapshot_sha256',?)", (self.snapshot_hash,))
            self.db.execute("INSERT INTO meta VALUES('revision','0')")
            self.db.execute("INSERT INTO meta VALUES('case_id',?)", (self.case_id,))
            self.db.commit()
        else:
            existing_case = self.db.execute("SELECT value FROM meta WHERE key='case_id'").fetchone()
            if existing_case is None and self.case_id == "SYNTH-001":
                self.db.execute("INSERT INTO meta VALUES('case_id',?)", (self.case_id,))
                self.db.commit()
            elif not existing_case or existing_case["value"] != self.case_id:
                raise ConflictError("review database belongs to another case")

    def close(self):
        self.db.close()

    def _catalog_warning(self, doc, suffix, text):
        identifier = f"{doc['source_id']}:{suffix}"
        if identifier in self.coverage_catalog:
            raise ValueError("duplicate extraction warning ID")
        self.coverage_catalog[identifier] = {"id": identifier, "source_id": doc["source_id"],
                                             "type": "warning", "block_type": None, "text": text,
                                             "blocking": any(marker in text.casefold() for marker in BLOCKING_WARNINGS)}

    def verify_source(self, doc):
        name = doc["filename"]
        if Path(name).name != name:
            raise ValueError("invalid source filename")
        source = check_fixture(self.root / name, self.root)
        if checksum(source) != doc["sha256"]:
            raise ConflictError("original source changed: " + doc["source_id"])

    def verify_snapshot(self):
        if hashlib.sha256((self.root / "extraction.json").read_bytes()).hexdigest() != self.snapshot_hash:
            raise ConflictError("extraction changed since review began")
        for doc in self.documents.values():
            self.verify_source(doc)

    def current_revision(self):
        return int(self.db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()["value"])

    def _transaction(self, expected_revision):
        if not isinstance(expected_revision, int):
            raise ValueError("expected integer revision required")
        self.db.execute("BEGIN IMMEDIATE")
        if expected_revision != self.current_revision():
            self.db.rollback()
            raise ConflictError("review changed; reload before submitting")

    def _bump(self):
        self.db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

    def _correction(self, candidate, doc, correction_id):
        ledger = self.root / "corrections.jsonl"
        if ledger.is_symlink() or not ledger.is_file():
            raise ValueError("correction ledger missing or invalid")
        events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        proposal = next((e for e in events if e.get("event_id") == correction_id and e.get("action") == "propose"), None)
        approval = next((e for e in events if e.get("proposal_id") == correction_id and e.get("action") == "review" and e.get("decision") == "accepted"), None)
        if (not proposal or not approval or proposal.get("source_id") != doc["source_id"]
                or proposal.get("source_sha256") != doc["sha256"]
                or proposal.get("block_id") != candidate["source"]["block_id"]
                or proposal.get("original_text") != candidate["source"]["exact_text"]
                or proposal.get("author", "").strip().casefold() == approval.get("reviewer", "").strip().casefold()):
            raise ValueError("candidate needs an accepted correction for its original block")
        if not LAB.fullmatch(proposal["suggested_text"].strip()):
            raise ValueError("corrected text must be one complete supported lab line")
        parsed = parse_lines([{"text": proposal["suggested_text"]}], source_id=doc["source_id"],
                             page_number=candidate["source"]["page"], channel="review", date=candidate["collection_date"])
        if (len(parsed) != 1 or parsed[0]["analyte_key"] != candidate["analyte_key"]
                or parsed[0]["unit_original"].casefold() != candidate["unit_original"].casefold()
                or proposal["suggested_text"].strip() != parsed[0]["source"]["exact_text"].strip()):
            raise ValueError("correction changes analyte/unit or cannot be parsed as a single lab line")
        return parsed[0]["value_decimal"]

    def decide(self, candidate_id, action, reviewer, note, expected_revision, *, correction_id=None):
        if candidate_id not in self.candidates or action not in ("verify", "exclude", "defer"):
            raise ValueError("unknown candidate or decision")
        if (not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 80
                or not isinstance(note, str) or len(note.strip()) < 4 or len(note) > 1000):
            raise ValueError("reviewer and explanatory note are required")
        if correction_id and action != "verify":
            raise ValueError("correction may only accompany verification")
        candidate, doc = self.candidates[candidate_id]
        self.verify_snapshot()
        chosen = (self._correction(candidate, doc, correction_id) if correction_id else candidate["value_decimal"]) if action == "verify" else None
        with self.lock:
            self._transaction(expected_revision)
            try:
                old = self.db.execute("SELECT * FROM decisions WHERE candidate_id=?", (candidate_id,)).fetchone()
                if old and old["action"] == action and old["effective_value"] == chosen and old["correction_id"] == correction_id:
                    raise ValueError("identical decision already recorded")
                self.db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET action=excluded.action,reviewer=excluded.reviewer,note=excluded.note,correction_id=excluded.correction_id,effective_value=excluded.effective_value,updated=excluded.updated", (candidate_id, action, reviewer.strip(), note.strip(), correction_id, chosen, timestamp()))
                payload = {"candidate_id": candidate_id, "action": action, "reviewer": reviewer.strip(),
                           "note": note.strip(), "correction_id": correction_id, "value": chosen,
                           "previous": dict(old) if old else None}
                self.db.execute("INSERT INTO events(event,at,payload) VALUES(?,?,?)", ("candidate_decision", timestamp(), json.dumps(payload)))
                self._bump()
                self.db.commit()
                return self.state()
            except BaseException:
                self.db.rollback()
                raise

    def state(self):
        with self.lock:
            decisions = {row["candidate_id"]: dict(row) for row in self.db.execute("SELECT * FROM decisions")}
            facts = [dict(row) for row in self.db.execute("SELECT * FROM facts ORDER BY created,id")]
            latest = self.db.execute("SELECT number,hash FROM versions ORDER BY number DESC LIMIT 1").fetchone()
            last_commit = self.db.execute("SELECT id FROM events WHERE event='case_committed' ORDER BY id DESC LIMIT 1").fetchone()
            changed = bool(self.db.execute("SELECT 1 FROM events WHERE event IN ('candidate_decision','fact_reviewed','coverage_decision') AND id>? LIMIT 1",
                                           (last_commit["id"] if last_commit else 0,)).fetchone())
            conflicting = any(len({decisions[cid]["effective_value"] for cid in conflict["candidate_ids"]
                                   if cid in decisions and decisions[cid]["action"] == "verify"}) > 1
                              for conflict in self.snapshot["conflicts"])
            coverage = self.coverage_status(decisions=decisions, facts=facts)
            return {"case_id": self.case_id, "revision": self.current_revision(),
                    "case_version": latest["number"] if latest else 0,
                    "candidates": [{"candidate": candidate, "source_filename": doc["filename"],
                                    "decision": decisions.get(candidate["id"])}
                                   for candidate, doc in self.candidates.values()],
                    "conflicts": self.snapshot["conflicts"],
                    "unreviewed": len(self.candidates) - len(decisions),
                    "facts": facts, "pending_facts": sum(f["action"] == "pending" for f in facts),
                    "coverage": coverage,
                    "can_commit": changed and len(decisions) == len(self.candidates)
                                  and not any(f["action"] == "pending" for f in facts)
                                  and (any(d["action"] == "verify" for d in decisions.values())
                                       or any(f["action"] == "verify" for f in facts)) and not conflicting,
                    "ready_for_diagnosis": False}

    def coverage_status(self, *, decisions=None, facts=None):
        with self.lock:
            if decisions is None:
                decisions = {row["candidate_id"]: dict(row) for row in self.db.execute("SELECT * FROM decisions")}
            if facts is None:
                facts = [dict(row) for row in self.db.execute("SELECT * FROM facts")]
            manual = {row["item_id"]: dict(row) for row in self.db.execute("SELECT * FROM coverage")}
            verified = {candidate["source"]["block_id"] for cid, (candidate, _doc) in self.candidates.items()
                        if cid in decisions and decisions[cid]["action"] == "verify"}
            verified.update(fact["block_id"] for fact in facts if fact["action"] == "verify")
            items = []
            for key, item in self.coverage_catalog.items():
                action = manual.get(key)
                if item["blocking"]:
                    state = "source_limitation"
                elif item["type"] == "block" and key in verified:
                    state = "linked_to_reviewed_observation"
                elif action and ((item["type"] == "block" and action["action"] == "reviewed_context")
                                 or (item["type"] == "warning" and action["action"] == "acknowledged")):
                    state = action["action"]
                elif action and action["action"] == "defer":
                    state = "deferred"
                else:
                    state = "unreviewed"
                items.append({**item, "state": state, "decision": action})
            outstanding = [item for item in items if item["state"] in ("source_limitation", "deferred", "unreviewed")]
            return {"total": len(items), "accounted_for": len(items) - len(outstanding),
                    "outstanding": len(outstanding), "complete": not outstanding,
                    "items": items, "scope": "extracted_blocks_only_not_full_clinical_completeness"}

    def decide_coverage(self, item_id, action, reviewer, note, expected_revision):
        item = self.coverage_catalog.get(item_id)
        if not item:
            raise ValueError("unknown source block or extraction warning")
        allowed = ("reviewed_context", "defer") if item["type"] == "block" else ("acknowledged", "defer")
        if action not in allowed or (item["blocking"] and action != "defer"):
            raise ValueError("unreadable or unsupported content cannot be marked complete")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 80:
            raise ValueError("reviewer identifier required")
        if not isinstance(note, str) or len(note.strip()) < 4 or len(note) > 1000:
            raise ValueError("explain what was reviewed or why it is deferred")
        self.verify_snapshot()
        with self.lock:
            self._transaction(expected_revision)
            try:
                previous = self.db.execute("SELECT * FROM coverage WHERE item_id=?", (item_id,)).fetchone()
                if previous and previous["action"] == action and previous["note"] == note.strip():
                    raise ValueError("identical coverage decision already recorded")
                self.db.execute("INSERT INTO coverage VALUES(?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET action=excluded.action,reviewer=excluded.reviewer,note=excluded.note,updated=excluded.updated",
                                (item_id, action, reviewer.strip(), note.strip(), timestamp()))
                self.db.execute("INSERT INTO events(event,at,payload) VALUES(?,?,?)",
                                ("coverage_decision", timestamp(), json.dumps({"item_id": item_id,
                                                                                   "action": action, "reviewer": reviewer.strip(),
                                                                                   "previous": dict(previous) if previous else None})))
                self._bump()
                self.db.commit()
                return self.state()
            except BaseException:
                self.db.rollback()
                raise

    def record_correction(self, event, expected_revision):
        with self.lock:
            self._transaction(expected_revision)
            try:
                self.db.execute("INSERT INTO events(event,at,payload) VALUES(?,?,?)",
                                ("correction_" + event["action"], timestamp(), json.dumps({"event_id": event["event_id"]})))
                self._bump()
                self.db.commit()
                return self.state()
            except BaseException:
                self.db.rollback()
                raise

    def propose_fact(self, source_id, block_id, category, statement, event_date,
                     date_precision, attribution, proposer, expected_revision):
        doc = self.documents.get(source_id)
        block = next((item for item in doc["blocks"] if item["id"] == block_id), None) if doc else None
        if not block or block["type"] == "image":
            raise ValueError("fact must reference an extracted text or cell block")
        if block["type"] == "cell" and block.get("formula") and block.get("cached_value") is None:
            raise ValueError("a formula without a saved value cannot support a fact")
        if category not in FACT_TYPES or attribution not in ATTRIBUTIONS or date_precision not in ("exact", "approximate", "unknown"):
            raise ValueError("invalid fact type, attribution or date precision")
        if not isinstance(statement, str) or not statement.strip() or len(statement) > 1200:
            raise ValueError("source statement required (maximum 1200 characters)")
        if not isinstance(proposer, str) or not proposer.strip() or len(proposer) > 80:
            raise ValueError("proposer identifier required")
        event_date = validate_event_date(event_date, date_precision)
        self.verify_snapshot()
        fact_id = "FACT-" + uuid4().hex
        with self.lock:
            self._transaction(expected_revision)
            try:
                now = timestamp()
                self.db.execute("INSERT INTO facts(id,source_id,block_id,category,statement,event_date,date_precision,attribution,proposer,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (fact_id, source_id, block_id, category, statement.strip(), event_date,
                                 date_precision, attribution, proposer.strip(), now))
                self.db.execute("INSERT INTO events(event,at,payload) VALUES(?,?,?)",
                                ("fact_proposed", now, json.dumps({"fact_id": fact_id, "block_id": block_id,
                                                                     "source_sha256": doc["sha256"]})))
                self._bump()
                self.db.commit()
                return {"fact_id": fact_id, "status": "pending", "revision": self.current_revision()}
            except BaseException:
                self.db.rollback()
                raise

    def decide_fact(self, fact_id, action, reviewer, note, expected_revision):
        if action not in ("verify", "exclude", "defer") or not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("decision and reviewer are required")
        if not isinstance(note, str) or len(note.strip()) < 4 or len(note) > 1000:
            raise ValueError("explain why this statement matches or does not match its source")
        self.verify_snapshot()
        with self.lock:
            self._transaction(expected_revision)
            try:
                fact = self.db.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
                if not fact:
                    raise ValueError("unknown proposed fact")
                if fact["action"] != "pending":
                    raise ValueError("fact was already reviewed; propose a new statement if it changes")
                if reviewer.strip().casefold() == fact["proposer"].casefold():
                    raise ValueError("fact needs an independent reviewer")
                self.db.execute("UPDATE facts SET action=?,reviewer=?,review_note=?,updated=? WHERE id=?",
                                (action, reviewer.strip(), note.strip(), timestamp(), fact_id))
                self.db.execute("INSERT INTO events(event,at,payload) VALUES(?,?,?)",
                                ("fact_reviewed", timestamp(), json.dumps({"fact_id": fact_id, "action": action,
                                                                              "reviewer": reviewer.strip()})))
                self._bump()
                self.db.commit()
                return self.state()
            except BaseException:
                self.db.rollback()
                raise

    def commit(self, expected_revision):
        self.verify_snapshot()
        with self.lock:
            self._transaction(expected_revision)
            try:
                decisions = {r["candidate_id"]: dict(r) for r in self.db.execute("SELECT * FROM decisions")}
                facts = [dict(row) for row in self.db.execute("SELECT * FROM facts ORDER BY created,id")]
                missing = set(self.candidates) - set(decisions)
                if missing:
                    raise ValueError(f"review all candidates before committing ({len(missing)} remaining)")
                if any(fact["action"] == "pending" for fact in facts):
                    raise ValueError("review all proposed source statements before committing")
                for conflict in self.snapshot["conflicts"]:
                    values = {decisions[cid]["effective_value"] for cid in conflict["candidate_ids"]
                              if decisions[cid]["action"] == "verify"}
                    if len(values) > 1:
                        raise ValueError("conflicting verified values need review: " + conflict["analyte"])
                coverage = self.coverage_status(decisions=decisions, facts=facts)
                observations = []
                held = []
                source_statements = []
                timeline = []
                for candidate_id, (candidate, doc) in self.candidates.items():
                    decision = decisions[candidate_id]
                    if decision["action"] != "verify":
                        held.append({"candidate_id": candidate_id, "status": decision["action"], "reason": decision["note"]})
                        continue
                    if decision["correction_id"] and self._correction(candidate, doc, decision["correction_id"]) != decision["effective_value"]:
                        raise ConflictError("correction changed since review; reassess candidate")
                    observations.append({
                        "id": candidate_id, "type": "lab", "analyte_original": candidate["analyte_original"],
                        "value_decimal": decision["effective_value"], "original_value": candidate["value_original"],
                        "unit_original": candidate["unit_original"], "reference_range_original": candidate["reference_range_original"],
                        "collection_date": candidate["collection_date"], "source_sha256": doc["sha256"],
                        "source": candidate["source"], "correction_id": decision["correction_id"],
                        "reviewed_by": decision["reviewer"], "review_note": decision["note"],
                    })
                    if candidate["collection_date"]:
                        timeline.append({"date": candidate["collection_date"], "precision": "exact",
                                         "kind": "lab", "observation_id": candidate_id})
                for fact in facts:
                    if fact["action"] != "verify":
                        held.append({"fact_id": fact["id"], "status": fact["action"],
                                     "reason": fact["review_note"]})
                        continue
                    doc = self.documents[fact["source_id"]]
                    block = next(item for item in doc["blocks"] if item["id"] == fact["block_id"])
                    source_statements.append({
                        "id": fact["id"], "category": fact["category"], "statement": fact["statement"],
                        "attribution": fact["attribution"], "event_date": fact["event_date"],
                        "date_precision": fact["date_precision"], "source_id": doc["source_id"],
                        "source_sha256": doc["sha256"], "source_block_id": block["id"],
                        "original_text": block["text"], "original_locator": block["locator"],
                        "proposed_by": fact["proposer"], "reviewed_by": fact["reviewer"],
                        "review_note": fact["review_note"], "status": "reviewed_source_statement",
                    })
                    if fact["event_date"]:
                        timeline.append({"date": fact["event_date"], "precision": fact["date_precision"],
                                         "kind": fact["category"], "observation_id": fact["id"]})
                timeline.sort(key=lambda item: (item["date"], item["observation_id"]))
                previous = self.db.execute("SELECT number,hash,content FROM versions ORDER BY number DESC LIMIT 1").fetchone()
                if previous:
                    last_commit = self.db.execute("SELECT id FROM events WHERE event='case_committed' ORDER BY id DESC LIMIT 1").fetchone()
                    changed = self.db.execute("SELECT 1 FROM events WHERE event IN ('candidate_decision','fact_reviewed','coverage_decision') AND id>? LIMIT 1",
                                              (last_commit["id"],)).fetchone()
                    if not changed:
                        raise ValueError("no new reviewed decisions since last case version")
                if not observations and not source_statements:
                    raise ValueError("at least one verified observation or source statement required")
                content = {"case_id": self.case_id, "status": "reviewed_subset", "ready_for_diagnosis": False,
                           "source_snapshot_sha256": self.snapshot_hash, "observations": observations,
                           "source_statements": source_statements, "timeline": timeline,
                           "held_candidates": held, "coverage": coverage,
                           "intake_coverage_complete": coverage["complete"], "unabstracted_content": not coverage["complete"]}
                if previous:
                    prior_content = json.loads(previous["content"])
                    prior_content.pop("version", None)
                    if prior_content == content:
                        raise ValueError("no changes since last case version")
                number = previous["number"] + 1 if previous else 1
                content["version"] = number
                fingerprint = digest({"content": content, "previous_hash": previous["hash"] if previous else None})
                self.db.execute("INSERT INTO versions VALUES(?,?,?,?,?)", (number, timestamp(), json.dumps(content, ensure_ascii=False), fingerprint, previous["hash"] if previous else None))
                self.db.execute("INSERT INTO events(event,at,payload) VALUES(?,?,?)", ("case_committed", timestamp(), json.dumps({"version": number, "hash": fingerprint})))
                self._bump()
                self.db.commit()
                return {**content, "hash": fingerprint, "previous_hash": previous["hash"] if previous else None}
            except BaseException:
                self.db.rollback()
                raise

    def version(self, number=None):
        with self.lock:
            row = (self.db.execute("SELECT * FROM versions ORDER BY number DESC LIMIT 1").fetchone()
                   if number is None else self.db.execute("SELECT * FROM versions WHERE number=?", (number,)).fetchone())
            if not row:
                raise ValueError("case version does not exist")
            return {**json.loads(row["content"]), "hash": row["hash"], "previous_hash": row["previous_hash"]}
