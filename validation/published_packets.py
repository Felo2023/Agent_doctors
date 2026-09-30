"""Scorer-owned preparation and review of published case packets.

No model calls. Article/reference material never goes into agent workspaces.
Eligibility here records human attestations, not proof of clinical competence.
The current synthetic model runner still refuses published cases.
"""

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from validation.curation import BASE, PMCID, get, metadata
from validation.harness import PROJECT, distinct_paths, write_atomic


VAL_ID = re.compile(r"VAL-[0-9]{3,6}\Z")
CHECKS = ("license_and_reuse", "adult_case", "deidentified", "preanswer_cutoff",
          "packet_faithfulness", "answer_leakage_checked", "reference_adjudicated",
          "contamination_risk_recorded")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def markdown_text(value):
    return html.escape(str(value), quote=False).replace("`", "&#96;").replace("[", "\\[").replace("]", "\\]")


def review_worksheet(folder, draft, packet, reference, source):
    """Answer-bearing reviewer document. Never place this in the agent input."""
    credit = source["metadata_private"]
    rows = ["# Published-case preparation worksheet · " + draft["case_id"], "",
            "**SCORER / REVIEWER ONLY — contains reference answer. Not an agent input.**", "",
            "Status: pending independent clinical review. An AI-assisted draft is not a validated case.", "",
            "## Source and reuse", "",
            markdown_text(credit.get("title_private_scorer_only") or "Source title unavailable"),
            markdown_text(credit.get("authors_private_scorer_only") or "Authors: see original article"),
            markdown_text(credit.get("doi") or credit["pmcid"]),
            "License: " + ", ".join(markdown_text(url) for url in credit["license_urls"]), "",
            "## Diagnostic task and cutoff", "", markdown_text(packet["clinical_question"]), "",
            markdown_text(draft["selection"]["cutoff"]["description"]), "",
            "Last included original paragraph: " + markdown_text(draft["selection"]["cutoff"]["last_included_passage_id"]),
            "Excluded answer-bearing paragraphs: " + ", ".join(markdown_text(pid) for pid in draft["selection"]["cutoff"]["excluded_answer_passage_ids"]),
            "Contamination risk: " + draft["selection"]["contamination_risk"], "",
            "## Proposed answer-free packet", ""]
    for item, selected in zip(packet["evidence"], draft["selection"]["evidence"]):
        rows.extend(["### " + item["id"] + " — original paragraph " + markdown_text(selected["passage_id"]), "",
                     "> " + markdown_text(item["text"]), ""])
    rows.extend(["## Reference answer — must not reach models before scoring", "",
                 "Accepted labels (proposed): " + ", ".join(markdown_text(label) for label in reference["accepted_diagnoses"]),
                 "Answer paragraphs: " + ", ".join(markdown_text(pid) for pid in reference["answer_passage_ids"]),
                 markdown_text(reference["reference_reason"]), "",
                 "## Review checks", ""])
    rows.extend("- [ ] " + name.replace("_", " ") for name in CHECKS)
    rows.extend(["", "Complete `review-template.json` only after inspecting the archived article and selected passages.",
                 "Clinical qualification and reviewer identity are operator-verified, not authenticated by this prototype.",
                 "Partial text, aliases, critical flags, and the chronological cutoff require substantive clinical review.", ""])
    (folder / "review-worksheet-private.md").write_text("\n".join(rows), encoding="utf-8")


def owner_root(path):
    raw = Path(path)
    if raw.is_symlink():
        raise ValueError("scorer-owned roots cannot be symlinks")
    root = raw.resolve()
    if root.is_relative_to(PROJECT):
        raise ValueError("answer-bearing curation must stay outside the repository")
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def passages(xml):
    """Index body prose only. Headings/title/abstract stay operator-only metadata."""
    tree = ET.fromstring(xml)
    body = tree.find("body")
    if body is None:
        raise ValueError("article has no accessible body")
    rows = []
    ids = set()

    def visit(node, section):
        if node.tag == "sec":
            section = node.findtext("title") or section
        if node.tag == "p":
            text = " ".join("".join(node.itertext()).split())
            pid = node.get("id") or f"body-paragraph-{len(rows) + 1}"
            if pid in ids:
                raise ValueError("duplicate article paragraph IDs")
            ids.add(pid)
            if text:
                rows.append({"passage_id": pid, "section_private": section, "original_text": text,
                             "original_text_sha256": hashlib.sha256(text.encode()).hexdigest()})
            return  # Do not mix nested figure captions into separate clinical paragraphs.
        for child in node:
            if child.tag in ("sec", "p"):
                visit(child, section)
    visit(body, "Body")
    if not rows:
        raise ValueError("article body has no indexed paragraphs")
    return rows


