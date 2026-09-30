"""Operator-curated public literature snapshots and claim support records.

This stage fetches exact PMIDs, not patient-specific search queries. Citation
identity, source passages, and substantive support review are separate checks.
No evidence is automatically inserted into a diagnosis or patient report.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone
from uuid import uuid4
import urllib.parse
import xml.etree.ElementTree as ET

from validation.curation import BASE, get
from validation.harness import PROJECT, write_atomic


PMID = re.compile(r"[0-9]{1,9}\Z")
SUPPORT = ("supports", "contradicts", "insufficient", "unclear")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def private_library(root):
    path = Path(root)
    if path.is_symlink() or path.resolve().is_relative_to(PROJECT):
        raise ValueError("evidence storage must be a regular operator directory outside repository")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path.resolve()


def text_of(node):
    return " ".join("".join(node.itertext()).split())


def abstract_text(raw):
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        return text_of(ET.fromstring("<abstract>" + raw + "</abstract>"))
    except ET.ParseError:
        # Preserve original text rather than making undocumented repairs.
        return raw


def capture(root, pmid, *, fetch=get, excluded_ids=()):
    if not isinstance(pmid, str) or not PMID.fullmatch(pmid):
        raise ValueError("exact PMID required; patient text and arbitrary URLs are not accepted")
    excluded = {str(value).casefold() for value in excluded_ids}
    if pmid.casefold() in excluded or ("PMID:" + pmid).casefold() in excluded:
        raise ValueError("requested reference excluded from this evidence corpus")
    root = private_library(root)
    target = root / ("PMID-" + pmid)
    if target.exists():
        raise ValueError("source already captured; use a new library for refreshed evidence")
    url = BASE + "search?" + urllib.parse.urlencode({"query": f"EXT_ID:{pmid} AND SRC:MED",
                                                   "format": "json", "resultType": "core"})
    raw = fetch(url)
    found = json.loads(raw).get("resultList", {}).get("result", [])
    source = next((item for item in found if str(item.get("pmid")) == pmid), None)
    if not source or not isinstance(source.get("title"), str):
        raise ValueError("PMID does not resolve to the requested publication")
    aliases = [pmid, "PMID:" + pmid, source.get("pmcid"), source.get("doi")]
    if any(str(alias).casefold() in excluded for alias in aliases if alias):
        raise ValueError("resolved reference aliases identify an excluded source")
    blocks = []
    abstract = abstract_text(source.get("abstractText"))
    if abstract:
        blocks.append({"id": "abstract", "section": "Abstract", "text": abstract, "channel": "indexed_abstract"})
    warnings = []
    body_raw = None
    licenses = []
    pmcid = source.get("pmcid")
    if isinstance(pmcid, str) and re.fullmatch(r"PMC\d{5,10}", pmcid) and source.get("isOpenAccess") == "Y":
        try:
            body_raw = fetch(BASE + pmcid + "/fullTextXML")
            article = ET.fromstring(body_raw)
            article_ids = {node.get("pub-id-type"): (node.text or "").strip()
                           for node in article.findall(".//{*}article-meta/{*}article-id")}
            if article_ids.get("pmcid") != pmcid or article_ids.get("pmid") != pmid:
                raise ValueError("full-text publication identity does not match requested PMID/PMCID")
            licenses = [node.text.strip() for node in article.findall(".//{*}permissions/{*}license/{*}license_ref") if node.text]
            if not licenses:
                licenses = re.findall(r"https?://creativecommons.org/(?:licenses|publicdomain)/[^\s<>\"']+",
                                      text_of(article.find(".//permissions"))) if article.find(".//permissions") is not None else []
            supported = any(url.startswith(("https://creativecommons.org/licenses/by/",
                                           "http://creativecommons.org/licenses/by/",
                                           "https://creativecommons.org/publicdomain/zero/")) for url in licenses)
            if supported:
                body = article.find("body")
                if body is not None:
                    for index, paragraph in enumerate(body.iter("p"), 1):
                        value = text_of(paragraph)
                        if value:
                            blocks.append({"id": "body-" + str(index), "section": "Article body",
                                           "xml_paragraph_id": paragraph.get("id"), "text": value,
                                           "channel": "licensed_full_text"})
                    for index, table in enumerate(body.iter("table-wrap"), 1):
                        blocks.append({"id": "table-" + str(index), "section": "Table (flattened text; inspect original layout)",
                                       "xml_table_id": table.get("id"), "text": text_of(table), "channel": "licensed_full_text"})
            else:
                warnings.append("full text indexed open-access but reuse license not recognized; abstract only")
                body_raw = None
        except Exception as exc:
            # Failures remain visible; never claim to have read full text.
            warnings.append("full-text retrieval failed: " + type(exc).__name__)
            body_raw = None
    if not any(block["channel"] == "licensed_full_text" for block in blocks):
        warnings.append("no full-text passages available; abstract is not a complete study or guideline")
    if not blocks:
        warnings.append("no abstract or readable passages; citation identity only")
    if source.get("isRetracted") == "Y":
        warnings.append("publication is indexed as retracted")
    if "guideline" in source["title"].casefold():
        warnings.append("guideline currency/version not verified; this publication may have been superseded")
    snapshot = {
        "schema_version": 1, "source_id": "PMID-" + pmid, "pmid": pmid, "pmcid": pmcid,
        "doi": source.get("doi"), "title": source["title"], "authors": source.get("authorString"),
        "published": source.get("firstPublicationDate") or source.get("pubYear"),
        "publication_types": source.get("pubTypeList", {}).get("pubType", []),
        "indexed_retraction_status": source.get("isRetracted", "unknown"),
        "retrieved_at": now(), "metadata_url": url,
        "full_text_url": BASE + pmcid + "/fullTextXML" if pmcid else None,
        "metadata_sha256": hashlib.sha256(raw).hexdigest(),
        "full_text_sha256": hashlib.sha256(body_raw).hexdigest() if body_raw else None,
        "license_urls": licenses, "passages": blocks, "warnings": warnings,
        "status": "retrieved_source_not_a_verified_clinical_claim",
        "currency_status": "unchecked",
    }
    target.mkdir(mode=0o700)
    (target / "metadata-original.json").write_bytes(raw)
    if body_raw:
        (target / "article-original.xml").write_bytes(body_raw)
    write_atomic(target / "snapshot.json", snapshot)
    write_atomic(target / "manifest.json", {"snapshot_sha256": digest(snapshot),
                                            "metadata_sha256": snapshot["metadata_sha256"],
                                            "full_text_sha256": snapshot["full_text_sha256"]})
    return snapshot


def read_source(root, source_id):
    root = private_library(root)
    if not isinstance(source_id, str) or not re.fullmatch(r"PMID-\d{1,9}", source_id):
        raise ValueError("invalid source ID")
    folder = root / source_id
    if folder.is_symlink():
        raise ValueError("symlinked source directory not allowed")
    for name in ("snapshot.json", "manifest.json", "metadata-original.json"):
        if (folder / name).is_symlink():
            raise ValueError("symlinked evidence artifact not allowed")
    snapshot = json.loads((folder / "snapshot.json").read_text())
    manifest = json.loads((folder / "manifest.json").read_text())
    if (snapshot.get("source_id") != source_id or manifest["snapshot_sha256"] != digest(snapshot)
            or manifest["metadata_sha256"] != hashlib.sha256((folder / "metadata-original.json").read_bytes()).hexdigest()):
        raise ValueError("retrieved source changed since capture")
    if manifest["full_text_sha256"]:
        file = folder / "article-original.xml"
        if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != manifest["full_text_sha256"]:
            raise ValueError("full-text evidence changed since capture")
    return snapshot, manifest["snapshot_sha256"]


def append(root, event):
    root = private_library(root)
    path = root / "claim-events.jsonl"
    if path.is_symlink():
        raise ValueError("symlinked claim ledger not allowed")
    with path.open("a+", encoding="utf-8") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        file.seek(0)
        prior = [json.loads(line) for line in file if line.strip()]
        if event["action"] == "review":
            proposal = next((item for item in prior if item["event_id"] == event["proposal_id"] and item["action"] == "propose"), None)
            if not proposal:
                raise ValueError("unknown claim proposal")
            if proposal["proposer"].casefold() == event["reviewer"].casefold():
                raise ValueError("substantive support review must be independent")
            if any(item.get("proposal_id") == event["proposal_id"] for item in prior):
                raise ValueError("claim was already reviewed")
            source, current_hash = read_source(root, proposal["source_id"])
            if current_hash != proposal["snapshot_sha256"]:
                raise ValueError("claim source snapshot no longer matches")
            if event["verdict"] == "supports" and source["indexed_retraction_status"] == "Y":
                raise ValueError("retracted publication cannot be approved as supporting evidence")
        file.seek(0, os.SEEK_END)
        file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        file.flush()
        os.fsync(file.fileno())
    return event


def propose_claim(root, source_id, passage_id, quote, claim, proposer, applicability):
    source, source_hash = read_source(root, source_id)
    passage = next((item for item in source["passages"] if item["id"] == passage_id), None)
    if not passage or not isinstance(quote, str) or not quote.strip() or quote not in passage["text"]:
        raise ValueError("claim requires an exact quote from a captured passage")
    for value in (claim, proposer, applicability):
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError("claim, proposer and applicability explanation required")
    event = {"event_id": "CLM-" + uuid4().hex, "action": "propose", "at": now(),
             "source_id": source_id, "snapshot_sha256": source_hash, "passage_id": passage_id,
             "quote": quote, "claim": claim, "proposer": proposer.strip(),
             "applicability": applicability, "status": "pending_substantive_support_review"}
    return append(root, event)


def review_claim(root, proposal_id, verdict, reviewer, rationale):
    if verdict not in SUPPORT:
        raise ValueError("verdict must be supports, contradicts, insufficient or unclear")
    if not isinstance(proposal_id, str) or not re.fullmatch(r"CLM-[a-f0-9]{32}", proposal_id):
        raise ValueError("valid claim proposal ID required")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 2000 for value in (reviewer, rationale)):
        raise ValueError("independent reviewer and substantive rationale required")
    return append(root, {"event_id": "SUP-" + uuid4().hex, "action": "review", "proposal_id": proposal_id,
                         "at": now(), "verdict": verdict, "reviewer": reviewer.strip(), "rationale": rationale})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--pmid", required=True)
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()
    snapshot = capture(args.root, args.pmid, excluded_ids=args.exclude)
    print(json.dumps({"source_id": snapshot["source_id"], "passages": len(snapshot["passages"]),
                      "warnings": snapshot["warnings"], "status": snapshot["status"]}, indent=2))


if __name__ == "__main__":
    main()
