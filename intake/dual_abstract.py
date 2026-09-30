"""Validate and compare two independent synthetic abstraction submissions.

The comparison is NOT adjudication. None of these items become case facts without
explicit human review of the original source and a separate reviewer decision.
"""

import json
import hashlib
from pathlib import Path

from intake.extract import SYNTHETIC_CASE, check_fixture, checksum
from intake.markdown import atomic_write
from case_review.store import ATTRIBUTIONS, FACT_TYPES, validate_event_date


def packet(snapshot):
    case_id = snapshot.get("case_id", "")
    if not isinstance(case_id, str) or not SYNTHETIC_CASE.fullmatch(case_id):
        raise ValueError("synthetic snapshot required")
    blocks = []
    for document in snapshot["documents"]:
        for block in document["blocks"]:
            if block["type"] == "image" or (block["type"] == "cell" and block.get("formula") and block.get("cached_value") is None):
                continue
            text = block["text"]
            if not isinstance(text, str) or not text.strip():
                continue
            blocks.append({"block_id": block["id"], "source_id": document["source_id"],
                           "source_format": document["format"], "locator": block["locator"],
                           "text": text})
    if not blocks or len(blocks) > 100 or sum(len(b["text"]) for b in blocks) > 25000:
        raise ValueError("synthetic abstraction packet exceeds limits; split sources into smaller batches")
    return {"case_id": case_id, "blocks": blocks}


def parse_reply(text):
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"items"} or not isinstance(data["items"], list) or len(data["items"]) > 100:
        raise ValueError("abstractor must return only a bounded JSON items array")
    return data["items"]


def validate_items(items, source_packet):
    lookup = {block["block_id"]: block for block in source_packet["blocks"]}
    output = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"block_id", "category", "statement", "event_date", "date_precision", "attribution"}:
            raise ValueError("abstractor item does not match required schema")
        if item["block_id"] not in lookup or item["category"] not in FACT_TYPES or item["attribution"] not in ATTRIBUTIONS:
            raise ValueError("unknown source block/category/attribution")
        if not isinstance(item["statement"], str) or not item["statement"].strip() or len(item["statement"]) > 1200:
            raise ValueError("statement is missing or too long")
        validate_event_date(item["event_date"], item["date_precision"])
        output.append({**item, "source_id": lookup[item["block_id"]]["source_id"],
                       "original_text": lookup[item["block_id"]]["text"]})
    return output


def compare(source_packet, a, b):
    """Concordance is lexical only; even identical model claims require review."""
    reviewed = {"abstractor-a": validate_items(a, source_packet),
                "abstractor-b": validate_items(b, source_packet)}
    groups = {}
    for name, entries in reviewed.items():
        for item in entries:
            groups.setdefault((item["block_id"], item["category"]), {"abstractor-a": [], "abstractor-b": []})[name].append(item)
    comparisons = []
    for key, proposals in groups.items():
        left, right = proposals["abstractor-a"], proposals["abstractor-b"]
        if not left or not right:
            status = "one_sided"
        elif (len(left) == len(right) == 1 and
              all(left[0].get(field) == right[0].get(field) for field in ("statement", "event_date", "date_precision", "attribution"))):
            status = "same_wording"
        else:
            status = "different_or_multiple"
        comparisons.append({"block_id": key[0], "category": key[1], "status": status,
                            "proposals": proposals, "human_review_required": True})
    cited = {block_id for block_id, _category in groups}
    for block in source_packet["blocks"]:
        if block["block_id"] not in cited:
            comparisons.append({"block_id": block["block_id"], "category": "not_proposed",
                                "status": "omitted_by_both",
                                "proposals": {"abstractor-a": [], "abstractor-b": []},
                                "human_review_required": True})
    return comparisons


def save(root, snapshot, a, b, *, sessions=None):
    root = check_fixture(root, root)
    raw = check_fixture(Path(root) / "extraction.json", root).read_bytes()
    if json.loads(raw) != snapshot:
        raise ValueError("synthetic extraction changed since abstractors received source blocks")
    for doc in snapshot["documents"]:
        source = check_fixture(Path(root) / doc["filename"], root)
        if source.name != doc["filename"] or checksum(source) != doc["sha256"]:
            raise ValueError("source changed since abstraction")
    source_packet = packet(snapshot)
    compared = compare(source_packet, a, b)
    result = {"case_id": source_packet["case_id"], "status": "unverified_independent_abstractions",
              "source_snapshot_sha256": hashlib.sha256(raw).hexdigest(),
              "source_ids": sorted({block["source_id"] for block in source_packet["blocks"]}),
              "comparisons": compared, "sessions": sessions or {}, "promoted_to_case": False}
    atomic_write(Path(root) / "dual_abstraction.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result