def archive_candidate(pmcid, case_id, root, *, fetch=get):
    if not isinstance(case_id, str) or not VAL_ID.fullmatch(case_id) or not PMCID.fullmatch(pmcid):
        raise ValueError("valid VAL case ID and PMCID required")
    root = owner_root(root)
    target = root / case_id
    if target.exists():
        raise ValueError("candidate already archived; use a new case ID instead of overwriting")
    details = metadata(pmcid, fetch=fetch)
    if not details["licensed_for_review"]:
        raise ValueError("no supported reuse license identified; inspect license manually")
    xml = fetch(BASE + pmcid + "/fullTextXML")
    rows = passages(xml)
    target.mkdir(mode=0o700)
    (target / "article.xml").write_bytes(xml)
    source = {"case_id": case_id, "metadata_private": details,
              "article_sha256": hashlib.sha256(xml).hexdigest(), "passages": rows}
    write_atomic(target / "source-private.json", source)
    write_atomic(target / "selection-template.json", {
        "case_id": case_id, "prepared_by": "", "clinical_question": "",
        "cutoff": {"description": "", "last_included_passage_id": "", "excluded_answer_passage_ids": []},
        "evidence": [{"passage_id": "", "quote": "", "note_private": "Choose exact pre-cutoff text; do not copy title/abstract."}],
        "known_diagnoses_private": [], "contamination_risk": "unassessed",
        "reference": {"accepted_diagnoses": [], "answer_passage_ids": [], "critical_flags": [],
                      "reference_reason": ""},
    })
    return target


def load_source(root, case_id):
    root = owner_root(root)
    if not VAL_ID.fullmatch(case_id):
        raise ValueError("invalid published case ID")
    folder = root / case_id
    if folder.is_symlink():
        raise ValueError("symlinked candidate directory is not allowed")
    for name in ("article.xml", "source-private.json"):
        if (folder / name).is_symlink():
            raise ValueError("symlinked source artifacts are not allowed")
    source = json.loads((folder / "source-private.json").read_text())
    actual = hashlib.sha256((folder / "article.xml").read_bytes()).hexdigest()
    if source["article_sha256"] != actual or source["case_id"] != case_id:
        raise ValueError("archived source changed or mismatched case")
    if source["passages"] != passages((folder / "article.xml").read_bytes()):
        raise ValueError("passage index does not match archived article")
    return folder, source


def nonempty(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + " is required")


