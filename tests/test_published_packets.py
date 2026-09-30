import json
from pathlib import Path
import tempfile
import unittest

from validation.published_packets import (CHECKS, archive_candidate, export_eligible,
                                           fingerprint, prepare_draft, record_review)
from validation.harness import SequentialSuite


XML = b"""<article><front><article-meta><permissions><license>
<license_ref>https://creativecommons.org/licenses/by/4.0/</license_ref>
</license></permissions></article-meta></front><body>
<sec><title>Presentation</title><p id="P1">A fictional adult described numbness before testing.</p>
<p id="P2">An earlier examination documented weakness.</p></sec>
<sec><title>Answer</title><p id="P3">The confirmed reference was Fictional Condition X.</p></sec>
</body></article>"""


class PublishedPacketContracts(unittest.TestCase):
    """Fake article fixtures exercise contracts, never attest real eligibility."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="published-packet-test-")
        self.root = Path(self.temp.name)
        self.owner = self.root / "scorer-owner"
        self.inputs = self.root / "answer-free-inputs"
        self.answers = self.root / "reference-answers"
        archive_candidate("PMC1234567", "VAL-001", self.owner, fetch=self.fetch)
        self.selection = {
            "case_id": "VAL-001", "prepared_by": "fixture-preparer", "clinical_question": "What explains the symptoms?",
            "cutoff": {"description": "Before confirmation test", "last_included_passage_id": "P2",
                       "excluded_answer_passage_ids": ["P3"]},
            "evidence": [{"passage_id": "P1", "quote": "A fictional adult described numbness before testing."},
                         {"passage_id": "P2", "quote": "An earlier examination documented weakness."}],
            "contamination_risk": "uncertain",
            "reference": {"accepted_diagnoses": ["Fictional Condition X"], "critical_flags": [],
                          "answer_passage_ids": ["P3"], "reference_reason": "P3 confirms the fictional reference"},
        }

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self, url):
        if "fullTextXML" in url:
            return XML
        return json.dumps({"resultList": {"result": [{"pmcid": "PMC1234567", "pmid": "12345678",
                            "title": "Fictional Condition X in an adult", "pubType": "case reports",
                            "isOpenAccess": "Y"}]}}).encode()

    def attestation(self, draft, **overrides):
        return {"case_id": "VAL-001", "packet_sha256": draft["packet_sha256"],
                "reference_sha256": draft["reference_sha256"], "reviewer": "fixture-reviewer",
                "reviewer_role": "qualified_clinician", "decision": "eligible_for_research_evaluation",
                "reason": "Fake test attestation only", "checks": {name: True for name in CHECKS},
                **overrides}

    def test_pending_packet_separates_titles_reference_and_rejects_unreviewed_export(self):
        draft = prepare_draft(self.owner, self.selection)
        folder = self.owner / "VAL-001"
        text = (folder / "packet-draft.json").read_text()
        self.assertNotIn("Fictional Condition X", text)
        self.assertNotIn("PMC1234567", text)
        self.assertEqual(draft["status"], "pending_independent_review")
        worksheet = (folder / "review-worksheet-private.md").read_text()
        self.assertIn("SCORER / REVIEWER ONLY", worksheet)
        self.assertIn("Fictional Condition X", worksheet)
        with self.assertRaisesRegex(ValueError, "review is missing"):
            export_eligible(self.owner, "VAL-001", self.inputs, self.answers)
        self.assertFalse(self.inputs.exists())

    def test_cutoff_exact_quotes_and_answer_exclusions_are_checked(self):
        bad = {**self.selection, "evidence": [{"passage_id": "P3", "quote": "The confirmed reference was Fictional Condition X."}]}
        with self.assertRaisesRegex(ValueError, "outside cutoff"):
            prepare_draft(self.owner, bad)
        bad = {**self.selection, "evidence": [{"passage_id": "P1", "quote": "Invented medical exam."}]}
        with self.assertRaisesRegex(ValueError, "exact substring"):
            prepare_draft(self.owner, bad)
        with self.assertRaisesRegex(ValueError, "unseen cannot be assumed"):
            prepare_draft(self.owner, {**self.selection, "contamination_risk": "unseen"})

    def test_eligibility_requires_independent_clinical_review_and_frozen_hashes(self):
        draft = prepare_draft(self.owner, self.selection)
        with self.assertRaisesRegex(ValueError, "own case"):
            record_review(self.owner, self.attestation(draft, reviewer="fixture-preparer"))
        with self.assertRaisesRegex(ValueError, "qualified clinician"):
            record_review(self.owner, self.attestation(draft, reviewer_role="AI_agent"))
        with self.assertRaisesRegex(ValueError, "all clinical eligibility"):
            record_review(self.owner, self.attestation(draft, checks={name: name != "preanswer_cutoff" for name in CHECKS}))
        record_review(self.owner, self.attestation(draft))
        exported = export_eligible(self.owner, "VAL-001", self.inputs, self.answers)
        self.assertFalse(exported["execution_enabled"])
        self.assertEqual(json.loads((self.inputs / "VAL-001.json").read_text())["status"], "reviewed_research_packet")
        self.assertNotIn("Fictional Condition X", (self.inputs / "VAL-001.json").read_text())
        self.assertIn("Fictional Condition X", (self.answers / "VAL-001.json").read_text())
        with self.assertRaisesRegex(ValueError, "export already exists"):
            export_eligible(self.owner, "VAL-001", self.inputs, self.answers)

    def test_hold_and_postreview_edit_block_export(self):
        draft = prepare_draft(self.owner, self.selection)
        record_review(self.owner, self.attestation(draft, decision="hold", reviewer_role="research_assistant",
                                                  checks={name: False for name in CHECKS}))
        with self.assertRaisesRegex(ValueError, "on hold"):
            export_eligible(self.owner, "VAL-001", self.inputs, self.answers)
        file = self.owner / "VAL-001/packet-draft.json"
        changed = json.loads(file.read_text())
        changed["clinical_question"] = "Edited after review"
        file.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "changed"):
            export_eligible(self.owner, "VAL-001", self.inputs, self.answers)

    def test_exported_published_case_does_not_enable_synthetic_runner(self):
        draft = prepare_draft(self.owner, self.selection)
        record_review(self.owner, self.attestation(draft))
        export_eligible(self.owner, "VAL-001", self.inputs, self.answers)
        manifest = self.inputs / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "kind": "published",
                                        "cases": [{"case_id": "VAL-001", "input_file": "VAL-001.json"}]}))
        with self.assertRaisesRegex(ValueError, "synthetic case manifest"):
            SequentialSuite(manifest, self.answers, self.root / "agents", self.root / "results")
        manifest.write_text(json.dumps({"schema_version": 1, "kind": "synthetic",
                                        "cases": [{"case_id": "VAL-001", "input_file": "VAL-001.json"}]}))
        with self.assertRaisesRegex(ValueError, "invalid or duplicate synthetic case IDs"):
            SequentialSuite(manifest, self.answers, self.root / "agents", self.root / "results")

    def test_no_overwrite_or_symlink_substitution_of_scorer_artifacts(self):
        with self.assertRaisesRegex(ValueError, "already archived"):
            archive_candidate("PMC1234567", "VAL-001", self.owner, fetch=self.fetch)
        prepare_draft(self.owner, self.selection)
        with self.assertRaisesRegex(ValueError, "draft exists"):
            prepare_draft(self.owner, self.selection)
        target = self.owner / "VAL-001/reference-private.json"
        saved = self.root / "other-reference.json"
        saved.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, "symlinked curation"):
            record_review(self.owner, self.attestation(json.loads((self.owner / "VAL-001/draft-private.json").read_text())))


if __name__ == "__main__":
    unittest.main()
