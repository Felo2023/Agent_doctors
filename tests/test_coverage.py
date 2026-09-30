import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


if importlib.util.find_spec("pymupdf"):
    import pymupdf
    from case_review.store import ReviewStore
    from intake.extract import extract_snapshot
else:
    pymupdf = None


@unittest.skipUnless(pymupdf, "install requirements-intake.txt")
class SyntheticCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-coverage-")
        self.root = Path(self.temp.name)
        (self.root / ".synthetic-fixture").write_text("SYNTHETIC_INTAKE_FIXTURE_V1")
        self.file = self.root / "fictional.pdf"
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((40, 70), "Fecha: 2025-12-04")
        page.insert_text((40, 100), "Glucosa 92 mg/dL")
        pdf.save(self.file)
        pdf.close()
        self.snapshot = extract_snapshot([self.file], self.root / "extraction.json", fixture_root=self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_source_block_coverage_is_versioned_without_enabling_diagnosis(self):
        store = ReviewStore(self.root)
        try:
            cid = self.snapshot["documents"][0]["candidates"][0]["id"]
            date_block = self.snapshot["documents"][0]["blocks"][0]["id"]
            store.decide(cid, "verify", "reviewer-one", "Compared fictional lab line with PDF", 0)
            self.assertEqual(store.state()["coverage"]["accounted_for"], 1)
            self.assertFalse(store.state()["coverage"]["complete"])
            first = store.commit(1)
            self.assertFalse(first["intake_coverage_complete"])
            store.decide_coverage(date_block, "reviewed_context", "reviewer-two",
                                  "Date header checked against the fictional PDF; no separate symptom asserted", 2)
            self.assertTrue(store.state()["coverage"]["complete"])
            second = store.commit(3)
            self.assertTrue(second["intake_coverage_complete"])
            self.assertEqual(second["coverage"]["outstanding"], 0)
            self.assertFalse(second["ready_for_diagnosis"])
            self.assertFalse(store.version(1)["intake_coverage_complete"])
            with self.assertRaisesRegex(ValueError, "no new reviewed decisions"):
                store.commit(4)
        finally:
            store.close()

    def test_missing_source_content_cannot_be_marked_reviewed_away(self):
        self.snapshot["documents"][0]["warnings"].append("Word charts present but not extracted")
        (self.root / "extraction.json").write_text(json.dumps(self.snapshot))
        store = ReviewStore(self.root)
        try:
            warning = next(item for item in store.state()["coverage"]["items"] if item["type"] == "warning")
            self.assertTrue(warning["blocking"])
            self.assertEqual(warning["state"], "source_limitation")
            with self.assertRaisesRegex(ValueError, "cannot be marked complete"):
                store.decide_coverage(warning["id"], "acknowledged", "reviewer-one",
                                      "I saw this warning but nothing was extracted", 0)
            store.decide_coverage(warning["id"], "defer", "reviewer-one",
                                  "Source chart needs separate extraction", 0)
            self.assertFalse(store.state()["coverage"]["complete"])
            self.assertEqual(store.state()["coverage"]["outstanding"], 3)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
