import base64
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer


if importlib.util.find_spec("pymupdf"):
    import pymupdf
    from case_review.server import Handler
    from case_review.store import ReviewStore
    from intake.extract import extract_snapshot
    from intake.markdown import write_bundle
    from intake.dual_abstract import save as save_abstractions
else:
    pymupdf = None


@unittest.skipUnless(pymupdf, "install requirements-intake.txt for review UI tests")
class SyntheticReviewHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-ui-")
        self.root = Path(self.temp.name)
        (self.root / ".synthetic-fixture").write_text("SYNTHETIC_INTAKE_FIXTURE_V1")
        file = self.root / "fictional-lab.pdf"
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((60, 70), "Fecha: 2025-12-04")
        page.insert_text((60, 110), "Glucosa 92 mg/dL")
        pdf.save(file)
        pdf.close()
        self.snapshot = extract_snapshot([file], self.root / "extraction.json", fixture_root=self.root)
        write_bundle(self.snapshot, self.root, fixture_root=self.root)
        self.store = ReviewStore(self.root)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.store = self.store
        self.server.password = "synthetic-local-review-password"
        self.server.csrf = "synthetic-form-token"
        self.host = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.store.close()
        self.temp.cleanup()

    def request(self, path, fields=None, *, password=None, origin=True):
        headers = {}
        if password is not None:
            credentials = base64.b64encode(("reviewer:" + password).encode()).decode()
            headers["Authorization"] = "Basic " + credentials
        if fields is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            if origin:
                headers["Origin"] = self.host
        request = urllib.request.Request(self.host + path, headers=headers,
            data=urllib.parse.urlencode(fields).encode() if fields is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def test_review_ui_requires_auth_and_yields_source_linked_version(self):
        self.assertEqual(self.request("/")[0], 401)
        self.assertEqual(self.request("/", password="wrong")[0], 401)
        status, body, headers = self.request("/", password=self.server.password)
        self.assertEqual(status, 200)
        self.assertIn(b"Make every fact traceable", body)
        self.assertIn(b"Glucosa", body)
        self.assertIn(b"no-store", headers["Cache-Control"].encode())
        candidate = self.snapshot["documents"][0]["candidates"][0]
        source = self.snapshot["documents"][0]["source_id"]
        self.assertEqual(self.request("/original/" + source, password=self.server.password)[1][:5], b"%PDF-")
        self.assertIn(source.encode(), self.request("/source/" + source, password=self.server.password)[1])
        form = {"csrf": self.server.csrf, "revision": 0, "candidate_id": candidate["id"], "action": "verify",
                "reviewer": "fictional-reviewer", "note": "Checked the fictional PDF page", "correction_id": ""}
        self.assertEqual(self.request("/decision", fields=form, password=self.server.password, origin=False)[0], 403)
        self.assertEqual(self.request("/decision", fields={**form, "csrf": "wrong"}, password=self.server.password)[0], 403)
        self.assertEqual(self.store.state()["revision"], 0)
        self.assertEqual(self.request("/decision", fields=form, password=self.server.password)[0], 200)  # follows 303
        self.assertEqual(self.request("/decision", fields=form, password=self.server.password)[0], 409)
        self.assertEqual(self.request("/case", password=self.server.password)[0], 409)
        self.assertEqual(self.request("/commit", fields={"csrf": self.server.csrf, "revision": 1}, password=self.server.password)[0], 200)
        status, body, _ = self.request("/case", password=self.server.password)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["observations"][0]["source"]["block_id"], candidate["source"]["block_id"])
        self.assertFalse(result["ready_for_diagnosis"])
        self.assertFalse(self.store.state()["can_commit"])
        self.assertEqual(json.loads(self.request("/case/version/1", password=self.server.password)[1])["version"], 1)

    def test_correction_ui_needs_independent_review_then_explicit_fact_verification(self):
        doc = self.snapshot["documents"][0]
        candidate = doc["candidates"][0]
        original_markdown = (self.root / (doc["source_id"] + ".md")).read_bytes()
        proposal_form = {"csrf": self.server.csrf, "revision": 0,
                         "candidate_id": candidate["id"], "suggested_text": "Glucosa 95 mg/dL",
                         "reason": "Fictional scan transcription", "reviewer": "reviewer-one"}
        self.assertEqual(self.request("/correction/propose", fields=proposal_form, password=self.server.password)[0], 200)
        proposal = json.loads((self.root / "corrections.jsonl").read_text().splitlines()[0])
        review_form = {"csrf": self.server.csrf, "revision": 1, "proposal_id": proposal["event_id"],
                       "decision": "accepted", "reason": "Checked fictional source", "reviewer": "reviewer-one"}
        self.assertEqual(self.request("/correction/review", fields=review_form, password=self.server.password)[0], 400)
        self.assertEqual(self.request("/correction/review", fields={**review_form, "reviewer": "reviewer-two"}, password=self.server.password)[0], 200)
        self.assertEqual(self.store.state()["case_version"], 0)
        self.assertIn(b"Status: **accepted**", self.request("/corrections", password=self.server.password)[1])
        decision = {"csrf": self.server.csrf, "revision": 2, "candidate_id": candidate["id"],
                    "action": "verify", "reviewer": "reviewer-three", "note": "Checked fictional original PDF",
                    "correction_id": proposal["event_id"]}
        self.assertEqual(self.request("/decision", fields=decision, password=self.server.password)[0], 200)
        self.assertEqual(self.request("/commit", fields={"csrf": self.server.csrf, "revision": 3}, password=self.server.password)[0], 200)
        version = json.loads(self.request("/case", password=self.server.password)[1])
        self.assertEqual(version["observations"][0]["value_decimal"], "95")
        self.assertEqual(version["observations"][0]["original_value"], "92")
        self.assertEqual((self.root / (doc["source_id"] + ".md")).read_bytes(), original_markdown)

    def test_fact_proposal_requires_second_reviewer_and_preserves_source_attribution(self):
        doc = self.snapshot["documents"][0]
        block = doc["blocks"][0]
        proposal = {"csrf": self.server.csrf, "revision": 0, "source_id": doc["source_id"],
                    "block_id": block["id"], "category": "test_report",
                    "statement": "Fictional report lists 2025-12-04 as a date",
                    "event_date": "2025-12-04", "date_precision": "exact",
                    "attribution": "source_text", "proposer": "fictional-proposer-A"}
        self.assertEqual(self.request("/fact/propose", fields=proposal, password=self.server.password)[0], 200)
        fact_id = self.store.state()["facts"][0]["id"]
        review = {"csrf": self.server.csrf, "revision": 1, "fact_id": fact_id,
                  "action": "verify", "reviewer": "fictional-proposer-A", "note": "Checked original fictional date line"}
        self.assertEqual(self.request("/fact/review", fields=review, password=self.server.password)[0], 400)
        self.assertEqual(self.request("/fact/review", fields={**review, "reviewer": "reviewer-B"}, password=self.server.password)[0], 200)
        self.assertFalse(self.store.state()["can_commit"])  # still has unreviewed lab candidate
        lab = doc["candidates"][0]
        decision = {"csrf": self.server.csrf, "revision": 2, "candidate_id": lab["id"],
                    "action": "verify", "reviewer": "reviewer-B", "note": "Checked fictional lab page", "correction_id": ""}
        self.assertEqual(self.request("/decision", fields=decision, password=self.server.password)[0], 200)
        self.assertTrue(self.store.state()["can_commit"])
        self.assertEqual(self.request("/commit", fields={"csrf": self.server.csrf, "revision": 3}, password=self.server.password)[0], 200)
        version = json.loads(self.request("/case", password=self.server.password)[1])
        self.assertEqual(version["source_statements"][0]["original_text"], block["text"])
        self.assertEqual(version["source_statements"][0]["attribution"], "source_text")
        self.assertEqual({item["kind"] for item in version["timeline"]}, {"lab", "test_report"})

    def test_independent_abstractor_comparison_is_visible_but_never_auto_reviewed(self):
        block_id = self.snapshot["documents"][0]["candidates"][0]["source"]["block_id"]
        item = {"block_id": block_id, "category": "test_report", "statement": "Fictional source has glucose 92 mg/dL",
                "event_date": None, "date_precision": "unknown", "attribution": "source_text"}
        save_abstractions(self.root, self.snapshot, [item], [{**item, "statement": "Document records glucose 92 mg/dL"}])
        status, body, _ = self.request("/abstractions", password=self.server.password)
        self.assertEqual(status, 200)
        self.assertIn(b"Agreement is not evidence", body)
        self.assertIn(b"Fictional source has glucose", body)
        self.assertIn(b"Document records glucose", body)
        self.assertEqual(self.store.state()["facts"], [])
        self.assertEqual(self.store.state()["case_version"], 0)

    def test_coverage_decision_is_separate_from_fact_review_and_versioned(self):
        doc = self.snapshot["documents"][0]
        candidate = doc["candidates"][0]
        date_block = doc["blocks"][0]["id"]
        self.assertIn(b"Source coverage", self.request("/", password=self.server.password)[1])
        review = {"csrf": self.server.csrf, "revision": 0, "candidate_id": candidate["id"],
                  "action": "verify", "reviewer": "reviewer-1", "note": "Checked fictional source page", "correction_id": ""}
        self.assertEqual(self.request("/decision", fields=review, password=self.server.password)[0], 200)
        self.assertFalse(self.store.state()["coverage"]["complete"])
        coverage = {"csrf": self.server.csrf, "revision": 1, "item_id": date_block,
                    "action": "reviewed_context", "reviewer": "reviewer-2",
                    "note": "Reviewed the fictional date header as context"}
        self.assertEqual(self.request("/coverage", fields=coverage, password=self.server.password)[0], 200)
        self.assertTrue(self.store.state()["coverage"]["complete"])
        self.assertEqual(self.request("/commit", fields={"csrf": self.server.csrf, "revision": 2}, password=self.server.password)[0], 200)
        version = json.loads(self.request("/case", password=self.server.password)[1])
        self.assertTrue(version["intake_coverage_complete"])
        self.assertFalse(version["ready_for_diagnosis"])


if __name__ == "__main__":
    unittest.main()
