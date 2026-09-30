import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from bus.evaluation import EvaluationBus, Handler, PEERS, ROLES


class EvaluationMailBusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-eval-bus-")
        self.path = Path(self.temp.name) / "mail.sqlite"
        self.data = {"presentation": "Fictional marker A", "allowed_diagnosis_ids": ["A", "B"]}
        self.admin = "operator-" + "x" * 32
        self.bus = EvaluationBus(self.path, "SYNTH-941", self.data, self.admin)
        self.tokens = {role: role + "-" + "a" * 32 for role in ROLES}
        for role in ROLES:
            self.bus.register(role, "ses_" + role, self.tokens[role])

    def tearDown(self):
        self.bus.close()
        self.temp.cleanup()

    def finish(self, role, code="A", reason="source-A-only"):
        job = self.bus.job(role)["job"]
        self.bus.started(role, "ses_" + role, job["id"], "TURN-" + "a" * 24)
        position = {"diagnoses": [code], "critical_flags": [],
                    "disagreements" if role == "lead" else "reason": [] if role == "lead" else reason}
        if self.bus.phase() == "answers":
            position["answers"] = [{"question_id": thread["id"], "answer": "Checked fictional marker A"}
                                   for thread in self.bus.question_threads(recipient=role)]
        self.bus.submit(role, "ses_" + role, job["id"], position)
        return job, position

    def test_blind_gate_anonymous_exchange_and_immutable_positions(self):
        self.bus.transition("blind")
        self.assertIsNone(self.bus.job("lead")["job"])
        with self.assertRaisesRegex(ValueError, "unfinished"):
            self.bus.transition("critique")
        job, position = self.finish(PEERS[0], reason="secret-first-opinion-A")
        for role in PEERS[1:]:
            self.assertNotIn("secret-first-opinion-A", self.bus.job(role)["job"]["prompt"])
            self.finish(role, code="B", reason="other-independent-source-reason")
        self.bus.submit(PEERS[0], "ses_" + PEERS[0], job["id"], position)  # same receipt is idempotent
        with self.assertRaisesRegex(ValueError, "cannot overwrite"):
            self.bus.submit(PEERS[0], "ses_" + PEERS[0], job["id"], {**position, "reason": "changed after final"})
        self.bus.transition("critique")
        prompt = self.bus.job(PEERS[0])["job"]["prompt"]
        self.assertIn("other-independent-source-reason", prompt)
        self.assertNotIn("secret-first-opinion-A", prompt)
        self.assertTrue(all(role not in prompt for role in ROLES))
        for role in PEERS:
            self.finish(role)
        self.bus.transition("questions")
        for role in PEERS:
            self.finish(role)
        self.bus.transition("answers")
        for role in PEERS:
            self.finish(role)
        self.bus.transition("final")
        for role in PEERS:
            self.finish(role)
        self.bus.transition("synthesis")
        self.assertIn("secret-first-opinion-A", self.bus.job("lead")["job"]["prompt"])
        self.finish("lead")
        self.bus.transition("closed")
        self.assertEqual(len(self.bus.report()["jobs"]), 16)
        self.assertFalse(self.bus.report()["clinical_validation"])

    def test_restart_preserves_running_marker_without_creating_another_job(self):
        self.bus.transition("blind")
        role = PEERS[0]
        job = self.bus.job(role)["job"]
        marker = "TURN-" + "b" * 24
        self.bus.started(role, "ses_" + role, job["id"], marker)
        self.bus.close()
        self.bus = EvaluationBus(self.path, "SYNTH-941", self.data, self.admin)
        restored = self.bus.job(role)["job"]
        self.assertEqual(restored["status"], "running")
        self.assertEqual(restored["turn_marker"], marker)
        self.bus.started(role, "ses_" + role, job["id"], marker)
        with self.assertRaisesRegex(ValueError, "already dispatched"):
            self.bus.started(role, "ses_" + role, job["id"], "TURN-" + "c" * 24)
        self.bus.submit(role, "ses_" + role, job["id"], {"diagnoses": ["A"], "critical_flags": [], "reason": "restored completed turn"})
        self.assertEqual(len(self.bus.report()["jobs"]), 3)
        with self.assertRaisesRegex(ValueError, "another case"):
            EvaluationBus(self.path, "SYNTH-942", self.data, self.admin)

    def test_case_input_is_not_mutated_by_caller_and_invalid_positions_are_rejected(self):
        self.data["presentation"] = "changed caller data"
        self.data["allowed_diagnosis_ids"].append("INVENTED")
        self.bus.transition("blind")
        job = self.bus.job(PEERS[0])["job"]
        self.assertNotIn("changed caller data", job["prompt"])
        self.assertNotIn("INVENTED", job["prompt"])
        with self.assertRaisesRegex(ValueError, "valid OpenCode turn marker"):
            self.bus.started(PEERS[0], "ses_" + PEERS[0], job["id"], "not-a-turn")
        self.bus.started(PEERS[0], "ses_" + PEERS[0], job["id"], "TURN-" + "a" * 24)
        with self.assertRaisesRegex(ValueError, "invalid codes"):
            self.bus.submit(PEERS[0], "ses_" + PEERS[0], job["id"],
                            {"diagnoses": ["INVENTED"], "critical_flags": [], "reason": "caller changed catalog"})
        self.assertEqual(self.bus.job(PEERS[0])["job"]["status"], "running")

    def test_http_scope_role_and_operator_views_are_separated(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.bus = self.bus
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"

        def request(path, token, body=None):
            req = urllib.request.Request(url + path, headers={"Authorization": "Bearer " + token,
                "Content-Type": "application/json"}, data=json.dumps(body).encode() if body is not None else None)
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())
        binding = {"case_id": self.bus.case_id, "case_hash": self.bus.case_hash}
        try:
            self.assertEqual(request("/report", self.tokens[PEERS[0]])[0], 403)
            self.assertEqual(request("/job", "wrong-token")[0], 401)
            self.assertEqual(request("/phase", self.tokens[PEERS[0]], {**binding, "phase": "blind"})[0], 403)
            self.assertEqual(request("/phase", self.admin, {**binding, "case_id": "SYNTH-942", "phase": "blind"})[0], 400)
            self.assertEqual(request("/phase", self.admin, {**binding, "phase": "blind"})[0], 200)
            _, own = request("/job", self.tokens[PEERS[0]])
            self.assertEqual(own["job"]["recipient"], PEERS[0])
            forged = {**binding, "session_id": "ses_" + PEERS[1], "job_id": own["job"]["id"], "turn_marker": "TURN-" + "a" * 24}
            self.assertEqual(request("/started", self.tokens[PEERS[0]], forged)[0], 400)
            self.assertEqual(request("/started", self.tokens[PEERS[1]], {**forged, "session_id": "ses_" + PEERS[1]})[0], 400)
            self.assertEqual(request("/job", self.admin)[0], 403)
            question = {**binding, "session_id": "ses_" + PEERS[0], "recipient": PEERS[1],
                        "body": "Check fictional marker A", "dedupe_key": "http-question-key"}
            self.assertEqual(request("/question", self.tokens[PEERS[0]], question)[0], 400)  # blind phase locked
            for peer in PEERS:
                self.finish(peer)
            self.bus.transition("critique")
            for peer in PEERS:
                self.finish(peer)
            self.bus.transition("questions")
            job = self.bus.job(PEERS[0])["job"]
            self.bus.started(PEERS[0], "ses_" + PEERS[0], job["id"], "TURN-" + "a" * 24)
            self.assertEqual(request("/question", self.admin, question)[0], 403)
            self.assertEqual(request("/question", self.tokens[PEERS[1]], question)[0], 400)
            self.assertEqual(request("/question", self.tokens[PEERS[0]], {**question, "case_hash": "other-case"})[0], 400)
            self.assertEqual(request("/question", self.tokens[PEERS[0]], question)[0], 200)
            self.assertEqual(request("/question", self.tokens[PEERS[0]], {**question, "body": "another", "dedupe_key": "second"})[0], 400)
            self.assertEqual(len(self.bus.question_threads()), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def question_phase(self):
        self.bus.transition("blind")
        for role in PEERS:
            self.finish(role)
        self.bus.transition("critique")
        for role in PEERS:
            self.finish(role)
        self.bus.transition("questions")

    def test_targeted_peer_question_budgets_replies_and_final_handoff(self):
        with self.assertRaisesRegex(ValueError, "outside their question round"):
            self.bus.ask(PEERS[0], "ses_" + PEERS[0], PEERS[1], "Check source", "key")
        self.question_phase()
        question_job = self.bus.job(PEERS[0])["job"]
        self.bus.started(PEERS[0], "ses_" + PEERS[0], question_job["id"], "TURN-" + "a" * 24)
        receipt = self.bus.ask(PEERS[0], "ses_" + PEERS[0], PEERS[1], "Does marker A support code A?", "tool-turn-key")
        self.assertTrue(self.bus.ask(PEERS[0], "ses_" + PEERS[0], PEERS[1], "Does marker A support code A?", "tool-turn-key")["duplicate"])
        with self.assertRaisesRegex(ValueError, "budget exhausted"):
            self.bus.ask(PEERS[0], "ses_" + PEERS[0], PEERS[2], "Another question", "second-key")
        with self.assertRaisesRegex(ValueError, "never self or Lead"):
            self.bus.ask(PEERS[0], "ses_" + PEERS[0], "lead", "Hidden answer?", "third-key")
        with self.assertRaisesRegex(ValueError, "session does not match"):
            self.bus.ask(PEERS[0], "ses_" + PEERS[1], PEERS[1], "Forged session", "key")
        for role in PEERS:
            self.finish(role)
        self.bus.transition("answers")
        own = self.bus.job(PEERS[1])["job"]
        self.assertIn(receipt["question_id"], own["prompt"])
        self.assertNotIn(receipt["question_id"], self.bus.job(PEERS[2])["job"]["prompt"])
        with self.assertRaisesRegex(ValueError, "outside their question round"):
            self.bus.ask(PEERS[1], "ses_" + PEERS[1], PEERS[0], "Recursive question", "key")
        self.bus.started(PEERS[1], "ses_" + PEERS[1], own["id"], "TURN-" + "a" * 24)
        with self.assertRaisesRegex(ValueError, "each assigned question"):
            self.bus.submit(PEERS[1], "ses_" + PEERS[1], own["id"],
                            {"diagnoses": ["A"], "critical_flags": [], "reason": "Checked source", "answers": []})
        for role in PEERS:
            self.finish(role)
        self.bus.transition("final")
        self.assertIn("Checked fictional marker A", self.bus.job(PEERS[0])["job"]["prompt"])
        for role in PEERS:
            self.finish(role)
        self.bus.transition("synthesis")
        lead = self.bus.job("lead")["job"]["prompt"]
        self.assertIn("Does marker A support code A?", lead)
        self.assertIn("Checked fictional marker A", lead)
        self.assertIn("final_positions", lead)

    def test_question_and_answer_survive_restart_without_changing_original(self):
        self.question_phase()
        role, recipient = PEERS[:2]
        job = self.bus.job(role)["job"]
        self.bus.started(role, "ses_" + role, job["id"], "TURN-" + "a" * 24)
        receipt = self.bus.ask(role, "ses_" + role, recipient, "Check fictional marker A", "tool-key")
        self.bus.close()
        self.bus = EvaluationBus(self.path, "SYNTH-941", self.data, self.admin)
        self.assertEqual(self.bus.question_threads()[0]["body"], "Check fictional marker A")
        self.assertEqual(self.bus.ask(role, "ses_" + role, recipient, "Check fictional marker A", "tool-key")["question_id"], receipt["question_id"])
        for peer in PEERS:
            self.finish(peer)
        self.bus.transition("answers")
        for peer in PEERS:
            self.finish(peer)
        before = self.bus.report()["question_threads"]
        self.bus.close()
        self.bus = EvaluationBus(self.path, "SYNTH-941", self.data, self.admin)
        self.assertEqual(self.bus.report()["question_threads"], before)
        self.assertIsNotNone(before[0]["answer"])
        self.bus.transition("final")
        self.assertIn(before[0]["answer"], self.bus.job(role)["job"]["prompt"])


if __name__ == "__main__":
    unittest.main()