def prepare_draft(root, selection):
    """Build answer-free text and sealed reference separately, never auto-approve."""
    case_id = selection.get("case_id", "")
    folder, source = load_source(root, case_id)
    if (folder / "draft-private.json").exists():
        raise ValueError("draft exists; create a new candidate version instead of overwriting")
    nonempty(selection.get("prepared_by"), "preparer")
    nonempty(selection.get("clinical_question"), "clinical question")
    cutoff = selection.get("cutoff", {})
    nonempty(cutoff.get("description"), "diagnostic cutoff explanation")
    if selection.get("contamination_risk") not in ("possible", "likely", "uncertain"):
        raise ValueError("record contamination risk; unseen cannot be assumed")
    index = {row["passage_id"]: row for row in source["passages"]}
    order = {row["passage_id"]: i for i, row in enumerate(source["passages"])}
    last = cutoff.get("last_included_passage_id")
    if last not in index:
        raise ValueError("cutoff must identify an original paragraph")
    reference = selection.get("reference", {})
    diagnoses = reference.get("accepted_diagnoses")
    answers = reference.get("answer_passage_ids")
    flags = reference.get("critical_flags")
    if (not isinstance(diagnoses, list) or not diagnoses or any(not isinstance(d, str) or not d.strip() for d in diagnoses)
            or not isinstance(answers, list) or not answers or any(pid not in index for pid in answers)
            or not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags)):
        raise ValueError("reference diagnoses, source passages, and flags required")
    nonempty(reference.get("reference_reason"), "reference adjudication rationale")
    excluded = cutoff.get("excluded_answer_passage_ids")
    if not isinstance(excluded, list) or not set(answers).issubset(excluded) or any(pid not in index for pid in excluded):
        raise ValueError("answer-bearing passages must be explicitly excluded")
    evidence = selection.get("evidence")
    if not isinstance(evidence, list) or not evidence or len(evidence) > 100:
        raise ValueError("bounded, explicit source evidence selection required")
    packet = {"schema_version": 1, "case_id": case_id, "clinical_question": selection["clinical_question"],
              "evidence": [], "status": "draft_not_eligible_for_evaluation"}
    for i, item in enumerate(evidence, 1):
        pid, quote = item.get("passage_id"), item.get("quote")
        if pid not in index or pid in excluded or order[pid] > order[last]:
            raise ValueError("evidence is outside cutoff or overlaps answer-bearing passages")
        nonempty(quote, "exact source quote")
        if quote not in index[pid]["original_text"]:
            raise ValueError("selected quote is not an exact substring of the archived passage")
        packet["evidence"].append({"id": f"E-{i:03d}", "text": quote})
    encoded = json.dumps(packet, ensure_ascii=False).casefold()
    forbidden = [source["metadata_private"].get(key, "") for key in ("pmcid", "pmid", "doi", "title_private_scorer_only")]
    if any(value and value.casefold() in encoded for value in forbidden):
        raise ValueError("packet contains answer-bearing publication identifiers or title")
    # Answers may be described indirectly or as pre-existing diagnoses; exact-label
    # matches are flags for human review, not automatic rejection or redaction.
    label_flags = [diagnosis for diagnosis in diagnoses if diagnosis.casefold() in encoded]
    sealed = {"case_id": case_id, "accepted_diagnoses": diagnoses, "critical_flags": flags,
              "answer_passage_ids": answers, "reference_reason": reference["reference_reason"]}
    draft = {"case_id": case_id, "status": "pending_independent_review", "prepared_by": selection["prepared_by"],
             "source_sha256": source["article_sha256"], "selection": selection,
             "packet_sha256": fingerprint(packet), "reference_sha256": fingerprint(sealed),
             "possible_answer_label_leaks": label_flags, "created": now()}
    write_atomic(folder / "packet-draft.json", packet)
    write_atomic(folder / "reference-private.json", sealed)
    write_atomic(folder / "draft-private.json", draft)
    write_atomic(folder / "review-template.json", {
        "case_id": case_id, "packet_sha256": draft["packet_sha256"],
        "reference_sha256": draft["reference_sha256"], "reviewer": "",
        "reviewer_role": "", "decision": "hold", "reason": "",
        "checks": {name: False for name in CHECKS}, "possible_leak_disposition": "",
    })
    review_worksheet(folder, draft, packet, sealed, source)
    return draft


def load_draft(root, case_id):
    folder, source = load_source(root, case_id)
    for name in ("draft-private.json", "packet-draft.json", "reference-private.json"):
        if (folder / name).is_symlink():
            raise ValueError("symlinked curation artifacts are not allowed")
    draft = json.loads((folder / "draft-private.json").read_text())
    packet = json.loads((folder / "packet-draft.json").read_text())
    reference = json.loads((folder / "reference-private.json").read_text())
    if (draft["source_sha256"] != source["article_sha256"] or draft["packet_sha256"] != fingerprint(packet)
            or draft["reference_sha256"] != fingerprint(reference)):
        raise ValueError("draft packet/reference changed; a new reviewed version is required")
    if any(item.get("case_id") != case_id for item in (draft, packet, reference)):
        raise ValueError("curation artifacts belong to another case")
    return folder, draft, packet, reference


def record_review(root, attestation):
    case_id = attestation.get("case_id", "")
    folder, draft, packet, reference = load_draft(root, case_id)
    if (folder / "review-private.json").exists():
        raise ValueError("review already recorded; do not overwrite")
    nonempty(attestation.get("reviewer"), "independent reviewer")
    nonempty(attestation.get("reason"), "review rationale")
    if attestation["reviewer"].strip().casefold() == draft["prepared_by"].strip().casefold():
        raise ValueError("preparer cannot independently approve their own case")
    if (attestation.get("packet_sha256") != draft["packet_sha256"]
            or attestation.get("reference_sha256") != draft["reference_sha256"]):
        raise ValueError("review must match the frozen packet and reference hashes")
    if attestation.get("decision") not in ("hold", "eligible_for_research_evaluation"):
        raise ValueError("invalid review decision")
    if attestation["decision"] == "eligible_for_research_evaluation":
        if attestation.get("reviewer_role") != "qualified_clinician":
            raise ValueError("clinical case eligibility requires an identified qualified clinician")
        if any(attestation.get("checks", {}).get(name) is not True for name in CHECKS):
            raise ValueError("all clinical eligibility checks must be explicitly attested")
        if draft["possible_answer_label_leaks"]:
            nonempty(attestation.get("possible_leak_disposition"), "possible answer leakage disposition")
    else:
        nonempty(attestation.get("reviewer_role"), "reviewer role")
    record = {**attestation, "issued_at": now(), "status": attestation["decision"],
              "credential_verification": "operator_responsibility_not_verified_by_software"}
    write_atomic(folder / "review-private.json", record)
    return record


