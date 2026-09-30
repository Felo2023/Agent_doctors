import json
from pathlib import Path
import tempfile
import unittest

from validation.curation import metadata, save_metadata
from validation.harness import PROJECT


class PublishedCandidateMetadataTests(unittest.TestCase):
    def fake_fetch(self, url):
        if "fullTextXML" in url:
            return b"<article><front><article-meta><permissions><license><license_ref>https://creativecommons.org/licenses/by/4.0/</license_ref></license></permissions></article-meta></front></article>"
        return json.dumps({"resultList": {"result": [{"pmcid": "PMC1234567", "pmid": "12345678",
                         "title": "Answer-bearing article title", "pubType": "case reports",
                         "isOpenAccess": "Y", "firstPublicationDate": "2025-06-01"}]}}).encode()

    def test_metadata_stays_scorer_owned_and_requires_manual_cutoff(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-public-metadata-") as directory:
            target = Path(directory) / "candidate.json"
            result = save_metadata(["PMC1234567"], target, fetch=self.fake_fetch)
            self.assertTrue(result[0]["licensed_for_review"])
            self.assertIn("diagnosis hidden", str(result[0]["manual_checks"]))
            self.assertEqual(json.loads(target.read_text())["status"], "metadata_candidates_only_not_a_benchmark")
            with self.assertRaisesRegex(ValueError, "repository"):
                save_metadata(["PMC1234567"], PROJECT / "validation/candidate.json", fetch=self.fake_fetch)
        with self.assertRaisesRegex(ValueError, "PMCID"):
            metadata("../private-case", fetch=self.fake_fetch)


if __name__ == "__main__":
    unittest.main()
