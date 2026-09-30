"""Separate append-only human correction events against synthetic source blocks."""

import fcntl
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from intake.extract import SYNTHETIC_CASE, check_fixture
from intake.markdown import anchor, atomic_write, escape, quote


def ledger_path(path, fixture_root):
    path = Path(path)
    check_fixture(path.parent, fixture_root)
    if path.is_symlink() or path.name != "corrections.jsonl":
        raise ValueError("correction ledger must be a plain corrections.jsonl file")
    return path.parent.resolve() / path.name


def append_event(path, event, allowed_hashes=None):
    descriptor = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        with os.fdopen(descriptor, "r+", encoding="utf-8") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            file.seek(0)
            existing = [json.loads(line) for line in file if line.strip()]
            if event["action"] == "review":
                proposal = next((e for e in existing if e["event_id"] == event["proposal_id"] and e["action"] == "propose"), None)
                if not proposal:
                    raise ValueError("unknown correction proposal")
                if proposal["source_sha256"] not in allowed_hashes:
                    raise ValueError("source hash no longer matches review snapshot")
                if proposal["author"].strip().casefold() == event["reviewer"].strip().casefold():
                    raise ValueError("correction needs an independent reviewer")
                if any(e.get("proposal_id") == event["proposal_id"] and e["action"] == "review" for e in existing):
                    raise ValueError("correction already reviewed")
            file.seek(0, os.SEEK_END)
            file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            file.flush()
            os.fsync(file.fileno())
            fcntl.flock(file, fcntl.LOCK_UN)
    except BaseException:
        # fdopen owns descriptor; no duplicate close.
        raise
    return event


def propose(snapshot, path, *, fixture_root, source_id, block_id, suggested_text, reason, author):
    if not SYNTHETIC_CASE.fullmatch(snapshot.get("case_id", "")):
        raise ValueError("only synthetic corrections are supported")
    doc = next((d for d in snapshot["documents"] if d["source_id"] == source_id), None)
    block = next((b for b in doc["blocks"] if b["id"] == block_id), None) if doc else None
    if not block or not all(isinstance(v, str) and v.strip() for v in (suggested_text, reason, author)):
        raise ValueError("correction requires an existing source block and nonempty fields")
    event = {
        "event_id": "COR-" + uuid4().hex, "action": "propose",
        "time": datetime.now(timezone.utc).isoformat(), "source_id": source_id,
        "source_sha256": doc["sha256"], "block_id": block_id,
        "original_text": block["text"], "suggested_text": suggested_text,
        "reason": reason, "author": author, "status": "pending",
    }
    stored = append_event(ledger_path(path, fixture_root), event)
    write_view(snapshot, path, fixture_root=fixture_root)
    return stored


def review(snapshot, path, *, fixture_root, proposal_id, decision, reviewer, reason):
    if not SYNTHETIC_CASE.fullmatch(snapshot.get("case_id", "")) or decision not in ("accepted", "rejected"):
        raise ValueError("invalid review decision or case")
    if not all(isinstance(v, str) and v.strip() for v in (proposal_id, reviewer, reason)):
        raise ValueError("reviewer and reason required")
    event = {"event_id": "REV-" + uuid4().hex, "action": "review", "proposal_id": proposal_id,
             "time": datetime.now(timezone.utc).isoformat(), "decision": decision,
             "reviewer": reviewer, "reason": reason}
    stored = append_event(ledger_path(path, fixture_root), event, {d["sha256"] for d in snapshot["documents"]})
    write_view(snapshot, path, fixture_root=fixture_root)
    return stored


def write_view(snapshot, path, *, fixture_root):
    path = ledger_path(path, fixture_root)
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    decisions = {event["proposal_id"]: event for event in events if event["action"] == "review"}
    lines = ["# Synthetic correction log", "", "Corrections are separate from the original source and source Markdown. An accepted event is **not** an automatic change to case facts.", ""]
    for event in events:
        if event["action"] != "propose":
            continue
        doc = next((d for d in snapshot["documents"] if d["source_id"] == event["source_id"] and d["sha256"] == event["source_sha256"]), None)
        if doc is None:
            raise ValueError("correction references a source outside the snapshot")
        decision = decisions.get(event["event_id"])
        target = f"{doc['source_id']}.md#{anchor(event['block_id'])}"
        lines += [f"## `{escape(event['event_id'])}`", "",
                  f"- Source: [{escape(doc['filename'])} · `{escape(event['block_id'])}`]({target})",
                  f"- Status: **{escape(decision['decision'] if decision else 'pending')}**",
                  f"- Reason: {escape(event['reason'])}", f"- Proposed by: {escape(event['author'])}",
                  "- Original:", quote(event["original_text"]), "- Proposed:", quote(event["suggested_text"]), ""]
        if decision:
            lines += [f"- Reviewed by: {escape(decision['reviewer'])}", f"- Review reason: {escape(decision['reason'])}", ""]
    output = path.parent / "corrections.md"
    atomic_write(output, "\n".join(lines).rstrip() + "\n")
    return output
