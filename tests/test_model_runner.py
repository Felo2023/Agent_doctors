import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.synthetic_roundtrip import call

from validation.harness import SequentialSuite, write_atomic
from validation.model_runner import blind_peer_packet, parse_response, run_active


class ModelRunnerContracts(unittest.TestCase):
    def test_strict_json_and_blind_peer_packet(self):
        allowed = ["FICTIONAL-A", "FICTIONAL-B"]
        self.assertEqual(parse_response('{"diagnoses":["FICTIONAL-A"],"critical_flags":[]}',
                                        role="baseline", allowed=allowed)["diagnoses"], ["FICTIONAL-A"])
        with self.assertRaisesRegex(ValueError, "unknown/duplicate"):
            parse_response('{"diagnoses":["NOT-ALLOWED"],"critical_flags":[]}', role="baseline", allowed=allowed)
        with self.assertRaisesRegex(ValueError, "required structured schema"):
            parse_response('{"diagnoses":[],"critical_flags":[],"answer":"leak"}', role="baseline", allowed=allowed)
        peers = blind_peer_packet({"presentation": "fictional"}, [{"diagnoses": ["FICTIONAL-B"],
                         "critical_flags": [], "reason": "source-derived alternative", "role": "not-to-be-shown"}])
        self.assertNotIn("not-to-be-shown", json.dumps(peers))

    def test_real_runner_orchestration_contract_with_fake_model_processes(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-runner-test-") as directory:
            root = Path(directory)
            inputs = root / "inputs"
            sealed = root / "answers"
            inputs.mkdir()
            sealed.mkdir()
            (inputs / "case.txt").write_text(json.dumps({"presentation": "Fictional marker A is present.",
                                                        "allowed_diagnosis_ids": ["FICTIONAL-A", "FICTIONAL-B"]}))
            manifest = inputs / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "kind": "synthetic",
                                            "cases": [{"case_id": "SYNTH-901", "input_file": "case.txt"}]}))
            (sealed / "SYNTH-901.json").write_text(json.dumps({"case_id": "SYNTH-901",
                                                             "accepted_ids": ["FICTIONAL-A"], "critical_flags": []}))
            suite = SequentialSuite(manifest, sealed, root / "work", root / "results")
            requests = []
            closing = []

            class FakeProcess:
                def __init__(self, role, model, active, credential):
                    self.role, self.model, self.session = role, model, "ses_fake_" + role
                    self.closed = False

                def prompt(self, question, marker=None):
                    self.question = question
                    requests.append((self.role, question))
                    if question.startswith("Question round"):
                        from bus.evaluation import PEERS
                        recipient = PEERS[(PEERS.index(self.role) + 1) % len(PEERS)]
                        call(self.mail_url, "/question", token=self.mail_token, method="POST",
                             body={**self.mail_binding, "session_id": self.session, "recipient": recipient,
                                   "body": "Which source marker supports FICTIONAL-A?", "dedupe_key": self.role + ":question"})
                    return marker or "test-turn"

                def configure_mail(self, url, binding, role_token):
                    self.mail_url, self.mail_binding, self.mail_token = url, binding, role_token

                def wait(self, marker, timeout=240):
                    self_ref = self
                    if self_ref.role == "lead":
                        return json.dumps({"diagnoses": ["FICTIONAL-A"], "critical_flags": [], "disagreements": []})
                    if self_ref.role == "baseline":
                        return json.dumps({"diagnoses": ["FICTIONAL-A"], "critical_flags": []})
                    if self.question.startswith("Answer each assigned"):
                        packet = json.loads(self.question.split("\n", 1)[1])
                        return json.dumps({"diagnoses": ["FICTIONAL-A"], "critical_flags": [], "reason": "fictional marker",
                                           "answers": [{"question_id": q["id"], "answer": "The fictional input explicitly provides marker A."}
                                                       for q in packet["assigned_questions"]]})
                    return json.dumps({"diagnoses": ["FICTIONAL-A"], "critical_flags": [], "reason": "fictional marker"})

                def export(self, target):
                    write_atomic(target, [{"role": self.role, "turn": "synthetic"}])

                def close(self):
                    if not self.closed:
                        closing.append(self.role)
                        self.closed = True

            with patch("validation.model_runner.ModelProcess", FakeProcess):
                for expected_arm in ("team", "opus", "astra", "sol"):
                    active = suite.prepare_next()
                    self.assertEqual(active["arm"], expected_arm)
                    output = run_active(suite, root / "fake-credential")
                    self.assertFalse(Path(active["workspace"]).exists())
                    saved = json.loads(output.read_text())
                    self.assertEqual(saved["diagnoses"], ["FICTIONAL-A"])
                    self.assertEqual(saved["provenance"]["kind"], "model_backed")
            self.assertEqual(len(closing), 7)  # 4 panel roles + 3 baselines
            initial = [text for role, text in requests if role != "lead" and "First independent" in text]
            self.assertEqual(len(initial), 3)
            self.assertTrue(all("other_opinions" not in text and "critique" not in text for text in initial))
            self.assertEqual(suite.score()["status"], "synthetic_model_backed_operational_test")
            self.assertFalse(suite.score()["clinical_validation"])
            self.assertTrue((root / "results/audit/SYNTH-901/team/panel-notes.json").exists())
            report = json.loads((root / "results/audit/SYNTH-901/team/evaluation-mail-report.json").read_text())
            self.assertEqual(report["phase"], "closed")
            self.assertEqual(len(report["jobs"]), 16)
            self.assertEqual(len(report["question_threads"]), 3)
            self.assertTrue(all(thread["answer"] for thread in report["question_threads"]))
            self.assertTrue(all(job["status"] == "completed" for job in report["jobs"]))


if __name__ == "__main__":
    unittest.main()
