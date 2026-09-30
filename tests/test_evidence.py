import json
from pathlib import Path
import tempfile
import unittest

from evidence.library import capture, propose_claim, read_source, review_claim


class EvidenceLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="public-evidence-test-")
        self.root = Path(self.temp.name) / "library"

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self, url):
        if "fullTextXML" in url:
            return b'''<article><front><article-meta><article-id pub-id-type="pmid">12345678</article-id>
                <article-id pub-id-type="pmcid">PMC1234567</article-id><permissions><license>
                <license_ref>https://creativecommons.org/licenses/by/4.0/</license_ref></license></permissions>
                </article-meta></front><body><sec><p id="Par1">Fictional study reports 10 of 100 participants improved.</p>
                <table-wrap id="T1"><table><tr><td>100 participants</td></tr></table></table-wrap></sec></body></article>'''
        return json.dumps({"resultList": {"result": [{"pmid": "12345678", "pmcid": "PMC1234567",
            "title": "Fictional evidence article", "abstractText": "<p>Summary only.</p>", "isOpenAccess": "Y",
            "isRetracted": "N", "doi": "10.0000/fictional-test", "pubTypeList": {"pubType": ["Journal Article"]}}]}}).encode()

    def test_publication_identity_passages_and_quote_review_are_separate(self):
        source = capture(self.root, "12345678", fetch=self.fetch)
        self.assertEqual(source["status"], "retrieved_source_not_a_verified_clinical_claim")
        self.assertTrue(any(block["channel"] == "licensed_full_text" for block in source["passages"]))
        self.assertEqual(source["passages"][0]["text"], "Summary only.")
        claim = propose_claim(self.root, source["source_id"], "body-1", "10 of 100 participants improved",
                              "The fictional study reported improvement for ten participants.",
                              "proposer-A", "Applies only to the fictional study population; not a personal prognosis.")
        self.assertEqual(claim["status"], "pending_substantive_support_review")
        with self.assertRaisesRegex(ValueError, "independent"):
            review_claim(self.root, claim["event_id"], "supports", "proposer-A", "self approval")
        reviewed = review_claim(self.root, claim["event_id"], "supports", "reviewer-B", "The quote explicitly states the count and denominator.")
        self.assertEqual(reviewed["verdict"], "supports")
        self.assertEqual(len((self.root / "claim-events.jsonl").read_text().splitlines()), 2)
        with self.assertRaisesRegex(ValueError, "already reviewed"):
            review_claim(self.root, claim["event_id"], "supports", "reviewer-B", "duplicate")
        with self.assertRaisesRegex(ValueError, "exact quote"):
            propose_claim(self.root, source["source_id"], "body-1", "90 of 100 participants improved",
                          "Invented", "proposer-A", "not supported")
        with self.assertRaisesRegex(ValueError, "already captured"):
            capture(self.root, "12345678", fetch=self.fetch)

    def test_excluded_reference_aliases_are_rejected_before_text_reaches_library(self):
        requests = []
        def tracked(url):
            requests.append(url)
            return self.fetch(url)
        with self.assertRaisesRegex(ValueError, "excluded source"):
            capture(self.root, "12345678", fetch=tracked, excluded_ids=["PMC1234567"])
        self.assertEqual(len(requests), 1)
        self.assertFalse((self.root / "PMID-12345678").exists())
        with self.assertRaisesRegex(ValueError, "exact PMID"):
            capture(self.root, "patient has symptoms", fetch=tracked)

    def test_missing_fulltext_and_wrong_identity_are_disclosed(self):
        def unavailable(url):
            if "fullTextXML" in url:
                raise TimeoutError("source timeout")
            return self.fetch(url)
        source = capture(self.root, "12345678", fetch=unavailable)
        self.assertEqual(len(source["passages"]), 1)
        self.assertTrue(any("abstract is not a complete" in warning for warning in source["warnings"]))
        self.assertIsNone(source["full_text_sha256"])
        other = Path(self.temp.name) / "wrong-identity"
        def wrong(url):
            value = self.fetch(url)
            return value.replace(b"PMC1234567", b"PMC9999999") if "fullTextXML" in url else value
        source = capture(other, "12345678", fetch=wrong)
        self.assertTrue(any("retrieval failed: ValueError" in warning for warning in source["warnings"]))
        self.assertEqual(len(source["passages"]), 1)

    def test_snapshot_drift_and_retracted_source_cannot_support_claim(self):
        def retracted(url):
            value = self.fetch(url)
            return value.replace(b'"isRetracted": "N"', b'"isRetracted": "Y"') if "fullTextXML" not in url else value
        source = capture(self.root, "12345678", fetch=retracted)
        event = propose_claim(self.root, source["source_id"], "abstract", "Summary only.",
                              "A tentative claim", "proposer-A", "unknown relevance")
        with self.assertRaisesRegex(ValueError, "retracted"):
            review_claim(self.root, event["event_id"], "supports", "reviewer-B", "This claim would rely on retracted work")
        self.assertEqual(review_claim(self.root, event["event_id"], "insufficient", "reviewer-B", "Retracted evidence cannot establish this claim.")["verdict"], "insufficient")
        snapshot = self.root / source["source_id"] / "snapshot.json"
        changed = json.loads(snapshot.read_text())
        changed["title"] = "Changed after capture"
        snapshot.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "source changed"):
            read_source(self.root, source["source_id"])

    def test_guideline_capture_does_not_claim_current_recommendation(self):
        def guideline(url):
            value = self.fetch(url)
            if "fullTextXML" not in url:
                value = value.replace(b"Fictional evidence article", b"Fictional guideline update")
            return value
        snapshot = capture(self.root, "12345678", fetch=guideline)
        self.assertEqual(snapshot["currency_status"], "unchecked")
        self.assertTrue(any("superseded" in warning for warning in snapshot["warnings"]))


if __name__ == "__main__":
    unittest.main()
