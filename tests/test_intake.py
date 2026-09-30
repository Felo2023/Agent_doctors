import importlib.util
import pathlib
import sys
import tempfile
import unittest


if importlib.util.find_spec("pymupdf"):
    import pymupdf
    from intake.extract import extract_pdf, extract_snapshot, reconcile
else:
    pymupdf = None


@unittest.skipIf(pymupdf is None, "install requirements-intake.txt to run PDF tests")
class SyntheticIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-intake-")
        self.root = pathlib.Path(self.temp.name)
        (self.root / ".synthetic-fixture").write_text("SYNTHETIC_INTAKE_FIXTURE_V1")

    def tearDown(self):
        self.temp.cleanup()

    def document(self, name, lines, *, scan=False, mixed=False):
        file = self.root / name
        pdf = pymupdf.open()
        page = pdf.new_page(width=600, height=800)
        for index, line in enumerate(lines):
            page.insert_text((55, 80 + index * 30), line, fontsize=17)
        if scan or mixed:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            if scan:
                pdf.close()
                pdf = pymupdf.open()
                page = pdf.new_page(width=600, height=800)
            page.insert_image(page.rect, stream=pix.tobytes("png"))
        pdf.save(file)
        pdf.close()
        return file

    def test_native_pdf_provenance_and_decimal_comma(self):
        file = self.document("fictional-a.pdf", ["Fecha toma: 2025-12-04", "Creatinina 1,4 mg/dL (0,6 - 1,1)"])
        record = extract_pdf(file, fixture_root=self.root)
        self.assertEqual(len(record["candidates"]), 1)
        candidate = record["candidates"][0]
        self.assertEqual(candidate["value_decimal"], "1.4")
        self.assertEqual(candidate["reference_range_original"], "0,6 - 1,1")
        self.assertEqual(candidate["collection_date"], "2025-12-04")
        self.assertEqual(candidate["source"]["page"], 1)
        self.assertEqual(candidate["source"]["channel"], "native")
        self.assertEqual(candidate["status"], "unverified_candidate")
        self.assertEqual(record["pages"][0]["ocr_lines"], [])

    def test_conflicts_are_retained_not_silently_merged(self):
        first = self.document("fictional-first.pdf", ["Fecha: 2025-12-04", "Glucosa 92 mg/dL"])
        second = self.document("fictional-second.pdf", ["Fecha: 2025-12-04", "Glucosa 128 mg/dL"])
        output = self.root / "result.json"
        result = extract_snapshot([first, second], output, fixture_root=self.root)
        self.assertTrue(output.exists())
        self.assertEqual(result["case_id"], "SYNTH-001")
        self.assertEqual(result["conflicts"][0]["status"], "unresolved")
        self.assertEqual(len(result["conflicts"][0]["candidate_ids"]), 2)
        self.assertEqual({item["value_decimal"] for doc in result["documents"] for item in doc["candidates"]}, {"92", "128"})

    def test_missing_dates_are_not_combined_across_documents(self):
        a = self.document("fictional-undated-a.pdf", ["Glucosa 92 mg/dL"])
        b = self.document("fictional-undated-b.pdf", ["Glucosa 128 mg/dL"])
        result = extract_snapshot([a, b], self.root / "out.json", fixture_root=self.root)
        self.assertEqual(result["conflicts"], [])
        self.assertTrue(all(item["collection_date"] is None for doc in result["documents"] for item in doc["candidates"]))

    def test_ocr_missing_is_flagged_instead_of_treated_as_blank(self):
        file = self.document("fictional-scan.pdf", ["Fecha: 2025-12-04", "Creatinina 1,4 mg/dL"], scan=True)

        def unavailable(_):
            raise RuntimeError("synthetic OCR unavailable")

        result = extract_pdf(file, fixture_root=self.root, ocr=unavailable)
        self.assertEqual(result["candidates"], [])
        self.assertTrue(result["pages"][0]["requires_review"])
        self.assertTrue(any("OCR failed" in msg for msg in result["pages"][0]["warnings"]))

    @unittest.skipUnless(sys.platform == "darwin", "Apple Vision requires macOS")
    def test_scanned_pdf_uses_local_vision_ocr(self):
        file = self.document("fictional-ocr.pdf", ["Fecha: 2025-12-04", "Creatinina 1,4 mg/dL"], scan=True)
        result = extract_pdf(file, fixture_root=self.root)
        self.assertEqual(result["pages"][0]["native_lines"], [])
        self.assertTrue(result["pages"][0]["requires_review"])
        self.assertTrue(result["pages"][0]["ocr_lines"])
        self.assertTrue(any(c["source"]["channel"] == "ocr" for c in result["candidates"]))

    def test_mixed_page_keeps_both_channels_for_review(self):
        file = self.document("fictional-mixed.pdf", ["Fecha: 2025-12-04", "Glucosa 92 mg/dL"], mixed=True)
        result = extract_pdf(file, fixture_root=self.root, ocr=lambda _: [{"text": "Glucosa 128 mg/dL", "confidence": 0.95}])
        self.assertTrue(result["pages"][0]["requires_review"])
        self.assertTrue(any("mixed" in message for message in result["pages"][0]["warnings"]))
        self.assertEqual({c["value_decimal"] for c in result["candidates"]}, {"92", "128"})
        self.assertEqual(len(reconcile([result])), 1)

    def test_real_case_flag_rejected(self):
        file = self.document("fictional-only.pdf", ["Glucosa 92 mg/dL"])
        with self.assertRaisesRegex(ValueError, "disabled"):
            extract_pdf(file, fixture_root=self.root, synthetic_only=False)
        with self.assertRaisesRegex(ValueError, "explicit marked"):
            extract_pdf(file)
        other = self.root / "unmarked"
        other.mkdir()
        copied = other / file.name
        copied.write_bytes(file.read_bytes())
        with self.assertRaisesRegex(ValueError, "marker mismatch"):
            extract_pdf(copied, fixture_root=other)


if __name__ == "__main__":
    unittest.main()
