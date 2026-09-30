import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


if importlib.util.find_spec("pymupdf"):
    import pymupdf
    from intake.dual_abstract import compare, packet, parse_reply, save
    from intake.extract import extract_snapshot
    from case_review.store import ReviewStore
else:
    pymupdf = None


@unittest.skipUnless(pymupdf, "install requirements-intake.txt")
class IndependentSyntheticAbstractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-abstraction-test-")
        self.root = Path(self.temp.name)
        (self.root / ".synthetic-fixture").write_text("SYNTHETIC_INTAKE_FIXTURE_V1")
        file = self.root / "fictional.pdf"
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((40, 70), "Fecha: 2025-12-04")
        page.insert_text((40, 100), "Glucosa 92 mg/dL")
        pdf.save(file)
        pdf.close()
        self.snapshot = extract_snapshot([file], self.root / "extraction.json", fixture_root=self.root)
        self.source = packet(self.snapshot)
        self.block_id = self.source["blocks"][1]["block_id"]

    def tearDown(self):
        self.temp.cleanup()

    def item(self, *, statement="Document reports glucose 92 mg/dL", block_id=None):
        return {"block_id": block_id or self.block_id, "category": "test_report", "statement": statement,
                "event_date": "2025-12-04", "date_precision": "exact", "attribution": "source_text"}

    def test_comparison_exposes_agreement_difference_and_one_sided_claim(self):
        same = compare(self.source, [self.item()], [self.item()])
        self.assertEqual(same[0]["status"], "same_wording")
        self.assertTrue(same[0]["human_review_required"])
        different = compare(self.source, [self.item()], [self.item(statement="Other interpretation of fictional result")])
        self.assertEqual(different[0]["status"], "different_or_multiple")
        one_sided = compare(self.source, [self.item()], [])
        self.assertEqual(one_sided[0]["status"], "one_sided")
        self.assertEqual(one_sided[1]["status"], "omitted_by_both")
        self.assertEqual(len(compare(self.source, [], [])), len(self.source["blocks"]))

    def test_validation_rejects_fabricated_citations_and_dates(self):
        with self.assertRaisesRegex(ValueError, "unknown source block"):
            compare(self.source, [self.item(block_id="SRC-invented:p1")], [])
        wrong = {**self.item(), "event_date": None}
        with self.assertRaisesRegex(ValueError, "invalid date precision or date"):
            compare(self.source, [wrong], [])
        approximate = {**self.item(), "event_date": "2024", "date_precision": "approximate"}
        self.assertEqual(compare(self.source, [approximate], [approximate])[0]["status"], "same_wording")
        with self.assertRaisesRegex(ValueError, "exact date requires"):
            compare(self.source, [{**approximate, "date_precision": "exact"}], [])
        with self.assertRaisesRegex(ValueError, "required schema"):
            compare(self.source, [{**self.item(), "diagnosis": "invented"}], [])

    def test_saving_comparison_never_creates_reviewed_fact(self):
        parsed = parse_reply('```json\n{"items": []}\n```')
        self.assertEqual(parsed, [])
        result = save(self.root, self.snapshot, [self.item()], parsed)
        self.assertEqual(result["status"], "unverified_independent_abstractions")
        self.assertFalse(result["promoted_to_case"])
        self.assertEqual(len(result["source_snapshot_sha256"]), 64)
        saved = json.loads((self.root / "dual_abstraction.json").read_text())
        self.assertEqual(saved["comparisons"][0]["proposals"]["abstractor-a"][0]["source_id"],
                         self.snapshot["documents"][0]["source_id"])
        store = ReviewStore(self.root)
        try:
            self.assertEqual(store.state()["facts"], [])
            self.assertEqual(store.state()["case_version"], 0)
        finally:
            store.close()

    def test_changed_source_blocks_abstraction_report(self):
        original = self.root / "fictional.pdf"
        original.write_bytes(original.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "source changed"):
            save(self.root, self.snapshot, [], [])
        self.assertFalse((self.root / "dual_abstraction.json").exists())


if __name__ == "__main__":
    unittest.main()
