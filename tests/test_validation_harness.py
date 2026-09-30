import json
import fcntl
from pathlib import Path
import tempfile
import unittest

from validation.harness import ARMS, SequentialSuite


class SequentialValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-scoring-")
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.answers = self.root / "sealed-answers"
        self.work = self.root / "one-agent-workspace"
        self.results = self.root / "operator-results"
        self.inputs.mkdir()
        self.answers.mkdir()
        (self.inputs / "case.txt").write_text("Fictional presentation with no answer included.")
        self.manifest = self.inputs / "manifest.json"
        self.manifest.write_text(json.dumps({"schema_version": 1, "kind": "synthetic",
                                             "cases": [{"case_id": "SYNTH-901", "input_file": "case.txt"}]}))
        (self.answers / "SYNTH-901.json").write_text(json.dumps({"case_id": "SYNTH-901",
                                                               "accepted_ids": ["FICTIONAL-A"],
                                                               "critical_flags": ["RED-FLAG-X"]}))

    def tearDown(self):
        self.temp.cleanup()

    def suite(self):
        return SequentialSuite(self.manifest, self.answers, self.work, self.results)

    def submission(self, arm, answers):
        file = self.root / (arm + ".json")
        file.write_text(json.dumps({"case_id": "SYNTH-901", "arm": arm,
                                    "diagnoses": answers, "critical_flags": ["RED-FLAG-X"]}))
        return file

    def test_one_active_arm_at_a_time_and_sealed_answers_never_in_workspace(self):
        suite = self.suite()
        for index, arm in enumerate(ARMS):
            active = suite.prepare_next()
            self.assertEqual((active["case_id"], active["arm"]), ("SYNTH-901", arm))
            workspace = Path(active["workspace"])
            self.assertEqual([p.name for p in workspace.iterdir()], ["case-input.txt"])
            self.assertNotIn("FICTIONAL-A", (workspace / "case-input.txt").read_text())
            with self.assertRaisesRegex(ValueError, "active arm"):
                suite.prepare_next()
            if index == 0:
                with self.assertRaisesRegex(ValueError, "all case/arm"):
                    suite.score()
                with self.assertRaisesRegex(ValueError, "answer key or agent input"):
                    suite.submit(self.answers / "SYNTH-901.json")
            suite.submit(self.submission(arm, ["OTHER", "FICTIONAL-A"] if arm == "team" else ["OTHER"]))
            self.assertFalse(workspace.exists())
        self.assertIsNone(suite.prepare_next())
        scored = suite.score()
        self.assertFalse(scored["clinical_validation"])
        self.assertEqual(scored["summary_counts"]["team"]["top_1"], 0)
        self.assertEqual(scored["summary_counts"]["team"]["top_3"], 1)
        self.assertEqual(scored["scores"][0]["critical_missed"], [])

    def test_wrong_arm_and_stale_manifest_fail_without_advancing(self):
        suite = self.suite()
        suite.prepare_next()
        with self.assertRaisesRegex(ValueError, "active case/arm"):
            suite.submit(self.submission("astra", ["FICTIONAL-A"]))
        self.assertEqual(suite.ensure_progress()["next_index"], 0)
        self.manifest.write_text(self.manifest.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            suite.ensure_progress()

    def test_refuses_answer_root_inside_repository_or_workspace(self):
        from validation.harness import PROJECT
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            SequentialSuite(self.manifest, PROJECT / "validation/cases-private", self.work, self.results)
        with self.assertRaisesRegex(ValueError, "disjoint"):
            SequentialSuite(self.manifest, self.work / "answers", self.work, self.results)
        with self.assertRaisesRegex(ValueError, "manifest cannot be inside sealed answers"):
            SequentialSuite(self.manifest, self.inputs, self.work, self.results)

    def test_public_candidate_catalog_rejects_out_of_set_submissions(self):
        (self.inputs / "case.txt").write_text(json.dumps({"presentation": "fictional markers",
                                                          "allowed_diagnosis_ids": ["FICTIONAL-A", "FICTIONAL-B"]}))
        suite = self.suite()
        suite.prepare_next()
        with self.assertRaisesRegex(ValueError, "outside the prepared diagnosis catalog"):
            suite.submit(self.submission("team", ["INVENTED-BY-MODEL"]))
        self.assertEqual(suite.ensure_progress()["next_index"], 0)
        output = suite.submit(self.submission("team", ["FICTIONAL-A"]),
                              provenance={"kind": "model_backed", "model_ids": ["synthetic-test-model"]})
        self.assertEqual(json.loads(output.read_text())["provenance"]["kind"], "model_backed")

    def test_concurrent_steps_and_changed_frozen_submission_are_rejected(self):
        suite = self.suite()
        suite.result_root.mkdir()
        with (suite.result_root / ".suite.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, "another operator step"):
                self.suite().prepare_next()
        for arm in ARMS:
            suite.prepare_next()
            suite.submit(self.submission(arm, ["OTHER"]))
        frozen = self.results / "submissions/SYNTH-901/team.json"
        record = json.loads(frozen.read_text())
        record["diagnoses"] = ["FICTIONAL-A"]
        frozen.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "frozen submission changed"):
            suite.score()


if __name__ == "__main__":
    unittest.main()