def export_eligible(root, case_id, inputs, answers):
    """Export only a reviewed packet; reference stays in a separate scorer directory."""
    folder, draft, packet, reference = load_draft(root, case_id)
    review_file = folder / "review-private.json"
    if not review_file.is_file() or review_file.is_symlink():
        raise ValueError("independent clinical review is missing")
    record = json.loads(review_file.read_text())
    if (record.get("status") != "eligible_for_research_evaluation"
            or record.get("reviewer_role") != "qualified_clinician"
            or record.get("packet_sha256") != draft["packet_sha256"]
            or record.get("reference_sha256") != draft["reference_sha256"]
            or record.get("reviewer", "").strip().casefold() == draft["prepared_by"].strip().casefold()
            or any(record.get("checks", {}).get(name) is not True for name in CHECKS)
            or (draft["possible_answer_label_leaks"] and not record.get("possible_leak_disposition", "").strip())):
        raise ValueError("case is on hold or has invalid clinical-review metadata")
    if Path(inputs).is_symlink() or Path(answers).is_symlink():
        raise ValueError("symlinked export roots are not allowed")
    input_root, answer_root, _ = distinct_paths((inputs, answers, root))
    input_file, answer_file = input_root / (case_id + ".json"), answer_root / (case_id + ".json")
    if input_file.exists() or answer_file.exists():
        raise ValueError("export already exists; never overwrite evaluation inputs/answers")
    write_atomic(input_file, {**packet, "status": "reviewed_research_packet"})
    write_atomic(answer_file, {**reference, "review_private": record, "source_sha256": draft["source_sha256"]})
    return {"case_id": case_id, "input_file": str(input_file), "reference_file": str(answer_file),
            "clinical_validation": False, "execution_enabled": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("archive", "draft", "worksheet", "review", "status", "export"))
    parser.add_argument("--owner-root", required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--pmcid")
    parser.add_argument("--selection")
    parser.add_argument("--attestation")
    parser.add_argument("--inputs")
    parser.add_argument("--answers")
    args = parser.parse_args()
    if args.action == "archive":
        if not args.pmcid or not args.case_id:
            parser.error("archive requires --pmcid and --case-id")
        output = {"draft_directory": str(archive_candidate(args.pmcid, args.case_id, args.owner_root)),
                  "status": "requires_manual_selection"}
    elif args.action == "draft":
        if not args.selection:
            parser.error("draft requires --selection")
        output = prepare_draft(args.owner_root, json.loads(Path(args.selection).read_text()))
    elif args.action == "review":
        if not args.attestation:
            parser.error("review requires --attestation")
        output = record_review(args.owner_root, json.loads(Path(args.attestation).read_text()))
    elif args.action == "worksheet":
        if not args.case_id:
            parser.error("worksheet requires --case-id")
        folder, draft, packet, sealed = load_draft(args.owner_root, args.case_id)
        _, source = load_source(args.owner_root, args.case_id)
        review_worksheet(folder, draft, packet, sealed, source)
        output = {"case_id": args.case_id, "status": draft["status"]}
    elif args.action == "status":
        if not args.case_id:
            parser.error("status requires --case-id")
        folder, draft, packet, sealed = load_draft(args.owner_root, args.case_id)
        review_file = folder / "review-private.json"
        if review_file.is_symlink():
            raise ValueError("review artifact cannot be a symlink")
        recorded = json.loads(review_file.read_text()) if review_file.is_file() else None
        output = {"case_id": args.case_id,
                  "status": recorded.get("status") if recorded else draft["status"],
                  "execution_enabled": False}
    else:
        if not args.case_id or not args.inputs or not args.answers:
            parser.error("export requires --case-id --inputs --answers")
        output = export_eligible(args.owner_root, args.case_id, args.inputs, args.answers)
    # Do not print reference diagnoses or article titles to shared terminal logs.
    print(json.dumps({key: output[key] for key in ("case_id", "status", "draft_directory", "execution_enabled") if key in output}, indent=2))


if __name__ == "__main__":
    main()
